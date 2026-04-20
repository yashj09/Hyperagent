"""
Strategy configuration screen.

Allows the user to:
  - Select a strategy from the improved set
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
    "trend_follower": (
        "ADX Trend Follower (CTA-style)\n"
        "Uses ADX(14) on 4h candles to confirm trends, +DI/-DI for "
        "direction, EMA(21/55) for confirmation, and pullback-to-EMA "
        "entries. ATR-based dynamic stops. Sharpe 0.3-0.7 historically."
    ),
    "momentum": (
        "Enhanced Momentum (Weighted Scoring)\n"
        "6-signal weighted scoring: RSI(8), MACD+slope, EMA crossover, "
        "BB %B, volume-momentum, 4h confirmation. ADX gate filters "
        "choppy markets. Score >= 60 to trigger."
    ),
    "funding_carry": (
        "Funding Rate Carry\n"
        "Research-calibrated funding arbitrage. Requires >0.03% rate "
        "(~33% APR), settlement timing window, trend filter, and "
        "funding persistence. $200 positions for meaningful income."
    ),
    "volatility_breakout": (
        "Volatility Squeeze Breakout\n"
        "Detects Bollinger squeeze (BB inside Keltner), then trades "
        "the breakout with ATR-adaptive thresholds, volume confirmation, "
        "and continuation check. 15m candles, all 8 assets."
    ),
    "pairs_reversion": (
        "Pairs Mean Reversion (Stat Arb)\n"
        "Market-neutral pairs trading on BTC/ETH and SOL/AVAX. "
        "Z-score on log price ratio: entry at 2σ, exit at mean, "
        "stop at 3.5σ. Near-zero directional exposure. Sharpe 0.8-1.5."
    ),
}

TREND_PARAMS = [
    ("ADX Period", str(config.TREND_ADX_PERIOD)),
    ("ADX Threshold", str(config.TREND_ADX_THRESHOLD)),
    ("EMA Fast", str(config.TREND_EMA_FAST)),
    ("EMA Slow", str(config.TREND_EMA_SLOW)),
    ("Candle Interval", config.TREND_CANDLE_INTERVAL),
    ("Stop (ATR mult)", f"{config.TREND_STOP_ATR_MULT}x"),
    ("TP (ATR mult)", f"{config.TREND_TP_ATR_MULT}x"),
    ("Trail (ATR mult)", f"{config.TREND_TRAIL_ATR_MULT}x"),
    ("Pullback (ATR mult)", f"{config.TREND_PULLBACK_ATR_MULT}x"),
]

MOMENTUM_PARAMS = [
    ("RSI Period", str(config.MOMENTUM_RSI_PERIOD)),
    ("RSI Bull/Bear", f"{config.MOMENTUM_RSI_BULL}/{config.MOMENTUM_RSI_BEAR}"),
    ("MACD", f"{config.MOMENTUM_MACD_FAST}/{config.MOMENTUM_MACD_SLOW}/{config.MOMENTUM_MACD_SIGNAL}"),
    ("EMA Fast/Slow", f"{config.MOMENTUM_EMA_FAST}/{config.MOMENTUM_EMA_SLOW}"),
    ("ADX Gate", str(config.MOMENTUM_ADX_GATE)),
    ("Score Threshold", str(config.MOMENTUM_VOTE_THRESHOLD)),
    ("Stop %", "1.5%"),
    ("TP %", "3.5%"),
    ("Trail %", "1.2%"),
]

FUNDING_PARAMS = [
    ("Funding Threshold", f"{config.FUNDING_THRESHOLD * 100:.3f}%"),
    ("High Threshold", f"{config.FUNDING_HIGH_THRESHOLD * 100:.3f}%"),
    ("Settlement Window", f"{config.FUNDING_SETTLEMENT_WINDOW}s"),
    ("Persistence Periods", str(config.FUNDING_PERSISTENCE_PERIODS)),
    ("Position Size", f"${config.FUNDING_POSITION_SIZE}"),
    ("Stop %", "2.0%"),
    ("Trail %", "1.5%"),
]

BREAKOUT_PARAMS = [
    ("ATR Multiplier", f"{config.BREAKOUT_ATR_MULT}x"),
    ("Squeeze Bars Min", str(config.BREAKOUT_SQUEEZE_BARS)),
    ("Volume Multiplier", f"{config.BREAKOUT_VOLUME_MULT}x"),
    ("Candle Interval", config.BREAKOUT_CANDLE_INTERVAL),
    ("Lookback Candles", str(config.BREAKOUT_LOOKBACK_CANDLES)),
    ("Stop %", "2.5%"),
    ("TP %", "5.0%"),
    ("Trail %", "2.0%"),
]

PAIRS_PARAMS = [
    ("Z-Score Entry", str(config.PAIRS_ZSCORE_ENTRY)),
    ("Z-Score Exit", str(config.PAIRS_ZSCORE_EXIT)),
    ("Z-Score Stop", str(config.PAIRS_ZSCORE_STOP)),
    ("Lookback Hours", str(config.PAIRS_LOOKBACK_HOURS)),
    ("Min Correlation", str(config.PAIRS_MIN_CORRELATION)),
    ("Size Per Leg", f"${config.PAIRS_POSITION_SIZE_PER_LEG}"),
    ("Pairs", "BTC/ETH, SOL/AVAX"),
]

STRATEGY_PARAMS = {
    "trend_follower": TREND_PARAMS,
    "momentum": MOMENTUM_PARAMS,
    "funding_carry": FUNDING_PARAMS,
    "volatility_breakout": BREAKOUT_PARAMS,
    "pairs_reversion": PAIRS_PARAMS,
}


class StrategyConfigScreen(Container):
    """Interactive strategy configuration panel."""

    class StrategyChanged(Message):
        def __init__(self, strategy: str):
            super().__init__()
            self.strategy = strategy

    class StrategyToggled(Message):
        def __init__(self, running: bool):
            super().__init__()
            self.running = running

    class AIToggled(Message):
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
                        ("Trend Follower (CTA)", "trend_follower"),
                        ("Momentum (Weighted)", "momentum"),
                        ("Funding Carry", "funding_carry"),
                        ("Volatility Breakout", "volatility_breakout"),
                        ("Pairs Reversion", "pairs_reversion"),
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
        table = self.query_one("#strategy-params-table", DataTable)
        table.add_columns("Parameter", "Value")
        self._populate_params_table(self.state.active_strategy)

    def _populate_params_table(self, strategy: str):
        table = self.query_one("#strategy-params-table", DataTable)
        table.clear()
        params = STRATEGY_PARAMS.get(strategy, TREND_PARAMS)
        for name, value in params:
            table.add_row(name, value)

    def on_select_changed(self, event: Select.Changed):
        if event.select.id == "strategy-select" and event.value is not None:
            strategy = str(event.value)
            self.state.active_strategy = strategy

            desc_widget = self.query_one("#strategy-description", Static)
            desc_widget.update(STRATEGY_DESCRIPTIONS.get(strategy, ""))

            self._populate_params_table(strategy)
            self.post_message(self.StrategyChanged(strategy))

    def on_switch_changed(self, event: Switch.Changed):
        if event.switch.id == "ai-switch":
            self.state.ai_enabled = event.value
            self.post_message(self.AIToggled(event.value))

    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "strategy-start-btn":
            btn = event.button
            if self.state.is_running:
                self.state.is_running = False
                self.state.status_message = "Stopped"
                self.state.add_log("[STRATEGY] Stopped by user")
                btn.label = "Start Strategy"
                btn.variant = "success"
            else:
                self.state.is_running = True
                self.state.status_message = "Running"
                self.state.add_log(
                    f"[STRATEGY] Started: {self.state.active_strategy}"
                )
                btn.label = "Stop Strategy"
                btn.variant = "error"

            self.post_message(self.StrategyToggled(self.state.is_running))

    def refresh_state(self, state: AgentState):
        self.state = state
        try:
            sw = self.query_one("#ai-switch", Switch)
            if sw.value != state.ai_enabled:
                sw.value = state.ai_enabled
        except Exception:
            pass
