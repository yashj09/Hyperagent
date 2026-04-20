"""
HyperAgent — Autonomous Trading Terminal for Hyperliquid.

Main Textual application entry point. Composes the dashboard, strategy
configuration, and trade journal screens inside a tabbed layout and runs
background workers for price feeds, scanning, strategy execution, and
stop-loss monitoring.
"""

import asyncio
import logging
import time

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TabbedContent, TabPane
from textual.binding import Binding
from textual import work

import config
from core.state import AgentState, Signal, ActivePosition
from core.client import HyperLiquidClient
from core.risk import RiskManager
from scanner.liquidation_scanner import LiquidationScanner
from scanner.whale_addresses import get_all_addresses
from core.regime import RegimeDetector
from core.candle_cache import CandleCache
from core.hypedexer_client import HypeDexerClient
from core.liquidation_aggregator import LiquidationAggregator
from strategies.trend_follower import TrendFollowerStrategy
from strategies.momentum import MomentumStrategy
from strategies.funding_sniper import FundingSniperStrategy
from strategies.volatility_breakout import VolatilityBreakoutStrategy
from strategies.pairs_reversion import PairsReversionStrategy
from strategies.liquidation_cascade_v2 import LiquidationCascadeV2Strategy
from strategies.ai_wrapper import AIWrapper
from tui.screens.dashboard import DashboardScreen
from tui.screens.strategy_config import StrategyConfigScreen
from tui.screens.trade_journal import TradeJournalScreen

logger = logging.getLogger(__name__)


