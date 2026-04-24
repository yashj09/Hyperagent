"""
Core data engine: scans mainnet whale addresses to extract liquidation prices
and clusters them for cascade detection.
"""

import asyncio
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

from hyperliquid.info import Info

from hyperagent import config
from hyperagent.core.state import LiquidationLevel, LiquidationCluster

logger = logging.getLogger(__name__)


class LiquidationScanner:
    """Scans whale addresses on Hyperliquid mainnet and clusters liquidation levels."""

    def __init__(self, mainnet_info: Info, addresses: List[str]):
        self.info = mainnet_info
        self.addresses = addresses[:config.MAX_ADDRESSES_PER_SCAN]
        self.executor = ThreadPoolExecutor(max_workers=config.SCANNER_WORKERS)
        self.cache: Dict[str, List[LiquidationLevel]] = {}
        self.last_scan: float = 0

    def _scan_address_sync(self, address: str) -> List[LiquidationLevel]:
        """Synchronous scan of a single address. Called from thread pool."""
        try:
            user_state = self.info.user_state(address)
            levels = []

            if user_state and "assetPositions" in user_state:
                for pos_data in user_state["assetPositions"]:
                    position = pos_data.get("position", {})
                    coin = position.get("coin", "")
                    szi = float(position.get("szi", 0))
                    liq_px = position.get("liquidationPx")
                    entry_px = position.get("entryPx")
                    leverage = position.get("leverage", {})

                    if szi != 0 and liq_px and coin in config.MONITORED_ASSETS:
                        liq_price = float(liq_px)
                        entry_price = float(entry_px) if entry_px else 0
                        lev_value = (
                            float(leverage.get("value", 1))
                            if isinstance(leverage, dict)
                            else float(leverage)
                        )
                        notional = abs(szi) * entry_price if entry_price else 0

                        levels.append(
                            LiquidationLevel(
                                coin=coin,
                                price=liq_price,
                                side="long" if szi > 0 else "short",
                                notional_usd=notional,
                                address=address,
                                leverage=lev_value,
                                timestamp=time.time(),
                            )
                        )
            return levels
        except Exception as e:
            logger.debug(f"Error scanning {address[:10]}...: {e}")
            return []

    async def scan_all(self) -> Dict[str, List[LiquidationLevel]]:
        """Scan all addresses using thread pool. Returns {coin: [levels]}."""
        loop = asyncio.get_event_loop()
        futures = [
            loop.run_in_executor(self.executor, self._scan_address_sync, addr)
            for addr in self.addresses
        ]
        results = await asyncio.gather(*futures, return_exceptions=True)

        levels_by_coin: Dict[str, List[LiquidationLevel]] = {}
        for result in results:
            if isinstance(result, list):
                for level in result:
                    levels_by_coin.setdefault(level.coin, []).append(level)

        self.cache = levels_by_coin
        self.last_scan = time.time()
        return levels_by_coin

    def cluster_levels(
        self,
        coin: str,
        levels: List[LiquidationLevel],
        current_price: float,
    ) -> List[LiquidationCluster]:
        """Group nearby liquidation levels into clusters using sliding window."""
        if not levels:
            return []

        # Separate longs (liquidated below price) and shorts (liquidated above price)
        long_levels = sorted(
            [l for l in levels if l.side == "long"], key=lambda x: x.price
        )
        short_levels = sorted(
            [l for l in levels if l.side == "short"], key=lambda x: x.price
        )

        clusters = []
        for side_levels, side in [(long_levels, "long"), (short_levels, "short")]:
            clusters.extend(
                self._cluster_side(coin, side_levels, side, current_price)
            )

        return sorted(clusters, key=lambda c: c.density, reverse=True)

    def _cluster_side(
        self,
        coin: str,
        levels: List[LiquidationLevel],
        side: str,
        current_price: float,
    ) -> List[LiquidationCluster]:
        """Cluster one side (long or short) of liquidation levels."""
        if not levels:
            return []

        window = current_price * config.CASCADE_CLUSTER_WIDTH_PCT
        clusters = []
        current_cluster = [levels[0]]

        for i in range(1, len(levels)):
            if levels[i].price - current_cluster[0].price <= window:
                current_cluster.append(levels[i])
            else:
                cluster = self._finalize_cluster(
                    coin, current_cluster, side, current_price
                )
                if cluster:
                    clusters.append(cluster)
                current_cluster = [levels[i]]

        # Don't forget the last cluster
        cluster = self._finalize_cluster(coin, current_cluster, side, current_price)
        if cluster:
            clusters.append(cluster)

        return clusters

    def _finalize_cluster(
        self,
        coin: str,
        levels: List[LiquidationLevel],
        side: str,
        current_price: float,
    ) -> LiquidationCluster | None:
        """Convert a group of levels into a LiquidationCluster if it meets density threshold."""
        if len(levels) < config.CASCADE_DENSITY_THRESHOLD:
            return None

        prices = [l.price for l in levels]
        center = sum(prices) / len(prices)
        total_notional = sum(l.notional_usd for l in levels)
        width = (max(prices) - min(prices)) / current_price if current_price else 0

        return LiquidationCluster(
            coin=coin,
            center_price=center,
            levels=levels,
            total_notional=total_notional,
            side=side,
            density=len(levels),
            width_pct=width,
        )

    def shutdown(self):
        """Clean up the thread pool."""
        self.executor.shutdown(wait=False)
