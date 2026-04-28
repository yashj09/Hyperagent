"""
Abstract base class for all HyperAgent trading strategies.
"""

import time
from abc import ABC, abstractmethod
from typing import Optional, Dict

from hyperagent.core.state import Signal, AgentState, TickDiagnostics

# Seconds per HL candle interval. Used by the bar-close gate to compute
# "time to next bar" ETAs shown in the dashboard blocker text.
_INTERVAL_SEC = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1_800,
    "1h": 3_600, "4h": 14_400, "1d": 86_400,
}


class BaseStrategy(ABC):
    """Base class that all strategies must inherit from."""

    # Populated by the worker before each call to generate_signal().
    # Strategies mutate this (via self.tick.reject(...), etc.) so the
    # UI can see why a tick produced no signal. Worker pulls the record
    # off after the call to attach timing + publish to state.last_tick.
    tick: Optional[TickDiagnostics] = None

    # Per-(coin, interval) map of the last bar-open ts we evaluated. Candle
    # strategies poll every 15s but new data only arrives on bar close, so
    # re-evaluating the same bar 240+ times per 4h period is wasted work
    # and burns HL API budget. Populated lazily by same_bar_blocker().
    _last_bar_ts: Dict[tuple, int]

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """One-line description of the strategy."""
        ...

    @abstractmethod
    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        """Analyze market data and return a Signal or None."""
        pass

    @abstractmethod
    def get_config_schema(self) -> Dict:
        """Return configurable parameters as {name: {type, default, description}}."""
        pass

    def begin_tick(self, strategy_code: str) -> TickDiagnostics:
        """Attach a fresh TickDiagnostics to this strategy for the upcoming
        signal pass. Safe to call even if the worker doesn't (e.g. tests)
        — in that case self.tick is just discarded.

        Returns the diagnostics so call sites can bind it locally and
        avoid repeated `self.tick is not None` checks in hot loops.
        """
        diag = TickDiagnostics(strategy=strategy_code, started_at=time.time())
        self.tick = diag
        return diag

    def bar_close_blocker(
        self, coin: str, interval: str, candles: list
    ) -> Optional[str]:
        """Return an ETA blocker string if this bar was already seen, else None.

        Candle-based strategies poll on a 15s tick but new signal info only
        arrives on bar close. This lets them short-circuit with a
        user-visible blocker like "awaiting 4h bar close (~1h 23m)".

        Caller should skip this coin when a string is returned. The string
        is suitable to assign to TickDiagnostics.blocker.
        """
        if not candles:
            return None
        last = candles[-1]
        # HL candles use "t" = open-time ms. If schema changes unexpectedly,
        # fall through to normal processing rather than hard-blocking.
        bar_ts = last.get("t")
        if not isinstance(bar_ts, (int, float)):
            return None
        bar_ts = int(bar_ts)

        if not hasattr(self, "_last_bar_ts") or self._last_bar_ts is None:
            self._last_bar_ts = {}
        key = (coin, interval)
        prev = self._last_bar_ts.get(key)

        if prev == bar_ts:
            interval_sec = _INTERVAL_SEC.get(interval, 3600)
            next_close_ms = bar_ts + interval_sec * 1000
            remaining_sec = max(0, (next_close_ms - int(time.time() * 1000)) // 1000)
            mins, secs = divmod(int(remaining_sec), 60)
            hours, mins = divmod(mins, 60)
            if hours:
                eta = f"{hours}h {mins}m"
            elif mins:
                eta = f"{mins}m {secs}s"
            else:
                eta = f"{secs}s"
            return f"awaiting {interval} bar close (~{eta})"

        self._last_bar_ts[key] = bar_ts
        return None

    async def initialize(self):
        """Called when strategy is activated. Override for setup."""
        pass

    async def cleanup(self):
        """Called when strategy is deactivated. Override for cleanup."""
        pass
