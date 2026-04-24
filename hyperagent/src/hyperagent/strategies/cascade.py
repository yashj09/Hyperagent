"""
Liquidation Cascade Predictor strategy.

Scans whale positions via LiquidationScanner, clusters liquidation levels,
and scores cascade probability (0-100) using four factors:
  40% proximity  - how close price is to the nearest cluster
  30% density    - cluster notional vs total OI
  20% momentum   - is price trending toward the cluster
  10% funding    - does funding confirm the overcrowded side

Direction logic:
  Long liquidation cluster below price -> signal SHORT (cascade pushes down)
  Short liquidation cluster above price -> signal LONG  (cascade pushes up)
"""

import asyncio
import logging
from typing import Optional, Dict, List

from hyperliquid.info import Info

from hyperagent import config
from hyperagent.core.state import (
    AgentState,
    Signal,
    LiquidationCluster,
)
from hyperagent.strategies.base import BaseStrategy
from hyperagent.scanner.liquidation_scanner import LiquidationScanner

logger = logging.getLogger(__name__)


class CascadeStrategy(BaseStrategy):
    """Predicts liquidation cascades by scanning whale positions."""

    def __init__(self, scanner: LiquidationScanner, mainnet_info: Info):
        self.scanner = scanner
        self.info = mainnet_info

    @property
    def name(self) -> str:
        return "Liquidation Cascade"

    @property
    def description(self) -> str:
        return (
            "Predicts liquidation cascades by scanning whale positions "
            "and trading the anticipated volatility"
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        """Main signal generation. Reads clusters from state, scores, and emits signal."""
        best_signal: Optional[Signal] = None
        best_score: float = 0

        for coin in config.MONITORED_ASSETS:
            clusters = state.clusters.get(coin, [])
            current_price = state.prices.get(coin)
            if not clusters or not current_price:
                continue

            total_oi = state.open_interest.get(coin, 0)
            funding_rate = state.funding_rates.get(coin, 0)

            # Fetch recent candles for momentum scoring
            candle_closes = await self._fetch_candle_closes(coin)

            for cluster in clusters:
                score = self._score_cluster(
                    cluster, current_price, total_oi, funding_rate, candle_closes
                )
                state.cascade_scores[coin] = max(
                    state.cascade_scores.get(coin, 0), score
                )

                if score > best_score and score >= config.CASCADE_SIGNAL_THRESHOLD:
                    best_score = score
                    direction = self._determine_direction(cluster, current_price)
                    confidence = (
                        "HIGH"
                        if score >= config.CASCADE_HIGH_CONFIDENCE
                        else "MEDIUM"
                        if score >= config.CASCADE_SIGNAL_THRESHOLD
                        else "LOW"
                    )
                    best_signal = Signal(
                        coin=coin,
                        direction=direction,
                        strategy="cascade",
                        score=score,
                        confidence=confidence,
                        reason=(
                            f"Liq cluster ({cluster.side}) at "
                            f"${cluster.center_price:,.0f} | "
                            f"{cluster.density} levels | "
                            f"${cluster.total_notional:,.0f} notional"
                        ),
                    )

        return best_signal

    def _score_cluster(
        self,
        cluster: LiquidationCluster,
        current_price: float,
        total_oi: float,
        funding_rate: float,
        candle_closes: List[float],
    ) -> float:
        """Compute composite cascade score (0-100) for a single cluster."""
        schema = self.get_config_schema()
        w_prox = schema["proximity_weight"]["default"]
        w_dens = schema["density_weight"]["default"]
        w_mom = schema["momentum_weight"]["default"]
        w_fund = schema["funding_weight"]["default"]

        prox = self._proximity_score(cluster, current_price)
        dens = self._density_score(cluster, total_oi)
        mom = self._momentum_score(cluster, candle_closes, current_price)
        fund = self._funding_score(cluster, funding_rate)

        return w_prox * prox + w_dens * dens + w_mom * mom + w_fund * fund

    # ------------------------------------------------------------------
    # Sub-scores
    # ------------------------------------------------------------------

    def _proximity_score(self, cluster: LiquidationCluster, current_price: float) -> float:
        """Score based on how close current price is to the cluster center."""
        if current_price <= 0:
            return 0
        distance_pct = abs(current_price - cluster.center_price) / current_price
        if distance_pct < 0.005:
            return 100
        elif distance_pct < 0.01:
            return 80
        elif distance_pct < 0.02:
            return 50
        elif distance_pct < 0.05:
            return 20
        return 0

    def _density_score(self, cluster: LiquidationCluster, total_oi: float) -> float:
        """Score based on cluster notional relative to total open interest."""
        if total_oi <= 0:
            return 0
        ratio = cluster.total_notional / total_oi
        return min(100, ratio * 1000)

    def _momentum_score(
        self,
        cluster: LiquidationCluster,
        candle_closes: List[float],
        current_price: float,
    ) -> float:
        """Check if price is trending toward the cluster using simple linear slope."""
        if len(candle_closes) < 12:
            return 0

        # Use the last 12 closes for a short-term trend
        recent = candle_closes[-12:]
        n = len(recent)
        x_mean = (n - 1) / 2
        y_mean = sum(recent) / n

        numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(recent))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator else 0

        # Normalize slope as percentage of price per candle
        slope_pct = slope / current_price if current_price else 0

        # Positive slope = price rising; negative = price falling
        # Long cluster is below price -> cascade if price falls toward it
        # Short cluster is above price -> cascade if price rises toward it
        trending_toward = False
        if cluster.side == "long" and slope_pct < -0.001:
            trending_toward = True
        elif cluster.side == "short" and slope_pct > 0.001:
            trending_toward = True

        if trending_toward:
            return min(100, abs(slope_pct) * 10000)
        return 0

    def _funding_score(self, cluster: LiquidationCluster, funding_rate: float) -> float:
        """Score based on funding rate confirming overcrowded side.

        Positive funding + long cluster below price = longs overcrowded -> 100
        Negative funding + short cluster above price = shorts overcrowded -> 100
        """
        if cluster.side == "long" and funding_rate > 0:
            return min(100, abs(funding_rate) * 10000)
        elif cluster.side == "short" and funding_rate < 0:
            return min(100, abs(funding_rate) * 10000)
        return 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_direction(
        self, cluster: LiquidationCluster, current_price: float
    ) -> str:
        """Determine trade direction based on cluster side.

        Long cluster below price -> SHORT (cascade pushes price down)
        Short cluster above price -> LONG  (cascade pushes price up)
        """
        if cluster.side == "long" and cluster.center_price < current_price:
            return "SHORT"
        elif cluster.side == "short" and cluster.center_price > current_price:
            return "LONG"
        # Fallback: trade against the overcrowded side
        return "SHORT" if cluster.side == "long" else "LONG"

    async def _fetch_candle_closes(self, coin: str) -> List[float]:
        """Fetch recent candle closes from mainnet."""
        try:
            import time as _time

            end_time = int(_time.time() * 1000)
            # 100 hourly candles = ~4 days
            start_time = end_time - (config.MOMENTUM_CANDLE_COUNT * 60 * 60 * 1000)

            candles = await asyncio.to_thread(
                self.info.candles_snapshot,
                coin,
                config.MOMENTUM_CANDLE_INTERVAL,
                start_time,
                end_time,
            )
            if candles:
                return [float(c["c"]) for c in candles]
        except Exception as e:
            logger.debug(f"Failed to fetch candles for {coin}: {e}")
        return []

    def get_config_schema(self) -> Dict:
        return {
            "proximity_weight": {
                "type": "float",
                "default": 0.4,
                "description": "Weight for proximity score",
            },
            "density_weight": {
                "type": "float",
                "default": 0.3,
                "description": "Weight for density score",
            },
            "momentum_weight": {
                "type": "float",
                "default": 0.2,
                "description": "Weight for momentum score",
            },
            "funding_weight": {
                "type": "float",
                "default": 0.1,
                "description": "Weight for funding score",
            },
            "signal_threshold": {
                "type": "float",
                "default": config.CASCADE_SIGNAL_THRESHOLD,
                "description": "Minimum composite score to emit a signal",
            },
        }
