"""
Main dashboard screen — a Container placed inside a TabPane.

Layout (using Textual containers, no CSS grid):
  Row 0: StatusBar (strategy / status / account / daily PnL)
  Row 1: StrategyTickPanel (last tick timing + gate summary)
  Row 2: MarketTicker (full width)
  Row 3: Liquidation Stats (left) | AI Panel (right)
  Row 4: Positions (left) | Log (right)
"""

import time

from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Static, RichLog
from rich.text import Text

from hyperagent import config
from hyperagent.core.state import AgentState
from hyperagent.tui.widgets.market_ticker import MarketTicker
from hyperagent.tui.widgets.liquidation_stats import LiquidationStatsPanel
from hyperagent.tui.widgets.positions_panel import PositionsPanel


# Human-friendly labels for the strategy keys in app.py's self.strategies.
# Kept here (not on the strategy class) because the status bar is the only
# consumer and we don't want to couple strategy internals to display text.
_STRATEGY_LABELS = {
    "trend_follower": "Trend Follower",
    "momentum": "Momentum",
    "funding_carry": "Funding Carry",
    "volatility_breakout": "Volatility Breakout",
    "pairs_reversion": "Pairs Reversion",
    "liquidation_cascade_v2": "Liquidation Cascade",
}


class StatusBar(Static):
    """One-line dashboard header: strategy, run state, account, daily PnL.

    Refreshes from AgentState every DASHBOARD_REFRESH_RATE. Intentionally
    compact — it's the first thing a user looks at to answer "is this
    thing working right now?" without parsing the log.
    """

    def __init__(self, **kwargs):
        super().__init__("", id="status-bar", **kwargs)

    def update_status(self, state: AgentState):
        output = Text()

        strategy_label = _STRATEGY_LABELS.get(
            state.active_strategy, state.active_strategy
        )
        output.append("Strategy: ", style="dim")
        output.append(strategy_label, style="bold white")
        output.append("  •  ", style="dim")

        # Status: RUNNING / IDLE. RUNNING means the strategy worker is
        # actually evaluating; IDLE means the user hasn't hit Start yet.
        output.append("Status: ", style="dim")
        if state.is_running:
            output.append("RUNNING", style="bold #3fb950")
        else:
            output.append("IDLE", style="bold #d29922")
        output.append("  •  ", style="dim")

        # Account — dim when zero so it reads as "no data yet" instead
        # of an alarming "$0".
        output.append("Account: ", style="dim")
        if state.account_value > 0:
            output.append(
                f"${state.account_value:,.2f}", style="bold white"
            )
        else:
            output.append("—", style="dim")
        output.append("  •  ", style="dim")

        # Daily PnL — green when positive, red when negative, dim at zero.
        output.append("Daily PnL: ", style="dim")
        if state.daily_pnl > 0:
            output.append(f"+${state.daily_pnl:,.2f}", style="bold #3fb950")
        elif state.daily_pnl < 0:
            output.append(
                f"-${abs(state.daily_pnl):,.2f}", style="bold #f85149"
            )
        else:
            output.append("$0.00", style="dim")
        output.append("  •  ", style="dim")

        output.append("AI: ", style="dim")
        if state.ai_enabled and config.AWS_ACCESS_KEY_ID:
            output.append("ON", style="bold #a371f7")
        elif state.ai_enabled:
            output.append("OFF (no creds)", style="#d29922")
        else:
            output.append("OFF", style="dim")

        self.update(output)


class StrategyTickPanel(Static):
    """Compact one-liner showing the latest strategy tick.

    This is the user's answer to "is my strategy actually running, and
    why hasn't it fired a signal?". Reads state.last_tick (atomically
    written by the strategy worker after each generate_signal call)
    and renders:

      - last tick age (e.g. "3s ago")
      - strategy name + tick duration
      - outcome: SIGNAL fired, BLOCKED with reason, or "no signal" with
        gate rejection summary
      - next tick countdown (poll interval minus last tick age)

    Kept to one visual line so it doesn't crowd the dashboard.
    """

    def __init__(self, **kwargs):
        super().__init__("", id="strategy-tick-panel", **kwargs)

    def update_tick(self, state: AgentState):
        output = Text()
        output.append("Tick: ", style="dim")

        if not state.is_running:
            output.append("stopped", style="dim")
            output.append("  — hit Start on the Strategy tab to begin", style="dim")
            self.update(output)
            return

        tick = state.last_tick
        if tick is None or state.last_tick_time <= 0:
            output.append(
                "awaiting first scan…",
                style="#d29922",
            )
            output.append(
                "  (data warmup ~15s)",
                style="dim",
            )
            self.update(output)
            return

        now = time.time()
        age = now - state.last_tick_time
        # Color the age: fresh = green, stale = yellow, very stale = red.
        # The strategy loop sleeps STRATEGY_POLL_INTERVAL between ticks,
        # so age > 2× interval means the loop is stuck or crunching.
        if age < config.STRATEGY_POLL_INTERVAL + 5:
            age_style = "#3fb950"
        elif age < config.STRATEGY_POLL_INTERVAL * 2:
            age_style = "#d29922"
        else:
            age_style = "#f85149"
        output.append(f"{age:.0f}s ago", style=age_style)
        output.append("  •  ", style="dim")

        output.append(f"{tick.elapsed_ms}ms", style="white")
        output.append("  •  ", style="dim")

        # Outcome summary — SIGNAL wins the color war, then blocker, then
        # the gate rejection dump as a plain dim line.
        if tick.signal_fired:
            output.append(
                f"SIGNAL {tick.signal_direction} {tick.signal_coin} "
                f"score={tick.signal_score:.0f}",
                style="bold #3fb950",
            )
        elif tick.blocker:
            output.append(f"blocked: {tick.blocker}", style="#d29922")
        else:
            # Compact no-signal readout. Show "closest miss" when available
            # so users see progress instead of a blank "no signal".
            if tick.top_candidate_coin:
                output.append(
                    f"no signal (top: {tick.top_candidate_coin} "
                    f"{tick.top_candidate_score:.0f})",
                    style="dim",
                )
            else:
                output.append("no signal", style="dim")
            # Show up to 3 most-common gate rejections — full list is in
            # the log. Keeping this panel to one line.
            if tick.gate_rejections:
                top_gates = sorted(
                    tick.gate_rejections.items(), key=lambda kv: -kv[1]
                )[:3]
                output.append("  [", style="dim")
                output.append(
                    ", ".join(f"{k}:{v}" for k, v in top_gates),
                    style="dim italic",
                )
                output.append("]", style="dim")

        # AI timing suffix — only when a Bedrock call actually happened
        # this tick (i.e. a signal met the reasoning threshold).
        if tick.ai_latency_ms:
            ai_color = "#d29922" if tick.ai_latency_ms > 3000 else "#a371f7"
            output.append(f"  AI+{tick.ai_latency_ms}ms", style=ai_color)

        self.update(output)


