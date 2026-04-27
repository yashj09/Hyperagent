"""
ADX Trend Follower strategy — CTA-style trend following.

Uses ADX(14) on 4h candles to confirm trend existence, +DI/-DI for
direction, EMA(21/55) for confirmation, and pullback-to-EMA for entry
timing. ATR-based dynamic stops.

Research basis: CTA funds compounded 14.52% annually (1980-2010).
Trend following Sharpe: 0.3-0.7, spikes during crises.
"""

import asyncio
import logging
import time
from typing import Optional, Dict, List

import pandas as pd
import ta
from hyperliquid.info import Info

from hyperagent import config
from hyperagent.core.state import AgentState, Signal
from hyperagent.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class TrendFollowerStrategy(BaseStrategy):

    def __init__(self, mainnet_info: Info, candle_cache=None):
        self.info = mainnet_info
        self.candle_cache = candle_cache

    @property
    def name(self) -> str:
        return "Trend Follower"

    @property
    def description(self) -> str:
        return (
            "CTA-style trend following using ADX(14) to confirm trends, "
            "+DI/-DI for direction, EMA(21/55) for confirmation, and "
            "pullback-to-EMA entries. ATR-based dynamic stops."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_score: float = 0
        diag = self.tick  # Set by worker via begin_tick; may be None in tests.

        for coin in config.MONITORED_ASSETS:
            price = state.prices.get(coin)
            if not price:
                if diag:
                    diag.coins_skipped_no_data += 1
                continue
            if diag:
                diag.coins_evaluated += 1

            # Regime gate: trend following requires a trend. Skip ranging/squeeze
            # markets where whipsaws dominate.
            regime = state.regime.get(coin)
            if regime in ("ranging", "squeeze"):
                if diag:
                    diag.reject("regime_not_trending")
                continue

            candles = await self._fetch_candles(coin)
            if not candles or len(candles) < 60:
                if diag:
                    diag.reject("candles_missing")
                continue
            await asyncio.sleep(0.1)  # Stagger between coins

            close = pd.Series([float(c["c"]) for c in candles])
            high = pd.Series([float(c["h"]) for c in candles])
            low = pd.Series([float(c["l"]) for c in candles])

            adx_ind = ta.trend.ADXIndicator(
                high, low, close, window=config.TREND_ADX_PERIOD
            )
            adx_val = adx_ind.adx().iloc[-1]
            plus_di = adx_ind.adx_pos().iloc[-1]
            minus_di = adx_ind.adx_neg().iloc[-1]

            if adx_val < config.TREND_ADX_THRESHOLD:
                if diag:
                    diag.reject("adx_below_threshold")
                    diag.note_candidate(
                        coin, adx_val, f"ADX={adx_val:.1f}<{config.TREND_ADX_THRESHOLD}"
                    )
                continue

            ema_fast = ta.trend.EMAIndicator(
                close, window=config.TREND_EMA_FAST
            ).ema_indicator()
            ema_slow = ta.trend.EMAIndicator(
                close, window=config.TREND_EMA_SLOW
            ).ema_indicator()
            ema_fast_val = ema_fast.iloc[-1]
            ema_slow_val = ema_slow.iloc[-1]

            atr_ind = ta.volatility.AverageTrueRange(high, low, close, window=14)
            atr_val = atr_ind.average_true_range().iloc[-1]

            if atr_val <= 0:
                if diag:
                    diag.reject("atr_zero")
                continue

            if plus_di > minus_di and ema_fast_val > ema_slow_val:
                direction = "LONG"
            elif minus_di > plus_di and ema_fast_val < ema_slow_val:
                direction = "SHORT"
            else:
                if diag:
                    diag.reject("di_ema_disagree")
                continue

            pullback_dist = abs(price - ema_fast_val)
            max_pullback = config.TREND_PULLBACK_ATR_MULT * atr_val
            if pullback_dist > max_pullback:
                if diag:
                    diag.reject("pullback_too_far")
                continue

            funding = state.funding_rates.get(coin, 0)
            if direction == "LONG" and funding < -0.0003:
                if diag:
                    diag.reject("funding_against")
                continue
            if direction == "SHORT" and funding > 0.0003:
                if diag:
                    diag.reject("funding_against")
                continue

            adx_score = min(40, (adx_val - 25) / 50 * 40)
            di_score = min(30, abs(plus_di - minus_di) / 30 * 30)
            ema_score = 15.0
            pullback_score = 15.0 * (1 - pullback_dist / max_pullback) if max_pullback > 0 else 0

            score = adx_score + di_score + ema_score + pullback_score

            if diag:
                diag.note_candidate(
                    coin, score, f"{direction} ADX={adx_val:.0f} score={score:.0f}"
                )

            if score < 55:
                if diag:
                    diag.reject("score_below_min")
                continue
            if score <= best_score:
                continue

            best_score = score
            confidence = "HIGH" if score >= 75 else "MEDIUM"

            stop_pct = config.TREND_STOP_ATR_MULT * atr_val / price
            tp_pct = config.TREND_TP_ATR_MULT * atr_val / price
            trail_pct = config.TREND_TRAIL_ATR_MULT * atr_val / price

            best_signal = Signal(
                coin=coin,
                direction=direction,
                strategy="trend_follower",
                score=score,
                confidence=confidence,
                reason=(
                    f"ADX={adx_val:.1f} +DI={plus_di:.1f} -DI={minus_di:.1f} | "
                    f"EMA21={'>' if ema_fast_val > ema_slow_val else '<'}EMA55 | "
                    f"Pullback {pullback_dist/atr_val:.1f}x ATR"
                ),
                stop_loss_pct=stop_pct,
                take_profit_pct=tp_pct,
                trailing_stop_pct=trail_pct,
            )

        return best_signal

    async def _fetch_candles(self, coin: str) -> list:
        if self.candle_cache:
            return await self.candle_cache.get(
                coin, config.TREND_CANDLE_INTERVAL, config.TREND_CANDLE_COUNT
            )
        try:
            end_time = int(time.time() * 1000)
            interval_ms = 4 * 60 * 60 * 1000
            start_time = end_time - (config.TREND_CANDLE_COUNT * interval_ms)
            candles = await asyncio.to_thread(
                self.info.candles_snapshot,
                coin,
                config.TREND_CANDLE_INTERVAL,
                start_time,
                end_time,
            )
            return candles or []
        except Exception as e:
            logger.debug(f"Failed to fetch 4h candles for {coin}: {e}")
            return []

    def get_config_schema(self) -> Dict:
        return {
            "adx_threshold": {
                "type": "int",
                "default": config.TREND_ADX_THRESHOLD,
                "description": "Min ADX to confirm trend",
            },
            "ema_fast": {
                "type": "int",
                "default": config.TREND_EMA_FAST,
                "description": "Fast EMA period",
            },
            "ema_slow": {
                "type": "int",
                "default": config.TREND_EMA_SLOW,
                "description": "Slow EMA period",
            },
            "stop_atr_mult": {
                "type": "float",
                "default": config.TREND_STOP_ATR_MULT,
                "description": "Stop loss as multiple of ATR",
            },
        }
