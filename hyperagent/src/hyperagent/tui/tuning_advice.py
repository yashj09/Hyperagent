"""
Gate-rejection → parameter tuning advice.

When a strategy has been silent for several ticks, the
TickDiagnostics.gate_rejections dict tells us WHY. This module turns
those gate counts into concrete, actionable suggestions:

    "Lower BREAKOUT_SQUEEZE_BARS from 3 → 2 (fewer bars in Keltner band)"

Design notes:
  - The advice table is curated by hand. Gate codes are finite and stable
    (they're string literals in each strategy's generate_signal), so a
    dict lookup is simpler than any generic rule engine.
  - Suggestions respect the ParamSpec min/max bounds declared in
    tui/param_schema.py. We never recommend a value the edit modal would
    reject.
  - Non-tunable gates (candles_missing, atr_zero, zscore_calc_failed,
    bad_candle_data) return a diagnostic message with no config_key, so
    the UI can display the reason without pointing at a param to edit.
  - probe_alternatives() reads AgentState directly (funding_rates,
    liquidation_stats) instead of running other strategies — we don't
    want to double the work every tick just to ask "would X have fired?".
    The three strategies that consume pre-aggregated data (funding_carry,
    liquidation_cascade_v2, pairs_reversion) can be cheaply probed;
    indicator-based strategies (trend_follower, momentum,
    volatility_breakout) are skipped because their gates need fresh OHLC.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from hyperagent import config
from hyperagent.core.state import AgentState, TickDiagnostics
from hyperagent.tui.param_schema import ParamSpec, get_spec_by_key


# ---------------------------------------------------------------------------
# Advice records
# ---------------------------------------------------------------------------


@dataclass
class Advice:
    """How to respond to one gate-rejection code.

    Either points at a config knob (config_key set) or is purely
    informational (config_key=None, note set).
    """

    config_key: Optional[str]
    # "lower" means "reduce the numeric value to make the gate pass more
    # easily"; "raise" is the opposite. Signalling direction explicitly
    # because some params are thresholds (lower = looser) and some are
    # counts (raise = looser — e.g. LOOKBACK_CANDLES).
    direction: str
    # Multiplier applied to the CURRENT value to produce the suggestion.
    # 0.5 = half, 0.7 = 70%. Chosen so a single click nudges the param
    # without jumping to an extreme. Clamped inside ParamSpec bounds.
    factor: float
    rationale: str
    # If True, skip the config_key lookup — used for informational
    # advice (e.g. "data stale, wait a tick"). note is returned as-is.
    note: Optional[str] = None


@dataclass
class Suggestion:
    """Resolved suggestion — safe to render directly in the UI."""

    config_key: Optional[str]
    label: str                   # e.g. "Squeeze Bars Min"
    current_display: str         # e.g. "3"
    suggested_display: str       # e.g. "2"
    current_value: Any           # raw numeric (for tests + comparisons)
    suggested_value: Any
    rationale: str


# ---------------------------------------------------------------------------
# Curated gate → advice map
# ---------------------------------------------------------------------------
#
# Each strategy's dict maps a stable gate_code string (from its
# generate_signal) to ONE Advice. Multiple gates can point at the same
# param (e.g. score_below_min on a strategy with multiple scoring
# contributors), which the builder de-duplicates.
#
# Why a flat advice-per-gate instead of a list? Because a single gate has
# exactly one most-effective knob. Overloading gates with 3 suggestions
# each produces a noisy UI where users don't know where to start.
#
# _INFO() is a small helper for informational-only rows (no config_key).


def _INFO(note: str) -> Advice:
    return Advice(config_key=None, direction="", factor=0.0, rationale=note, note=note)


_ADVICE: Dict[str, Dict[str, Advice]] = {
    "trend_follower": {
        "regime_not_trending": _INFO(
            "Regime is ranging/squeeze — trend-following has no edge here. "
            "Wait for a trend or switch strategies."
        ),
        "adx_below_threshold": Advice(
            "TREND_ADX_THRESHOLD", "lower", 0.8,
            "Accept weaker trends by reducing the ADX floor."
        ),
        "pullback_too_far": Advice(
            "TREND_PULLBACK_ATR_MULT", "raise", 1.5,
            "Allow entries further from the EMA (wider pullback window)."
        ),
        "funding_against": _INFO(
            "Funding rate contradicts trend direction on evaluated coins."
        ),
        "score_below_min": Advice(
            "TREND_ADX_THRESHOLD", "lower", 0.8,
            "Loosen ADX to raise component scores and clear the 55-pt floor."
        ),
        "candles_missing": _INFO(
            "Price history still warming up — wait ~30s after start."
        ),
        "atr_zero": _INFO("ATR calculation returned zero — price feed issue."),
        "di_ema_disagree": _INFO(
            "DI direction and EMA direction disagree — waiting for alignment."
        ),
    },
    "momentum": {
        "regime_ranging": _INFO(
            "Markets are ranging — momentum has no edge here."
        ),
        "adx_below_gate": Advice(
            "MOMENTUM_ADX_GATE", "lower", 0.7,
            "Accept choppier markets by lowering the ADX gate."
        ),
        "score_below_threshold": Advice(
            "MOMENTUM_VOTE_THRESHOLD", "lower", 0.8,
            "Lower the weighted-score threshold to fire on weaker momentum."
        ),
        "htf_disagree": _INFO(
            "4h timeframe disagrees with 1h direction. Wait for HTF alignment."
        ),
        "candles_missing": _INFO(
            "Price history still warming up — wait ~30s."
        ),
    },
    "funding_carry": {
        "funding_too_low": Advice(
            "FUNDING_THRESHOLD", "lower", 0.5,
            "Accept smaller funding rates (trades become less lucrative)."
        ),
        "trend_against_carry": _INFO(
            "Strong trend would hurt carry PnL — strategy skipping by design."
        ),
    },
    "volatility_breakout": {
        "no_squeeze": Advice(
            "BREAKOUT_SQUEEZE_BARS", "lower", 0.5,
            "Require fewer BB-inside-Keltner bars — easier squeeze detection."
        ),
        "move_below_breakout": Advice(
            "BREAKOUT_ATR_MULT", "lower", 0.7,
            "Require a smaller breakout move relative to ATR."
        ),
        "volume_too_low": Advice(
            "BREAKOUT_VOLUME_MULT", "lower", 0.7,
            "Accept breakouts with less volume confirmation."
        ),
        "score_below_min": Advice(
            "BREAKOUT_ATR_MULT", "lower", 0.7,
            "Loosen the main breakout gate to raise scores above 55."
        ),
        "candles_missing": _INFO(
            "15m candles still warming up — wait ~1 min."
        ),
        "atr_zero": _INFO("ATR calculation returned zero."),
        "bad_candle_data": _INFO("Malformed candle data from API — retrying."),
    },
    "pairs_reversion": {
        "correlation_too_low": Advice(
            "PAIRS_MIN_CORRELATION", "lower", 0.8,
            "Trade pairs with looser correlation (higher divergence risk)."
        ),
        "zscore_below_entry": Advice(
            "PAIRS_ZSCORE_ENTRY", "lower", 0.8,
            "Enter on smaller divergences (lower edge per trade)."
        ),
        "score_below_min": Advice(
            "PAIRS_ZSCORE_ENTRY", "lower", 0.8,
            "Lower z-score entry to push component scores past 55."
        ),
        "zscore_calc_failed": _INFO(
            "Z-score computation failed — likely insufficient candle history."
        ),
    },
    "liquidation_cascade_v2": {
        "below_usd_threshold": Advice(
            "CASCADE_V2_THRESHOLD_DEFAULT_USD", "lower", 0.5,
            "Trigger on smaller liquidation events (more signals, more noise)."
        ),
        "imbalance_too_low": Advice(
            "CASCADE_V2_IMBALANCE_RATIO", "lower", 0.7,
            "Require less one-sided dominance for cascade detection."
        ),
        "acceleration_too_low": Advice(
            "CASCADE_V2_ACCELERATION_THRESHOLD", "lower", 0.7,
            "Accept cascades that are fading (less timely entries)."
        ),
        "no_dominant_side": _INFO(
            "Current liquidation flow is balanced — no cascade direction."
        ),
    },
}


# ---------------------------------------------------------------------------
# Bound-respecting value resolution
# ---------------------------------------------------------------------------


def _apply_factor(
    current: float, direction: str, factor: float, spec: ParamSpec
) -> Optional[float]:
    """Multiply `current` by the advice's factor, respecting direction and
    ParamSpec min/max. Returns None if the suggestion would be a no-op
    (i.e. current is already at the recommended extreme).
    """
    if direction == "lower":
        # Factor < 1 reduces. If factor was authored > 1 by mistake, guard
        # against it so we never suggest "lower X from 3 to 6".
        f = min(factor, 1.0)
        proposed = current * f
        if spec.min is not None and proposed < spec.min:
            proposed = spec.min
        if proposed >= current:
            return None  # already at or below the bound
    else:  # "raise"
        f = max(factor, 1.0)
        proposed = current * f
        if spec.max is not None and proposed > spec.max:
            proposed = spec.max
        if proposed <= current:
            return None  # already at or above the bound

    # Integer-kind specs need whole-number values. Truncate toward the
    # loosening direction so we don't accidentally round up a "lower"
    # suggestion into a no-op (e.g. lower 3 * 0.5 = 1.5, int() = 1, good).
    if spec.kind in ("int", "seconds", "minutes"):
        proposed = int(proposed) if direction == "lower" else int(proposed + 0.999)
        if proposed == current:
            return None

    return proposed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def suggest(
    strategy: str, tick: TickDiagnostics, max_items: int = 3
) -> List[Suggestion]:
    """Produce up to `max_items` ranked suggestions from a tick's gate
    rejections.

    Ranking: by gate count desc (most-rejected gate first), with
    informational rows interleaved but de-prioritized. Suggestions
    pointing at the same config_key are collapsed (keeping the first —
    i.e. the most-rejected gate's advice for that param).

    Returns an empty list when no actionable advice exists (e.g. all
    gates are informational-only like "data warming up").
    """
    advice_table = _ADVICE.get(strategy, {})
    if not advice_table or not tick.gate_rejections:
        return []

    # Sort gates by count desc so the biggest blocker gets suggested first.
    # Skip the ai_latency_ms metric that ai_wrapper stamps on — it's not a
    # gate count (should be a separate field; legacy compat).
    ranked = sorted(
        (
            (gate, count)
            for gate, count in tick.gate_rejections.items()
            if gate != "ai_latency_ms" and count > 0
        ),
        key=lambda kv: -kv[1],
    )

    out: List[Suggestion] = []
    seen_keys: set = set()
    seen_info: set = set()

    for gate, _count in ranked:
        if len(out) >= max_items:
            break
        advice = advice_table.get(gate)
        if advice is None:
            continue

        if advice.config_key is None:
            # Informational row — no param change, just a note.
            # Deduplicate on rationale text so two gates pointing at the
            # same note don't duplicate the line.
            note = advice.note or advice.rationale
            if note in seen_info:
                continue
            seen_info.add(note)
            out.append(Suggestion(
                config_key=None,
                label="(info)",
                current_display="",
                suggested_display="",
                current_value=None,
                suggested_value=None,
                rationale=note,
            ))
            continue

        # Param-pointing advice — look up the spec and the current value.
        if advice.config_key in seen_keys:
            continue
        spec = get_spec_by_key(strategy, advice.config_key)
        if spec is None:
            # Advice references a key not in the schema (schema drift).
            # Skip silently — don't break the UI.
            continue
        current = getattr(config, advice.config_key, None)
        if current is None:
            continue

        proposed = _apply_factor(float(current), advice.direction, advice.factor, spec)
        if proposed is None:
            continue

        seen_keys.add(advice.config_key)
        out.append(Suggestion(
            config_key=advice.config_key,
            label=spec.label,
            current_display=spec.format(current),
            suggested_display=spec.format(proposed),
            current_value=current,
            suggested_value=proposed,
            rationale=advice.rationale,
        ))

    return out


def probe_alternatives(current: str, state: AgentState) -> List[str]:
    """Return up to 2 "try this other strategy" hints based on data
    already available in AgentState. Cheap: no strategy logic runs,
    only dict inspection.

    Skipped entirely when the probe would be too expensive or the
    target strategy has the same data-dependency as `current`.
    """
    hints: List[str] = []

    # funding_carry probe — easy: any funding rate above threshold?
    if current != "funding_carry" and state.funding_rates:
        best_coin = None
        best_rate = 0.0
        for coin, rate in state.funding_rates.items():
            if abs(rate) > abs(best_rate):
                best_rate = rate
                best_coin = coin
        if best_coin and abs(best_rate) >= config.FUNDING_THRESHOLD:
            hints.append(
                f"funding_carry would signal now on {best_coin} "
                f"(funding {best_rate:+.4%})"
            )

    # liquidation_cascade_v2 probe — any coin passing all 3 gates?
    if current != "liquidation_cascade_v2" and state.liquidation_stats:
        for coin, stats in state.liquidation_stats.items():
            # stats is a CoinLiquidationStats; import lazily to avoid cycle.
            if not hasattr(stats, "dominant_side"):
                continue
            if stats.dominant_side is None:
                continue
            dom_usd = (
                stats.hour_long_usd
                if stats.dominant_side == "Long"
                else stats.hour_short_usd
            )
            if (
                dom_usd >= stats.threshold_usd()
                and stats.imbalance_ratio >= config.CASCADE_V2_IMBALANCE_RATIO
                and stats.acceleration >= config.CASCADE_V2_ACCELERATION_THRESHOLD
            ):
                hints.append(
                    f"liquidation_cascade_v2 would signal now on {coin} "
                    f"({stats.dominant_side} dominant ${dom_usd/1e6:.1f}M)"
                )
                break  # one cascade hint is enough

    return hints[:2]
