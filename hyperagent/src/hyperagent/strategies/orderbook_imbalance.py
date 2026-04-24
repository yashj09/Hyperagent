"""
Order Book Imbalance strategy.

Reads L2 orderbook depth and detects when one side is significantly
heavier than the other. A 2:1+ buy/sell imbalance predicts short-term
upward price movement (and vice versa). Signals fire every few seconds.
"""

import asyncio
import logging
from typing import Optional, Dict

from hyperagent import config
from hyperagent.core.state import AgentState, Signal
from hyperagent.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

IMBALANCE_THRESHOLD = 1.3
STRONG_IMBALANCE = 1.8
DEPTH_LEVELS = 10


class OrderbookImbalanceStrategy(BaseStrategy):

    def __init__(self, mainnet_info):
        self.info = mainnet_info

    @property
    def name(self) -> str:
        return "Orderbook Imbalance"

    @property
    def description(self) -> str:
        return (
            "Reads L2 orderbook depth in real-time. When buy-side volume "
            "is 2x+ sell-side, goes LONG (or vice versa). Imbalance "
            "predicts short-term price direction."
        )

    async def generate_signal(self, state: AgentState) -> Optional[Signal]:
        best_signal: Optional[Signal] = None
        best_ratio: float = 0

        for coin in ["BTC", "ETH", "SOL"]:
            price = state.prices.get(coin, 0)
            if not price:
                continue

            book = await self._fetch_orderbook(coin)
            if not book:
                continue

            bids = book.get("levels", [[], []])[0] if isinstance(book.get("levels"), list) else []
            asks = book.get("levels", [[], []])[1] if isinstance(book.get("levels"), list) else []

            if not bids or not asks:
                continue

            bid_volume = sum(
                float(level.get("sz", 0))
                for level in bids[:DEPTH_LEVELS]
                if isinstance(level, dict)
            )
            ask_volume = sum(
                float(level.get("sz", 0))
                for level in asks[:DEPTH_LEVELS]
                if isinstance(level, dict)
            )

            if bid_volume <= 0 or ask_volume <= 0:
                continue

            if bid_volume > ask_volume:
                ratio = bid_volume / ask_volume
                direction = "LONG"
                side_desc = "bids"
            else:
                ratio = ask_volume / bid_volume
                direction = "SHORT"
                side_desc = "asks"

            if ratio < IMBALANCE_THRESHOLD:
                continue

            if ratio <= best_ratio:
                continue

            if ratio >= STRONG_IMBALANCE:
                confidence = "HIGH"
                score = min(90, 65 + (ratio - 1) * 15)
            else:
                confidence = "MEDIUM"
                score = min(70, 45 + (ratio - 1) * 15)

            best_ratio = ratio
            best_signal = Signal(
                coin=coin,
                direction=direction,
                strategy="orderbook_imbalance",
                score=score,
                confidence=confidence,
                reason=(
                    f"Book imbalance: {side_desc} {ratio:.1f}x heavier | "
                    f"Bid vol: {bid_volume:.2f} Ask vol: {ask_volume:.2f}"
                ),
            )

        return best_signal

    async def _fetch_orderbook(self, coin: str) -> Optional[dict]:
        try:
            book = await asyncio.to_thread(self.info.l2_snapshot, coin)
            return book
        except Exception as e:
            logger.debug(f"Failed to fetch orderbook for {coin}: {e}")
            return None

    def get_config_schema(self) -> Dict:
        return {
            "imbalance_threshold": {
                "type": "float",
                "default": IMBALANCE_THRESHOLD,
                "description": "Min bid/ask ratio to trigger (1.8x)",
            },
            "strong_imbalance": {
                "type": "float",
                "default": STRONG_IMBALANCE,
                "description": "Strong imbalance threshold (2.5x)",
            },
            "depth_levels": {
                "type": "int",
                "default": DEPTH_LEVELS,
                "description": "Number of orderbook levels to analyze",
            },
        }
