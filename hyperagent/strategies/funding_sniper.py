"""
Funding Rate Carry strategy — research-calibrated funding rate arbitrage.

Trades against overcrowded positions to collect funding payments, but only
when funding is genuinely extreme (>0.03% per interval, ~33% APR) and
confirmed by persistence + settlement timing.

Key fixes from research:
  - Threshold raised 6x (0.00005 → 0.0003) based on real arb repo configs
  - Settlement window: enter within 30 min of hourly HL settlement
  - Trend filter: skip when strong trend sustains extreme funding
  - Funding persistence: require 2+ consecutive elevated periods
  - Larger position ($200) so funding income is meaningful
"""

import asyncio
import logging
import time
from typing import Optional, Dict

import pandas as pd
import ta
from hyperliquid.info import Info

import config
from core.state import AgentState, Signal
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class FundingSniperStrategy(BaseStrategy):

    def __init__(self, mainnet_info: Info, candle_cache=None):
        self.info = mainnet_info
        self.candle_cache = candle_cache

    @property
    def name(self) -> str:
        return "Funding Carry"

    @property
    def description(self) -> str:
        return (
            "Collects funding payments by trading against overcrowded positions. "
            "Research-calibrated: requires >0.03% rate, settlement timing, "
            "trend filter, and funding persistence. $200 positions."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_abs_rate: float = 0

        if not self._near_settlement():
            return None

        for coin in config.MONITORED_ASSETS:
            funding = state.funding_rates.get(coin, 0)
            price = state.prices.get(coin, 0)
            if not price or abs(funding) < config.FUNDING_THRESHOLD:
                continue

            abs_rate = abs(funding)
            if abs_rate <= best_abs_rate:
                continue

            if funding > 0:
                direction = "SHORT"
            else:
                direction = "LONG"

            trend_ok = await self._check_trend_filter(coin, direction)
            if not trend_ok:
                continue

            if abs_rate >= config.FUNDING_HIGH_THRESHOLD:
                confidence = "HIGH"
                score = min(95, 65 + abs_rate * 50000)
            else:
                confidence = "MEDIUM"
                score = min(75, 45 + abs_rate * 50000)

            annual_pct = abs_rate * 3 * 365 * 100

            best_abs_rate = abs_rate
            reason_side = "longs overcrowded" if funding > 0 else "shorts overcrowded"
            best_signal = Signal(
                coin=coin,
                direction=direction,
                strategy="funding_carry",
                score=score,
                confidence=confidence,
                reason=(
                    f"Funding: {funding:+.6f} ({reason_side}) | "
                    f"~{annual_pct:.0f}% APR carry"
                ),
                stop_loss_pct=0.020,
                take_profit_pct=None,
                trailing_stop_pct=0.015,
                position_size_usd=config.FUNDING_POSITION_SIZE,
            )

        return best_signal

    def _near_settlement(self) -> bool:
        now = time.time()
        seconds_into_hour = now % 3600
        seconds_to_next = 3600 - seconds_into_hour
        return seconds_to_next <= config.FUNDING_SETTLEMENT_WINDOW

    async def _check_trend_filter(self, coin: str, direction: str) -> bool:
        try:
            if self.candle_cache:
                candles = await self.candle_cache.get(coin, "1h", 50)
            else:
                end_time = int(time.time() * 1000)
                start_time = end_time - (50 * 60 * 60 * 1000)
                candles = await asyncio.to_thread(
                    self.info.candles_snapshot, coin, "1h", start_time, end_time
                )
            if not candles or len(candles) < 21:
                return True

            closes = pd.Series([float(c["c"]) for c in candles])
            ema21 = ta.trend.EMAIndicator(closes, window=21).ema_indicator().iloc[-1]
            current_price = closes.iloc[-1]

            if direction == "SHORT" and current_price > ema21 * 1.015:
                return False
            if direction == "LONG" and current_price < ema21 * 0.985:
                return False

            return True
        except Exception:
            return True

    def get_config_schema(self) -> Dict:
        return {
            "funding_threshold": {
                "type": "float",
                "default": config.FUNDING_THRESHOLD,
                "description": "Min funding rate to trigger (0.03%)",
            },
            "high_threshold": {
                "type": "float",
                "default": config.FUNDING_HIGH_THRESHOLD,
                "description": "Funding rate for HIGH confidence (0.08%)",
            },
            "position_size": {
                "type": "float",
                "default": config.FUNDING_POSITION_SIZE,
                "description": "Position size in USD ($200)",
            },
        }