class AIPanel(Static):

    def __init__(self, **kwargs):
        super().__init__("", id="ai-panel", **kwargs)

    def update_ai(self, state: AgentState):
        output = Text()
        output.append("AI ASSISTANT\n", style="bold cyan")
        output.append("-" * 40 + "\n", style="dim")

        if not state.ai_enabled:
            output.append("  AI is OFF. Press ", style="dim")
            output.append("'a'", style="bold yellow")
            output.append(" to enable.\n", style="dim")
            self.update(output)
            return

        if not config.AWS_ACCESS_KEY_ID:
            output.append("  AI is not configured.\n", style="bold #d29922")
            output.append("  Run ", style="dim")
            output.append("`hyperagent setup`", style="bold yellow")
            output.append(" to add AWS Bedrock credentials.\n", style="dim")
            self.update(output)
            return

        output.append("  Status: ", style="dim")
        output.append("ENABLED\n", style="bold #3fb950")

        latest_reasoning = None
        for sig in reversed(state.active_signals):
            if sig.ai_reasoning:
                latest_reasoning = sig.ai_reasoning
                break
        if not latest_reasoning:
            for trade in reversed(state.trade_history):
                if trade.ai_reasoning:
                    latest_reasoning = trade.ai_reasoning
                    break

        if latest_reasoning:
            output.append("\n  Latest Analysis:\n", style="dim")
            words = latest_reasoning.split()
            line = "  "
            for word in words:
                if len(line) + len(word) + 1 > 55:
                    output.append(line + "\n", style="white")
                    line = "  " + word
                else:
                    line += " " + word if line.strip() else "  " + word
            if line.strip():
                output.append(line + "\n", style="white")
        else:
            output.append("  No AI analysis yet.\n", style="dim italic")

        self.update(output)


class LogPanel(RichLog):

    def __init__(self, **kwargs):
        super().__init__(id="log-panel", auto_scroll=True, wrap=True, max_lines=500, **kwargs)
        self._seen_count = 0

    def update_log(self, state: AgentState):
        lines = list(state.log_lines)
        new_lines = lines[self._seen_count:]
        self._seen_count = len(lines)

        for line in new_lines:
            styled = Text()
            if "[STOP-LOSS" in line or "ERROR" in line:
                styled.append(line, style="#f85149")
            elif "[RISK]" in line:
                styled.append(line, style="#d29922")
            elif "[SIGNAL]" in line or "[TRADE]" in line:
                styled.append(line, style="#3fb950")
            elif "[AI]" in line:
                styled.append(line, style="#a371f7")
            elif "[LIQ]" in line:
                styled.append(line, style="#58a6ff")
            elif "[TICK" in line:
                # Heartbeat lines — subtle cyan so they're visible as
                # progress but don't dominate the log like signals/trades.
                styled.append(line, style="#58a6ff dim")
            else:
                styled.append(line, style="dim")
            self.write(styled)


class DashboardScreen(Container):

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="dashboard-container", **kwargs)
        self.state = state

    def compose(self):
        yield StatusBar()
        yield StrategyTickPanel()
        yield MarketTicker()
        with Horizontal(id="dashboard-row2"):
            with VerticalScroll(id="liquidation-stats-scroll"):
                yield LiquidationStatsPanel()
            yield AIPanel()
        with Horizontal(id="dashboard-row3"):
            with VerticalScroll(id="positions-scroll"):
                yield PositionsPanel()
            yield LogPanel()

    def refresh_data(self, state: AgentState):
        self.state = state
        try:
            self.query_one(StatusBar).update_status(state)
        except Exception:
            pass
        try:
            self.query_one(StrategyTickPanel).update_tick(state)
        except Exception:
            pass
        try:
            self.query_one(MarketTicker).update_prices(state.prices)
        except Exception:
            pass
        try:
            self.query_one(LiquidationStatsPanel).update_stats(state)
        except Exception:
            pass
        try:
            self.query_one(PositionsPanel).update_positions(state)
        except Exception:
            pass
        try:
            self.query_one(AIPanel).update_ai(state)
        except Exception:
            pass
        try:
            self.query_one(LogPanel).update_log(state)
        except Exception:
            pass

    def flash_stop_loss(self):
        try:
            self.query_one(PositionsPanel).trigger_stop_loss_flash()
        except Exception:
            pass
