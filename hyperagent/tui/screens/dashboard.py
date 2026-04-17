"""
Main dashboard screen (actually a Container placed inside a TabPane).

Grid layout:
  Row 1: MarketTicker (full width)
  Row 2: Heatmap (left) | Cascade Alerts + AI Panel (right)
  Row 3: Positions (left) | Trade Log (right)
"""

from textual.containers import Container, Vertical
from textual.widgets import Static
from rich.text import Text

from core.state import AgentState
from tui.widgets.market_ticker import MarketTicker
from tui.widgets.heatmap import LiquidationHeatmap
from tui.widgets.cascade_gauge import CascadeGauge
from tui.widgets.positions_panel import PositionsPanel


class AIPanel(Static):
    """Displays the latest AI reasoning snippet."""

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

        output.append("  Status: ", style="dim")
        output.append("ENABLED\n", style="bold #3fb950")

        # Show latest AI reasoning from signals
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
            # Wrap reasoning text
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


class LogPanel(Static):
    """Scrolling log of agent events."""

    def __init__(self, **kwargs):
        super().__init__("", id="log-panel", **kwargs)

    def update_log(self, state: AgentState):
        output = Text()
        output.append("TRADE LOG\n", style="bold cyan")
        output.append("=" * 55 + "\n", style="dim")

        if not state.log_lines:
            output.append(
                "\n  No log entries yet.\n",
                style="dim italic",
            )
            self.update(output)
            return

        # Show the last N lines that fit
        lines = list(state.log_lines)
        display_lines = lines[-25:]  # show last 25 lines

        for line in display_lines:
            # Color-code log lines by content
            if "[STOP-LOSS" in line or "ERROR" in line:
                output.append(f"  {line}\n", style="#f85149")
            elif "[RISK]" in line:
                output.append(f"  {line}\n", style="#d29922")
            elif "[SIGNAL]" in line or "[TRADE]" in line:
                output.append(f"  {line}\n", style="#3fb950")
            elif "[AI]" in line:
                output.append(f"  {line}\n", style="#a371f7")
            elif "[SCAN]" in line:
                output.append(f"  {line}\n", style="#58a6ff")
            else:
                output.append(f"  {line}\n", style="dim")

        self.update(output)


class DashboardScreen(Container):
    """
    The main dashboard container with a grid layout holding
    all monitoring widgets.
    """

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="dashboard-container", **kwargs)
        self.state = state

    def compose(self):
        # Row 1: full-width market ticker
        yield MarketTicker()

        # Row 2 left: heatmap
        yield LiquidationHeatmap()

        # Row 2 right: cascade alerts + AI panel stacked vertically
        with Vertical(id="right-top-container"):
            yield CascadeGauge()
            yield AIPanel()

        # Row 3 left: positions
        yield PositionsPanel()

        # Row 3 right: log
        yield LogPanel()

    def refresh_data(self, state: AgentState):
        """Update all child widgets with fresh state data."""
        self.state = state

        ticker = self.query_one(MarketTicker)
        ticker.update_prices(state.prices)

        heatmap = self.query_one(LiquidationHeatmap)
        heatmap.update_clusters(state)

        gauge = self.query_one(CascadeGauge)
        gauge.update_scores(state)

        positions = self.query_one(PositionsPanel)
        positions.update_positions(state)

        ai_panel = self.query_one(AIPanel)
        ai_panel.update_ai(state)

        log_panel = self.query_one(LogPanel)
        log_panel.update_log(state)

    def flash_stop_loss(self):
        """Trigger the stop-loss flash on the positions panel."""
        positions = self.query_one(PositionsPanel)
        positions.trigger_stop_loss_flash()
