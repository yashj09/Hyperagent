"""
Strategy configuration screen.

Allows the user to:
  - Select a strategy (Liquidation Cascade / Momentum Flip)
  - Toggle the AI assistant on/off
  - View strategy parameters in a read-only DataTable
  - Start or stop the active strategy
"""

from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static, Select, Switch, Button, DataTable, Label
from textual.message import Message

from core.state import AgentState
import config


STRATEGY_DESCRIPTIONS = {
    "cascade": (
        "Liquidation Cascade Strategy\n"
        "Scans for dense clusters of liquidation levels near the current price. "
        "When the cascade score exceeds the threshold, the agent opens a position "
        "in the direction of the expected cascade (short when longs are about to "
        "be liquidated, long when shorts are clustered). Uses trailing stops to "
        "ride the momentum of cascading liquidations."
    ),
    "momentum": (
        "Momentum Flip Strategy\n"
        "Multi-indicator momentum strategy using RSI, MACD, EMA crossovers, "
        "and Bollinger Bands. A voting system determines signal strength: "
        "when enough indicators agree on direction, the agent enters. "
        "Designed for trending markets with clear directional bias."
    ),
}

CASCADE_PARAMS = [
    ("Proximity %", f"{config.CASCADE_PROXIMITY_PCT * 100:.1f}%"),
    ("Density Threshold", str(config.CASCADE_DENSITY_THRESHOLD)),
    ("Cluster Width %", f"{config.CASCADE_CLUSTER_WIDTH_PCT * 100:.2f}%"),
    ("Signal Threshold", str(config.CASCADE_SIGNAL_THRESHOLD)),
    ("High Confidence", str(config.CASCADE_HIGH_CONFIDENCE)),
    ("Position Size USD", f"${config.POSITION_SIZE_USD}"),
    ("Max Leverage", f"{config.MAX_LEVERAGE}x"),
    ("Trailing Stop %", f"{config.TRAILING_STOP_PCT * 100:.1f}%"),
    ("Initial Stop %", f"{config.INITIAL_STOP_PCT * 100:.1f}%"),
    ("Take Profit %", f"{config.TAKE_PROFIT_PCT * 100:.1f}%"),
    ("Max Concurrent", str(config.MAX_CONCURRENT_POSITIONS)),
    ("Max Daily Loss", f"${config.MAX_DAILY_LOSS_USD}"),
    ("Scan Interval", f"{config.SCAN_INTERVAL_SECONDS}s"),
]

MOMENTUM_PARAMS = [
    ("RSI Period", str(config.MOMENTUM_RSI_PERIOD)),
    ("MACD Fast", str(config.MOMENTUM_MACD_FAST)),
    ("MACD Slow", str(config.MOMENTUM_MACD_SLOW)),
    ("MACD Signal", str(config.MOMENTUM_MACD_SIGNAL)),
    ("EMA Fast", str(config.MOMENTUM_EMA_FAST)),
    ("EMA Slow", str(config.MOMENTUM_EMA_SLOW)),
    ("BB Period", str(config.MOMENTUM_BB_PERIOD)),
    ("BB Std Dev", str(config.MOMENTUM_BB_STD)),
    ("Vote Threshold", str(config.MOMENTUM_VOTE_THRESHOLD)),
    ("Candle Interval", config.MOMENTUM_CANDLE_INTERVAL),
    ("Candle Count", str(config.MOMENTUM_CANDLE_COUNT)),
    ("Position Size USD", f"${config.POSITION_SIZE_USD}"),
    ("Trailing Stop %", f"{config.TRAILING_STOP_PCT * 100:.1f}%"),
    ("Take Profit %", f"{config.TAKE_PROFIT_PCT * 100:.1f}%"),
]


