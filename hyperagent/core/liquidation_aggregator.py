"""
Liquidation data aggregator.

Polls HypeDexer for recent liquidation events, then maintains rolling
per-coin / per-direction statistics used by the Liquidation Cascade v2
strategy.

Key computed metrics (per coin):
  - hour_long_usd       Total USD value of LONG positions liquidated in last hour
  - hour_short_usd      Total USD value of SHORT positions liquidated in last hour
  - recent_15m_usd      USD value liquidated in last 15 minutes (acceleration)
  - dominant_side       "Long", "Short", or None (which side is getting washed out)
  - imbalance_ratio     hour_long_usd / hour_short_usd (or inverse)
  - acceleration        recent_15m_usd / (hour_total / 4)
  - last_event_time_ms  Newest event timestamp (for staleness check)
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import config
from core.hypedexer_client import HypeDexerClient, LiquidationEvent

logger = logging.getLogger(__name__)


@dataclass
class CoinLiquidationStats:
    """Rolling liquidation stats for one coin."""

    coin: str
    hour_long_usd: float = 0.0       # last 60 min, long side
    hour_short_usd: float = 0.0      # last 60 min, short side
    recent_15m_usd: float = 0.0      # last 15 min, dominant side only
    hour_event_count: int = 0        # number of events in last hour
    dominant_side: Optional[str] = None  # "Long" / "Short" / None
    imbalance_ratio: float = 1.0
    acceleration: float = 0.0
    last_event_time_ms: int = 0

    def threshold_usd(self) -> float:
        """Return the cascade-trigger USD threshold for this coin."""
        if self.coin == "BTC":
            return config.CASCADE_V2_THRESHOLD_BTC_USD
        if self.coin == "ETH":
            return config.CASCADE_V2_THRESHOLD_ETH_USD
        return config.CASCADE_V2_THRESHOLD_DEFAULT_USD


class LiquidationAggregator:
    """
    Pulls liquidation events from HypeDexer and maintains rolling stats.

    Stores raw events in a deque (keyed by coin), trimming events older
    than CASCADE_V2_WINDOW_MINUTES on each poll. This is O(events) per poll
    which is fine given we're talking hundreds not millions.
    """

    def __init__(self, client: HypeDexerClient):
        self.client = client
        # Per-coin deque of (time_ms, LiquidationEvent)
        self._events: Dict[str, Deque[LiquidationEvent]] = {}
        self._last_poll_time_ms: int = 0

    async def poll(self, monitored_assets: List[str]) -> Dict[str, CoinLiquidationStats]:
        """
        Fetch new liquidation events and return updated stats per coin.

        Called every config.HYPEDEXER_POLL_INTERVAL seconds from the app.
        Returns a dict keyed by coin name with latest CoinLiquidationStats.
        """
        now_ms = int(time.time() * 1000)
        window_start_ms = now_ms - (config.CASCADE_V2_WINDOW_MINUTES * 60 * 1000)

        # Fetch events since last poll (or since window start on first run).
        # Use the max of the two to avoid re-fetching the entire window each time.
        since_ms = max(window_start_ms, self._last_poll_time_ms)

        events = await self.client.get_recent_liquidations(
            min_notional_usd=config.CASCADE_V2_MIN_EVENT_USD,
            limit=config.CASCADE_V2_FETCH_LIMIT,
            start_time_ms=since_ms if self._last_poll_time_ms > 0 else None,
        )

        self._last_poll_time_ms = now_ms

        # Ingest: filter to monitored assets only and append to per-coin deques
        monitored_set = set(monitored_assets)
        for ev in events:
            if ev.coin not in monitored_set:
                continue
            if ev.coin not in self._events:
                self._events[ev.coin] = deque()
            self._events[ev.coin].append(ev)

        # Compute stats for each coin (including those with no new events)
        stats: Dict[str, CoinLiquidationStats] = {}
        for coin in monitored_assets:
            stats[coin] = self._compute_stats(coin, now_ms, window_start_ms)

        return stats

    def _compute_stats(
        self, coin: str, now_ms: int, window_start_ms: int
    ) -> CoinLiquidationStats:
        """Compute rolling stats for one coin by iterating its event deque."""
        events = self._events.get(coin)
        if not events:
            return CoinLiquidationStats(coin=coin)

        # Trim stale events from the left (oldest) to keep the window bounded
        while events and events[0].time_ms < window_start_ms:
            events.popleft()

        if not events:
            return CoinLiquidationStats(coin=coin)

        # Recent-15min window for acceleration metric
        recent_cutoff_ms = now_ms - (15 * 60 * 1000)

        hour_long = 0.0
        hour_short = 0.0
        recent_long = 0.0
        recent_short = 0.0
        last_ts = 0

        for ev in events:
            if ev.liq_dir == "Long":
                hour_long += ev.notional_total
                if ev.time_ms >= recent_cutoff_ms:
                    recent_long += ev.notional_total
            elif ev.liq_dir == "Short":
                hour_short += ev.notional_total
                if ev.time_ms >= recent_cutoff_ms:
                    recent_short += ev.notional_total
            if ev.time_ms > last_ts:
                last_ts = ev.time_ms

        # Determine dominant side
        dominant = None
        if hour_long > hour_short and hour_long > 0:
            dominant = "Long"
        elif hour_short > hour_long and hour_short > 0:
            dominant = "Short"

        # Imbalance ratio: dominant / subdominant. To avoid division by zero
        # producing astronomical ratios when the opposite side is literally $0
        # (often due to sparse data on illiquid coins), floor the divisor at
        # 1% of dominant side. This caps max imbalance at 100x, which is still
        # extreme enough to be meaningful but not nonsensical.
        if dominant == "Long":
            imbalance = hour_long / max(hour_short, hour_long * 0.01, 1.0)
            recent_dominant = recent_long
        elif dominant == "Short":
            imbalance = hour_short / max(hour_long, hour_short * 0.01, 1.0)
            recent_dominant = recent_short
        else:
            imbalance = 1.0
            recent_dominant = 0.0

        # Acceleration: recent-15min vs hourly average (hour/4 quarters)
        hour_total = hour_long + hour_short
        quarter_avg = hour_total / 4.0 if hour_total > 0 else 1.0
        acceleration = recent_dominant / quarter_avg if quarter_avg > 0 else 0.0

        return CoinLiquidationStats(
            coin=coin,
            hour_long_usd=hour_long,
            hour_short_usd=hour_short,
            recent_15m_usd=recent_dominant,
            hour_event_count=len(events),
            dominant_side=dominant,
            imbalance_ratio=imbalance,
            acceleration=acceleration,
            last_event_time_ms=last_ts,
        )