class HyperAgentApp(App):
    """The main HyperAgent TUI application."""

    TITLE = "HyperAgent"
    SUB_TITLE = "Autonomous Trading Terminal for Hyperliquid"
    CSS_PATH = "tui/styles.tcss"

    BINDINGS = [
        Binding("d", "switch_tab('tab-dashboard')", "Dashboard"),
        Binding("s", "switch_tab('tab-strategy')", "Strategy"),
        Binding("j", "switch_tab('tab-journal')", "Journal"),
        Binding("a", "toggle_ai", "Toggle AI"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()

        self.state = AgentState()
        self.state.ai_enabled = config.AI_ENABLED_DEFAULT

        self.client = HyperLiquidClient(testnet=True)
        self.risk = RiskManager(self.client, self.state)

        addresses = get_all_addresses()
        self.scanner = LiquidationScanner(self.client.info, addresses)

        self.candle_cache = CandleCache(self.client.info)
        self.regime_detector = RegimeDetector(self.client.info, self.candle_cache)

        # HypeDexer client + aggregator power the Liquidation Cascade v2 strategy.
        # Initialized regardless of whether the strategy is active, so users can
        # switch to it at runtime without a restart.
        self.hypedexer = HypeDexerClient()
        self.liq_aggregator = LiquidationAggregator(self.hypedexer)

        self.strategies = {
            "trend_follower": TrendFollowerStrategy(self.client.info, self.candle_cache),
            "momentum": MomentumStrategy(self.client.info, self.candle_cache),
            "funding_carry": FundingSniperStrategy(self.client.info, self.candle_cache),
            "volatility_breakout": VolatilityBreakoutStrategy(self.client.info, self.candle_cache),
            "pairs_reversion": PairsReversionStrategy(self.client.info, self.candle_cache),
            "liquidation_cascade_v2": LiquidationCascadeV2Strategy(self.client.info, self.candle_cache),
        }
        self.ai_wrapper: AIWrapper | None = None

        self._prev_prices: dict = {}

        self.state.add_log("[INIT] HyperAgent v2 starting up...")
        self.state.add_log(f"[INIT] Monitoring: {', '.join(config.MONITORED_ASSETS)}")
        self.state.add_log(f"[INIT] Whale addresses loaded: {len(addresses)}")
        self.state.add_log(
            f"[INIT] Strategies: {', '.join(self.strategies.keys())}"
        )
        self.state.add_log(f"[INIT] Default strategy: {self.state.active_strategy}")

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            with TabPane("Dashboard", id="tab-dashboard"):
                yield DashboardScreen(state=self.state)
            with TabPane("Strategy", id="tab-strategy"):
                yield StrategyConfigScreen(state=self.state)
            with TabPane("Journal", id="tab-journal"):
                yield TradeJournalScreen(state=self.state)
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self):
        """Start background workers and set up the refresh timer."""
        self.state.add_log("[INIT] Mounting TUI and starting workers...")

        # Start background loops
        self.run_price_feed()
        self.run_scanner()
        self.run_strategy()
        self.run_stop_loss_monitor()
        self.run_regime_detector()
        self.run_liquidation_poller()

        # Periodic UI refresh
        self.set_interval(
            config.DASHBOARD_REFRESH_RATE, self._refresh_display
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch to a specific tab by ID."""
        try:
            tabs = self.query_one(TabbedContent)
            tabs.active = tab_id
        except Exception:
            pass

    def action_toggle_ai(self) -> None:
        """Toggle the AI assistant on/off."""
        self.state.ai_enabled = not self.state.ai_enabled
        status = "ENABLED" if self.state.ai_enabled else "DISABLED"
        self.state.add_log(f"[AI] AI assistant {status}")

        # Sync the strategy config switch
        try:
            strategy_screen = self.query_one(StrategyConfigScreen)
            strategy_screen.refresh_state(self.state)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI refresh (called by set_interval from the main thread)
    # ------------------------------------------------------------------

    def _refresh_display(self) -> None:
        """Push latest state into all screen widgets."""
        try:
            dashboard = self.query_one(DashboardScreen)
            dashboard.refresh_data(self.state)
        except Exception:
            pass

        try:
            journal = self.query_one(TradeJournalScreen)
            journal.refresh_journal(self.state)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------

    @work(exclusive=True, thread=True, group="price_feed")
    def run_price_feed(self):
        """Fetch prices every PRICE_POLL_INTERVAL seconds."""
        self.state.add_log("[PRICE] Price feed worker started")
        while True:
            try:
                prices = asyncio.run(self.client.get_prices())
                # Filter to monitored assets only
                filtered = {
                    coin: price
                    for coin, price in prices.items()
                    if coin in config.MONITORED_ASSETS
                }
                if filtered:
                    self._prev_prices = self.state.prices.copy()
                    self.state.prices = filtered

                    # Update position prices via risk manager
                    self.risk.update_position_prices(filtered)

                # Fetch OI and funding from meta (needed by cascade strategy)
                try:
                    meta = asyncio.run(self.client.get_meta_and_asset_ctxs())
                    if meta and len(meta) > 1:
                        universe = meta[0].get("universe", [])
                        contexts = meta[1] if isinstance(meta[1], list) else []
                        for i, ctx in enumerate(contexts):
                            if i < len(universe) and isinstance(ctx, dict):
                                coin = universe[i].get("name", "")
                                if coin in config.MONITORED_ASSETS:
                                    oi = ctx.get("openInterest")
                                    if oi:
                                        self.state.open_interest[coin] = float(oi)
                                    funding = ctx.get("funding")
                                    if funding:
                                        self.state.funding_rates[coin] = float(funding)
                except Exception:
                    pass

                # Try to fetch account info
                try:
                    account = asyncio.run(self.client.get_account_info())
                    if account:
                        margin_summary = account.get("marginSummary", {})
                        self.state.account_value = float(
                            margin_summary.get("accountValue", 0)
                        )
                        self.state.available_margin = float(
                            margin_summary.get("totalMarginUsed", 0)
                        )
                        avail = self.state.account_value - self.state.available_margin
                        self.state.available_margin = max(0, avail)
                except Exception:
                    pass

            except Exception as exc:
                msg = str(exc)
                if "429" in msg:
                    self.state.add_log("[PRICE] Rate limited, backing off 30s")
                    time.sleep(30)
                    continue
                self.state.add_log(f"[PRICE] Error: {msg[:100]}")
                logger.exception("Price feed error")

            time.sleep(config.PRICE_POLL_INTERVAL)

    @work(exclusive=True, thread=True, group="scanner")
    def run_scanner(self):
        """Run the liquidation scanner every SCAN_INTERVAL_SECONDS."""
        self.state.add_log("[SCAN] Scanner worker started")
        time.sleep(5)

        while True:
            if not self.state.is_running:
                time.sleep(config.SCAN_INTERVAL_SECONDS)
                continue

            try:
                levels_by_coin = asyncio.run(self.scanner.scan_all())
                self.state.liquidation_levels = levels_by_coin
                total_levels = sum(len(v) for v in levels_by_coin.values())
                self.state.addresses_scanned = len(self.scanner.addresses)

                all_clusters = {}
                for coin, levels in levels_by_coin.items():
                    price = self.state.prices.get(coin, 0)
                    if price > 0:
                        clusters = self.scanner.cluster_levels(coin, levels, price)
                        if clusters:
                            all_clusters[coin] = clusters
                self.state.clusters = all_clusters
                self.state.last_scan_time = time.time()

                cluster_count = sum(len(v) for v in all_clusters.values())
                self.state.add_log(
                    f"[SCAN] Done: {total_levels} liq levels, "
                    f"{cluster_count} clusters from {self.state.addresses_scanned} addrs"
                )
            except Exception as exc:
                self.state.add_log(f"[SCAN] Error: {exc}")
                logger.exception("Scanner error")

            time.sleep(config.SCAN_INTERVAL_SECONDS)

    @work(exclusive=True, thread=True, group="strategy")
    def run_strategy(self):
        """Run the active strategy — fast loop, instant execution."""
        self.state.add_log("[STRATEGY] Strategy worker started")
        time.sleep(3)

        while True:
            if not self.state.is_running:
                time.sleep(1)
                continue

            try:
                strategy_name = self.state.active_strategy
                strategy = self.strategies.get(strategy_name)
                if not strategy:
                    time.sleep(1)
                    continue

                if self.state.ai_enabled:
                    if not self.ai_wrapper or self.ai_wrapper.strategy is not strategy:
                        self.ai_wrapper = AIWrapper(strategy)
                    signal = asyncio.run(self.ai_wrapper.generate_signal(self.state))
                else:
                    signal = asyncio.run(strategy.generate_signal(self.state))

                if signal:
                    self.state.active_signals.append(signal)
                    self.state.cascade_scores[signal.coin] = signal.score
                    self.state.add_log(
                        f"[SIGNAL] {signal.strategy}: {signal.direction} {signal.coin} "
                        f"score={signal.score:.0f} ({signal.confidence})"
                    )
                    if signal.ai_reasoning:
                        self.state.add_log(f"[AI] {signal.ai_reasoning[:120]}")

                    # Run gates in cheapest-first order. Each rejection is
                    # recorded in state.rejected_signals so the UI can show
                    # opportunity-cost and post-mortem stats.
                    existing_coins = {p.coin for p in self.state.positions}
                    if signal.coin in existing_coins:
                        self.state.add_rejected_signal(signal, "duplicate_coin")
                    elif not self.risk.is_cooled_down(signal.coin):
                        self.state.add_log(f"[RISK] Cooldown active for {signal.coin}")
                        self.state.add_rejected_signal(signal, "cooldown")
                    elif not self.risk.check_correlation_guard(
                        signal.coin, signal.direction
                    ):
                        self.state.add_rejected_signal(signal, "correlation")
                    elif not self.risk.check_net_directional(signal.direction):
                        self.state.add_rejected_signal(signal, "net_directional")
                    elif not self.risk.check_daily_limits():
                        self.state.add_rejected_signal(signal, "daily_loss_or_maxpos")
                    else:
                        self._execute_signal(signal)

            except Exception as exc:
                self.state.add_log(f"[STRATEGY] Error: {exc}")
                logger.exception("Strategy error")

            time.sleep(config.STRATEGY_POLL_INTERVAL)

    def _execute_signal(self, signal: Signal):
        """Execute a trade based on a signal. Called from strategy worker thread."""
        try:
            coin = signal.coin
            price = self.state.prices.get(coin, 0)
            if price <= 0:
                self.state.add_log(f"[TRADE] No price for {coin}, skipping")
                return

            size = self.risk.calculate_position_size(coin, price, signal)

            # Skip-trade floor: calculate_position_size returns 0 when the
            # dynamic scalars push the notional below MIN_POSITION_SIZE_USD.
            # In that case, don't force a floor-sized position — just record
            # the rejection and wait for a better setup.
            if size <= 0:
                self.state.add_log(
                    f"[TRADE] {coin} sized below min — skipping (vol/score too weak)"
                )
                self.state.add_rejected_signal(signal, "size_below_min")
                return

            # Total-exposure cap must run AFTER sizing — needs the notional.
            notional_usd = size * price
            if not self.risk.check_total_exposure(notional_usd):
                self.state.add_rejected_signal(signal, "exposure")
                return

            side = "buy" if signal.direction == "LONG" else "sell"
            result = asyncio.run(self.client.place_market_order(coin, side, size))

            if result.success and result.executed_size > 0:
                entry_price = result.executed_price
                is_long = signal.direction == "LONG"

                sl_pct = signal.stop_loss_pct or config.INITIAL_STOP_PCT
                tp_pct = signal.take_profit_pct or config.TAKE_PROFIT_PCT
                trail_pct = signal.trailing_stop_pct or config.TRAILING_STOP_PCT

                if is_long:
                    sl_price = entry_price * (1 - sl_pct)
                    tp_price = entry_price * (1 + tp_pct) if tp_pct else entry_price * 2
                    trail_price = entry_price * (1 - trail_pct)
                else:
                    sl_price = entry_price * (1 + sl_pct)
                    tp_price = entry_price * (1 - tp_pct) if tp_pct else entry_price * 0.5
                    trail_price = entry_price * (1 + trail_pct)

                position = ActivePosition(
                    coin=coin,
                    side="long" if is_long else "short",
                    entry_price=entry_price,
                    current_price=entry_price,
                    size=result.executed_size,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                    trailing_stop_price=trail_price,
                    high_water_mark=entry_price,
                    signal=signal,
                    entry_time=time.time(),
                    pair_id=signal.pair_id,
                )
                # State mutations under the lock; risk manager reads these
                # from other threads and can't tolerate torn writes.
                with self.state._lock:
                    self.state.positions.append(position)
                    self.state.last_trade_time[coin] = time.time()

                asyncio.run(self.risk.on_position_opened(position))

                self.state.add_log(
                    f"[TRADE] Opened {signal.direction} {coin} "
                    f"size={result.executed_size} @ ${entry_price:,.2f} "
                    f"SL={sl_pct:.1%} TP={tp_pct:.1%} Trail={trail_pct:.1%}"
                )

                # Execute hedge leg for pairs trading
                if signal.hedge_coin and signal.hedge_direction:
                    self._execute_hedge_leg(signal)

            elif result.error_message:
                self.state.add_log(f"[TRADE] Failed: {result.error_message}")
        except Exception as exc:
            self.state.add_log(f"[TRADE] Execution error: {exc}")
            logger.exception("Trade execution error")

    def _execute_hedge_leg(self, signal: Signal):
        """Execute the hedge leg for pairs trading.

        Carries the same pair_id as the primary leg so the risk manager
        closes both atomically when either trailing stop fires.
        """
        try:
            hedge_coin = signal.hedge_coin
            hedge_price = self.state.prices.get(hedge_coin, 0)
            if hedge_price <= 0:
                self.state.add_log(f"[TRADE] No price for hedge {hedge_coin}")
                return

            hedge_size = self.risk.calculate_position_size(
                hedge_coin, hedge_price, signal
            )
            if hedge_size <= 0:
                # Hedge leg undersized — primary leg is now naked. Close it
                # immediately rather than hold an unintentional directional trade.
                self.state.add_log(
                    f"[TRADE] Hedge {hedge_coin} sized below min — closing primary "
                    f"leg {signal.coin} to avoid naked exposure"
                )
                try:
                    asyncio.run(self.client.close_position(signal.coin))
                    with self.state._lock:
                        self.state.positions = [
                            p for p in self.state.positions
                            if p.coin != signal.coin or p.pair_id != signal.pair_id
                        ]
                except Exception:
                    logger.exception("Failed to unwind primary leg after hedge size=0")
                return

            hedge_side = "buy" if signal.hedge_direction == "LONG" else "sell"

            result = asyncio.run(
                self.client.place_market_order(hedge_coin, hedge_side, hedge_size)
            )

            if result.success and result.executed_size > 0:
                entry_price = result.executed_price
                is_long = signal.hedge_direction == "LONG"

                sl_pct = signal.stop_loss_pct or config.INITIAL_STOP_PCT
                trail_pct = signal.trailing_stop_pct or config.TRAILING_STOP_PCT

                if is_long:
                    sl_price = entry_price * (1 - sl_pct)
                    tp_price = entry_price * 2
                    trail_price = entry_price * (1 - trail_pct)
                else:
                    sl_price = entry_price * (1 + sl_pct)
                    tp_price = entry_price * 0.5
                    trail_price = entry_price * (1 + trail_pct)

                hedge_signal = Signal(
                    coin=hedge_coin,
                    direction=signal.hedge_direction,
                    strategy=signal.strategy,
                    score=signal.score,
                    confidence=signal.confidence,
                    reason=f"Hedge leg for {signal.coin}",
                    stop_loss_pct=signal.stop_loss_pct,
                    take_profit_pct=signal.take_profit_pct,
                    trailing_stop_pct=signal.trailing_stop_pct,
                    pair_id=signal.pair_id,
                )
                position = ActivePosition(
                    coin=hedge_coin,
                    side="long" if is_long else "short",
                    entry_price=entry_price,
                    current_price=entry_price,
                    size=result.executed_size,
                    stop_loss_price=sl_price,
                    take_profit_price=tp_price,
                    trailing_stop_price=trail_price,
                    high_water_mark=entry_price,
                    signal=hedge_signal,
                    entry_time=time.time(),
                    pair_id=signal.pair_id,
                )
                with self.state._lock:
                    self.state.positions.append(position)
                    self.state.last_trade_time[hedge_coin] = time.time()

                asyncio.run(self.risk.on_position_opened(position))

                self.state.add_log(
                    f"[TRADE] Hedge {signal.hedge_direction} {hedge_coin} "
                    f"size={result.executed_size} @ ${entry_price:,.2f} "
                    f"(pair_id={signal.pair_id[:20] if signal.pair_id else 'none'})"
                )
            elif result.error_message:
                # Hedge market order failed. Primary leg is now naked — unwind it.
                self.state.add_log(
                    f"[TRADE] Hedge order failed ({result.error_message}); "
                    f"closing primary leg {signal.coin}"
                )
                try:
                    asyncio.run(self.client.close_position(signal.coin))
                    with self.state._lock:
                        self.state.positions = [
                            p for p in self.state.positions
                            if p.coin != signal.coin or p.pair_id != signal.pair_id
                        ]
                except Exception:
                    logger.exception("Failed to unwind primary leg after hedge failure")
        except Exception as exc:
            self.state.add_log(f"[TRADE] Hedge execution error: {exc}")
            logger.exception("Hedge execution error")

    @work(exclusive=True, thread=True, group="stop_loss")
    def run_stop_loss_monitor(self):
        """Check trailing stops every STOP_LOSS_POLL_INTERVAL seconds."""
        self.state.add_log("[RISK] Stop-loss monitor started")

        while True:
            if self.state.positions:
                try:
                    messages = asyncio.run(self.risk.check_trailing_stops())
                    for msg in messages:
                        self.state.add_log(msg)
                        if "[STOP-LOSS TRIGGERED]" in msg:
                            # Trigger the flash on the dashboard
                            self.call_from_thread(self._flash_stop_loss)
                except Exception as exc:
                    self.state.add_log(f"[RISK] Stop monitor error: {exc}")
                    logger.exception("Stop-loss monitor error")

            time.sleep(config.STOP_LOSS_POLL_INTERVAL)

    @work(exclusive=True, thread=True, group="regime")
    def run_regime_detector(self):
        """Classify market regimes every REGIME_UPDATE_INTERVAL seconds."""
        self.state.add_log("[REGIME] Regime detector started")
        time.sleep(10)

        while True:
            try:
                asyncio.run(self.regime_detector.update(self.state))
                regimes = ", ".join(
                    f"{c}={r}" for c, r in list(self.state.regime.items())[:4]
                )
                if regimes:
                    self.state.add_log(f"[REGIME] {regimes}")
            except Exception as exc:
                self.state.add_log(f"[REGIME] Error: {exc}")
                logger.exception("Regime detector error")

            time.sleep(config.REGIME_UPDATE_INTERVAL)

    @work(exclusive=True, thread=True, group="liquidations")
    def run_liquidation_poller(self):
        """Poll HypeDexer for liquidation events every HYPEDEXER_POLL_INTERVAL seconds.

        Populates state.liquidation_stats with per-coin rolling stats used by
        the LiquidationCascadeV2Strategy. Runs regardless of active strategy so
        users can switch to cascade v2 at any time with data already primed.
        """
        if not config.HYPEDEXER_API_KEY:
            self.state.add_log(
                "[LIQ] HYPEDEXER_API_KEY not set — cascade v2 strategy disabled"
            )
            return

        self.state.add_log("[LIQ] Liquidation poller started (HypeDexer)")
        time.sleep(5)  # Let price feed warm up first

        stats_log_counter = 0
        while True:
            try:
                stats = asyncio.run(
                    self.liq_aggregator.poll(config.MONITORED_ASSETS)
                )
                self.state.liquidation_stats = stats
                self.state.liquidation_stats_updated = time.time()

                # Log significant cascades only (to keep noise down)
                for coin, s in stats.items():
                    if s.dominant_side and s.imbalance_ratio >= 2.0:
                        dominant_usd = (
                            s.hour_long_usd
                            if s.dominant_side == "Long"
                            else s.hour_short_usd
                        )
                        if dominant_usd >= s.threshold_usd() * 0.5:
                            # Close to / above threshold — worth logging
                            self.state.add_log(
                                f"[LIQ] {coin} {s.dominant_side}-dominant: "
                                f"${dominant_usd/1e6:.1f}M "
                                f"({s.imbalance_ratio:.1f}x imb, "
                                f"{s.acceleration:.1f}x accel)"
                            )

                # Fetch 24h summary every ~5 polls (every ~2.5 min)
                stats_log_counter += 1
                if stats_log_counter >= 5:
                    stats_log_counter = 0
                    summary = asyncio.run(
                        self.hypedexer.get_liquidation_stats(days=1)
                    )
                    if summary:
                        self.state.liquidation_24h_summary = summary
                        self.state.add_log(
                            f"[LIQ] 24h: {summary.get('number_liquidation', 0)} events, "
                            f"${summary.get('amount_liquidated_usd', 0)/1e6:.1f}M "
                            f"({summary.get('number_long_liquidated', 0)}L/"
                            f"{summary.get('number_short_liquidated', 0)}S)"
                        )

            except Exception as exc:
                self.state.add_log(f"[LIQ] Error: {str(exc)[:100]}")
                logger.exception("Liquidation poller error")

            time.sleep(config.HYPEDEXER_POLL_INTERVAL)

    def _flash_stop_loss(self):
        """Called from worker thread to flash the positions panel."""
        try:
            dashboard = self.query_one(DashboardScreen)
            dashboard.flash_stop_loss()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Handle messages from child screens
    # ------------------------------------------------------------------

    def on_strategy_config_screen_strategy_changed(
        self, event: StrategyConfigScreen.StrategyChanged
    ):
        """React to strategy selection changes."""
        self.state.add_log(
            f"[STRATEGY] Switched to: {event.strategy}"
        )

    def on_strategy_config_screen_strategy_toggled(
        self, event: StrategyConfigScreen.StrategyToggled
    ):
        """React to Start/Stop button."""
        if event.running:
            self.state.add_log("[STRATEGY] Agent started by user")
        else:
            self.state.add_log("[STRATEGY] Agent stopped by user")

    def on_strategy_config_screen_ai_toggled(
        self, event: StrategyConfigScreen.AIToggled
    ):
        """React to AI toggle from strategy screen."""
        status = "ENABLED" if event.enabled else "DISABLED"
        self.state.add_log(f"[AI] AI assistant {status} (from config)")


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = HyperAgentApp()
    app.run()
