"""
Trade journal screen.

Shows both active (open) trades and completed (closed) trades
with summary statistics at the bottom.
"""

import time as _time

from textual.containers import Container
from textual.widgets import Static, DataTable
from rich.text import Text

from hyperagent.core.state import AgentState


class TradeJournalScreen(Container):

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="journal-container", **kwargs)
        self.state = state
        self._last_open_count = 0
        self._last_closed_count = 0

    def compose(self):
        yield Static("TRADE JOURNAL", id="journal-header")
        yield DataTable(id="journal-table")
        yield Static("", id="journal-summary")

    def on_mount(self):
        table = self.query_one("#journal-table", DataTable)
        table.add_columns(
            "Status", "Time", "Strategy", "Asset", "Side",
            "Entry", "Exit/Current", "PnL", "AI Reasoning",
        )

    def refresh_journal(self, state: AgentState):
        self.state = state

        open_count = len(state.positions)
        closed_count = len(state.trade_history)

        if open_count != self._last_open_count or closed_count != self._last_closed_count:
            self._rebuild_table()
            self._last_open_count = open_count
            self._last_closed_count = closed_count

        self._update_summary()

    def _rebuild_table(self):
        table = self.query_one("#journal-table", DataTable)
        table.clear()

        for pos in self.state.positions:
            time_str = _time.strftime("%H:%M:%S", _time.localtime(pos.entry_time))

            if pos.entry_price >= 10_000:
                entry_str = f"${pos.entry_price:,.0f}"
                current_str = f"${pos.current_price:,.0f}"
            elif pos.entry_price >= 100:
                entry_str = f"${pos.entry_price:,.2f}"
                current_str = f"${pos.current_price:,.2f}"
            else:
                entry_str = f"${pos.entry_price:,.4f}"
                current_str = f"${pos.current_price:,.4f}"

            pnl = pos.unrealized_pnl
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

            reasoning = ""
            if pos.signal and pos.signal.ai_reasoning:
                reasoning = pos.signal.ai_reasoning[:40] + "..." if len(pos.signal.ai_reasoning) > 40 else pos.signal.ai_reasoning
            elif pos.signal:
                reasoning = pos.signal.reason[:40] + "..." if len(pos.signal.reason) > 40 else pos.signal.reason

            table.add_row(
                "OPEN",
                time_str,
                pos.signal.strategy if pos.signal else "?",
                pos.coin,
                pos.side.upper(),
                entry_str,
                current_str,
                pnl_str,
                reasoning or "-",
            )

        for trade in reversed(self.state.trade_history):
            time_str = _time.strftime("%H:%M:%S", _time.localtime(trade.exit_time))

            if trade.entry_price >= 10_000:
                entry_str = f"${trade.entry_price:,.0f}"
                exit_str = f"${trade.exit_price:,.0f}"
            elif trade.entry_price >= 100:
                entry_str = f"${trade.entry_price:,.2f}"
                exit_str = f"${trade.exit_price:,.2f}"
            else:
                entry_str = f"${trade.entry_price:,.4f}"
                exit_str = f"${trade.exit_price:,.4f}"

            pnl_str = f"+${trade.pnl:.2f}" if trade.pnl >= 0 else f"-${abs(trade.pnl):.2f}"

            reasoning = trade.ai_reasoning or trade.signal.reason if trade.signal else "-"
            if len(reasoning) > 40:
                reasoning = reasoning[:37] + "..."

            table.add_row(
                "CLOSED",
                time_str,
                trade.strategy,
                trade.coin,
                trade.side.upper(),
                entry_str,
                exit_str,
                pnl_str,
                reasoning or "-",
            )

    def _update_summary(self):
        summary = self.query_one("#journal-summary", Static)
        output = Text()

        open_count = len(self.state.positions)
        closed_count = self.state.total_trades
        wins = self.state.winning_trades
        win_rate = self.state.win_rate
        total_pnl = self.state.daily_pnl

        unrealized = sum(p.unrealized_pnl for p in self.state.positions)

        output.append("  Open: ", style="dim")
        output.append(f"{open_count}", style="bold #58a6ff")
        output.append("  |  Closed: ", style="dim")
        output.append(f"{closed_count}", style="bold white")
        output.append("  |  Wins: ", style="dim")
        output.append(f"{wins}", style="bold #3fb950")
        output.append("  |  Win Rate: ", style="dim")
        output.append(
            f"{win_rate:.0f}%",
            style="bold #3fb950" if win_rate >= 50 else "bold #f85149"
        )
        output.append("  |  Realized: ", style="dim")
        output.append(
            f"${total_pnl:+.2f}",
            style="bold #3fb950" if total_pnl >= 0 else "bold #f85149"
        )
        output.append("  |  Unrealized: ", style="dim")
        output.append(
            f"${unrealized:+.2f}",
            style="bold #3fb950" if unrealized >= 0 else "bold #f85149"
        )

        summary.update(output)
