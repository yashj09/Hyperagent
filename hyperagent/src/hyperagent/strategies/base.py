"""
Abstract base class for all HyperAgent trading strategies.
"""

import time
from abc import ABC, abstractmethod
from typing import Optional, Dict

from hyperagent.core.state import Signal, AgentState, TickDiagnostics


class BaseStrategy(ABC):
    """Base class that all strategies must inherit from."""

    # Populated by the worker before each call to generate_signal().
    # Strategies mutate this (via self.tick.reject(...), etc.) so the
    # UI can see why a tick produced no signal. Worker pulls the record
    # off after the call to attach timing + publish to state.last_tick.
    tick: Optional[TickDiagnostics] = None

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

    async def initialize(self):
        """Called when strategy is activated. Override for setup."""
        pass

    async def cleanup(self):
        """Called when strategy is deactivated. Override for cleanup."""
        pass
