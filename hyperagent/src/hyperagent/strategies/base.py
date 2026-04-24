"""
Abstract base class for all HyperAgent trading strategies.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict

from hyperagent.core.state import Signal, AgentState


class BaseStrategy(ABC):
    """Base class that all strategies must inherit from."""

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

    async def initialize(self):
        """Called when strategy is activated. Override for setup."""
        pass

    async def cleanup(self):
        """Called when strategy is deactivated. Override for cleanup."""
        pass
