"""
Market price ticker widget.

Displays current prices for monitored assets in a horizontal bar with
colored percentage changes (green for positive, red for negative).
"""

from textual.widgets import Static
from rich.text import Text

from hyperagent import config


class MarketTicker(Static):
    """Horizontal bar showing live prices: BTC $84,532 (+0.3%) | ETH ..."""

    def __init__(self, **kwargs):
        super().__init__(" MARKETS  Connecting...", id="market-panel", **kwargs)
        self._prices: dict = {}
        self._prev_prices: dict = {}

    def update_prices(self, prices: dict, prev_prices: dict | None = None):
        """
        Update the ticker with new prices.

        Parameters
        ----------
        prices : dict
            {coin: float} current prices.
        prev_prices : dict, optional
            {coin: float} previous prices for calculating % change.
        """
        if prev_prices is not None:
            self._prev_prices = prev_prices
        elif self._prices:
            self._prev_prices = self._prices.copy()
        self._prices = prices
        self._render_ticker()

    def _render_ticker(self):
        """Build the rich Text and update the widget content."""
        output = Text()
        output.append(" MARKETS ", style="bold cyan on #161b22")
        output.append("  ")

        first = True
        for coin in config.MONITORED_ASSETS:
            price = self._prices.get(coin)
            if price is None:
                continue

            if not first:
                output.append(" | ", style="dim")
            first = False

            prev = self._prev_prices.get(coin, price)
            if prev > 0:
                pct_change = ((price - prev) / prev) * 100
            else:
                pct_change = 0.0

            # Format the price string based on magnitude
            if price >= 10_000:
                price_str = f"${price:,.0f}"
            elif price >= 100:
                price_str = f"${price:,.2f}"
            elif price >= 1:
                price_str = f"${price:,.3f}"
            else:
                price_str = f"${price:,.5f}"

            output.append(f"{coin} ", style="bold white")
            output.append(price_str, style="white")
            output.append(" ")

            if pct_change >= 0:
                output.append(f"(+{pct_change:.1f}%)", style="bold #3fb950")
            else:
                output.append(f"({pct_change:.1f}%)", style="bold #f85149")

        if not self._prices:
            output.append("Waiting for price data...", style="dim italic")

        self.update(output)
