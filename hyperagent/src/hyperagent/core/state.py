"""
Shared state dataclass used by all HyperAgent modules.
Central data store that Textual widgets read from.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Deque
from collections import deque
import threading
import time


@dataclass
class Signal:
    coin: str
    direction: str  # "LONG" or "SHORT"
    strategy: str
    score: float  # 0-100
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    reason: str
    ai_reasoning: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
    trailing_stop_pct: Optional[float] = None
    position_size_usd: Optional[float] = None
    hedge_coin: Optional[str] = None
    hedge_direction: Optional[str] = None
    # pair_id: shared identifier for multi-leg trades (pairs reversion).
    # When both legs carry the same pair_id, risk.py closes both atomically
    # when either one trips its trailing stop — prevents one leg exiting
    # and leaving the other naked-directional.
    pair_id: Optional[str] = None


@dataclass
class TradeRecord:
    coin: str
    side: str
    strategy: str
    entry_price: float
    exit_price: float
    size: float
    pnl: float
    signal: Signal
    entry_time: float
    exit_time: float
    ai_reasoning: Optional[str] = None


@dataclass
class ActivePosition:
    coin: str
    side: str  # "long" or "short"
    entry_price: float
    current_price: float
    size: float
    stop_loss_price: float
    take_profit_price: float
    trailing_stop_price: float
    high_water_mark: float
    signal: Signal
    entry_time: float
    unrealized_pnl: float = 0.0
    # Echoed from signal.pair_id on open. None for standalone positions.
    pair_id: Optional[str] = None


@dataclass
class RejectedSignal:
    """Records a signal that was rejected before execution. Used for analysis."""

    signal: Signal
    reason: str  # "cooldown", "correlation", "daily_loss", "exposure", "net_directional", "duplicate_coin"
    timestamp: float = field(default_factory=time.time)


@dataclass
class TickDiagnostics:
    """Per-tick progress record emitted by every strategy.

    The strategy mutates this like an accumulator as it walks its coin
    universe. The worker stores the final snapshot on state.last_tick
    after each generate_signal() call so the UI can show users WHY no
    signal fired (or which coin won, with its score).

    `gate_rejections`: bucket counts keyed by a short stable code.
      Strategies pick their own codes — e.g. trend_follower uses
      "regime", "adx_low", "pullback_too_far", "funding_wrong_side".
    `top_candidate`: the best coin+score seen even if below the
      threshold, so the user sees "closest miss" instead of silence.
    """

    strategy: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    coins_evaluated: int = 0
    coins_skipped_no_data: int = 0
    gate_rejections: Dict[str, int] = field(default_factory=dict)
    top_candidate_coin: Optional[str] = None
    top_candidate_score: float = 0.0
    top_candidate_detail: str = ""
    signal_fired: bool = False
    signal_coin: Optional[str] = None
    signal_direction: Optional[str] = None
    signal_score: float = 0.0
    # Optional human-readable "why nothing fired" — only set when the
    # strategy explicitly blocked itself (e.g. funding waiting for
    # settlement window).
    blocker: Optional[str] = None
    # Bedrock latency in ms for the most recent AI reasoning call, if any.
    # Zero means no AI call was made this tick (either AI off, or no
    # signal met the reasoning threshold).
    ai_latency_ms: int = 0

    def reject(self, gate: str, count: int = 1) -> None:
        """Record a gate rejection by code."""
        self.gate_rejections[gate] = self.gate_rejections.get(gate, 0) + count

    def note_candidate(self, coin: str, score: float, detail: str = "") -> None:
        """Track the best candidate seen so far, even below threshold."""
        if score > self.top_candidate_score:
            self.top_candidate_coin = coin
            self.top_candidate_score = score
            self.top_candidate_detail = detail

    def summary(self) -> str:
        """One-line text summary for the log. Compact on purpose."""
        ai_suffix = f" (AI +{self.ai_latency_ms}ms)" if self.ai_latency_ms else ""
        if self.blocker:
            return f"blocked: {self.blocker}"
        if self.signal_fired:
            return (
                f"SIGNAL {self.signal_direction} {self.signal_coin} "
                f"score={self.signal_score:.0f}"
            ) + ai_suffix
        gates = ", ".join(
            f"{k}={v}" for k, v in sorted(self.gate_rejections.items())
        )
        top = ""
        if self.top_candidate_coin:
            top = (
                f" | top: {self.top_candidate_coin} "
                f"score={self.top_candidate_score:.0f}"
            )
        return (
            f"no signal | evaluated={self.coins_evaluated}{top}"
            + (f" | {gates}" if gates else "")
            + ai_suffix
        )

    @property
    def elapsed_ms(self) -> int:
        if self.finished_at <= 0 or self.started_at <= 0:
            return 0
        return int((self.finished_at - self.started_at) * 1000)


@dataclass
class AgentState:
    # Market data
    prices: Dict[str, float] = field(default_factory=dict)
    funding_rates: Dict[str, float] = field(default_factory=dict)
    open_interest: Dict[str, float] = field(default_factory=dict)

    # Strategy
    active_strategy: str = "trend_follower"
    ai_enabled: bool = False
    active_signals: list = field(default_factory=list)

    # Trading
    positions: list = field(default_factory=list)  # List[ActivePosition]
    trade_history: list = field(default_factory=list)  # List[TradeRecord]
    daily_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0

    # Account
    account_value: float = 0.0
    available_margin: float = 0.0

    # Regime & analytics
    regime: Dict[str, str] = field(default_factory=dict)
    atr: Dict[str, float] = field(default_factory=dict)
    last_trade_time: Dict[str, float] = field(default_factory=dict)

    # HypeDexer liquidation cascade v2 stats (per coin)
    # Values are CoinLiquidationStats objects (imported lazily to avoid cycle)
    liquidation_stats: Dict[str, object] = field(default_factory=dict)
    liquidation_stats_updated: float = 0.0
    liquidation_24h_summary: Optional[Dict] = None

    # Rejected-signal audit trail (for post-mortem analysis)
    rejected_signals: Deque = field(default_factory=lambda: deque(maxlen=200))

    # Equity curve snapshots: (timestamp_seconds, equity_usd) samples
    # appended by run_equity_tracker worker (1/min). maxlen=2880 = 48h
    # at 1-min resolution. Read by Analytics tab equity chart.
    equity_history: Deque[Tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=2880)
    )

    # Coins that user chose to ignore at startup reconciliation — the HL
    # position is left alone and strategies skip trading the coin until
    # restart. Populated by the reconciliation modal, read in run_strategy.
    reconciliation_ignored: Set[str] = field(default_factory=set)

    # Status
    is_running: bool = False
    status_message: str = "Idle"

    # Latest completed strategy tick — the UI reads this to show the user
    # what the strategy worker is actually doing. Overwritten atomically
    # by the worker (pointer-swap of a new TickDiagnostics dataclass), so
    # readers get a consistent snapshot without holding a lock.
    last_tick: Optional[TickDiagnostics] = None
    # Wall-clock time of the most recent tick completion. The dashboard
    # renders "last tick Ns ago" off this so users instantly see if the
    # strategy worker has stalled.
    last_tick_time: float = 0.0

    # Coaching surface — populated by the strategy worker whenever the
    # strategy has been silent past the suggestion threshold. Three UI
    # surfaces read these: the Dashboard Tick row (inline), the tab label
    # (badge), and the Strategy screen's panel. Keeping them on state
    # means all three render consistently from one source of truth.
    #
    # Stored as raw Suggestion objects (not formatted strings) so each
    # renderer can pick its own format (compact vs. full). Empty list =
    # no coaching needed (strategy firing, silent < threshold, or AI off
    # wouldn't matter — suggestions are strategy-driven).
    active_suggestions: list = field(default_factory=list)
    # "Try strategy X" hints, already rendered to string. Only populated
    # after SUGGESTIONS_STALE_TICKS_STRONG ticks silent.
    active_alternatives: List[str] = field(default_factory=list)
    # Number of consecutive silent ticks the strategy worker has logged.
    # Surfaces read this to show "silent Nm" countdowns.
    silent_tick_count: int = 0

    # Log
    log_lines: Deque = field(default_factory=lambda: deque(maxlen=100))

    # Re-entrant lock for thread-safe mutation of positions, trade_history,
    # daily_pnl, rejected_signals, last_trade_time. Re-entrant so the same
    # thread can acquire it multiple times (e.g. risk.check_trailing_stops
    # already holds the lock when it calls internal helpers).
    # IMPORTANT: Dataclass cannot default to threading.RLock() because deepcopy
    # would break — use default_factory.
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    def add_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{timestamp}] {message}")

    def add_rejected_signal(self, signal: "Signal", reason: str) -> None:
        """Record a rejected signal for analysis."""
        with self._lock:
            self.rejected_signals.append(RejectedSignal(signal=signal, reason=reason))

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100
