"""
Funding Rate Sniper strategy.

Polls funding rates across all monitored assets. When funding is extreme
(overcrowded longs or shorts), enters the opposite direction to COLLECT
funding payments. Signals fire instantly when rates spike.
"""

import asyncio
import logging
from typing import Optional, Dict

import config
from core.state import AgentState, Signal
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

FUNDING_THRESHOLD = 0.00005
FUNDING_HIGH_THRESHOLD = 0.0001


class FundingSniperStrategy(BaseStrategy):

    def __init__(self, mainnet_info):
        self.info = mainnet_info

    @property
    def name(self) -> str:
        return "Funding Sniper"

    @property
    def description(self) -> str:
        return (
            "Collects funding payments by trading against overcrowded positions. "
            "When funding rate is extreme (longs paying too much), goes SHORT to "
            "receive payments. Near risk-free carry trade."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_abs_rate: float = 0

        for coin in config.MONITORED_ASSETS:
            funding = state.funding_rates.get(coin, 0)
            price = state.prices.get(coin, 0)
            if not price or abs(funding) < FUNDING_THRESHOLD:
                continue

            abs_rate = abs(funding)
            if abs_rate <= best_abs_rate:
                continue

            if funding > 0:
                direction = "SHORT"
                reason_side = "longs overcrowded, funding positive"
            else:
                direction = "LONG"
                reason_side = "shorts overcrowded, funding negative"

            if abs_rate >= FUNDING_HIGH_THRESHOLD:
                confidence = "HIGH"
                score = min(95, 60 + abs_rate * 100000)
            elif abs_rate >= FUNDING_THRESHOLD:
                confidence = "MEDIUM"
                score = min(70, 40 + abs_rate * 100000)
            else:
                continue

            annual_pct = abs_rate * 3 * 365 * 100

            best_abs_rate = abs_rate
            best_signal = Signal(
                coin=coin,
                direction=direction,
                strategy="funding_sniper",
                score=score,
                confidence=confidence,
                reason=(
                    f"Funding: {funding:+.6f} ({reason_side}) | "
                    f"~{annual_pct:.0f}% APR carry"
                ),
            )

        return best_signal

    def get_config_schema(self) -> Dict:
        return {
            "funding_threshold": {
                "type": "float",
                "default": FUNDING_THRESHOLD,
                "description": "Min funding rate to trigger signal",
            },
            "high_threshold": {
                "type": "float",
                "default": FUNDING_HIGH_THRESHOLD,
                "description": "Funding rate for HIGH confidence",
            },
        }
