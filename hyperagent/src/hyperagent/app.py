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
from typing import Dict, List, Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, TabbedContent, TabPane
from textual.binding import Binding
from textual import work

from hyperagent import config
from hyperagent.core.state import AgentState, Signal, ActivePosition
from hyperagent.core.client import HyperLiquidClient
from hyperagent.core.risk import RiskManager
from hyperagent.scanner.liquidation_scanner import LiquidationScanner
from hyperagent.scanner.whale_addresses import get_all_addresses
from hyperagent.core.regime import RegimeDetector
from hyperagent.core.candle_cache import CandleCache
from hyperagent.core.hypedexer_client import HypeDexerClient
from hyperagent.core.liquidation_aggregator import LiquidationAggregator
from hyperagent.strategies.trend_follower import TrendFollowerStrategy
from hyperagent.strategies.momentum import MomentumStrategy
from hyperagent.strategies.funding_sniper import FundingSniperStrategy
from hyperagent.strategies.volatility_breakout import VolatilityBreakoutStrategy
from hyperagent.strategies.pairs_reversion import PairsReversionStrategy
from hyperagent.strategies.liquidation_cascade_v2 import LiquidationCascadeV2Strategy
from hyperagent.strategies.ai_wrapper import AIWrapper
from hyperagent.tui.screens.dashboard import DashboardScreen
from hyperagent.tui.screens.strategy_config import StrategyConfigScreen
from hyperagent.tui.screens.trade_journal import TradeJournalScreen
from hyperagent.tui.screens.analytics import AnalyticsScreen
from hyperagent.tui.screens.confirm_kill_modal import ConfirmKillModal
from hyperagent.tui.screens.reconcile_modal import ReconcileModal

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
        Binding("n", "switch_tab('tab-analytics')", "Analytics"),
        Binding("a", "toggle_ai", "Toggle AI"),
        Binding("k", "kill_positions", "Kill All"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()

        # Set when the user hits `q` or Ctrl-C. All background @work(thread=True)
        # loops poll this on each iteration and exit cleanly, so Textual's
        # shutdown-join doesn't hang. Without this, pressing `q` left workers
        # stuck in `while True` + `time.sleep(...)` and Textual waited
        # forever on executor join → the user had to Ctrl-C the process,
        # which raised KeyboardInterrupt deep inside asyncio.
        self._shutting_down: bool = False

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
            with TabPane("Analytics", id="tab-analytics"):
                yield AnalyticsScreen(state=self.state)
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
        self.run_equity_tracker()

        # Periodic UI refresh
        self.set_interval(
            config.DASHBOARD_REFRESH_RATE, self._refresh_display
        )

        self.state.add_log(
            "[SAFETY] Testnet mode locked — mainnet trading is disabled."
        )

        # Startup reconciliation only runs when the user opted in via the
        # wizard (step 2). Previously this fired on every boot, which was
        # noisy and surprised users who'd already reconciled. The flag is
        # one-shot — we clear it from ~/.config/hyperagent/.env below once
        # the reconcile worker finishes, so the next boot stays quiet.
        if config.HL_RECONCILE_ON_BOOT:
            self.run_reconcile_on_startup()
            self._clear_reconcile_flag()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_quit(self) -> None:
        """Signal workers to stop, then exit Textual.

        Overrides the default quit action so we can flip _shutting_down
        BEFORE Textual begins its shutdown sequence. Each background
        worker's `while not self._shutting_down:` loop notices on the
        next iteration (worst case one poll-interval later) and returns
        cleanly, so the executor join in asyncio shutdown completes
        promptly instead of hanging until Ctrl-C.
        """
        self._shutting_down = True
        self.exit()

    def _interruptible_sleep(self, seconds: float, step: float = 1.0) -> None:
        """Sleep that wakes up promptly when _shutting_down flips True.

        Plain time.sleep(N) blocks the worker for up to N seconds during
        shutdown — for the regime detector that's 300s. This helper
        sleeps in `step`-second chunks and returns early on shutdown so
        the worst-case shutdown delay is ~1 second regardless of the
        nominal interval.
        """
        end = time.time() + seconds
        while time.time() < end and not self._shutting_down:
            remaining = end - time.time()
            time.sleep(min(step, max(0.0, remaining)))

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
    # Kill switch (force-close positions)
    # ------------------------------------------------------------------

    def action_kill_positions(self) -> None:
        """Bound to 'k' — confirm then flatten ALL open positions."""
        count = len(self.state.positions)
        self.app.push_screen(
            ConfirmKillModal(coins=None, position_count=count),
            self._on_kill_confirmed_all,
        )

    def close_position_by_coin(self, coin: str) -> None:
        """Called from UI widgets (e.g. position rows) to close a single coin.

        Wrapped in the same confirmation modal as the bulk kill — never
        fire a destructive market-close without explicit user confirmation.
        """
        matching = [p for p in self.state.positions if p.coin == coin]
        if not matching:
            self.state.add_log(f"[KILL] No open position in {coin}")
            return
        self.app.push_screen(
            ConfirmKillModal(coins=[coin], position_count=len(matching)),
            lambda confirmed: self._on_kill_confirmed_coin(confirmed, coin),
        )

    def _on_kill_confirmed_all(self, confirmed: bool | None) -> None:
        """Callback from ConfirmKillModal for the 'close all' path."""
        if not confirmed:
            return
        self._run_force_close(coins=None)

    def _on_kill_confirmed_coin(self, confirmed: bool | None, coin: str) -> None:
        """Callback from ConfirmKillModal for the per-coin path."""
        if not confirmed:
            return
        self._run_force_close(coins=[coin])

    def _run_force_close(self, coins: list[str] | None) -> None:
        """Drive risk.force_close and pipe its log messages into the TUI log."""
        try:
            messages = asyncio.run(self.risk.force_close(coins))
            for msg in messages:
                self.state.add_log(msg)
        except Exception as exc:
            self.state.add_log(f"[KILL] ERROR: {exc}")
            logger.exception("Kill switch error")

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

        try:
            analytics = self.query_one(AnalyticsScreen)
            analytics.refresh_data(self.state)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Background workers
    # ------------------------------------------------------------------

    @work(exclusive=True, thread=True, group="price_feed")
    def run_price_feed(self):
        """Fetch prices every PRICE_POLL_INTERVAL seconds."""
        self.state.add_log("[PRICE] Price feed worker started")

        # Escalating backoff for repeated rate-limits. Resets to 0 on a
        # successful price fetch, so single 429s don't compound forever.
        # Sequence: 30s, 60s, 120s, 240s, then capped.
        consecutive_429s = 0

        while not self._shutting_down:
            try:
                prices = asyncio.run(self.client.get_prices())
                consecutive_429s = 0  # success resets the counter
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

                # Fetch OI and funding from meta (needed by cascade strategy).
                # The client returns {} on rate-limit, so we just skip silently.
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

                # Try to fetch account info (client returns {} on rate-limit).
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
                # The client now normalises 429s to a short RuntimeError
                # message, so detection is clean and logs fit on one line.
                if "429" in msg or "rate-limited" in msg.lower():
                    consecutive_429s += 1
                    # 30, 60, 120, 240, then cap at 300s (5 min)
                    backoff = min(30 * (2 ** (consecutive_429s - 1)), 300)
                    self.state.add_log(
                        f"[PRICE] Rate-limited (x{consecutive_429s}) — backing off {backoff}s"
                    )
                    time.sleep(backoff)
                    continue
                # Non-429 errors: log a single short line, no multi-line dump.
                self.state.add_log(f"[PRICE] Error: {msg[:80]}")
                logger.exception("Price feed error")

            self._interruptible_sleep(config.PRICE_POLL_INTERVAL)

    @work(exclusive=True, thread=True, group="scanner")
    def run_scanner(self):
        """Run the liquidation scanner every SCAN_INTERVAL_SECONDS."""
        self.state.add_log("[SCAN] Scanner worker started")
        time.sleep(5)

        while not self._shutting_down:
            if not self.state.is_running:
                self._interruptible_sleep(config.SCAN_INTERVAL_SECONDS)
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

            self._interruptible_sleep(config.SCAN_INTERVAL_SECONDS)

    @work(exclusive=True, thread=True, group="strategy")
    def run_strategy(self):
        """Run the active strategy — fast loop, instant execution."""
        self.state.add_log("[STRATEGY] Strategy worker started")
        time.sleep(3)

        while not self._shutting_down:
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
                    if signal.coin in self.state.reconciliation_ignored:
                        # User explicitly chose "Ignore" at startup for a
                        # pre-existing HL position on this coin — respect
                        # that for the whole session.
                        self.state.add_rejected_signal(signal, "reconciliation_ignored")
                    elif signal.coin in existing_coins:
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

            self._interruptible_sleep(config.STRATEGY_POLL_INTERVAL)

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

        while not self._shutting_down:
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

            self._interruptible_sleep(config.STOP_LOSS_POLL_INTERVAL)

    @work(exclusive=True, thread=True, group="regime")
    def run_regime_detector(self):
        """Classify market regimes every REGIME_UPDATE_INTERVAL seconds."""
        self.state.add_log("[REGIME] Regime detector started")
        time.sleep(10)

        while not self._shutting_down:
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

            self._interruptible_sleep(config.REGIME_UPDATE_INTERVAL)

    def _clear_reconcile_flag(self) -> None:
        """Flip HL_RECONCILE_ON_BOOT=0 in the user's .env after one fire.

        The wizard sets this to 1 when the user opts in; we set it back to
        0 so subsequent boots don't re-prompt. Best-effort — if the .env
        isn't writable we just log and move on.
        """
        path = config.ENV_FILE_PATH
        if path is None or not path.is_file():
            return
        try:
            lines = path.read_text().splitlines()
            new_lines = []
            found = False
            for line in lines:
                if line.startswith("HL_RECONCILE_ON_BOOT="):
                    new_lines.append("HL_RECONCILE_ON_BOOT=0")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append("HL_RECONCILE_ON_BOOT=0")
            path.write_text("\n".join(new_lines) + "\n")
        except OSError as exc:
            logger.warning("Could not clear reconcile flag: %s", exc)

    @work(exclusive=True, thread=True, group="reconcile")
    def run_reconcile_on_startup(self):
        """One-shot reconciliation: detect HL positions we didn't open.

        Runs once at app start, then exits. Pushes ReconcileModal if any
        unknown positions are found; otherwise silently logs "clean start".

        Implemented as a thread worker (not inline in on_mount) because:
          - We can't block the Textual main thread on a network call.
          - We need to wait briefly for price feed to populate current_price
            so the modal can display it.
        """
        self.state.add_log("[RECONCILE] Checking HL for untracked positions...")
        # Let the price feed warm up so current_price is available for
        # the modal display. If we skip this, the Current column shows "—".
        time.sleep(4)

        try:
            account = asyncio.run(self.client.get_account_info())
        except Exception as exc:
            self.state.add_log(
                f"[RECONCILE] Network error fetching account_info: {exc}. "
                "Proceeding without reconciliation — manually verify HL "
                "positions before enabling the strategy."
            )
            logger.exception("Reconcile fetch failed")
            return

        if not account:
            self.state.add_log("[RECONCILE] No account data returned.")
            return

        # HL shape: {assetPositions: [{position: {coin, szi, entryPx, ...}}, ...]}
        unknown: list[dict] = []
        known_coins = {p.coin for p in self.state.positions}

        for entry in account.get("assetPositions", []):
            pos = entry.get("position", {}) if isinstance(entry, dict) else {}
            coin = pos.get("coin")
            szi = float(pos.get("szi", 0))
            if not coin or szi == 0:
                continue
            if coin in known_coins:
                # This session opened this position — already tracked locally.
                continue
            unknown.append({
                "coin": coin,
                "side": "long" if szi > 0 else "short",
                "size": abs(szi),
                "entry_price": float(pos.get("entryPx", 0)),
                "current_price": self.state.prices.get(coin, 0),
            })

        if not unknown:
            self.state.add_log("[RECONCILE] Clean start — no untracked positions.")
            return

        self.state.add_log(
            f"[RECONCILE] Found {len(unknown)} untracked position(s): "
            + ", ".join(p["coin"] for p in unknown)
        )

        # Push the modal from the main thread — Textual requires widget
        # mutations on the main thread. call_from_thread schedules the
        # push_screen call + callback wiring there.
        def _push_modal():
            self.push_screen(
                ReconcileModal(unknown),
                lambda actions: self._apply_reconcile_actions(actions, unknown),
            )

        self.call_from_thread(_push_modal)

    def _apply_reconcile_actions(
        self, actions: Optional[Dict[str, str]], positions: List[Dict]
    ) -> None:
        """Process the reconcile modal's verdict.

        actions: {coin -> 'adopt' | 'ignore' | 'close'} or None on dismiss
        positions: the same list we passed into the modal, for size/entry lookup
        """
        if not actions:
            # User dismissed — treat all as Ignore (safest).
            actions = {p["coin"]: "ignore" for p in positions}

        # Build a map for O(1) lookup of the original position details
        pos_by_coin = {p["coin"]: p for p in positions}

        for coin, action in actions.items():
            pos = pos_by_coin.get(coin)
            if pos is None:
                continue
            try:
                if action == "ignore":
                    with self.state._lock:
                        self.state.reconciliation_ignored.add(coin)
                    self.state.add_log(f"[RECONCILE] Ignoring {coin} — strategies will skip this coin this session")
                elif action == "adopt":
                    self._adopt_position(pos)
                elif action == "close":
                    self._close_untracked(coin)
            except Exception as exc:
                self.state.add_log(f"[RECONCILE] ERROR on {coin} ({action}): {exc}")
                logger.exception("Reconcile action failed for %s", coin)

        self.state.add_log(
            "[RECONCILE] Done. Click 'Start Strategy' when ready."
        )

    def _adopt_position(self, pos: Dict) -> None:
        """Build an ActivePosition from reconciliation data and track it."""
        coin = pos["coin"]
        side = pos["side"]
        size = pos["size"]
        entry_price = pos["entry_price"]
        current_price = self.state.prices.get(coin, entry_price)

        # Synthesize a minimal Signal — we don't know the original strategy,
        # so tag it 'adopted' so attribution keeps it separate.
        adopted_signal = Signal(
            coin=coin,
            direction=side.upper(),
            strategy="adopted",
            score=0.0,
            confidence="MEDIUM",
            reason="Adopted at startup via reconciliation",
        )

        # Use global default trailing stop — we lost the original strategy
        # context, and these are the safest blanket defaults.
        trail_pct = config.TRAILING_STOP_PCT
        if side == "long":
            trail_price = current_price * (1 - trail_pct)
            sl_price = entry_price * (1 - config.INITIAL_STOP_PCT)
            tp_price = entry_price * (1 + config.TAKE_PROFIT_PCT)
        else:
            trail_price = current_price * (1 + trail_pct)
            sl_price = entry_price * (1 + config.INITIAL_STOP_PCT)
            tp_price = entry_price * (1 - config.TAKE_PROFIT_PCT)

        position = ActivePosition(
            coin=coin,
            side=side,
            entry_price=entry_price,
            current_price=current_price,
            size=size,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            trailing_stop_price=trail_price,
            high_water_mark=current_price,
            signal=adopted_signal,
            entry_time=time.time(),
            pair_id=None,
        )

        with self.state._lock:
            self.state.positions.append(position)

        self.state.add_log(
            f"[RECONCILE] Adopted {coin} {side.upper()} size={size} "
            f"entry=${entry_price:,.2f} trail=${trail_price:,.2f}"
        )

    def _close_untracked(self, coin: str) -> None:
        """Market-close an untracked HL position. No TradeRecord — we don't
        know the original strategy context, so attribution would be wrong."""
        try:
            result = asyncio.run(self.client.close_position(coin))
            if result.success:
                self.state.add_log(
                    f"[RECONCILE] Closed {coin} @ ${result.executed_price:,.2f}"
                )
                asyncio.run(self.client.cancel_all_orders(coin))
            else:
                self.state.add_log(
                    f"[RECONCILE] Close failed for {coin}: {result.error_message}"
                )
        except Exception as exc:
            self.state.add_log(f"[RECONCILE] Close error for {coin}: {exc}")
            logger.exception("Reconcile close failed for %s", coin)

    @work(exclusive=True, thread=True, group="equity")
    def run_equity_tracker(self):
        """Snapshot total equity every 30s to feed the Analytics equity curve.

        Equity = account_value (HL-reported) + unrealized on open positions
                                             + realized PnL since boot

        The realized-since-boot term is what makes the curve useful on an
        EMPTY testnet wallet. account_value might be $0, but if the user
        opens a position and it ticks up $0.50, we want to see the curve
        go up — not stay flat at zero because of the "wait for funds" gate.

        Samples are TAKEN regardless of account_value — an empty-wallet
        user running paper-style testnet trades is a legitimate mode and
        needs feedback too.

        First sample fires quickly (5s after boot) so the Analytics tab
        has something to show within seconds of first opening.
        """
        self.state.add_log("[EQUITY] Equity tracker started (30s cadence)")
        # Short warmup — just enough for the first price_feed tick.
        time.sleep(5)

        while not self._shutting_down:
            try:
                unrealized = sum(p.unrealized_pnl for p in self.state.positions)
                # daily_pnl is the running total of realized trade PnL
                # since the app started (reset on restart). Including it
                # keeps the curve connected when positions close.
                equity = (
                    self.state.account_value
                    + unrealized
                    + self.state.daily_pnl
                )
                self.state.equity_history.append((time.time(), equity))
            except Exception as exc:
                # Diagnostic feed — must never crash the app.
                logger.exception("Equity tracker error: %s", exc)

            # 30s cadence (was 60s) — twice as responsive, still cheap.
            # With maxlen=2880 snapshots that's 24h of history, which is
            # plenty for intraday analysis.
            self._interruptible_sleep(30)

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
        while not self._shutting_down:
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

            self._interruptible_sleep(config.HYPEDEXER_POLL_INTERVAL)

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

def _config_is_complete() -> bool:
    """True iff config has the minimum required fields for live trading.

    We treat "no agent key" as "run the wizard". HL_MAIN_ADDRESS is
    recommended but optional — single-key mode still works for legacy
    setups (see core/client.py).
    """
    return bool(config.HL_AGENT_PRIVATE_KEY)


def _run_setup_flow(force: bool = False) -> None:
    """Launch the CLI wizard. Writes ~/.config/hyperagent/.env on success.

    force=True → always run (used by `hyperagent setup`), pre-filling with
    existing values.
    force=False → run only when config is incomplete.
    """
    from hyperagent.onboarding.wizard import (
        run_wizard, save_config, load_existing, default_config_path,
    )

    if not force and _config_is_complete():
        return

    existing_path = default_config_path()
    existing = load_existing(existing_path) if force else {}

    try:
        cfg = run_wizard(existing=existing)
    except (KeyboardInterrupt, EOFError):
        # User cancelled — nothing saved. Exit cleanly so we don't drop
        # into the TUI with a half-configured state.
        print("\nSetup cancelled.")
        raise SystemExit(1)

    path = save_config(cfg, existing_path)
    print(f"\nSaved to {path} (chmod 600).")

    # Reload env vars into the current process so the TUI sees the new
    # config without requiring a restart.
    for k, v in cfg.items():
        import os as _os
        _os.environ[k] = v
    # Reload config module so its os.getenv(...) calls see the new values.
    import importlib
    importlib.reload(config)


def main() -> None:
    """Console-script entry point (`hyperagent` command)."""
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Subcommand dispatch (bare-bones — one subcommand, no argparse needed).
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        _run_setup_flow(force=True)
        # After a manual `setup` run, fall through and launch the TUI so
        # the user doesn't have to type `hyperagent` a second time.
    else:
        _run_setup_flow(force=False)

    if not _config_is_complete():
        # Wizard ran but config still missing — should only happen if the
        # user skipped it somehow. Bail clearly.
        print("No agent wallet configured. Run `hyperagent setup` to set one up.")
        raise SystemExit(1)

    app = HyperAgentApp()
    app.run()


if __name__ == "__main__":
    main()
