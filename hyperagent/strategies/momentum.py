"""
Momentum Flip strategy — 6-signal voting system.

Inspired by Blazefit/hyperliquid-strategy. Uses technical indicators
to vote on direction. A 4/6 (configurable) majority triggers a signal.

Signals:
  1. RSI(8) — oversold = bullish, overbought = bearish
  2. MACD(14, 23, 9) — histogram crossover
  3. EMA crossover (7 vs 26)
  4. Bollinger Bands(20, 2) — price outside bands
  5. 6h momentum (simple returns)
  6. 12h momentum (simple returns)
"""

import asyncio
import logging
import time
from typing import Optional, Dict, List

import pandas as pd
import ta
from hyperliquid.info import Info

import config
from core.state import AgentState, Signal
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """6-signal voting system for momentum-based entries."""

    def __init__(self, mainnet_info: Info):
        self.info = mainnet_info

    @property
    def name(self) -> str:
        return "Momentum Flip"

    @property
    def description(self) -> str:
        return "6-signal voting system: RSI, MACD, EMA, Bollinger Bands, 6h/12h momentum"

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        """Evaluate each monitored asset and return highest-conviction signal."""
        best_signal: Optional[Signal] = None
        best_vote_count: int = 0

        for coin in config.MONITORED_ASSETS:
            current_price = state.prices.get(coin)
            if not current_price:
                continue

            candles = await self._fetch_candles(coin)
            if not candles or len(candles) < 30:
                continue

            closes = [float(c["c"]) for c in candles]
            highs = [float(c["h"]) for c in candles]
            lows = [float(c["l"]) for c in candles]

            signals = self._compute_signals(closes, highs, lows)

            # Count votes
            bull_votes = sum(1 for v in signals.values() if v == 1)
            bear_votes = sum(1 for v in signals.values() if v == -1)

            threshold = config.MOMENTUM_VOTE_THRESHOLD

            if bull_votes >= threshold and bull_votes > bear_votes:
                direction = "LONG"
                vote_count = bull_votes
            elif bear_votes >= threshold and bear_votes > bull_votes:
                direction = "SHORT"
                vote_count = bear_votes
            else:
                continue

            # Score: scale votes into 0-100
            score = (vote_count / 6) * 100

            if vote_count > best_vote_count:
                best_vote_count = vote_count
                # Build reason string from individual signals
                signal_details = ", ".join(
                    f"{name}={'B' if v == 1 else 'S' if v == -1 else '-'}"
                    for name, v in signals.items()
                )
                confidence = (
                    "HIGH" if vote_count >= 5 else "MEDIUM" if vote_count >= 4 else "LOW"
                )
                best_signal = Signal(
                    coin=coin,
                    direction=direction,
                    strategy="momentum",
                    score=score,
                    confidence=confidence,
                    reason=f"{vote_count}/6 signals {direction} | {signal_details}",
                )

        return best_signal

    def _compute_signals(
        self, closes: List[float], highs: List[float], lows: List[float]
    ) -> Dict[str, int]:
        """Compute all 6 signals. Returns {signal_name: +1/-1/0}."""
        close_series = pd.Series(closes)
        high_series = pd.Series(highs)
        low_series = pd.Series(lows)

        signals: Dict[str, int] = {}

        # 1. RSI(8) -- oversold=bullish, overbought=bearish
        rsi = ta.momentum.RSIIndicator(
            close_series, window=config.MOMENTUM_RSI_PERIOD
        ).rsi()
        last_rsi = rsi.iloc[-1]
        signals["rsi"] = 1 if last_rsi < 30 else (-1 if last_rsi > 70 else 0)

        # 2. MACD(14, 23, 9)
        macd = ta.trend.MACD(
            close_series,
            window_slow=config.MOMENTUM_MACD_SLOW,
            window_fast=config.MOMENTUM_MACD_FAST,
            window_sign=config.MOMENTUM_MACD_SIGNAL,
        )
        macd_diff = macd.macd_diff().iloc[-1]
        signals["macd"] = 1 if macd_diff > 0 else -1

        # 3. EMA crossover (7 vs 26)
        ema_fast = ta.trend.EMAIndicator(
            close_series, window=config.MOMENTUM_EMA_FAST
        ).ema_indicator()
        ema_slow = ta.trend.EMAIndicator(
            close_series, window=config.MOMENTUM_EMA_SLOW
        ).ema_indicator()
        signals["ema"] = 1 if ema_fast.iloc[-1] > ema_slow.iloc[-1] else -1

        # 4. Bollinger Bands(20, 2)
        bb = ta.volatility.BollingerBands(
            close_series,
            window=config.MOMENTUM_BB_PERIOD,
            window_dev=config.MOMENTUM_BB_STD,
        )
        last_close = close_series.iloc[-1]
        if last_close < bb.bollinger_lband().iloc[-1]:
            signals["bb"] = 1  # Below lower band -> bullish reversal
        elif last_close > bb.bollinger_hband().iloc[-1]:
            signals["bb"] = -1  # Above upper band -> bearish reversal
        else:
            signals["bb"] = 0

        # 5. 6h momentum (simple returns)
        if len(closes) >= 6:
            mom_6h = (closes[-1] - closes[-6]) / closes[-6]
            signals["mom_6h"] = 1 if mom_6h > 0 else -1
        else:
            signals["mom_6h"] = 0

        # 6. 12h momentum
        if len(closes) >= 12:
            mom_12h = (closes[-1] - closes[-12]) / closes[-12]
            signals["mom_12h"] = 1 if mom_12h > 0 else -1
        else:
            signals["mom_12h"] = 0

        return signals

    async def _fetch_candles(self, coin: str) -> list:
        """Fetch last MOMENTUM_CANDLE_COUNT candles from mainnet."""
        try:
            end_time = int(time.time() * 1000)
            interval = config.MOMENTUM_CANDLE_INTERVAL
            # Convert interval to milliseconds for start_time calculation
            interval_ms = self._interval_to_ms(interval)
            start_time = end_time - (config.MOMENTUM_CANDLE_COUNT * interval_ms)

            candles = await asyncio.to_thread(
                self.info.candles_snapshot, coin, interval, start_time, end_time
            )
            return candles or []
        except Exception as e:
            logger.debug(f"Failed to fetch candles for {coin}: {e}")
            return []

    @staticmethod
    def _interval_to_ms(interval: str) -> int:
        """Convert candle interval string (e.g. '1h', '15m') to milliseconds."""
        unit = interval[-1]
        value = int(interval[:-1])
        multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
        return value * multipliers.get(unit, 3_600_000)

    def get_config_schema(self) -> Dict:
        return {
            "vote_threshold": {
                "type": "int",
                "default": config.MOMENTUM_VOTE_THRESHOLD,
                "description": "Signals needed to trigger (out of 6)",
            },
            "rsi_period": {
                "type": "int",
                "default": config.MOMENTUM_RSI_PERIOD,
                "description": "RSI lookback period",
            },
            "candle_interval": {
                "type": "str",
                "default": config.MOMENTUM_CANDLE_INTERVAL,
                "description": "Candle interval for indicator computation",
            },
        }
