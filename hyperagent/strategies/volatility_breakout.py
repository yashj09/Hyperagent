"""
Volatility Breakout strategy.

Monitors short-term candles (5m) for sudden price moves. When a single
candle moves more than the breakout threshold, enters in the direction
of the move to ride the momentum. Tight trailing stop catches reversal.
"""

import asyncio
import logging
import time
from typing import Optional, Dict, List

import config
from core.state import AgentState, Signal
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

BREAKOUT_PCT = 0.003
STRONG_BREAKOUT_PCT = 0.006
CANDLE_INTERVAL = "5m"
LOOKBACK_CANDLES = 20


class VolatilityBreakoutStrategy(BaseStrategy):

    def __init__(self, mainnet_info):
        self.info = mainnet_info

    @property
    def name(self) -> str:
        return "Volatility Breakout"

    @property
    def description(self) -> str:
        return (
            "Detects sudden price spikes on 5-minute candles and enters in the "
            "breakout direction. Rides momentum with tight trailing stop. "
            "Most active in volatile markets."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_move: float = 0

        for coin in ["BTC", "ETH", "SOL"]:
            price = state.prices.get(coin, 0)
            if not price:
                continue

            candles = await self._fetch_candles(coin)
            if len(candles) < 3:
                continue

            latest = candles[-1]
            prev = candles[-2]

            o = float(latest.get("o", 0))
            c = float(latest.get("c", 0))
            h = float(latest.get("h", 0))
            l = float(latest.get("l", 0))

            if o <= 0:
                continue

            candle_move = (c - o) / o
            candle_range = (h - l) / o
            abs_move = abs(candle_move)

            if abs_move < BREAKOUT_PCT:
                continue

            prev_range = 0
            if len(candles) >= 5:
                ranges = []
                for cd in candles[-6:-1]:
                    ch = float(cd.get("h", 0))
                    cl = float(cd.get("l", 0))
                    co = float(cd.get("o", 1))
                    if co > 0:
                        ranges.append((ch - cl) / co)
                if ranges:
                    prev_range = sum(ranges) / len(ranges)

            range_ratio = candle_range / prev_range if prev_range > 0 else 2.0

            if abs_move <= best_move:
                continue

            direction = "LONG" if candle_move > 0 else "SHORT"

            if abs_move >= STRONG_BREAKOUT_PCT or range_ratio >= 3.0:
                confidence = "HIGH"
                score = min(95, 70 + abs_move * 5000)
            else:
                confidence = "MEDIUM"
                score = min(75, 50 + abs_move * 5000)

            best_move = abs_move
            best_signal = Signal(
                coin=coin,
                direction=direction,
                strategy="volatility_breakout",
                score=score,
                confidence=confidence,
                reason=(
                    f"5m candle move: {candle_move:+.2%} | "
                    f"Range: {candle_range:.2%} | "
                    f"Ratio vs avg: {range_ratio:.1f}x"
                ),
            )

        return best_signal

    async def _fetch_candles(self, coin: str) -> List[dict]:
        try:
            now_ms = int(time.time() * 1000)
            start_ms = now_ms - (LOOKBACK_CANDLES * 5 * 60 * 1000)
            candles = await asyncio.to_thread(
                self.info.candles_snapshot, coin, CANDLE_INTERVAL, start_ms, now_ms
            )
            return candles if candles else []
        except Exception as e:
            logger.debug(f"Failed to fetch 5m candles for {coin}: {e}")
            return []

    def get_config_schema(self) -> Dict:
        return {
            "breakout_pct": {
                "type": "float",
                "default": BREAKOUT_PCT,
                "description": "Min candle move % to trigger (0.3%)",
            },
            "strong_breakout_pct": {
                "type": "float",
                "default": STRONG_BREAKOUT_PCT,
                "description": "Strong breakout threshold (0.6%)",
            },
            "candle_interval": {
                "type": "str",
                "default": CANDLE_INTERVAL,
                "description": "Candle timeframe",
            },
        }
