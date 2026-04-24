"""
Risk manager for HyperAgent.

Handles:
  - Software trailing stop-loss (polls every 2s, tracks high-water mark)
  - Native HL trigger order placement (as backup TP/SL)
  - Daily loss limit tracking
  - Position size limits
"""

import logging
import time
from typing import Dict, List, Optional

import config
from core.state import AgentState, ActivePosition, Signal, TradeRecord
from core.client import HyperLiquidClient

logger = logging.getLogger(__name__)


class RiskManager:
    """Monitors open positions and enforces risk rules."""

    def __init__(self, client: HyperLiquidClient, state: AgentState):
        self.client = client
        self.state = state

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------

    async def on_position_opened(self, position: ActivePosition):
        """
        Called right after a new position is opened.
        Places native TP and SL trigger orders on HL as a safety net
        (in case the software trailing-stop loop is interrupted).

        Native stops are placed WIDER than software stops by
        config.NATIVE_STOP_WIDEN_MULT so the software trailing loop
        gets the first chance to exit; the native order is disaster
        recovery only.
        """
        is_long = position.side == "long"
        close_side = "sell" if is_long else "buy"

        # Compute widened native SL price.
        entry = position.entry_price
        sw_sl_pct = abs(position.stop_loss_price - entry) / entry if entry > 0 else 0.0
        native_sl_pct = sw_sl_pct * config.NATIVE_STOP_WIDEN_MULT

        if is_long:
            native_sl_price = entry * (1 - native_sl_pct)
            # TP can stay the same — we WANT TP to fire if software loop is down
            native_tp_price = position.take_profit_price
        else:
            native_sl_price = entry * (1 + native_sl_pct)
            native_tp_price = position.take_profit_price

        # --- Take-profit trigger ---
        try:
            tp_result = await self.client.place_trigger_order(
                coin=position.coin,
                side=close_side,
                size=position.size,
                trigger_price=native_tp_price,
                is_tp=True,
            )
            status = tp_result.get("status", "error")
            self.state.add_log(
                f"[RISK] Native TP placed for {position.coin} "
                f"@ ${native_tp_price:.2f}  (status={status})"
            )
        except Exception as exc:
            self.state.add_log(
                f"[RISK] Failed to place native TP for {position.coin}: {exc}"
            )
            logger.exception("Native TP placement failed")

        # --- Stop-loss trigger ---
        try:
            sl_result = await self.client.place_trigger_order(
                coin=position.coin,
                side=close_side,
                size=position.size,
                trigger_price=native_sl_price,
                is_tp=False,
            )
            status = sl_result.get("status", "error")
            self.state.add_log(
                f"[RISK] Native SL placed for {position.coin} "
                f"@ ${native_sl_price:.2f} "
                f"(software SL ${position.stop_loss_price:.2f}, "
                f"widen={config.NATIVE_STOP_WIDEN_MULT}x)  (status={status})"
            )
        except Exception as exc:
            self.state.add_log(
                f"[RISK] Failed to place native SL for {position.coin}: {exc}"
            )
            logger.exception("Native SL placement failed")

    # ------------------------------------------------------------------
    # Trailing-stop engine (called every STOP_LOSS_POLL_INTERVAL seconds)
    # ------------------------------------------------------------------

    async def check_trailing_stops(self) -> List[str]:
        """
        Walk every active position, compare current price to the trailing
        stop, and close the position if breached.

        Returns a list of human-readable log messages for the TUI.
        """
        messages: List[str] = []
        positions_to_close: List[ActivePosition] = []

        # Acquire snapshot under lock and compute close-list.
        with self.state._lock:
            positions_snapshot = list(self.state.positions)

            for pos in positions_snapshot:
                price = pos.current_price
                if price <= 0:
                    continue

                is_long = pos.side == "long"

                if is_long:
                    if price <= pos.trailing_stop_price:
                        positions_to_close.append(pos)
                        messages.append(
                            f"[STOP-LOSS TRIGGERED] {pos.coin} LONG "
                            f"trailing stop hit @ ${price:.2f} "
                            f"(stop was ${pos.trailing_stop_price:.2f})"
                        )
                else:  # short
                    if price >= pos.trailing_stop_price:
                        positions_to_close.append(pos)
                        messages.append(
                            f"[STOP-LOSS TRIGGERED] {pos.coin} SHORT "
                            f"trailing stop hit @ ${price:.2f} "
                            f"(stop was ${pos.trailing_stop_price:.2f})"
                        )

            # Pair-aware expansion: if any position in positions_to_close has a pair_id,
            # drag its siblings into the close list so the hedge doesn't end up naked.
            if positions_to_close:
                pair_ids_closing = {p.pair_id for p in positions_to_close if p.pair_id}
                if pair_ids_closing:
                    for pos in list(self.state.positions):
                        if pos in positions_to_close:
                            continue
                        if pos.pair_id and pos.pair_id in pair_ids_closing:
                            positions_to_close.append(pos)
                            messages.append(
                                f"[RISK] Closing pair sibling {pos.coin} {pos.side.upper()} "
                                f"(pair_id={pos.pair_id[:20]})"
                            )

        # Release lock for network I/O. _close_and_record handles the
        # per-position network call + state mutation + cancel cleanup.
        for pos in positions_to_close:
            msg = await self._close_and_record(pos, tag="RISK")
            messages.append(msg)

        return messages

    # ------------------------------------------------------------------
    # Shared close path — used by check_trailing_stops AND force_close
    # ------------------------------------------------------------------

    async def _close_and_record(self, pos: ActivePosition, tag: str = "RISK") -> str:
        """Close one position, record the trade, cancel leftover native orders.

        Extracted from check_trailing_stops so user-initiated closes (via the
        kill switch) follow the exact same pattern: network call outside the
        lock, state mutations inside, orders cancelled last. Returns a
        single log message describing the outcome.

        `tag` is the log prefix — [RISK] for stop-driven closes, [KILL] for
        user-initiated. Anything else for future flows (e.g. [RECONCILE]).
        """
        try:
            result = await self.client.close_position(pos.coin)
            exit_price = result.executed_price if result.success else pos.current_price

            pnl = self._calculate_pnl(pos, exit_price)
            record = TradeRecord(
                coin=pos.coin,
                side=pos.side,
                strategy=pos.signal.strategy,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                size=pos.size,
                pnl=pnl,
                signal=pos.signal,
                entry_time=pos.entry_time,
                exit_time=time.time(),
                ai_reasoning=pos.signal.ai_reasoning,
            )

            with self.state._lock:
                self.state.trade_history.append(record)
                self.state.total_trades += 1
                if pnl > 0:
                    self.state.winning_trades += 1
                self.state.daily_pnl += pnl
                if pos in self.state.positions:
                    self.state.positions.remove(pos)

            # Cancel leftover trigger orders (TP/SL) outside the lock — this
            # is a network call and can fail without affecting state.
            await self.client.cancel_all_orders(pos.coin)

            return (
                f"[{tag}] Closed {pos.coin} {pos.side.upper()} — "
                f"PnL: ${pnl:+.2f}"
            )
        except Exception as exc:
            logger.exception("Failed to close position %s", pos.coin)
            return f"[{tag}] ERROR closing {pos.coin}: {exc}"

    async def force_close(
        self, coins: Optional[List[str]] = None
    ) -> List[str]:
        """User-initiated close of positions.

        coins=None  -> flatten every open position
        coins=[...] -> close only positions for those coins (plus any
                       pair siblings — we never leave a pair half-open)

        Returns a list of log messages (same shape as check_trailing_stops)
        so callers can pipe them into state.add_log.

        Does NOT stop the strategy loop — the user must click Stop on the
        Strategy tab if they want to block further entries. This matches
        the locked-in decision: "Positions only".
        """
        messages: List[str] = []
        positions_to_close: List[ActivePosition] = []

        # Build close list under lock. Same pair-expansion pattern used by
        # check_trailing_stops — if we close one leg of a pair, the sibling
        # follows automatically so the hedge never ends up naked.
        with self.state._lock:
            for pos in list(self.state.positions):
                if coins is None or pos.coin in coins:
                    positions_to_close.append(pos)

            if positions_to_close:
                pair_ids_closing = {
                    p.pair_id for p in positions_to_close if p.pair_id
                }
                if pair_ids_closing:
                    for pos in list(self.state.positions):
                        if pos in positions_to_close:
                            continue
                        if pos.pair_id and pos.pair_id in pair_ids_closing:
                            positions_to_close.append(pos)
                            messages.append(
                                f"[KILL] Dragging pair sibling {pos.coin} "
                                f"{pos.side.upper()} (pair_id={pos.pair_id[:20]})"
                            )

        if not positions_to_close:
            return [
                "[KILL] No positions to close"
                if coins is None
                else f"[KILL] No matching positions for {coins}"
            ]

        # Announce before network work so users see immediate feedback even
        # if the close takes a few seconds round-trip.
        messages.insert(
            0,
            f"[KILL] Force-closing {len(positions_to_close)} position"
            f"{'s' if len(positions_to_close) != 1 else ''}"
            f"{(' — coins: ' + ','.join(sorted({p.coin for p in positions_to_close}))) if coins else ''}",
        )

        for pos in positions_to_close:
            messages.append(await self._close_and_record(pos, tag="KILL"))

        return messages

    # ------------------------------------------------------------------
    # Price updates & trailing-stop adjustment
    # ------------------------------------------------------------------

    def update_position_prices(self, prices: Dict[str, float]):
        with self.state._lock:
            for pos in self.state.positions:
                new_price = prices.get(pos.coin)
                if new_price is None:
                    continue

                pos.current_price = new_price

                trail_pct = config.TRAILING_STOP_PCT
                if pos.signal and pos.signal.trailing_stop_pct:
                    trail_pct = pos.signal.trailing_stop_pct

                is_long = pos.side == "long"

                if is_long:
                    if new_price > pos.high_water_mark:
                        pos.high_water_mark = new_price
                        pos.trailing_stop_price = round(
                            new_price * (1 - trail_pct), 4
                        )
                else:
                    if new_price < pos.high_water_mark:
                        pos.high_water_mark = new_price
                        pos.trailing_stop_price = round(
                            new_price * (1 + trail_pct), 4
                        )

                # Unrealised PnL
                pos.unrealized_pnl = self._calculate_pnl(pos, new_price)

    # ------------------------------------------------------------------
    # Daily limits
    # ------------------------------------------------------------------

    def check_daily_limits(self) -> bool:
        """
        Returns True if we can still open new trades today.
        False when the daily loss cap has been breached or max
        concurrent positions are filled.

        Loss limit is the STRICTER of:
          - absolute USD cap (config.MAX_DAILY_LOSS_USD)
          - percent-of-equity cap (config.MAX_DAILY_LOSS_PCT)
        """
        abs_limit = -config.MAX_DAILY_LOSS_USD
        pct_limit = (
            -(self.state.account_value * config.MAX_DAILY_LOSS_PCT)
            if self.state.account_value > 0
            else abs_limit
        )
        # Stricter = smaller loss tolerated = LESS negative = max()
        effective_limit = max(abs_limit, pct_limit)
        if self.state.daily_pnl <= effective_limit:
            self.state.add_log(
                f"[RISK] Daily loss limit: ${self.state.daily_pnl:.2f} <= ${effective_limit:.2f} "
                f"(abs=${abs_limit:.0f}, {config.MAX_DAILY_LOSS_PCT*100:.0f}%=${pct_limit:.0f})"
            )
            return False

        if len(self.state.positions) >= config.MAX_CONCURRENT_POSITIONS:
            self.state.add_log(
                f"[RISK] Max concurrent positions reached: "
                f"{len(self.state.positions)}/{config.MAX_CONCURRENT_POSITIONS}"
            )
            return False

        return True

    def check_total_exposure(self, new_size_usd: float) -> bool:
        """Block if adding new_size_usd would push total notional above cap."""
        # If account value unknown (price feed hasn't populated yet), allow.
        if self.state.account_value <= 0:
            return True
        with self.state._lock:
            total_notional = sum(
                pos.size * pos.current_price for pos in self.state.positions
            )
        cap = self.state.account_value * config.MAX_TOTAL_EXPOSURE_MULT
        if total_notional + new_size_usd > cap:
            self.state.add_log(
                f"[RISK] Total exposure cap: ${total_notional:.0f} + ${new_size_usd:.0f} "
                f"> ${cap:.0f} ({config.MAX_TOTAL_EXPOSURE_MULT}x account)"
            )
            return False
        return True

    def check_net_directional(self, direction: str) -> bool:
        """Block if adding this direction would push net bias past the cap."""
        with self.state._lock:
            long_count = sum(1 for p in self.state.positions if p.side == "long")
            short_count = sum(1 for p in self.state.positions if p.side == "short")
        net = long_count - short_count
        if direction == "LONG" and net >= config.MAX_NET_DIRECTIONAL_POSITIONS:
            self.state.add_log(
                f"[RISK] Net directional cap: {net} longs - shorts already at max (+{config.MAX_NET_DIRECTIONAL_POSITIONS})"
            )
            return False
        if direction == "SHORT" and net <= -config.MAX_NET_DIRECTIONAL_POSITIONS:
            self.state.add_log(
                f"[RISK] Net directional cap: {net} longs - shorts already at min (-{config.MAX_NET_DIRECTIONAL_POSITIONS})"
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self, coin: str, price: float, signal: Optional[Signal] = None
    ) -> float:
        if price <= 0:
            return 0.0

        base = config.POSITION_SIZE_USD
        if signal and signal.position_size_usd:
            base = signal.position_size_usd

        atr_val = self.state.atr.get(coin, 0)
        if atr_val > 0 and price > 0:
            atr_pct = atr_val / price
            vol_scalar = 0.02 / max(atr_pct, 0.005)
        else:
            vol_scalar = 1.0

        conv_scalar = 1.0
        if signal and signal.score:
            conv_scalar = signal.score / 75.0

        final_usd = base * vol_scalar * conv_scalar

        # Skip-trade floor: if size falls below the minimum, return 0 to
        # signal the caller to drop the trade rather than forcing a
        # floor-sized position.
        if final_usd < config.MIN_POSITION_SIZE_USD:
            return 0.0

        final_usd = min(config.MAX_POSITION_SIZE_USD, final_usd)

        raw_size = final_usd / price
        return self.client.format_size(raw_size, coin)

    def check_correlation_guard(self, coin: str, direction: str) -> bool:
        for group in config.CORRELATED_GROUPS:
            group_set = set(group)
            if coin not in group_set:
                continue
            with self.state._lock:
                same_dir_count = sum(
                    1
                    for p in self.state.positions
                    if p.coin in group_set and p.side == direction.lower()
                )
            if same_dir_count >= 2:
                self.state.add_log(
                    f"[RISK] Correlation guard: blocked {direction} {coin} "
                    f"({same_dir_count} correlated positions already open)"
                )
                return False
        return True

    def is_cooled_down(self, coin: str, cooldown: int = 0) -> bool:
        cd = cooldown or config.TRADE_COOLDOWN_DEFAULT
        last = self.state.last_trade_time.get(coin, 0)
        return (time.time() - last) >= cd

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_pnl(pos: ActivePosition, exit_price: float) -> float:
        """Compute PnL in USD for a given exit price."""
        if pos.side == "long":
            return (exit_price - pos.entry_price) * pos.size
        else:  # short
            return (pos.entry_price - exit_price) * pos.size
