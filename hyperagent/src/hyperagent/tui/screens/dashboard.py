"""
Main dashboard screen — a Container placed inside a TabPane.

Layout (using Textual containers, no CSS grid):
  Row 1: MarketTicker (full width)
  Row 2: Liquidation Stats (left) | AI Panel (right)
  Row 3: Positions (left) | Log (right)
"""

from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Static, RichLog
from rich.text import Text

from hyperagent import config
from hyperagent.core.state import AgentState
from hyperagent.tui.widgets.market_ticker import MarketTicker
from hyperagent.tui.widgets.liquidation_stats import LiquidationStatsPanel
from hyperagent.tui.widgets.positions_panel import PositionsPanel


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
            else:
                styled.append(line, style="dim")
            self.write(styled)


class DashboardScreen(Container):

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="dashboard-container", **kwargs)
        self.state = state

    def compose(self):
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