class StrategyConfigScreen(Container):
    """Interactive strategy configuration panel."""

    class StrategyChanged(Message):
        """Posted when the user changes the strategy selection."""
        def __init__(self, strategy: str):
            super().__init__()
            self.strategy = strategy

    class StrategyToggled(Message):
        """Posted when the user clicks Start/Stop."""
        def __init__(self, running: bool):
            super().__init__()
            self.running = running

    class AIToggled(Message):
        """Posted when the AI switch is toggled."""
        def __init__(self, enabled: bool):
            super().__init__()
            self.enabled = enabled

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="strategy-container", **kwargs)
        self.state = state

    def compose(self):
        yield Static("STRATEGY CONFIGURATION", id="strategy-header")

        with Horizontal(id="strategy-controls"):
            with Vertical(id="strategy-select-box"):
                yield Label("Strategy:")
                yield Select(
                    [
                        ("Liquidation Cascade", "cascade"),
                        ("Momentum Flip", "momentum"),
                    ],
                    value=self.state.active_strategy,
                    id="strategy-select",
                )

            with Vertical(id="ai-toggle-box"):
                yield Label("AI Assistant:")
                yield Switch(
                    value=self.state.ai_enabled,
                    id="ai-switch",
                )

            with Vertical(id="strategy-button"):
                yield Label("")
                yield Button(
                    "Start Strategy",
                    id="strategy-start-btn",
                    variant="success",
                )

        yield Static(
            STRATEGY_DESCRIPTIONS.get(self.state.active_strategy, ""),
            id="strategy-description",
        )

        yield DataTable(id="strategy-params-table")

    def on_mount(self):
        """Set up the parameters table with initial data."""
        table = self.query_one("#strategy-params-table", DataTable)
        table.add_columns("Parameter", "Value")
        self._populate_params_table(self.state.active_strategy)

    def _populate_params_table(self, strategy: str):
        """Fill the parameters table for the selected strategy."""
        table = self.query_one("#strategy-params-table", DataTable)
        table.clear()

        params = CASCADE_PARAMS if strategy == "cascade" else MOMENTUM_PARAMS
        for name, value in params:
            table.add_row(name, value)

    def on_select_changed(self, event: Select.Changed):
        """Handle strategy dropdown change."""
        if event.select.id == "strategy-select" and event.value is not None:
            strategy = str(event.value)
            self.state.active_strategy = strategy

            # Update description
            desc_widget = self.query_one("#strategy-description", Static)
            desc_widget.update(STRATEGY_DESCRIPTIONS.get(strategy, ""))

            # Update params table
            self._populate_params_table(strategy)

            self.post_message(self.StrategyChanged(strategy))

    def on_switch_changed(self, event: Switch.Changed):
        """Handle AI toggle."""
        if event.switch.id == "ai-switch":
            self.state.ai_enabled = event.value
            self.post_message(self.AIToggled(event.value))

    def on_button_pressed(self, event: Button.Pressed):
        """Handle Start/Stop button."""
        if event.button.id in ("strategy-start-btn", "strategy-stop-btn"):
            btn = event.button
            if self.state.is_running:
                # Stop
                self.state.is_running = False
                self.state.status_message = "Stopped"
                self.state.add_log("[STRATEGY] Stopped by user")
                btn.label = "Start Strategy"
                btn.id = "strategy-start-btn"
                btn.variant = "success"
            else:
                # Start
                self.state.is_running = True
                self.state.status_message = "Running"
                self.state.add_log(
                    f"[STRATEGY] Started: {self.state.active_strategy}"
                )
                btn.label = "Stop Strategy"
                btn.id = "strategy-stop-btn"
                btn.variant = "error"

            self.post_message(self.StrategyToggled(self.state.is_running))

    def refresh_state(self, state: AgentState):
        """Sync the UI with external state changes."""
        self.state = state
        # Update AI switch if changed externally
        try:
            sw = self.query_one("#ai-switch", Switch)
            if sw.value != state.ai_enabled:
                sw.value = state.ai_enabled
        except Exception:
            pass
