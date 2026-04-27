"""
Liquidation Cascade v2 — uses HypeDexer aggregated liquidation data.

This replaces the original cascade strategy that scanned only 28 whale
wallets. v2 sees the full liquidation firehose across all Hyperliquid
users via HypeDexer, making the cascade signal actually tradable.

Core thesis:
  Mass liquidations are self-reinforcing market events. When longs are
  forcibly closed, the forced selling pushes price down, which triggers
  MORE long liquidations -> more selling. We trade IN THE DIRECTION of
  the cascade (SHORT when longs are getting washed, LONG when shorts are
  getting squeezed), not against it.

Entry gate (ALL must pass):
  1. Dominant-side liquidation USD >= coin-specific threshold (e.g. $5M BTC)
  2. Imbalance ratio (dominant / subdominant) >= 3.0x
  3. Acceleration (recent 15min vs hourly avg) >= 1.3x
     -> confirms cascade is ongoing, not fading

Direction:
  dominant_side="Long"  (longs being liquidated)  -> enter SHORT
  dominant_side="Short" (shorts being squeezed)   -> enter LONG

Exit: trailing stop or time-based cap (cascades burn out in 1-3 hours).

Data source: HypeDexer /liquidations/recent (polled via LiquidationAggregator).
No direct API calls in this strategy — it reads from state.liquidation_stats.
"""

import logging
import time
from typing import Dict, Optional

from hyperliquid.info import Info

