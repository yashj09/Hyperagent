"""
Strategy configuration screen.

Allows the user to:
  - Select a strategy from the improved set
  - Toggle the AI assistant on/off
  - View strategy parameters in a DataTable
  - EDIT any parameter inline (click a row -> modal -> save mutates config)
  - Start or stop the active strategy

The parameter display is driven by tui/param_schema.py, which also
handles validation + format/parse so the same schema defines both the
read-only display and the edit dialog.

Mutations to `config` module globals take effect on the NEXT call to
`strategy.generate_signal(...)` because strategies read `config.X`
every iteration (module attribute lookup, not captured at init).
"""

from __future__ import annotations

from typing import Any, Optional

from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Static, Select, Switch, Button, DataTable, Label
from textual.message import Message

from core.state import AgentState
from tui.param_schema import ParamSpec, check_invariants, get_specs_for, get_spec_by_key
from tui.screens.edit_param_modal import EditParamModal
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
    "liquidation_cascade_v2": (
        "Liquidation Cascade v2 (HypeDexer)\n"
        "Trades ongoing liquidation cascades using the full HypeDexer "
        "liquidation firehose (not just 28 wallets). Enters SHORT on "
        "mass long-liquidations, LONG on mass short-squeezes. Gated by "
        "USD threshold + 3x imbalance + 1.3x acceleration. Requires "
        "HYPEDEXER_API_KEY env var."
    ),
}


