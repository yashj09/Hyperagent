"""
Trade journal screen.

Displays a DataTable of completed trades with summary statistics at the bottom.
"""

import time as _time

from textual.containers import Container
from textual.widgets import Static, DataTable
from rich.text import Text

from core.state import AgentState


class TradeJournalScreen(Container):
    """Trade history table with summary stats."""

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="journal-container", **kwargs)
        self.state = state
        self._row_count = 0

    def compose(self):
        yield Static("TRADE JOURNAL", id="journal-header")
        yield DataTable(id="journal-table")
        yield Static("", id="journal-summary")

    def on_mount(self):
        """Set up table columns."""
        table = self.query_one("#journal-table", DataTable)
        table.add_columns(
            "Time",
            "Strategy",
            "Asset",
            "Side",
            "Entry",
            "Exit",
            "PnL",
            "AI Reasoning",
        )
        # Populate with existing history
        self._full_refresh()

    def _full_refresh(self):
        """Rebuild the entire table from state."""
        table = self.query_one("#journal-table", DataTable)
        table.clear()
        self._row_count = 0

        for trade in self.state.trade_history:
            self._add_trade_row(table, trade)

        self._update_summary()

    def _add_trade_row(self, table: DataTable, trade):
        """Add a single trade record as a table row."""
        # Format time
        time_str = _time.strftime(
            "%H:%M:%S", _time.localtime(trade.exit_time)
        )

        # Format strategy
        strategy_str = trade.strategy.capitalize()

        # Format side
        side_str = trade.side.upper()

        # Format prices
        if trade.entry_price >= 10_000:
            entry_str = f"${trade.entry_price:,.0f}"
            exit_str = f"${trade.exit_price:,.0f}"
        elif trade.entry_price >= 100:
            entry_str = f"${trade.entry_price:,.2f}"
            exit_str = f"${trade.exit_price:,.2f}"
        else:
            entry_str = f"${trade.entry_price:,.4f}"
            exit_str = f"${trade.exit_price:,.4f}"

        # Format PnL with sign
        if trade.pnl >= 0:
            pnl_str = f"+${trade.pnl:.2f}"
        else:
            pnl_str = f"-${abs(trade.pnl):.2f}"

        # AI reasoning (truncated)
        reasoning = trade.ai_reasoning or "-"
        if len(reasoning) > 40:
            reasoning = reasoning[:37] + "..."

        table.add_row(
            time_str,
            strategy_str,
            trade.coin,
            side_str,
            entry_str,
            exit_str,
            pnl_str,
            reasoning,
        )
        self._row_count += 1

    def _update_summary(self):
        """Compute and display summary statistics."""
        summary = self.query_one("#journal-summary", Static)
        output = Text()

        total = self.state.total_trades
        wins = self.state.winning_trades
        win_rate = self.state.win_rate
        total_pnl = self.state.daily_pnl

        output.append("  Total Trades: ", style="dim")
        output.append(f"{total}", style="bold white")
        output.append("  |  Wins: ", style="dim")
        output.append(f"{wins}", style="bold #3fb950")
        output.append("  |  Win Rate: ", style="dim")

        if win_rate >= 50:
            output.append(f"{win_rate:.1f}%", style="bold #3fb950")
        else:
            output.append(f"{win_rate:.1f}%", style="bold #f85149")

        output.append("  |  Total PnL: ", style="dim")
        if total_pnl >= 0:
            output.append(f"+${total_pnl:.2f}", style="bold #3fb950")
        else:
            output.append(f"-${abs(total_pnl):.2f}", style="bold #f85149")

        summary.update(output)

    def refresh_journal(self, state: AgentState):
        """
        Incrementally update the journal — add new rows and refresh summary.
        """
        self.state = state
        table = self.query_one("#journal-table", DataTable)

        # Add any new trades that appeared since last refresh
        new_trades = self.state.trade_history[self._row_count:]
        for trade in new_trades:
            self._add_trade_row(table, trade)

        self._update_summary()
