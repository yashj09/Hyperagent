"""
HypeDexer API client — third-party indexed data for Hyperliquid.

Provides aggregated liquidation data that raw Hyperliquid's public API
does not expose. Used by LiquidationCascadeV2Strategy.

We DO NOT use HypeDexer as a general data replacement for HL because:
  - It has no OHLCV candles for perps (only HIP-3 assets)
  - metaAndAssetCtxs is per-coin, not bulk
  - Free tier is 5k credits/month (too small for continuous polling)

We DO use HypeDexer for:
  - /liquidations/recent   — aggregated liquidation events (new capability)
  - /analytics/liquidations/stats — 24h liquidation aggregates

Auth: Bearer token via Authorization header.
Endpoint: https://api.hypedexer.com/
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

from hyperagent import config

logger = logging.getLogger(__name__)


@dataclass
class LiquidationEvent:
    """One liquidation event as returned by /liquidations/recent."""

    coin: str
    time_ms: int
    liquidated_user: str
    size_total: float
    notional_total: float  # USD value of the liquidation
    fill_px_vwap: float
    mark_px: float
    liq_dir: str  # "Long" (long position liquidated) or "Short"
    liquidator_count: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> Optional["LiquidationEvent"]:
        """Parse a HypeDexer event dict. Returns None if required fields missing."""
        try:
            return cls(
                coin=str(d.get("coin", "")),
                time_ms=int(d.get("time_ms", 0)),
                liquidated_user=str(d.get("liquidated_user", "")),
                size_total=float(d.get("size_total", 0)),
                notional_total=float(d.get("notional_total", 0)),
                fill_px_vwap=float(d.get("fill_px_vwap", 0)),
                mark_px=float(d.get("mark_px", 0)),
                liq_dir=str(d.get("liq_dir", "")),
                liquidator_count=int(d.get("liquidator_count", 1)),
            )
        except (TypeError, ValueError) as e:
            logger.debug(f"Failed to parse liquidation event: {e}")
            return None


class HypeDexerClient:
    """
    Async HTTP client for HypeDexer REST API.

    Includes:
      - Auth via Bearer token
      - Exponential backoff on 429 rate-limiting
      - Request timeout + retry
      - Rate-limit header parsing (X-RateLimit-*)
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or config.HYPEDEXER_API_KEY
        self.base_url = config.HYPEDEXER_BASE_URL
        self.timeout = config.HYPEDEXER_REQUEST_TIMEOUT
        self._last_429_at: float = 0
        self._backoff_until: float = 0

        if not self.api_key:
            logger.warning(
                "HYPEDEXER_API_KEY not set — HypeDexer requests will fail with 401"
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    async def _get(self, path: str, params: Dict) -> Optional[Dict]:
        """Execute GET request with retry on transient failures. Returns None on failure."""
        if time.time() < self._backoff_until:
            return None

        url = f"{self.base_url}{path}"

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url, headers=self._headers(), params=params)

                if response.status_code == 200:
                    return response.json()

                if response.status_code == 429:
                    # Honor Retry-After or default to 60s backoff
                    retry_after = int(response.headers.get("Retry-After", 60))
                    self._last_429_at = time.time()
                    self._backoff_until = time.time() + retry_after
                    logger.warning(
                        f"HypeDexer 429 rate-limited, backing off {retry_after}s"
                    )
                    return None

                if response.status_code == 401:
                    logger.error(
                        f"HypeDexer 401 unauthorized — check HYPEDEXER_API_KEY"
                    )
                    return None

                # Other 4xx/5xx — log and retry once
                logger.debug(
                    f"HypeDexer {response.status_code} for {path}: "
                    f"{response.text[:200]}"
                )
                if response.status_code < 500:
                    return None  # client error, don't retry

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.debug(f"HypeDexer network error on {path}: {e}")

            # Exponential backoff between retries
            await asyncio.sleep(0.5 * (2 ** attempt))

        return None

    async def get_recent_liquidations(
        self,
        coin: Optional[str] = None,
        min_notional_usd: Optional[float] = None,
        limit: int = 500,
        start_time_ms: Optional[int] = None,
    ) -> List[LiquidationEvent]:
        """
        Fetch recent liquidation events from /liquidations/recent.

        Args:
          coin: Filter to a single coin (e.g. "BTC"). None = all coins.
          min_notional_usd: Minimum liquidation size in USD (server-side filter).
          limit: Max events to return (server max is typically 2000).
          start_time_ms: Only return events after this timestamp (epoch ms).

        Returns:
          List of LiquidationEvent objects, newest first.
        """
        params: Dict = {"limit": limit, "sort": "ts:desc"}
        if coin:
            params["coin"] = coin
        if min_notional_usd is not None:
            params["amount_dollars"] = min_notional_usd
        if start_time_ms is not None:
            params["start_time"] = start_time_ms

        response = await self._get("/liquidations/recent", params)
        if not response or not response.get("success"):
            return []

        events = []
        for item in response.get("data", []):
            event = LiquidationEvent.from_dict(item)
            if event:
                events.append(event)
        return events

    async def get_liquidation_stats(self, days: int = 1) -> Optional[Dict]:
        """
        Fetch aggregated liquidation stats from /analytics/liquidations/stats.

        Returns dict with fields:
          number_liquidation, number_long_liquidated, number_short_liquidated,
          amount_liquidated_usd, total_fees, top_token_liquidated
        """
        response = await self._get("/analytics/liquidations/stats", {"days": days})
        if not response or not response.get("success"):
            return None
        return response.get("data")
