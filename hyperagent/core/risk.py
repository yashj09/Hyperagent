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
from typing import Dict, List

import config
from core.state import AgentState, ActivePosition, TradeRecord
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
        """
        is_long = position.side == "long"
        close_side = "sell" if is_long else "buy"

        # --- Take-profit trigger ---
        try:
            tp_result = await self.client.place_trigger_order(
                coin=position.coin,
                side=close_side,
                size=position.size,
                trigger_price=position.take_profit_price,
                is_tp=True,
            )
            status = tp_result.get("status", "error")
            self.state.add_log(
                f"[RISK] Native TP placed for {position.coin} "
                f"@ ${position.take_profit_price:.2f}  (status={status})"
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
                trigger_price=position.stop_loss_price,
                is_tp=False,
            )
            status = sl_result.get("status", "error")
            self.state.add_log(
                f"[RISK] Native SL placed for {position.coin} "
                f"@ ${position.stop_loss_price:.2f}  (status={status})"
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

        for pos in list(self.state.positions):
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

        # Close breached positions
        for pos in positions_to_close:
            try:
                result = await self.client.close_position(pos.coin)
                exit_price = result.executed_price if result.success else pos.current_price

                # Build trade record
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
                self.state.trade_history.append(record)
                self.state.total_trades += 1
                if pnl > 0:
                    self.state.winning_trades += 1
                self.state.daily_pnl += pnl

                # Cancel remaining native orders for this coin
                await self.client.cancel_all_orders(pos.coin)

                # Remove from active positions
                if pos in self.state.positions:
                    self.state.positions.remove(pos)

                messages.append(
                    f"[RISK] Closed {pos.coin} {pos.side.upper()} — "
                    f"PnL: ${pnl:+.2f}"
                )
            except Exception as exc:
                messages.append(
                    f"[RISK] ERROR closing {pos.coin}: {exc}"
                )
                logger.exception("Failed to close position %s", pos.coin)

        return messages

    # ------------------------------------------------------------------
    # Price updates & trailing-stop adjustment
    # ------------------------------------------------------------------

    def update_position_prices(self, prices: Dict[str, float]):
        """
        Update current prices for all positions and adjust trailing stops
        when new highs/lows are printed.
        """
        for pos in self.state.positions:
            new_price = prices.get(pos.coin)
            if new_price is None:
                continue

            pos.current_price = new_price

            is_long = pos.side == "long"

            if is_long:
                # Update high-water mark and tighten trailing stop
                if new_price > pos.high_water_mark:
                    pos.high_water_mark = new_price
                    pos.trailing_stop_price = round(
                        new_price * (1 - config.TRAILING_STOP_PCT), 4
                    )
            else:
                # Short: track low-water mark (stored in high_water_mark field,
                # but semantically it's a low-water mark for shorts)
                if new_price < pos.high_water_mark:
                    pos.high_water_mark = new_price
                    pos.trailing_stop_price = round(
                        new_price * (1 + config.TRAILING_STOP_PCT), 4
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
        """
        if self.state.daily_pnl <= -config.MAX_DAILY_LOSS_USD:
            self.state.add_log(
                f"[RISK] Daily loss limit reached: "
                f"${self.state.daily_pnl:.2f} / -${config.MAX_DAILY_LOSS_USD}"
            )
            return False

        if len(self.state.positions) >= config.MAX_CONCURRENT_POSITIONS:
            self.state.add_log(
                f"[RISK] Max concurrent positions reached: "
                f"{len(self.state.positions)}/{config.MAX_CONCURRENT_POSITIONS}"
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(self, coin: str, price: float) -> float:
        """
        Calculate position size in coin units based on
        POSITION_SIZE_USD and the current *price*.
        Returns the formatted size ready for the exchange.
        """
        if price <= 0:
            return 0.0
        raw_size = config.POSITION_SIZE_USD / price
        return self.client.format_size(raw_size, coin)

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
