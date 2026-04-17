"""
Liquidation heatmap widget.

Renders liquidation clusters as a vertical list with colored density bars.
Long-side clusters (below price) are green, short-side (above price) are red.
"""

from textual.widgets import Static
from rich.text import Text

from core.state import AgentState, LiquidationCluster


class LiquidationHeatmap(Static):
    """Visual display of liquidation cluster density around current prices."""

    MAX_BAR_WIDTH = 16

    def __init__(self, **kwargs):
        super().__init__("", id="heatmap-panel", **kwargs)

    def update_clusters(self, state: AgentState):
        """Rebuild the heatmap from current state data."""
        output = Text()
        output.append("LIQUIDATION HEATMAP\n", style="bold cyan")
        output.append("=" * 60 + "\n", style="dim")

        clusters = state.clusters
        if not clusters:
            output.append(
                "\n  No liquidation data yet. Scanner running...\n",
                style="dim italic",
            )
            self.update(output)
            return

        for coin in sorted(clusters.keys()):
            coin_clusters = clusters[coin]
            if not coin_clusters:
                continue

            current_price = state.prices.get(coin, 0.0)
            if current_price >= 10_000:
                price_str = f"${current_price:,.0f}"
            elif current_price >= 1:
                price_str = f"${current_price:,.2f}"
            else:
                price_str = f"${current_price:,.4f}"

            output.append(f"\n{coin}", style="bold white")
            output.append(f"  (price: {price_str})\n", style="dim")

            # Separate long (below price) and short (above price) clusters
            long_clusters = [
                c for c in coin_clusters
                if isinstance(c, LiquidationCluster) and c.side == "long"
            ]
            short_clusters = [
                c for c in coin_clusters
                if isinstance(c, LiquidationCluster) and c.side == "short"
            ]

            # Also handle dict-format clusters
            if coin_clusters and isinstance(coin_clusters[0], dict):
                long_clusters = [
                    c for c in coin_clusters if c.get("side") == "long"
                ]
                short_clusters = [
                    c for c in coin_clusters if c.get("side") == "short"
                ]

            if long_clusters:
                output.append("  Long Liquidations (below price):\n", style="#3fb950")
                self._render_cluster_list(output, long_clusters, "#3fb950")

            if short_clusters:
                output.append("  Short Liquidations (above price):\n", style="#f85149")
                self._render_cluster_list(output, short_clusters, "#f85149")

            if not long_clusters and not short_clusters:
                output.append("  No clusters detected\n", style="dim")

        self.update(output)

    def _render_cluster_list(self, output: Text, clusters: list, color: str):
        """Render a list of clusters with density bars."""
        # Find the max notional for bar scaling
        max_notional = 0.0
        for c in clusters:
            notional = (
                c.total_notional if isinstance(c, LiquidationCluster)
                else c.get("total_notional", 0)
            )
            if notional > max_notional:
                max_notional = notional

        if max_notional == 0:
            max_notional = 1.0

        for c in clusters:
            if isinstance(c, LiquidationCluster):
                center = c.center_price
                width_pct = c.width_pct
                density = c.density
                notional = c.total_notional
            else:
                center = c.get("center_price", 0)
                width_pct = c.get("width_pct", 0.005)
                density = c.get("density", 0)
                notional = c.get("total_notional", 0)

            # Compute price range from center +/- half the width
            half_width = center * width_pct / 2
            lo = center - half_width
            hi = center + half_width

            # Format price range
            if center >= 10_000:
                range_str = f"${lo:,.0f}-${hi:,.0f}"
            elif center >= 100:
                range_str = f"${lo:,.1f}-${hi:,.1f}"
            else:
                range_str = f"${lo:,.2f}-${hi:,.2f}"

            # Build bar
            bar_fill = max(1, int((notional / max_notional) * self.MAX_BAR_WIDTH))
            bar_empty = self.MAX_BAR_WIDTH - bar_fill
            bar_str = "\u2588" * bar_fill + "\u2591" * bar_empty

            # Format notional
            if notional >= 1_000_000:
                notional_str = f"${notional / 1_000_000:.1f}M"
            elif notional >= 1_000:
                notional_str = f"${notional / 1_000:.0f}K"
            else:
                notional_str = f"${notional:.0f}"

            output.append(f"    {range_str:<22}", style="white")
            output.append(f"[{bar_str}]", style=color)
            output.append(f" {density:>3} pos  ", style="dim")
            output.append(f"{notional_str:>7}\n", style="bold " + color)
