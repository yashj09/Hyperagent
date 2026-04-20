"""
Volatility Squeeze Breakout strategy.

Detects Bollinger Band squeeze (BB inside Keltner Channel) as a
compression signal, then trades the breakout with ATR-adaptive
thresholds and volume confirmation.

Key fixes from research:
  - Fixed 0.1% threshold → ATR-adaptive (1.5 * ATR)
  - Added BB/Keltner squeeze detection (TTM Squeeze concept)
  - Added volume confirmation (1.5x average)
  - 5m → 15m candles (less noise)
  - Continuation check (wait 1 candle after breakout)
  - All 8 monitored assets (was only 3)
  - Wider stops: 2.5% SL, 5% TP, 2% trail
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


class VolatilityBreakoutStrategy(BaseStrategy):

    def __init__(self, mainnet_info: Info, candle_cache=None):
        self.info = mainnet_info
        self.candle_cache = candle_cache

    @property
    def name(self) -> str:
        return "Volatility Breakout"

    @property
    def description(self) -> str:
        return (
            "Detects Bollinger squeeze (BB inside Keltner), then trades "
            "the breakout with ATR-adaptive thresholds, volume confirmation, "
            "and continuation check. 15m candles, all 8 assets."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_score: float = 0

        for coin in config.MONITORED_ASSETS:
            price = state.prices.get(coin, 0)
            if not price:
                continue

            candles = await self._fetch_candles(coin)
            if not candles or len(candles) < 25:
                continue

            closes = pd.Series([float(c["c"]) for c in candles])
            highs = pd.Series([float(c["h"]) for c in candles])
            lows = pd.Series([float(c["l"]) for c in candles])
            volumes = pd.Series([float(c.get("v", 0)) for c in candles])

            atr = ta.volatility.AverageTrueRange(
                highs, lows, closes, window=20
            ).average_true_range()
            atr_val = atr.iloc[-1]

            if atr_val <= 0:
                continue

            squeeze_active = self._detect_squeeze(closes, highs, lows)

            if not squeeze_active:
                continue

            breakout_threshold = config.BREAKOUT_ATR_MULT * atr_val

            if len(candles) < 3:
                continue
            prev_candle = candles[-2]
            prev_o = float(prev_candle.get("o", 0))
            prev_c = float(prev_candle.get("c", 0))
            if prev_o <= 0:
                continue

            prev_move = prev_c - prev_o
            abs_move = abs(prev_move)

            if abs_move < breakout_threshold:
                continue

            latest = candles[-1]
            lat_o = float(latest.get("o", 0))
            lat_c = float(latest.get("c", 0))
            continuation = (prev_move > 0 and lat_c > lat_o) or (
                prev_move < 0 and lat_c < lat_o
            )

            avg_vol = volumes.iloc[-21:-1].mean() if len(volumes) >= 21 else volumes.mean()
            prev_vol = volumes.iloc[-2]
            vol_ratio = prev_vol / avg_vol if avg_vol > 0 else 1.0

            if vol_ratio < config.BREAKOUT_VOLUME_MULT:
                continue

            direction = "LONG" if prev_move > 0 else "SHORT"

            breakout_pts = min(30, (abs_move / atr_val - config.BREAKOUT_ATR_MULT) * 30)
            vol_pts = min(30, (vol_ratio - 1.0) * 30)
            squeeze_pts = 20.0
            continuation_pts = 20.0 if continuation else 0.0

            score = max(0, breakout_pts) + vol_pts + squeeze_pts + continuation_pts

            if score <= best_score or score < 55:
                continue

            best_score = score
            confidence = "HIGH" if score >= 75 else "MEDIUM"

            best_signal = Signal(
                coin=coin,
                direction=direction,
                strategy="volatility_breakout",
                score=score,
                confidence=confidence,
                reason=(
                    f"Squeeze breakout {direction} | "
                    f"Move: {abs_move/atr_val:.1f}x ATR | "
                    f"Vol: {vol_ratio:.1f}x avg | "
                    f"{'Confirmed' if continuation else 'Unconfirmed'}"
                ),
                stop_loss_pct=0.025,
                take_profit_pct=0.050,
                trailing_stop_pct=0.020,
            )

        return best_signal

    def _detect_squeeze(
        self, closes: pd.Series, highs: pd.Series, lows: pd.Series
    ) -> bool:
        bb = ta.volatility.BollingerBands(closes, window=20, window_dev=2.0)
        bb_upper = bb.bollinger_hband()
        bb_lower = bb.bollinger_lband()
        bb_width = bb_upper - bb_lower

        kc = ta.volatility.KeltnerChannel(highs, lows, closes, window=20, window_atr=10)
        kc_upper = kc.keltner_channel_hband()
        kc_lower = kc.keltner_channel_lband()
        kc_width = kc_upper - kc_lower

        squeeze_count = 0
        lookback = min(24, len(closes) - 1)
        for i in range(len(closes) - lookback, len(closes)):
            if i >= 0 and bb_width.iloc[i] < kc_width.iloc[i]:
                squeeze_count += 1

        return squeeze_count >= config.BREAKOUT_SQUEEZE_BARS

    async def _fetch_candles(self, coin: str) -> List[dict]:
        if self.candle_cache:
            return await self.candle_cache.get(
                coin, config.BREAKOUT_CANDLE_INTERVAL, config.BREAKOUT_LOOKBACK_CANDLES
            )
        try:
            now_ms = int(time.time() * 1000)
            interval_ms = 15 * 60 * 1000
            start_ms = now_ms - (config.BREAKOUT_LOOKBACK_CANDLES * interval_ms)
            candles = await asyncio.to_thread(
                self.info.candles_snapshot,
                coin,
                config.BREAKOUT_CANDLE_INTERVAL,
                start_ms,
                now_ms,
            )
            return candles if candles else []
        except Exception as e:
            logger.debug(f"Failed to fetch 15m candles for {coin}: {e}")
            return []

    def get_config_schema(self) -> Dict:
        return {
            "atr_mult": {
                "type": "float",
                "default": config.BREAKOUT_ATR_MULT,
                "description": "Breakout threshold as ATR multiple (1.5x)",
            },
            "squeeze_bars": {
                "type": "int",
                "default": config.BREAKOUT_SQUEEZE_BARS,
                "description": "Min squeeze bars required (3)",
            },
            "volume_mult": {
                "type": "float",
                "default": config.BREAKOUT_VOLUME_MULT,
                "description": "Min volume vs average (1.5x)",
            },
        }
