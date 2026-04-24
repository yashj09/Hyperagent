"""
Enhanced Momentum strategy — weighted 6-signal scoring system.

Replaces the old equal-weight voting with research-calibrated weighted
scoring, ADX gate, multi-timeframe confirmation, and momentum-oriented
indicator logic (not reversal).

Signals (100 total):
  RSI(8) momentum (40/60 thresholds): 20 pts
  MACD(14,23,9) + histogram slope:    20 pts
  EMA crossover (7 vs 26):            15 pts
  Bollinger %B direction:             15 pts
  Volume-weighted 12h momentum:       15 pts
  4h EMA trend confirmation:          15 pts

Gate: ADX(14) > 20 required (no signals in choppy markets).
"""

import asyncio
import logging
import time
from typing import Optional, Dict, List

import pandas as pd
import ta
from hyperliquid.info import Info

import hyperagent.config
from hyperagent.core.state import AgentState, Signal
from hyperagent.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):

    def __init__(self, mainnet_info: Info, candle_cache=None):
        self.info = mainnet_info
        self.candle_cache = candle_cache

    @property
    def name(self) -> str:
        return "Momentum"

    @property
    def description(self) -> str:
        return (
            "Weighted 6-signal scoring: RSI, MACD+slope, EMA crossover, "
            "BB %B, volume-momentum, 4h confirmation. ADX gate filters "
            "choppy markets. Score >= 60 to trigger."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_score: float = 0

        for coin in config.MONITORED_ASSETS:
            price = state.prices.get(coin)
            if not price:
                continue

            # Regime gate: skip ranging markets entirely. Momentum trades lose
            # systematically in chop; regime detector marks these.
            regime = state.regime.get(coin)
            if regime == "ranging":
                continue

            candles = await self._fetch_candles(
                coin, config.MOMENTUM_CANDLE_INTERVAL, config.MOMENTUM_CANDLE_COUNT
            )
            if not candles or len(candles) < 30:
                continue

            closes = pd.Series([float(c["c"]) for c in candles])
            highs = pd.Series([float(c["h"]) for c in candles])
            lows = pd.Series([float(c["l"]) for c in candles])
            volumes = pd.Series([float(c.get("v", 0)) for c in candles])

            adx_ind = ta.trend.ADXIndicator(highs, lows, closes, window=14)
            adx_val = adx_ind.adx().iloc[-1]
            if adx_val < config.MOMENTUM_ADX_GATE:
                continue

            bull_score, bear_score, details = self._compute_scores(
                closes, highs, lows, volumes
            )

            htf_candles = await self._fetch_candles(
                coin, config.MOMENTUM_HTF_INTERVAL, config.MOMENTUM_HTF_CANDLE_COUNT
            )
            htf_bull, htf_bear = 0, 0
            if htf_candles and len(htf_candles) >= 10:
                htf_closes = pd.Series([float(c["c"]) for c in htf_candles])
                ema_htf_fast = ta.trend.EMAIndicator(
                    htf_closes, window=config.TREND_EMA_FAST
                ).ema_indicator().iloc[-1]
                ema_htf_slow = ta.trend.EMAIndicator(
                    htf_closes, window=config.TREND_EMA_SLOW
                ).ema_indicator().iloc[-1]
                if ema_htf_fast > ema_htf_slow:
                    htf_bull = 15
                elif ema_htf_fast < ema_htf_slow:
                    htf_bear = 15

            total_bull = bull_score + htf_bull
            total_bear = bear_score + htf_bear

            if total_bull >= config.MOMENTUM_VOTE_THRESHOLD and total_bull > total_bear:
                direction = "LONG"
                score = total_bull
            elif total_bear >= config.MOMENTUM_VOTE_THRESHOLD and total_bear > total_bull:
                direction = "SHORT"
                score = total_bear
            else:
                continue

            # Multi-timeframe gate: HTF must agree with direction. This is a strict
            # gate (not just scoring bonus) — research shows MTF confirmation alone
            # lifts Sharpe by 0.2-0.4 on crypto hourly momentum.
            if direction == "LONG" and htf_bull == 0:
                continue
            if direction == "SHORT" and htf_bear == 0:
                continue

            if score <= best_score:
                continue

            best_score = score
            confidence = "HIGH" if score >= 75 else "MEDIUM"

            best_signal = Signal(
                coin=coin,
                direction=direction,
                strategy="momentum",
                score=score,
                confidence=confidence,
                reason=(
                    f"Score {score:.0f}/100 {direction} | ADX={adx_val:.0f} | "
                    f"{details} | 4h={'aligned' if (htf_bull if direction == 'LONG' else htf_bear) > 0 else 'neutral'}"
                ),
                stop_loss_pct=0.015,
                take_profit_pct=0.035,
                trailing_stop_pct=0.012,
            )

        return best_signal

    def _compute_scores(
        self, closes: pd.Series, highs: pd.Series, lows: pd.Series, volumes: pd.Series
    ) -> tuple:
        bull = 0.0
        bear = 0.0
        parts: List[str] = []

        rsi = ta.momentum.RSIIndicator(
            closes, window=config.MOMENTUM_RSI_PERIOD
        ).rsi().iloc[-1]
        if rsi > config.MOMENTUM_RSI_BULL:
            bull += 20
            parts.append(f"RSI={rsi:.0f}↑")
        elif rsi < config.MOMENTUM_RSI_BEAR:
            bear += 20
            parts.append(f"RSI={rsi:.0f}↓")
        else:
            parts.append(f"RSI={rsi:.0f}")

        macd = ta.trend.MACD(
            closes,
            window_slow=config.MOMENTUM_MACD_SLOW,
            window_fast=config.MOMENTUM_MACD_FAST,
            window_sign=config.MOMENTUM_MACD_SIGNAL,
        )
        hist = macd.macd_diff()
        hist_val = hist.iloc[-1]
        hist_prev = hist.iloc[-2] if len(hist) >= 2 else 0
        hist_slope_up = hist_val > hist_prev
        hist_slope_down = hist_val < hist_prev

        if hist_val > 0 and hist_slope_up:
            bull += 20
            parts.append("MACD↑")
        elif hist_val < 0 and hist_slope_down:
            bear += 20
            parts.append("MACD↓")
        elif hist_val > 0:
            bull += 10
            parts.append("MACD+")
        elif hist_val < 0:
            bear += 10
            parts.append("MACD-")

        ema_fast = ta.trend.EMAIndicator(
            closes, window=config.MOMENTUM_EMA_FAST
        ).ema_indicator().iloc[-1]
        ema_slow = ta.trend.EMAIndicator(
            closes, window=config.MOMENTUM_EMA_SLOW
        ).ema_indicator().iloc[-1]
        if ema_fast > ema_slow:
            bull += 15
            parts.append("EMA↑")
        else:
            bear += 15
            parts.append("EMA↓")

        bb = ta.volatility.BollingerBands(
            closes,
            window=config.MOMENTUM_BB_PERIOD,
            window_dev=config.MOMENTUM_BB_STD,
        )
        pct_b = bb.bollinger_pband().iloc[-1]
        if pct_b > 0.6:
            bull += 15
            parts.append(f"%B={pct_b:.2f}↑")
        elif pct_b < 0.4:
            bear += 15
            parts.append(f"%B={pct_b:.2f}↓")
        else:
            parts.append(f"%B={pct_b:.2f}")

        if len(closes) >= 12 and len(volumes) >= 20:
            ret_12h = (closes.iloc[-1] - closes.iloc[-12]) / closes.iloc[-12]
            avg_vol = volumes.iloc[-20:].mean()
            cur_vol = volumes.iloc[-1]
            vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
            weighted_mom = ret_12h * min(vol_ratio, 3.0)

            if weighted_mom > 0.005:
                bull += 15
                parts.append(f"VMom={weighted_mom:+.3f}")
            elif weighted_mom < -0.005:
                bear += 15
                parts.append(f"VMom={weighted_mom:+.3f}")
            else:
                parts.append(f"VMom={weighted_mom:+.3f}")

        return bull, bear, " ".join(parts)

    async def _fetch_candles(self, coin: str, interval: str, count: int) -> list:
        if self.candle_cache:
            return await self.candle_cache.get(coin, interval, count)
        try:
            end_time = int(time.time() * 1000)
            unit = interval[-1]
            value = int(interval[:-1])
            mult = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}.get(unit, 3_600_000)
            start_time = end_time - (count * value * mult)
            candles = await asyncio.to_thread(
                self.info.candles_snapshot, coin, interval, start_time, end_time
            )
            return candles or []
        except Exception as e:
            logger.debug(f"Failed to fetch {interval} candles for {coin}: {e}")
            return []

    def get_config_schema(self) -> Dict:
        return {
            "score_threshold": {
                "type": "int",
                "default": config.MOMENTUM_VOTE_THRESHOLD,
                "description": "Min weighted score to trigger (out of 100)",
            },
            "adx_gate": {
                "type": "int",
                "default": config.MOMENTUM_ADX_GATE,
                "description": "Min ADX to allow signals",
            },
            "rsi_period": {
                "type": "int",
                "default": config.MOMENTUM_RSI_PERIOD,
                "description": "RSI lookback period",
            },
        }
