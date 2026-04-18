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

import config

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
        import requests
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        session.mount("https://", adapter)
        self.info = Info(hl_constants.MAINNET_API_URL, skip_ws=True)
        self.info.session = session

        # ----- Testnet Exchange (trading) -----
        self.exchange: Optional[Exchange] = None
        self.address: Optional[str] = None

        key = private_key or config.TESTNET_PRIVATE_KEY
        if key:
            account = Account.from_key(key)
            self.address = account.address
            base_url = (
                hl_constants.TESTNET_API_URL if testnet
                else hl_constants.MAINNET_API_URL
            )
            self.exchange = Exchange(account, base_url)
            logger.info(
                "Exchange client initialised on %s for %s",
                "testnet" if testnet else "mainnet",
                self.address,
            )

        # Cache the universe metadata so we can resolve coin -> asset index
        self._meta: Optional[dict] = None

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

    async def get_prices(self) -> Dict[str, float]:
        """Return {coin: mid_price} for every listed perpetual."""
        raw = await asyncio.to_thread(self.info.all_mids)
        return {k: float(v) for k, v in raw.items()}

    async def get_user_state(self, address: str) -> dict:
        """Full margin / position state for an arbitrary address."""
        return await asyncio.to_thread(self.info.user_state, address)

    async def get_account_info(self) -> dict:
        """Convenience — our own account state on the exchange."""
        if not self.address:
            return {}
        return await asyncio.to_thread(self.info.user_state, self.address)

    async def get_meta_and_asset_ctxs(self) -> dict:
        """
        Returns the meta + per-asset context (funding, OI, oracle prices).
        The SDK exposes this through ``info.meta_and_asset_ctxs()``.
        """
        data = await asyncio.to_thread(self.info.meta_and_asset_ctxs)
        self._meta = data  # cache for coin->index lookups
        return data

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
