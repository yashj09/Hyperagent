"""
Cascade score gauge widget.

Displays the cascade score as a colored progress bar with severity label.
Thresholds:
  - < 40   : dim gray (LOW)
  - 40-59  : yellow (MODERATE)
  - 60-79  : orange with WATCH label
  - >= 80  : red bold with DANGER label
"""

from textual.widgets import Static
from rich.text import Text

from hyperagent.core.state import AgentState
import hyperagent.config


class CascadeGauge(Static):
    """Displays cascade scores for monitored assets with colored bars."""

    BAR_WIDTH = 20

    def __init__(self, **kwargs):
        super().__init__("CASCADE ALERTS\n" + "=" * 40 + "\n\n  Monitoring...", id="alerts-panel", **kwargs)

    def update_scores(self, state: AgentState):
        """Rebuild the gauge display from current cascade scores."""
        output = Text()
        output.append("CASCADE ALERTS\n", style="bold cyan")
        output.append("=" * 50 + "\n", style="dim")

        scores = state.cascade_scores
        if not scores:
            output.append(
                "\n  No cascade data yet. Waiting for scanner...\n",
                style="dim italic",
            )
            self.update(output)
            return

        for coin in config.MONITORED_ASSETS:
            score = scores.get(coin)
            if score is None:
                continue

            score = min(100, max(0, score))

            # Determine color and label
            if score >= 80:
                color = "#f85149"
                label = "DANGER"
                style = "bold #f85149"
            elif score >= 60:
                color = "#d29922"
                label = "WATCH"
                style = "bold #d29922"
            elif score >= 40:
                color = "#e3b341"
                label = "MODERATE"
                style = "#e3b341"
            else:
                color = "#484f58"
                label = "LOW"
                style = "dim"

            # Build the bar
            filled = max(0, int((score / 100) * self.BAR_WIDTH))
            empty = self.BAR_WIDTH - filled
            bar_str = "\u2588" * filled + "\u2591" * empty

            output.append(f"\n  {coin:<5} ", style="bold white")
            output.append(f"[{bar_str}]", style=color)
            output.append(f" {score:>3.0f}/100 ", style=style)
            output.append(f"{label}", style=style)

        # Show active signals
        if state.active_signals:
            output.append("\n\n")
            output.append("ACTIVE SIGNALS\n", style="bold #d29922")
            output.append("-" * 50 + "\n", style="dim")
            for sig in state.active_signals[-5:]:  # last 5 signals
                direction_style = (
                    "bold #3fb950" if sig.direction == "LONG"
                    else "bold #f85149"
                )
                output.append(f"  {sig.coin} ", style="bold white")
                output.append(f"{sig.direction} ", style=direction_style)
                output.append(f"({sig.strategy}) ", style="dim")
                output.append(f"score={sig.score:.0f} ", style=style)
                output.append(f"{sig.confidence}\n", style=style)

        self.update(output)
