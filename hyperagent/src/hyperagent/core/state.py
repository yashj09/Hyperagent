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
