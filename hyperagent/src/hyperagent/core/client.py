"""
HyperLiquid client wrapper for HyperAgent.

Maintains two SDK connections:
  - Mainnet Info client (read-only, no key needed) for market data
  - Testnet Exchange client (needs private key) for trading

All synchronous SDK calls are wrapped with asyncio.to_thread() so they
play nicely with Textual's async event loop.
"""

import asyncio
import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants as hl_constants
from eth_account import Account

from hyperagent import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type returned by order helpers
# ---------------------------------------------------------------------------

@dataclass
class TradeResult:
    success: bool
    order_id: Optional[str]
    executed_price: float
    executed_size: float
    error_message: Optional[str]
    timestamp: float


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class HyperLiquidClient:
    """Unified async wrapper around the HyperLiquid Python SDK."""

    def __init__(self, private_key: str = "", testnet: bool = True):
        # ----- Mainnet Info (read-only, public) -----
        # The HL SDK's Info() constructor makes a blocking spotMeta HTTP
        # call on init. If DNS/network is flaky at app launch, this used
        # to raise and crash the whole app before workers could start.
        # Now we retry a few times with backoff, and if still failing,
        # log clearly and let the app boot anyway — background workers
        # will recover once the network returns (get_prices/etc. already
        # handle ConnectionError in their own loops).
        import requests
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        session.mount("https://", adapter)

        self.info = self._init_info_with_retries(
            hl_constants.MAINNET_API_URL, session, max_retries=3
        )

        # ----- Testnet Exchange (trading) -----
        self.exchange: Optional[Exchange] = None
        self.address: Optional[str] = None

        key = private_key or config.HL_AGENT_PRIVATE_KEY
        if key:
            account = Account.from_key(key)
            agent_addr = account.address
            # Agent-wallet mode: agent key signs, trades target the main
            # wallet via account_address. If HL_MAIN_ADDRESS is absent we
            # fall back to single-key mode (the signer is also the master),
            # covering legacy TESTNET_PRIVATE_KEY setups that haven't migrated.
            main_addr = config.HL_MAIN_ADDRESS or agent_addr
            self.address = main_addr
            base_url = (
                hl_constants.TESTNET_API_URL if testnet
                else hl_constants.MAINNET_API_URL
            )
            self.exchange = Exchange(
                account, base_url, account_address=main_addr
            )
            mode = "agent->main" if config.HL_MAIN_ADDRESS else "single-key"
            logger.info(
                "Exchange client initialised on %s (%s): signer=%s main=%s",
                "testnet" if testnet else "mainnet",
                mode, agent_addr, main_addr,
            )

        # Cache the universe metadata so we can resolve coin -> asset index
        self._meta: Optional[dict] = None

    # ------------------------------------------------------------------
    # Resilient Info construction
    # ------------------------------------------------------------------

    @staticmethod
    def _init_info_with_retries(url: str, session, max_retries: int = 3):
        """Construct Info() with retries for transient DNS/network failures.

        The HL SDK's Info() constructor calls spotMeta() synchronously at
        init time. A single DNS hiccup (macOS mDNSResponder flake, Wi-Fi
        transition) would previously kill the whole app. Now we retry,
        and if everything fails, return a half-constructed Info whose
        session points at the right URL — subsequent calls will retry
        on their own and recover when the network returns.
        """
        import time as _t
        last_exc: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                info = Info(url, skip_ws=True)
                info.session = session
                return info
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "Info() init attempt %d/%d failed: %s — retrying in %ds",
                    attempt + 1, max_retries, exc, wait,
                )
                _t.sleep(wait)

        # All retries failed. Build a minimal Info-like object that exposes
        # a .session and .base_url so callers can still try — they'll fail
        # the same way on their first call but won't crash at construction.
        logger.error(
            "Info() init failed after %d retries (%s). Continuing in "
            "degraded mode — network calls will retry individually.",
            max_retries, last_exc,
        )
        # Create a stub Info that DOESN'T call spot_meta on construct.
        # We do this by constructing without calling __init__, then
        # patching the bare minimum attributes the SDK's methods need.
        stub = Info.__new__(Info)
        stub.base_url = url
        stub.session = session
        stub.session.headers.update({"Content-Type": "application/json"})
        # HL SDK methods read .coin_to_asset, .name_to_coin, etc. from
        # spot_meta results. If those aren't populated, each method call
        # will try to populate them — which will then succeed when the
        # network is back. If the user calls a method that references
        # .name_to_coin before the network recovers, they get a clear
        # AttributeError which the caller's try/except will handle.
        return stub

    # ------------------------------------------------------------------
    # Price / size formatting
    # ------------------------------------------------------------------

    @staticmethod
    def format_price(price: float, coin: str = "BTC") -> float:
        """Round a price to the exchange tick size for *coin*."""
        if coin == "BTC":
            if price > 100_000:
                return round(price)                # $1 tick
            if price > 10_000:
                return round(price, 1)             # $0.1
            if price > 1_000:
                return round(price, 2)             # $0.01
            return round(price, 3)                 # $0.001
        if coin == "ETH":
            if price > 10_000:
                return round(price, 1)
            if price > 1_000:
                return round(price, 2)
            return round(price, 3)
        # Default for altcoins
        return round(price, 3)

    @staticmethod
    def format_size(size: float, coin: str = "BTC") -> float:
        """Round a size to an acceptable lot increment for *coin*."""
        if coin == "BTC":
            return round(size, 5)   # e.g. 0.00100
        if coin == "ETH":
            return round(size, 4)
        if coin in ("SOL", "AVAX", "LINK", "SUI"):
            return round(size, 2)
        if coin in ("DOGE", "XRP"):
            return round(size, 1)
        return round(size, 2)

    # ------------------------------------------------------------------
    # Market data (read via mainnet Info)
    # ------------------------------------------------------------------
    #
    # All read methods absorb 429 (rate limit) errors at this layer and
    # return an empty/None sentinel. This prevents the noisy multi-kilobyte
    # header dump that HL's SDK puts into its exception when CloudFront
    # rate-limits us. Callers get a clean empty result and log a short
    # message; the _rate_limited_until timestamp forces ALL subsequent
    # read calls in the next 60 seconds to short-circuit without hitting
    # the network, so one 429 doesn't immediately cause 5 more.

    _rate_limited_until: float = 0.0

    def _is_rate_limited(self) -> bool:
        return time.time() < self._rate_limited_until

    def _note_rate_limit(self, seconds: float = 60.0) -> None:
        """Mark the client as rate-limited for the next *seconds* seconds."""
        self._rate_limited_until = max(
            self._rate_limited_until, time.time() + seconds
        )

    @staticmethod
    def _is_429(exc: BaseException) -> bool:
        """Detect a 429 in the many shapes HL's SDK wraps errors in."""
        # The SDK raises a tuple-like error where the first element is
        # the HTTP status code. We also check string form as a fallback.
        try:
            if exc.args and isinstance(exc.args[0], (tuple, list)):
                status = exc.args[0][0]
                if status == 429:
                    return True
        except Exception:
            pass
        msg = str(exc)
        return msg.startswith("(429,") or " 429 " in msg or "429," in msg

    async def get_prices(self) -> Dict[str, float]:
        """Return {coin: mid_price} for every listed perpetual, or {} on rate-limit."""
        if self._is_rate_limited():
            return {}
        try:
            raw = await asyncio.to_thread(self.info.all_mids)
            return {k: float(v) for k, v in raw.items()}
        except Exception as exc:
            if self._is_429(exc):
                self._note_rate_limit(60.0)
                # Raise a small, clean exception so the caller's log isn't
                # flooded with header dumps. Callers catch Exception so this
                # still takes the error path.
                raise RuntimeError("HL rate-limited (429) — backing off 60s") from None
            raise

    async def get_user_state(self, address: str) -> dict:
        """Full margin / position state for an arbitrary address."""
        if self._is_rate_limited():
            return {}
        try:
            return await asyncio.to_thread(self.info.user_state, address)
        except Exception as exc:
            if self._is_429(exc):
                self._note_rate_limit(60.0)
                return {}
            raise

    async def get_account_info(self) -> dict:
        """Convenience — our own account state on the exchange."""
        if not self.address:
            return {}
        return await self.get_user_state(self.address)

    async def get_meta_and_asset_ctxs(self) -> dict:
        """
        Returns the meta + per-asset context (funding, OI, oracle prices).
        The SDK exposes this through ``info.meta_and_asset_ctxs()``.
        Returns {} on rate-limit so callers can treat "no fresh data"
        and "rate limited" the same way.
        """
        if self._is_rate_limited():
            return {}
        try:
            data = await asyncio.to_thread(self.info.meta_and_asset_ctxs)
            self._meta = data  # cache for coin->index lookups
            return data
        except Exception as exc:
            if self._is_429(exc):
                self._note_rate_limit(60.0)
                return {}
            raise

    async def get_candles(
        self, coin: str, interval: str = "1h", count: int = 100
    ) -> List[dict]:
        """Fetch OHLCV candles for *coin*."""
        now_ms = int(time.time() * 1000)
        # Approximate interval to ms for the lookback window
        interval_map = {
            "1m": 60_000, "5m": 300_000, "15m": 900_000,
            "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
        }
        ms_per = interval_map.get(interval, 3_600_000)
        start_ms = now_ms - (count * ms_per)

        candles = await asyncio.to_thread(
            self.info.candles_snapshot, coin, interval, start_ms, now_ms
        )
        return candles

    # ------------------------------------------------------------------
    # Order placement (via testnet Exchange)
    # ------------------------------------------------------------------

    def _require_exchange(self):
        if self.exchange is None:
            raise RuntimeError(
                "Exchange client not initialised — provide a private key."
            )

    async def place_market_order(
        self,
        coin: str,
        side: str,
        size: float,
        reduce_only: bool = False,
    ) -> TradeResult:
        """
        Place an aggressive limit (IOC) order at mid +/- slippage.

        *side* is ``"buy"`` or ``"sell"`` (SDK convention).
        """
        self._require_exchange()

        prices = await self.get_prices()
        mid = prices.get(coin)
        if mid is None:
            return TradeResult(
                success=False, order_id=None, executed_price=0.0,
                executed_size=0.0,
                error_message=f"No mid-price for {coin}",
                timestamp=time.time(),
            )

        is_buy = side.lower() in ("buy", "long")
        slippage = config.SLIPPAGE_PCT
        limit_px = mid * (1 + slippage) if is_buy else mid * (1 - slippage)
        limit_px = self.format_price(limit_px, coin)
        sz = self.format_size(size, coin)

        try:
            result = await asyncio.to_thread(
                self.exchange.market_open,
                coin,
                is_buy,
                sz,
                limit_px,
                slippage,
            )

            status = result.get("status", "")
            if status == "ok":
                statuses = (
                    result.get("response", {})
                    .get("data", {})
                    .get("statuses", [])
                )
                first = statuses[0] if statuses else {}

                if "error" in first:
                    return TradeResult(
                        success=False, order_id=None,
                        executed_price=0.0, executed_size=0.0,
                        error_message=first["error"],
                        timestamp=time.time(),
                    )

                if "filled" in first:
                    f = first["filled"]
                    return TradeResult(
                        success=True,
                        order_id=str(f.get("oid", "")),
                        executed_price=float(f.get("avgPx", limit_px)),
                        executed_size=float(f.get("totalSz", sz)),
                        error_message=None,
                        timestamp=time.time(),
                    )

                if "resting" in first:
                    return TradeResult(
                        success=True,
                        order_id=str(first["resting"].get("oid", "")),
                        executed_price=limit_px,
                        executed_size=sz,
                        error_message="Order resting (not yet filled)",
                        timestamp=time.time(),
                    )

                return TradeResult(
                    success=True, order_id=None,
                    executed_price=limit_px, executed_size=sz,
                    error_message=None, timestamp=time.time(),
                )

            return TradeResult(
                success=False, order_id=None, executed_price=0.0,
                executed_size=0.0,
                error_message=f"Order rejected: {result}",
                timestamp=time.time(),
            )
        except Exception as exc:
            logger.exception("place_market_order failed")
            return TradeResult(
                success=False, order_id=None, executed_price=0.0,
                executed_size=0.0, error_message=str(exc),
                timestamp=time.time(),
            )

    async def place_trigger_order(
        self,
        coin: str,
        side: str,
        size: float,
        trigger_price: float,
        is_tp: bool = False,
    ) -> dict:
        """
        Place a native TP or SL trigger order on HyperLiquid.

        The SDK's ``order`` method accepts ``order_type`` with trigger info.
        *side* should be the closing side ("sell" to close a long, etc.).
        """
        self._require_exchange()

        trigger_px = self.format_price(trigger_price, coin)
        sz = self.format_size(size, coin)
        is_buy = side.lower() in ("buy", "long")

        # For TP on a long we trigger above (tp), for SL on a long we trigger below (sl)
        tpsl = "tp" if is_tp else "sl"

        order_type = {
            "trigger": {
                "triggerPx": trigger_px,
                "isMarket": True,
                "tpsl": tpsl,
            }
        }

        try:
            result = await asyncio.to_thread(
                self.exchange.order,
                coin,
                is_buy,
                sz,
                trigger_px,   # limit price (ignored when isMarket=True)
                order_type,
                reduce_only=True,
            )
            logger.info("Trigger order result: %s", result)
            return result
        except Exception as exc:
            logger.exception("place_trigger_order failed")
            return {"status": "error", "error": str(exc)}

    async def close_position(self, coin: str) -> TradeResult:
        """Close our entire position in *coin* via a market order."""
        self._require_exchange()

        state = await self.get_account_info()
        positions = state.get("assetPositions", [])
        pos = None
        for p in positions:
            item = p.get("position", {})
            if item.get("coin") == coin:
                pos = item
                break

        if pos is None:
            return TradeResult(
                success=False, order_id=None, executed_price=0.0,
                executed_size=0.0,
                error_message=f"No open position for {coin}",
                timestamp=time.time(),
            )

        szi = float(pos.get("szi", 0))
        if szi == 0:
            return TradeResult(
                success=False, order_id=None, executed_price=0.0,
                executed_size=0.0,
                error_message=f"Position size is 0 for {coin}",
                timestamp=time.time(),
            )

        abs_size = abs(szi)

        try:
            result = await asyncio.to_thread(
                self.exchange.market_close, coin, sz=abs_size
            )
            status = result.get("status", "")
            if status == "ok":
                statuses = (
                    result.get("response", {})
                    .get("data", {})
                    .get("statuses", [])
                )
                first = statuses[0] if statuses else {}
                if "filled" in first:
                    f = first["filled"]
                    return TradeResult(
                        success=True,
                        order_id=str(f.get("oid", "")),
                        executed_price=float(f.get("avgPx", 0)),
                        executed_size=float(f.get("totalSz", abs_size)),
                        error_message=None,
                        timestamp=time.time(),
                    )
            return TradeResult(
                success=True, order_id=None,
                executed_price=0.0, executed_size=abs_size,
                error_message=None, timestamp=time.time(),
            )
        except Exception as exc:
            logger.exception("close_position failed for %s", coin)
            return TradeResult(
                success=False, order_id=None, executed_price=0.0,
                executed_size=0.0, error_message=str(exc),
                timestamp=time.time(),
            )

    async def cancel_all_orders(self, coin: str) -> bool:
        """Cancel every open order for *coin*. Returns True on success."""
        self._require_exchange()
        try:
            open_orders = await asyncio.to_thread(
                self.info.open_orders, self.address
            )
            oids = [
                o["oid"] for o in open_orders
                if o.get("coin") == coin
            ]
            if not oids:
                return True

            for oid in oids:
                await asyncio.to_thread(
                    self.exchange.cancel, coin, oid
                )
            return True
        except Exception as exc:
            logger.exception("cancel_all_orders failed for %s", coin)
            return False