from hyperagent import config
from hyperagent.core.liquidation_aggregator import CoinLiquidationStats
from hyperagent.core.state import AgentState, Signal
from hyperagent.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class LiquidationCascadeV2Strategy(BaseStrategy):
    """
    Tradable cascade strategy using full-firehose liquidation data.

    Takes no API client directly — reads pre-aggregated stats from
    state.liquidation_stats, which the app-level background worker
    keeps fresh.
    """

    def __init__(self, mainnet_info: Info, candle_cache=None):
        # We don't need info or candle_cache for signals, but accept them
        # for interface consistency with other strategies.
        self.info = mainnet_info
        self.candle_cache = candle_cache

    @property
    def name(self) -> str:
        return "Liquidation Cascade v2"

    @property
    def description(self) -> str:
        return (
            "Trades ongoing liquidation cascades using full HypeDexer "
            "liquidation firehose. Enters in cascade direction (SHORT on "
            "mass long-liquidations, LONG on mass short-squeezes). Gates: "
            "threshold $ + 3x imbalance + 1.3x acceleration."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        diag = self.tick

        # Data-staleness guard — if we haven't updated in 2 minutes, skip
        if state.liquidation_stats_updated and \
                time.time() - state.liquidation_stats_updated > 120:
            if diag:
                diag.blocker = "liquidation stats stale (>2m old)"
            return None

        if not state.liquidation_stats:
            if diag:
                diag.blocker = "no liquidation data yet (HypeDexer key missing?)"
            return None

        # Find the strongest cascade signal across all monitored coins
        best_signal: Optional[Signal] = None
        best_score: float = 0

        for coin, stats in state.liquidation_stats.items():
            if not isinstance(stats, CoinLiquidationStats):
                continue
            if coin not in state.prices or state.prices[coin] <= 0:
                if diag:
                    diag.coins_skipped_no_data += 1
                continue
            if diag:
                diag.coins_evaluated += 1

            signal = self._evaluate_coin(stats, state, diag)
            if signal and signal.score > best_score:
                best_score = signal.score
                best_signal = signal

        return best_signal

    def _evaluate_coin(
        self, stats: CoinLiquidationStats, state: AgentState, diag=None
    ) -> Optional[Signal]:
        """Check if this coin's liquidation stats meet the cascade criteria."""
        # No dominant side = no cascade
        if stats.dominant_side is None:
            if diag:
                diag.reject("no_dominant_side")
            return None

        # Total dominant-side USD must exceed threshold
        dominant_usd = (
            stats.hour_long_usd
            if stats.dominant_side == "Long"
            else stats.hour_short_usd
        )
        threshold = stats.threshold_usd()
        if diag:
            diag.note_candidate(
                stats.coin, (dominant_usd / threshold) * 30 if threshold > 0 else 0,
                f"${dominant_usd/1e6:.1f}M dom / ${threshold/1e6:.1f}M thresh"
            )
        if dominant_usd < threshold:
            if diag:
                diag.reject("below_usd_threshold")
            return None

        # Imbalance: one side must dominate
        if stats.imbalance_ratio < config.CASCADE_V2_IMBALANCE_RATIO:
            if diag:
                diag.reject("imbalance_too_low")
            return None

        # Acceleration: cascade must still be unfolding
        if stats.acceleration < config.CASCADE_V2_ACCELERATION_THRESHOLD:
            if diag:
                diag.reject("acceleration_too_low")
            return None

        # Direction logic:
        #   Longs liquidated -> forced selling -> price pushed down -> go SHORT
        #   Shorts liquidated -> forced buying -> price pushed up -> go LONG
        direction = "SHORT" if stats.dominant_side == "Long" else "LONG"

        # Score: combine the three gates into a 0-100 strength measure.
        # Each gate gets up to ~33 pts; clamped so extreme cascades don't overshoot.
        threshold_score = min(40, (dominant_usd / threshold) * 20)  # 2x threshold = 40pts
        imbalance_score = min(30, (stats.imbalance_ratio / 3.0) * 15)  # 6x ratio = 30pts
        accel_score = min(30, (stats.acceleration / 1.3) * 15)  # 2.6x accel = 30pts

        score = threshold_score + imbalance_score + accel_score
        confidence = "HIGH" if score >= 75 else "MEDIUM"

        reason = (
            f"Cascade: {stats.dominant_side} liquidated "
            f"${dominant_usd/1_000_000:.1f}M (thresh ${threshold/1_000_000:.1f}M) "
            f"| imbalance {stats.imbalance_ratio:.1f}x "
            f"| accel {stats.acceleration:.1f}x "
            f"| {stats.hour_event_count} events/hr"
        )

        return Signal(
            coin=stats.coin,
            direction=direction,
            strategy="liquidation_cascade_v2",
            score=score,
            confidence=confidence,
            reason=reason,
            # Cascade trades need wider stops — they're volatile but directional
            stop_loss_pct=0.025,       # 2.5%
            take_profit_pct=0.050,     # 5% (2:1 R:R)
            trailing_stop_pct=0.018,   # 1.8% trail (tighter to lock in cascade gains)
        )

    def get_config_schema(self) -> Dict:
        return {
            "window_minutes": {
                "type": "int",
                "default": config.CASCADE_V2_WINDOW_MINUTES,
                "description": "Rolling window for liquidation aggregation",
            },
            "btc_threshold": {
                "type": "float",
                "default": config.CASCADE_V2_THRESHOLD_BTC_USD,
                "description": "USD liquidation threshold to trigger BTC cascade",
            },
            "eth_threshold": {
                "type": "float",
                "default": config.CASCADE_V2_THRESHOLD_ETH_USD,
                "description": "USD liquidation threshold to trigger ETH cascade",
            },
            "default_threshold": {
                "type": "float",
                "default": config.CASCADE_V2_THRESHOLD_DEFAULT_USD,
                "description": "USD threshold for other assets (DOGE, SOL, etc.)",
            },
            "imbalance_ratio": {
                "type": "float",
                "default": config.CASCADE_V2_IMBALANCE_RATIO,
                "description": "Dominant side must exceed subdominant by this factor",
            },
            "acceleration_threshold": {
                "type": "float",
                "default": config.CASCADE_V2_ACCELERATION_THRESHOLD,
                "description": "Last 15min vs hourly avg ratio (cascade still active)",
            },
        }
