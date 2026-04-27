"""
Live positions panel widget.

Shows active positions with entry, size, PnL, trailing stop visualization,
and take-profit targets. Flashes red on stop-loss events.
"""

import asyncio

from textual.widgets import Static
from rich.text import Text

from hyperagent.core.state import AgentState, ActivePosition


class PositionsPanel(Static):
    """Displays active positions with live PnL and trailing-stop info."""

    def __init__(self, **kwargs):
        super().__init__("POSITIONS\n" + "=" * 45 + "\n\n  No open positions.", id="positions-panel", **kwargs)
        self._flash_active = False

    def update_positions(self, state: AgentState):
        """Rebuild the positions display from current state."""
        output = Text()
        output.append("ACTIVE POSITIONS\n", style="bold cyan")
        output.append("=" * 55 + "\n", style="dim")

        positions = state.positions
        if not positions:
            # Explain *why* it's empty so "No positions" doesn't look
            # broken to a first-time user staring at a still screen.
            # Three states: strategy not running (user hasn't hit Start),
            # running but no signals yet (scanning), or running and
            # actively evaluating (rare to observe but covered).
            if not state.is_running:
                output.append(
                    "\n  No open positions.\n",
                    style="dim italic",
                )
                output.append(
                    "  Strategy is stopped. Press 's' → Start to begin.\n",
                    style="dim",
                )
            else:
                output.append(
                    "\n  No open positions.\n",
                    style="dim italic",
                )
                output.append(
                    "  Strategy running — scanning for entries...\n",
                    style="#3fb950",
                )
            # Show account info if available
            if state.account_value > 0:
                output.append(f"\n  Account Value: ", style="dim")
                output.append(f"${state.account_value:,.2f}\n", style="bold white")
                output.append(f"  Available Margin: ", style="dim")
                output.append(f"${state.available_margin:,.2f}\n", style="white")

            self.update(output)
            return

        for pos in positions:
            self._render_position(output, pos)

        # Summary line
        total_pnl = sum(p.unrealized_pnl for p in positions)
        output.append("\n" + "-" * 55 + "\n", style="dim")
        output.append(f"  Total Unrealized PnL: ", style="dim")
        if total_pnl >= 0:
            output.append(f"+${total_pnl:.2f}\n", style="bold #3fb950")
        else:
            output.append(f"-${abs(total_pnl):.2f}\n", style="bold #f85149")

        output.append(f"  Daily PnL: ", style="dim")
        if state.daily_pnl >= 0:
            output.append(f"+${state.daily_pnl:.2f}\n", style="#3fb950")
        else:
            output.append(f"-${abs(state.daily_pnl):.2f}\n", style="#f85149")

        self.update(output)

    def _render_position(self, output: Text, pos: ActivePosition):
        """Render a single position block."""
        side_str = pos.side.upper()
        side_style = "bold #f85149" if pos.side == "short" else "bold #3fb950"

        # Header: BTC SHORT @ $84,532
        output.append(f"\n  {pos.coin} ", style="bold white")
        output.append(f"{side_str}", style=side_style)
        output.append(f" @ ", style="dim")
        output.append(f"${pos.entry_price:,.2f}\n", style="white")

        # Size
        notional = pos.size * pos.current_price if pos.current_price > 0 else pos.size * pos.entry_price
        output.append(f"    Size: {pos.size} {pos.coin}", style="dim")
        output.append(f" (${notional:,.2f})\n", style="dim")

        # PnL
        pnl = pos.unrealized_pnl
        pnl_pct = 0.0
        cost_basis = pos.entry_price * pos.size
        if cost_basis > 0:
            pnl_pct = (pnl / cost_basis) * 100

        output.append("    PnL: ", style="dim")
        if pnl >= 0:
            output.append(
                f"+${pnl:.2f} (+{pnl_pct:.1f}%)\n",
                style="bold #3fb950",
            )
        else:
            output.append(
                f"-${abs(pnl):.2f} ({pnl_pct:.1f}%)\n",
                style="bold #f85149",
            )

        # Stop-loss
        output.append(f"    Stop: ", style="dim")
        output.append(f"${pos.trailing_stop_price:,.2f}", style="#d29922")
        output.append(" (trailing)\n", style="dim")

        # High-water mark
        hwm_label = "HWM" if pos.side == "long" else "LWM"
        output.append(f"    Trail {hwm_label}: ", style="dim")
        output.append(f"${pos.high_water_mark:,.2f}\n", style="white")

        # Take-profit
        output.append(f"    TP: ", style="dim")
        output.append(f"${pos.take_profit_price:,.2f}\n", style="#3fb950")

    def trigger_stop_loss_flash(self):
        """
        Flash the widget red for 3 seconds when a stop-loss fires.
        Uses add_class / remove_class for the CSS animation.
        """
        if self._flash_active:
            return
        self._flash_active = True
        self.add_class("stop-loss-flash")
        self.set_timer(3.0, self._remove_flash)

    def _remove_flash(self):
        """Remove the stop-loss flash class after the timer expires."""
        self.remove_class("stop-loss-flash")
        self._flash_active = False
