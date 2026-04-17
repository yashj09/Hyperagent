"""
Shared state dataclass used by all HyperAgent modules.
Central data store that Textual widgets read from.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Deque
from collections import deque
import time


@dataclass
class LiquidationLevel:
    coin: str
    price: float
    side: str  # "long" or "short"
    notional_usd: float
    address: str
    leverage: float
    timestamp: float


@dataclass
class LiquidationCluster:
    coin: str
    center_price: float
    levels: list  # List[LiquidationLevel]
    total_notional: float
    side: str  # "long" or "short"
    density: int
    width_pct: float


@dataclass
class Signal:
    coin: str
    direction: str  # "LONG" or "SHORT"
    strategy: str  # "cascade" or "momentum"
    score: float  # 0-100
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    reason: str
    ai_reasoning: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


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


@dataclass
class AgentState:
    # Market data
    prices: Dict[str, float] = field(default_factory=dict)
    funding_rates: Dict[str, float] = field(default_factory=dict)
    open_interest: Dict[str, float] = field(default_factory=dict)

    # Scanner
    liquidation_levels: Dict[str, list] = field(default_factory=dict)
    clusters: Dict[str, list] = field(default_factory=dict)
    addresses_scanned: int = 0
    last_scan_time: float = 0.0

    # Strategy
    active_strategy: str = "cascade"
    ai_enabled: bool = False
    cascade_scores: Dict[str, float] = field(default_factory=dict)
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

    # Status
    is_running: bool = False
    status_message: str = "Idle"

    # Log
    log_lines: Deque = field(default_factory=lambda: deque(maxlen=100))

    def add_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        self.log_lines.append(f"[{timestamp}] {message}")

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100
