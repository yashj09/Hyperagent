"""
Liquidation stats panel — reads HypeDexer-aggregated v2 liquidation data.

Displays per-coin: dominant side, hour-window USD, imbalance ratio,
acceleration, and a CASCADE badge when all three Liquidation Cascade v2
entry gates pass (threshold + imbalance + acceleration).

Read-only — the panel never fetches anything. `run_liquidation_poller`
in app.py populates state.liquidation_stats every HYPEDEXER_POLL_INTERVAL
seconds.
"""

import time

from textual.widgets import Static
from rich.text import Text

from hyperagent import config
from hyperagent.core.state import AgentState
from hyperagent.core.liquidation_aggregator import CoinLiquidationStats


class LiquidationStatsPanel(Static):
    """Per-coin rolling liquidation stats from the HypeDexer firehose."""

    def __init__(self, **kwargs):
        super().__init__(
            "LIQUIDATION STATS (v2)\n" + "=" * 45 + "\n\n  Warming up...",
            id="liquidation-stats-panel",
            **kwargs,
        )

    def update_stats(self, state: AgentState):
        output = Text()
        output.append("LIQUIDATION STATS (v2)\n", style="bold cyan")
        output.append("=" * 60 + "\n", style="dim")

        if not config.HYPEDEXER_API_KEY:
            output.append(
                "\n  HYPEDEXER_API_KEY not set — cascade v2 disabled.\n",
                style="dim italic",
            )
            output.append(
                "  Run `hyperagent setup` to add the key.\n",
                style="dim",
            )
            self.update(output)
            return

        stats = state.liquidation_stats
        if not stats:
            output.append(
                "\n  No liquidation data yet. HypeDexer poller warming up...\n",
                style="dim italic",
            )
            self.update(output)
            return

        # Staleness indicator — the v2 strategy itself bails after 120s, so
        # the panel warns a bit earlier to set user expectation.
        age_s = time.time() - state.liquidation_stats_updated
        if age_s > 90:
            output.append(
                f"\n  [warn] stats {age_s:.0f}s old — poller may be stuck\n",
                style="#d29922",
            )

        output.append(
            f"\n  {'Coin':<6}{'Side':<7}{'Hour $':<11}"
            f"{'Imb':<8}{'Accel':<8}{'Status'}\n",
            style="dim bold",
        )

        for coin in config.MONITORED_ASSETS:
            s = stats.get(coin)
            if not isinstance(s, CoinLiquidationStats):
                continue

            side = s.dominant_side or "—"
            side_style = (
                "#3fb950" if side == "Short"
                else "#f85149" if side == "Long"
                else "dim"
            )

            dominant_usd = (
                s.hour_long_usd if side == "Long"
                else s.hour_short_usd if side == "Short"
                else 0.0
            )
            if dominant_usd >= 1_000_000:
                usd_str = f"${dominant_usd / 1_000_000:.1f}M"
            elif dominant_usd >= 1_000:
                usd_str = f"${dominant_usd / 1_000:.0f}K"
            else:
                usd_str = "—"

            imb_str = f"{s.imbalance_ratio:.1f}x" if s.imbalance_ratio > 0 else "—"
            imb_style = (
                "bold #d29922"
                if s.imbalance_ratio >= config.CASCADE_V2_IMBALANCE_RATIO
                else "white"
            )

            accel_str = f"{s.acceleration:.1f}x" if s.acceleration > 0 else "—"
            accel_style = (
                "bold #d29922"
                if s.acceleration >= config.CASCADE_V2_ACCELERATION_THRESHOLD
                else "white"
            )

            # All three v2 gates: threshold + imbalance + acceleration.
            # Mirrors liquidation_cascade_v2.py:107-123 exactly.
            cascade_active = (
                s.dominant_side is not None
                and dominant_usd >= s.threshold_usd()
                and s.imbalance_ratio >= config.CASCADE_V2_IMBALANCE_RATIO
                and s.acceleration >= config.CASCADE_V2_ACCELERATION_THRESHOLD
            )
            status_str = "CASCADE" if cascade_active else ""
            status_style = "bold #f85149" if cascade_active else "dim"

            output.append(f"  {coin:<6}", style="bold white")
            output.append(f"{side:<7}", style=side_style)
            output.append(f"{usd_str:<11}", style="white")
            output.append(f"{imb_str:<8}", style=imb_style)
            output.append(f"{accel_str:<8}", style=accel_style)
            output.append(f"{status_str}\n", style=status_style)

        summary = state.liquidation_24h_summary
        if summary:
            output.append("\n  24h summary: ", style="dim")
            total = summary.get("total_usd") or summary.get("total")
            if total:
                output.append(f"${float(total) / 1_000_000:.1f}M liquidated\n", style="white")

        self.update(output)