class StrategyConfigScreen(Container):
    """Interactive strategy configuration panel with editable params."""

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

    class ParamChanged(Message):
        """Emitted after a parameter edit is applied to config."""

        def __init__(self, config_key: str, old_value: Any, new_value: Any):
            super().__init__()
            self.config_key = config_key
            self.old_value = old_value
            self.new_value = new_value

    def __init__(self, state: AgentState, **kwargs):
        super().__init__(id="strategy-container", **kwargs)
        self.state = state
        # Row index -> ParamSpec, rebuilt every time we repopulate the table.
        # Lets us resolve a clicked row back to its spec without relying on
        # label text (which may be formatted/localized).
        self._row_specs: list[ParamSpec] = []

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
                        ("Liquidation Cascade v2", "liquidation_cascade_v2"),
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

        # Enabled-assets row: one Switch per coin in the app's universe.
        # Toggling rebuilds config.MONITORED_ASSETS in place — strategies
        # read this attribute at signal-generation time so the change
        # takes effect on the next strategy loop iteration (<=15s).
        #
        # We snapshot the INITIAL list as the universe (union of current
        # MONITORED_ASSETS + anything user later adds via config edit).
        # Missing a coin here = can't enable it from the TUI, only via
        # editing config.py and restarting — matches the locked-in
        # "global allowlist only" decision.
        yield Label("Enabled Assets:", id="enabled-assets-label")
        with Horizontal(id="enabled-assets-row"):
            for coin in self._asset_universe():
                with Vertical(classes="asset-toggle-cell"):
                    yield Label(coin, classes="asset-toggle-label")
                    yield Switch(
                        value=coin in config.MONITORED_ASSETS,
                        id=f"asset-switch-{coin}",
                        classes="asset-switch",
                    )

        yield Static(
            STRATEGY_DESCRIPTIONS.get(self.state.active_strategy, ""),
            id="strategy-description",
        )

        yield Static(
            "Tip: click any parameter row (or press Enter) to edit its value.",
            id="strategy-params-hint",
        )

        yield DataTable(id="strategy-params-table", cursor_type="row")

    def on_mount(self):
        table = self.query_one("#strategy-params-table", DataTable)
        table.add_columns("Parameter", "Value")
        table.zebra_stripes = True
        self._populate_params_table(self.state.active_strategy)

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_params_table(self, strategy: str) -> None:
        """Fill the DataTable from schema, reading live config values.

        Called on initial mount, strategy change, and after any edit.
        """
        table = self.query_one("#strategy-params-table", DataTable)
        table.clear()
        self._row_specs = []

        specs = get_specs_for(strategy)
        # Group header: inserted as a spec-less row so users see the divide
        # between "this strategy's knobs" and "shared risk knobs".
        strategy_only = [s for s in specs if s.config_key in {
            sp.config_key for sp in _strategy_only_specs(strategy)
        }]
        risk_only = [s for s in specs if s not in strategy_only]

        if strategy_only:
            table.add_row("[b]─── Strategy ───[/b]", "")
            self._row_specs.append(None)
            for spec in strategy_only:
                self._add_spec_row(table, spec)

        if risk_only:
            table.add_row("[b]─── Risk & Sizing ───[/b]", "")
            self._row_specs.append(None)
            for spec in risk_only:
                self._add_spec_row(table, spec)

    def _add_spec_row(self, table: DataTable, spec: ParamSpec) -> None:
        raw = getattr(config, spec.config_key, None)
        value_str = spec.format(raw) if raw is not None else "?"
        table.add_row(spec.label, value_str)
        self._row_specs.append(spec)

    def _refresh_row(self, spec: ParamSpec) -> None:
        """Update one row in place after an edit (avoids full rebuild)."""
        table = self.query_one("#strategy-params-table", DataTable)
        try:
            row_index = self._row_specs.index(spec)
        except ValueError:
            # Spec no longer present (e.g. user switched strategy mid-edit)
            return
        raw = getattr(config, spec.config_key, None)
        table.update_cell_at(
            (row_index, 1),
            spec.format(raw) if raw is not None else "?",
        )

    # ------------------------------------------------------------------
    # UI events
    # ------------------------------------------------------------------

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
            return

        # Asset enable/disable switches. IDs follow pattern "asset-switch-<COIN>".
        sw_id = event.switch.id or ""
        if sw_id.startswith("asset-switch-"):
            coin = sw_id[len("asset-switch-") :]
            self._toggle_asset(coin, event.value)

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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """A parameter row was activated (click or Enter). Open the edit modal."""
        table = self.query_one("#strategy-params-table", DataTable)
        if event.data_table is not table:
            return

        # Resolve row index -> spec. Section headers have None and are ignored.
        idx = table.get_row_index(event.row_key)
        if idx is None or idx >= len(self._row_specs):
            return
        spec = self._row_specs[idx]
        if spec is None:
            return  # section header row

        current_value = getattr(config, spec.config_key, None)
        if current_value is None:
            return

        # Build a cross-field invariant checker that the modal can call on
        # every keystroke. Closes over the CURRENT state.active_strategy and
        # the CURRENT config module, so invariants are always evaluated
        # against fresh values.
        strategy_name = self.state.active_strategy

        def _check_cross_field(new_value: Any) -> Optional[str]:
            # If the edit is an integer-kind, coerce before running invariants
            # — else comparisons like EMA_FAST<EMA_SLOW get confused by
            # float-vs-int mixing (30.0 == 30 numerically but trips some
            # strict-int checks elsewhere).
            if spec.kind in ("int", "seconds", "minutes"):
                new_value = int(new_value)
            return check_invariants(config, strategy_name, spec.config_key, new_value)

        # Push the modal; callback runs on dismiss with the parsed new value
        # (or None if the user cancelled).
        def _on_save(new_value: Optional[Any]) -> None:
            if new_value is None:
                return
            self._apply_edit(spec, current_value, new_value)

        self.app.push_screen(
            EditParamModal(spec, current_value, extra_validator=_check_cross_field),
            _on_save,
        )

    # ------------------------------------------------------------------
    # Config mutation
    # ------------------------------------------------------------------

    def _apply_edit(self, spec: ParamSpec, old_value: Any, new_value: Any) -> None:
        """Mutate config module and refresh the row.

        Strategies read `config.X` on every generate_signal() call, so the
        new value takes effect on the next strategy loop iteration (within
        STRATEGY_POLL_INTERVAL, typically 15s).
        """
        # Preserve type: if the spec's canonical type is int but the parser
        # returned float (e.g. "30" parsed as 30.0), coerce back. This keeps
        # downstream type checks happy (e.g. `range(X)` needs int).
        if spec.kind in ("int", "seconds", "minutes"):
            new_value = int(new_value)

        setattr(config, spec.config_key, new_value)
        self._refresh_row(spec)
        self.state.add_log(
            f"[CONFIG] {spec.config_key}: "
            f"{spec.format(old_value)} -> {spec.format(new_value)}"
        )
        self.post_message(self.ParamChanged(spec.config_key, old_value, new_value))

    def refresh_state(self, state: AgentState):
        self.state = state
        try:
            sw = self.query_one("#ai-switch", Switch)
            if sw.value != state.ai_enabled:
                sw.value = state.ai_enabled
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Asset allowlist
    # ------------------------------------------------------------------

    _ASSET_UNIVERSE_SNAPSHOT: list[str] = []

    def _asset_universe(self) -> list[str]:
        """Return the full set of coins that get a switch in the UI.

        We snapshot the current MONITORED_ASSETS the FIRST time this is
        called (during compose), so the switch row is stable across
        toggles. If we re-read config.MONITORED_ASSETS every time, removed
        coins would lose their toggle — user couldn't re-enable them
        without editing config.py.
        """
        if not StrategyConfigScreen._ASSET_UNIVERSE_SNAPSHOT:
            StrategyConfigScreen._ASSET_UNIVERSE_SNAPSHOT = list(
                config.MONITORED_ASSETS
            )
        return StrategyConfigScreen._ASSET_UNIVERSE_SNAPSHOT

    def _toggle_asset(self, coin: str, enabled: bool) -> None:
        """Mutate config.MONITORED_ASSETS in response to a switch flip.

        Rebuilds the list from the universe snapshot so ordering stays
        stable. Logs the change to the Dashboard log so the user sees
        the effect. Strategies read config.MONITORED_ASSETS at signal-
        generation time so the toggle takes effect on the next loop.
        """
        universe = self._asset_universe()
        current = set(config.MONITORED_ASSETS)
        if enabled:
            current.add(coin)
        else:
            current.discard(coin)

        # Preserve the original ordering from the universe snapshot.
        new_list = [c for c in universe if c in current]

        old_str = ",".join(config.MONITORED_ASSETS) or "(none)"
        new_str = ",".join(new_list) or "(none)"
        config.MONITORED_ASSETS = new_list

        self.state.add_log(
            f"[CONFIG] MONITORED_ASSETS: {old_str} -> {new_str}"
        )
        # Note: existing open positions are NOT closed when you disable a
        # coin — only new entries are blocked. This matches the locked-in
        # behavior: "Disabling a coin does NOT close its open position".


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strategy_only_specs(strategy: str):
    """Return the strategy-specific specs (without the shared risk specs).

    Mirrors the logic in param_schema.get_specs_for but isolates the
    strategy portion so we can render a visual divider between the two
    sections in the table.
    """
    from tui.param_schema import STRATEGY_SPECS
    return STRATEGY_SPECS.get(strategy, [])
