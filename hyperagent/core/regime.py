"""
Regime detector — classifies each asset as trending, ranging, or squeeze.

Uses ADX(14) on 4h candles for trend detection and Bollinger Band width
percentile for squeeze detection. Also computes ATR(14) for dynamic
position sizing and stop placement.

Updated every REGIME_UPDATE_INTERVAL seconds (default 5 min).
"""

import asyncio
import logging
import time
from typing import Dict, Tuple

import pandas as pd
import ta
from hyperliquid.info import Info

import config
from core.state import AgentState

logger = logging.getLogger(__name__)


class RegimeDetector:

    def __init__(self, mainnet_info: Info, candle_cache=None):
        self.info = mainnet_info
        self.candle_cache = candle_cache

    async def update(self, state: AgentState) -> None:
        for coin in config.MONITORED_ASSETS:
            try:
                regime, atr_value = await self._classify(coin)
                state.regime[coin] = regime
                if atr_value > 0:
                    state.atr[coin] = atr_value
                await asyncio.sleep(0.2)  # Stagger to avoid burst rate limit
            except Exception as e:
                logger.debug(f"Regime detection failed for {coin}: {e}")

    async def _classify(self, coin: str) -> Tuple[str, float]:
        candles = await self._fetch_candles(coin)
        if not candles or len(candles) < 30:
            return "unknown", 0.0

        close = pd.Series([float(c["c"]) for c in candles])
        high = pd.Series([float(c["h"]) for c in candles])
        low = pd.Series([float(c["l"]) for c in candles])

        adx_ind = ta.trend.ADXIndicator(high, low, close, window=14)
        adx_val = adx_ind.adx().iloc[-1]

        atr_ind = ta.volatility.AverageTrueRange(high, low, close, window=14)
        atr_val = atr_ind.average_true_range().iloc[-1]

        bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        bb_width = bb.bollinger_wband()
        current_bbw = bb_width.iloc[-1]
        bbw_percentile = (bb_width < current_bbw).sum() / len(bb_width) * 100

        if bbw_percentile < 20:
            regime = "squeeze"
        elif adx_val > config.REGIME_ADX_TRENDING:
            regime = "trending"
        elif adx_val < config.REGIME_ADX_RANGING:
            regime = "ranging"
        else:
            regime = "transitioning"

        return regime, float(atr_val)

    async def _fetch_candles(self, coin: str) -> list:
        if self.candle_cache:
            return await self.candle_cache.get(
                coin, config.TREND_CANDLE_INTERVAL, config.TREND_CANDLE_COUNT
            )
        try:
            end_time = int(time.time() * 1000)
            start_time = end_time - (config.TREND_CANDLE_COUNT * 4 * 60 * 60 * 1000)
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
