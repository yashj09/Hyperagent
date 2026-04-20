"""
Cointegration-based Pairs Mean Reversion strategy.

Computes log price ratio between correlated assets, fits a rolling
OLS hedge ratio, and trades z-score deviations. Market-neutral
(long one leg, short the other).

Research basis: sujith-kamme/statistical-arbitrage-crypto achieved
Sharpe 1.76, 72.2% win rate, -1.38% max DD. Academic backtests
(Quantpedia, 1962-2002): Sharpe 1.22, 11.16% annual.

Parameters from QuantInsti: entry z=2.0, exit z=0, stop z=3.5.
"""

import asyncio
import logging
import math
import time
import uuid
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd
from hyperliquid.info import Info

import config
from core.state import AgentState, Signal
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

PAIRS = [
    ("BTC", "ETH"),
    ("SOL", "AVAX"),
]


class PairsReversionStrategy(BaseStrategy):

    def __init__(self, mainnet_info: Info, candle_cache=None):
        self.info = mainnet_info
        self.candle_cache = candle_cache

    @property
    def name(self) -> str:
        return "Pairs Reversion"

    @property
    def description(self) -> str:
        return (
            "Market-neutral pairs trading using z-score mean reversion on "
            "correlated assets (BTC/ETH, SOL/AVAX). Entry at 2σ divergence, "
            "exit at mean, stop at 3.5σ."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_zscore: float = 0

        for coin_a, coin_b in PAIRS:
            price_a = state.prices.get(coin_a)
            price_b = state.prices.get(coin_b)
            if not price_a or not price_b:
                continue

            try:
                z, corr, hedge_ratio = await self._compute_zscore(coin_a, coin_b)
            except Exception as e:
                logger.debug(f"Pairs calc failed for {coin_a}/{coin_b}: {e}")
                continue

            if corr < config.PAIRS_MIN_CORRELATION:
                continue

            abs_z = abs(z)
            if abs_z < config.PAIRS_ZSCORE_ENTRY:
                continue

            if abs_z <= best_zscore:
                continue

            if z > 0:
                primary_coin = coin_a
                primary_dir = "SHORT"
                hedge_dir = "LONG"
            else:
                primary_coin = coin_a
                primary_dir = "LONG"
                hedge_dir = "SHORT"

            z_score_pts = min(60, (abs_z - 2.0) / 2.0 * 60)
            corr_pts = min(30, corr * 30)

            funding_a = state.funding_rates.get(coin_a, 0)
            funding_b = state.funding_rates.get(coin_b, 0)
            funding_pts = 0
            if primary_dir == "SHORT" and funding_a > 0:
                funding_pts += 5
            elif primary_dir == "LONG" and funding_a < 0:
                funding_pts += 5
            if hedge_dir == "SHORT" and funding_b > 0:
                funding_pts += 5
            elif hedge_dir == "LONG" and funding_b < 0:
                funding_pts += 5

            score = z_score_pts + corr_pts + funding_pts
            if score < 55:
                continue

            best_zscore = abs_z
            confidence = "HIGH" if score >= 75 else "MEDIUM"

            # pair_id ties both legs together. Risk manager uses this to
            # atomically close the sibling when either leg's stop fires.
            # 12-char hex is plenty unique for concurrent live trades.
            pair_id = f"pair-{uuid.uuid4().hex[:12]}"

            best_signal = Signal(
                coin=primary_coin,
                direction=primary_dir,
                strategy="pairs_reversion",
                score=score,
                confidence=confidence,
                reason=(
                    f"{coin_a}/{coin_b} z={z:+.2f} corr={corr:.2f} "
                    f"hedge_ratio={hedge_ratio:.4f} | "
                    f"{primary_dir} {coin_a} + {hedge_dir} {coin_b}"
                ),
                position_size_usd=config.PAIRS_POSITION_SIZE_PER_LEG,
                hedge_coin=coin_b,
                hedge_direction=hedge_dir,
                pair_id=pair_id,
            )

        return best_signal

    async def _compute_zscore(
        self, coin_a: str, coin_b: str
    ) -> Tuple[float, float, float]:
        candles_a = await self._fetch_candles(coin_a)
        candles_b = await self._fetch_candles(coin_b)

        min_len = min(len(candles_a), len(candles_b))
        if min_len < config.PAIRS_LOOKBACK_HOURS + 5:
            raise ValueError(f"Insufficient candles: {min_len}")

        closes_a = np.array([float(c["c"]) for c in candles_a[-min_len:]])
        closes_b = np.array([float(c["c"]) for c in candles_b[-min_len:]])

        log_a = np.log(closes_a)
        log_b = np.log(closes_b)

        lookback = config.PAIRS_LOOKBACK_HOURS
        recent_log_a = log_a[-lookback:]
        recent_log_b = log_b[-lookback:]

        corr = float(np.corrcoef(recent_log_a, recent_log_b)[0, 1])

        x = recent_log_b
        y = recent_log_a
        x_mean = x.mean()
        y_mean = y.mean()
        hedge_ratio = float(
            np.sum((x - x_mean) * (y - y_mean)) / np.sum((x - x_mean) ** 2)
        )

        spread = log_a - hedge_ratio * log_b

        spread_window = spread[-lookback:]
        spread_mean = float(spread_window.mean())
        spread_std = float(spread_window.std())

        if spread_std < 1e-10:
            raise ValueError("Spread std too small")

        current_spread = float(spread[-1])
        z_score = (current_spread - spread_mean) / spread_std

        return z_score, corr, hedge_ratio

    async def _fetch_candles(self, coin: str) -> list:
        if self.candle_cache:
            return await self.candle_cache.get(
                coin, config.PAIRS_CANDLE_INTERVAL, config.PAIRS_CANDLE_COUNT
            )
        try:
            end_time = int(time.time() * 1000)
            interval_ms = 60 * 60 * 1000
            start_time = end_time - (config.PAIRS_CANDLE_COUNT * interval_ms)
            candles = await asyncio.to_thread(
                self.info.candles_snapshot,
                coin,
                config.PAIRS_CANDLE_INTERVAL,
                start_time,
                end_time,
            )
            return candles or []
        except Exception as e:
            logger.debug(f"Failed to fetch candles for {coin}: {e}")
            return []

    def get_config_schema(self) -> Dict:
        return {
            "zscore_entry": {
                "type": "float",
                "default": config.PAIRS_ZSCORE_ENTRY,
                "description": "Z-score threshold for entry (2.0σ)",
            },
            "zscore_stop": {
                "type": "float",
                "default": config.PAIRS_ZSCORE_STOP,
                "description": "Z-score threshold for stop (3.5σ)",
            },
            "min_correlation": {
                "type": "float",
                "default": config.PAIRS_MIN_CORRELATION,
                "description": "Minimum pair correlation (0.70)",
            },
        }
