"""
Analytics tab — equity curve, PnL bar charts, and attribution tables.

Three sections stacked top-to-bottom:

  1. Equity curve (top half)
     Line chart of (timestamp, equity_usd) from state.equity_history.

  2. Per-strategy + per-coin PnL (bottom half, side by side)
     Horizontal bar charts summing realized + unrealized PnL grouped by
     strategy and by coin.

  3. Attribution tables (below the bar charts, scrollable)
     Three DataTables breaking down trades by strategy / coin / side
     with trade count, win rate, realized, unrealized, total, avg PnL.

All compute is O(n_trades + n_positions) per refresh. No caching — trade
volume is small enough (<1000/day) that recomputing from scratch on each
DASHBOARD_REFRESH_RATE tick is trivial.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List

from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import DataTable, Static
from textual_plotext import PlotextPlot

from core.state import ActivePosition, AgentState, TradeRecord


# ---------------------------------------------------------------------------
# Aggregation helpers (shared by charts + tables)
# ---------------------------------------------------------------------------


@dataclass
class AttributionRow:
    """Per-group (strategy/coin/side) aggregated performance."""

    key: str
    trades: int = 0
    wins: int = 0
    realized: float = 0.0
    unrealized: float = 0.0

    @property
    def total(self) -> float:
        return self.realized + self.unrealized

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades else 0.0

    @property
    def avg_pnl(self) -> float:
        return (self.realized / self.trades) if self.trades else 0.0


def _aggregate(
    trades: List[TradeRecord],
    positions: List[ActivePosition],
    key_fn_trade: Callable[[TradeRecord], str],
    key_fn_position: Callable[[ActivePosition], str],
) -> Dict[str, AttributionRow]:
    """Build a {group_key -> AttributionRow} aggregation.

    key_fn_trade and key_fn_position extract the grouping key from a
    TradeRecord and ActivePosition respectively. Splitting them lets us
    group by coin (same field on both types) or by strategy (on trade
    it's .strategy, on position it's .signal.strategy).
    """
    rows: Dict[str, AttributionRow] = defaultdict(
        lambda: AttributionRow(key="")
    )

    for t in trades:
        key = key_fn_trade(t)
        row = rows[key]
        row.key = key
        row.trades += 1
        if t.pnl > 0:
            row.wins += 1
        row.realized += t.pnl

    for p in positions:
        key = key_fn_position(p)
        row = rows[key]
        row.key = key
        row.unrealized += p.unrealized_pnl

    return dict(rows)


# Canonical key extractors — kept as module-level constants so both the
# bar charts and the tables use identical grouping logic.
def _key_strategy_trade(t: TradeRecord) -> str:
    return t.strategy or "unknown"


def _key_strategy_position(p: ActivePosition) -> str:
    return p.signal.strategy if p.signal else "unknown"


def _key_coin_trade(t: TradeRecord) -> str:
    return t.coin


def _key_coin_position(p: ActivePosition) -> str:
    return p.coin


def _key_side_trade(t: TradeRecord) -> str:
    return t.side.upper()


def _key_side_position(p: ActivePosition) -> str:
    return p.side.upper()


# ---------------------------------------------------------------------------
# Screen
# ---------------------------------------------------------------------------


class AnalyticsScreen(Container):
    """Analytics tab — charts + attribution tables."""

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="analytics-container", **kwargs)
        self.state = state

    def compose(self):
        yield Static("PERFORMANCE ANALYTICS", id="analytics-header")

        # Top: equity curve (full width)
        yield PlotextPlot(id="analytics-equity-plot")

        # Middle: strategy + coin bar charts, side-by-side
        with Horizontal(id="analytics-bars-row"):
            yield PlotextPlot(id="analytics-strategy-plot")
            yield PlotextPlot(id="analytics-coin-plot")

        # Bottom: attribution tables, scrollable
        with VerticalScroll(id="analytics-tables-scroll"):
            yield Static("Attribution by Strategy", classes="analytics-subheader")
            yield DataTable(id="analytics-strategy-table")
            yield Static("Attribution by Coin", classes="analytics-subheader")
            yield DataTable(id="analytics-coin-table")
            yield Static("Attribution by Side", classes="analytics-subheader")
            yield DataTable(id="analytics-side-table")

    def on_mount(self) -> None:
        # Initialize all three tables with columns. Rows populate on first
        # refresh_data call.
        cols = ("Group", "Trades", "Win%", "Realized", "Unrealized", "Total", "Avg PnL")
        for tid in (
            "analytics-strategy-table",
            "analytics-coin-table",
            "analytics-side-table",
        ):
            t = self.query_one(f"#{tid}", DataTable)
            t.add_columns(*cols)
            t.zebra_stripes = True
            t.cursor_type = "row"

    # ------------------------------------------------------------------
    # Refresh (called from app._refresh_display on DASHBOARD_REFRESH_RATE)
    # ------------------------------------------------------------------

    def refresh_data(self, state: AgentState) -> None:
        self.state = state
        # We DO log exceptions now — previously we swallowed them silently,
        # which made "empty analytics" impossible to diagnose. Exceptions
        # still can't propagate out (UI refresh must not crash the app),
        # but they go to state.log_lines so the Dashboard log shows them.
        for name, fn in (
            ("equity_chart", self._refresh_equity_chart),
            ("bar_charts", self._refresh_bar_charts),
            ("tables", self._refresh_tables),
        ):
            try:
                fn()
            except Exception as exc:
                # Log to state so user sees it in the Dashboard log panel.
                self.state.add_log(f"[ANALYTICS] {name} error: {exc}")

    # ----- Equity curve -----

    def _refresh_equity_chart(self) -> None:
        plot = self.query_one("#analytics-equity-plot", PlotextPlot)
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()

        # Copy first — avoids "deque mutated during iteration" if the
        # tracker appends while we're reading.
        hist = list(self.state.equity_history)
        if not hist:
            # Zero samples — equity tracker hasn't fired yet.
            plt.title(
                "Equity Curve — waiting for first snapshot (5s after boot)"
            )
            plot.refresh()
            return

        now = time.time()
        if len(hist) == 1:
            # One sample: draw a horizontal line at that value so the
            # panel looks populated, with title hint that more samples
            # are coming. plotext needs >=2 points for plot(), so we
            # duplicate the single sample at "now".
            ts, eq = hist[0]
            ages = [(ts - now) / 60.0, 0.0]
            ys = [eq, eq]
            plt.plot(ages, ys, marker="braille")
            plt.title(f"Equity: ${eq:,.2f} — waiting for next sample")
            plt.xlabel("minutes ago")
            plt.ylabel("equity (USD)")
            plot.refresh()
            return

        # Convert timestamps to "minutes ago" so the axis stays readable
        # even across long sessions. plotext's datetime support is flaky
        # across versions — integer minutes is bulletproof.
        xs = [(ts - now) / 60.0 for ts, _ in hist]  # negative minutes
        ys = [eq for _, eq in hist]

        color = "green" if ys[-1] >= ys[0] else "red"
        plt.plot(xs, ys, color=color, marker="braille")
        delta = ys[-1] - ys[0]
        # Guard against baseline=0: percent change is meaningless then.
        # Show absolute dollar delta only in that case.
        baseline = ys[0]
        if abs(baseline) < 1e-9:
            title = f"Equity: ${ys[-1]:,.2f}  ({delta:+.2f})"
        else:
            delta_pct = (delta / abs(baseline)) * 100
            title = (
                f"Equity: ${ys[-1]:,.2f}  "
                f"({delta:+.2f} / {delta_pct:+.2f}%)"
            )
        plt.title(title)
        plt.xlabel("minutes ago")
        plt.ylabel("equity (USD)")
        plot.refresh()

    # ----- Bar charts -----

    def _refresh_bar_charts(self) -> None:
        trades = list(self.state.trade_history)
        positions = list(self.state.positions)

        # Include n_trades + n_positions in the subtitle so the user
        # immediately sees the data they're looking at. A blank chart
        # that says "n_trades=0, n_positions=1" is much more informative
        # than one that says "(no trades yet)" when you DO have a position.
        subtitle = f"[{len(trades)} closed, {len(positions)} open]"

        by_strategy = _aggregate(
            trades, positions, _key_strategy_trade, _key_strategy_position
        )
        self._render_bar_chart(
            "#analytics-strategy-plot",
            by_strategy,
            title="PnL by Strategy",
            subtitle=subtitle,
        )

        by_coin = _aggregate(
            trades, positions, _key_coin_trade, _key_coin_position
        )
        self._render_bar_chart(
            "#analytics-coin-plot",
            by_coin,
            title="PnL by Coin",
            subtitle=subtitle,
        )

    def _render_bar_chart(
        self,
        selector: str,
        rows: Dict[str, AttributionRow],
        title: str,
        subtitle: str = "",
    ) -> None:
        plot = self.query_one(selector, PlotextPlot)
        plt = plot.plt
        plt.clear_data()
        plt.clear_figure()

        full_title = f"{title} {subtitle}".strip() if subtitle else title

        if not rows:
            # textual_plotext doesn't proxy plotext.simple_bar — use the
            # exposed bar() with orientation='horizontal' instead.
            plt.bar(["(no data)"], [1.0], orientation="horizontal")
            plt.title(f"{full_title}\nwaiting for activity…")
            plot.refresh()
            return

        # Sort by total PnL descending so the best/worst jump out.
        ordered = sorted(rows.values(), key=lambda r: r.total, reverse=True)
        labels = [r.key for r in ordered]
        values = [round(r.total, 4) for r in ordered]

        # Edge case: all values are exactly 0 (position just opened, no tick yet).
        # bar() with all-zeros renders as empty bars + label. Add a tiny
        # positive value just for rendering AND show a status hint in the title,
        # so the user can see the labels and know data is incoming.
        if all(abs(v) < 1e-9 for v in values):
            display_values = [1e-6] * len(values)
            plt.bar(labels, display_values, orientation="horizontal")
            plt.title(f"{full_title}\nall PnL = $0.00 (waiting for price movement)")
        else:
            plt.bar(labels, values, orientation="horizontal")
            plt.title(full_title)
        plot.refresh()

    # ----- Attribution tables -----

    def _refresh_tables(self) -> None:
        trades = list(self.state.trade_history)
        positions = list(self.state.positions)

        self._fill_table(
            "#analytics-strategy-table",
            _aggregate(trades, positions, _key_strategy_trade, _key_strategy_position),
        )
        self._fill_table(
            "#analytics-coin-table",
            _aggregate(trades, positions, _key_coin_trade, _key_coin_position),
        )
        self._fill_table(
            "#analytics-side-table",
            _aggregate(trades, positions, _key_side_trade, _key_side_position),
        )

    def _fill_table(self, selector: str, rows: Dict[str, AttributionRow]) -> None:
        table = self.query_one(selector, DataTable)
        table.clear()
        if not rows:
            return
        # Sort by total PnL descending so winners float to the top.
        ordered = sorted(rows.values(), key=lambda r: r.total, reverse=True)
        for row in ordered:
            table.add_row(
                row.key,
                str(row.trades),
                f"{row.win_rate:.0f}%",
                f"${row.realized:+.2f}",
                f"${row.unrealized:+.2f}",
                f"${row.total:+.2f}",
                f"${row.avg_pnl:+.2f}",
            )
