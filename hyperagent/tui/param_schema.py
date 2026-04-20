"""
Schema for editable strategy parameters.

Each ParamSpec describes ONE knob on a strategy:
  - config_key: attribute name on the `config` module (mutated at runtime)
  - label:      how it appears in the DataTable
  - kind:       shapes the input widget + parser + formatter
  - min/max:    inclusive range for validation; None = unbounded
  - help:       one-line hint shown in the edit modal

The same schema drives:
  - the DataTable rows shown on the Strategy tab
  - the modal's input validation
  - the formatter for "current value" display
  - the parser that converts user input back to the canonical type

This avoids the old situation where we had TWO representations of a
parameter (a display string in strategy_config.py and a raw constant
in config.py) with no round-trip between them.

Kinds and their semantics:
  int              plain int
  float            plain float
  pct              float stored as decimal (0.025), displayed/edited as "2.5%"
  usd              float dollar amount, displayed as "$200"
  usd_millions     float dollar amount, displayed as "$5.0M", editable as 5 or 5M
  seconds          int seconds, displayed as "30s"
  minutes          int minutes, displayed as "60 min"
  ratio            float multiplier, displayed as "3.0x"

Why not reach for Pydantic? Pydantic is overkill for a 60-row config UI.
Dataclasses + a handful of branch-per-kind formatters keeps this readable
and lets the formatter string be customized per-row when we need to.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Core spec
# ---------------------------------------------------------------------------


@dataclass
class ParamSpec:
    config_key: str
    label: str
    kind: str  # see module docstring for allowed values
    min: Optional[float] = None
    max: Optional[float] = None
    help: str = ""

    def format(self, value: Any) -> str:
        """Render a canonical value for display in the DataTable."""
        return _format(self.kind, value)

    def parse(self, text: str) -> Any:
        """Parse user input back to canonical type. Raises ValueError on bad input."""
        return _parse(self.kind, text)

    def validate(self, value: Any) -> Optional[str]:
        """Return error string if value is out of range, else None."""
        if self.min is not None and value < self.min:
            return f"Must be >= {_format(self.kind, self.min)}"
        if self.max is not None and value > self.max:
            return f"Must be <= {_format(self.kind, self.max)}"
        return None


# ---------------------------------------------------------------------------
# Formatters (canonical value -> display string)
# ---------------------------------------------------------------------------


def _format(kind: str, v: Any) -> str:
    if v is None:
        return "-"
    if kind == "int":
        return f"{int(v)}"
    if kind == "float":
        return f"{float(v):g}"
    if kind == "pct":
        return f"{float(v) * 100:.3g}%"
    if kind == "usd":
        return f"${float(v):,.0f}"
    if kind == "usd_millions":
        amt = float(v)
        if amt >= 1_000_000:
            return f"${amt / 1_000_000:.2f}M"
        if amt >= 1_000:
            return f"${amt / 1_000:.1f}K"
        return f"${amt:,.0f}"
    if kind == "seconds":
        return f"{int(v)}s"
    if kind == "minutes":
        return f"{int(v)} min"
    if kind == "ratio":
        return f"{float(v):g}x"
    return str(v)


# ---------------------------------------------------------------------------
# Parsers (user text -> canonical value)
# ---------------------------------------------------------------------------


def _safe_float(s: str) -> float:
    """float() that normalizes OverflowError + NaN + inf to ValueError.

    Python's int(float("1e999")) raises OverflowError which isn't caught by
    the modal's generic (ValueError, TypeError) handler. Also, float("nan")
    and float("inf") succeed silently and would poison config. Catch both.
    """
    try:
        v = float(s)
    except (ValueError, TypeError) as e:
        raise ValueError(str(e))
    if math.isnan(v):
        raise ValueError("NaN is not allowed")
    if math.isinf(v):
        raise ValueError("value is too large")
    return v


def _safe_int(s: str) -> int:
    """int(float(s)) with overflow/NaN/inf guards."""
    v = _safe_float(s)
    # Python int() truncates toward zero; we want to reject non-whole values
    # explicitly so users don't silently lose precision when typing "3.7" for
    # an int field.
    if v != int(v):
        raise ValueError(f"expected whole number, got {v}")
    return int(v)


def _parse(kind: str, text: str) -> Any:
    """Parse `text` back to the kind's canonical Python type.

    Accepts values with or without units suffix, so users can type
    "3" or "3.0x" for a ratio, "5%" or "0.05" for pct, "5M" or
    "5000000" for usd_millions.

    Always raises ValueError on bad input — never TypeError, OverflowError,
    or lets NaN/inf through. The modal's validator relies on this guarantee.
    """
    if text is None:
        raise ValueError("empty input")
    s = text.strip().replace(",", "").replace("$", "").replace(" ", "")
    if not s:
        raise ValueError("empty input")

    if kind == "int":
        return _safe_int(s)
    if kind == "float":
        return _safe_float(s)
    if kind == "pct":
        # Accept "2.5%" or "0.025" — if user includes %, divide.
        if s.endswith("%"):
            return _safe_float(s[:-1]) / 100.0
        # Heuristic: bare number >= 1 is probably "2.5" meaning 2.5%;
        # smaller numbers treated as already-decimal.
        val = _safe_float(s)
        return val / 100.0 if val >= 1.0 else val
    if kind == "usd":
        return _safe_float(s)
    if kind == "usd_millions":
        if s.lower().endswith("m"):
            return _safe_float(s[:-1]) * 1_000_000
        if s.lower().endswith("k"):
            return _safe_float(s[:-1]) * 1_000
        return _safe_float(s)
    if kind == "seconds":
        if s.lower().endswith("s"):
            s = s[:-1]
        return _safe_int(s)
    if kind == "minutes":
        # Accept "60", "60m", "60min"
        low = s.lower()
        if low.endswith("min"):
            s = s[:-3]
        elif low.endswith("m"):
            s = s[:-1]
        return _safe_int(s)
    if kind == "ratio":
        if s.lower().endswith("x"):
            s = s[:-1]
        return _safe_float(s)
    return s


# ---------------------------------------------------------------------------
# Per-strategy parameter specs. Keys match strategy_config.STRATEGY_PARAMS
# ---------------------------------------------------------------------------


TREND_FOLLOWER_SPECS: List[ParamSpec] = [
    ParamSpec("TREND_ADX_PERIOD", "ADX Period", "int", min=5, max=50,
              help="ADX lookback; 14 is the industry default."),
    ParamSpec("TREND_ADX_THRESHOLD", "ADX Threshold", "int", min=15, max=50,
              help="Min ADX to confirm a trend exists. 25 is standard."),
    ParamSpec("TREND_ADX_EXIT", "ADX Exit", "int", min=10, max=40,
              help="Exit trend trades when ADX drops below this."),
    ParamSpec("TREND_EMA_FAST", "EMA Fast", "int", min=5, max=100,
              help="Fast EMA period."),
    ParamSpec("TREND_EMA_SLOW", "EMA Slow", "int", min=10, max=200,
              help="Slow EMA period. Must be > EMA Fast."),
    ParamSpec("TREND_PULLBACK_ATR_MULT", "Pullback (ATR)", "ratio", min=0.1, max=3.0,
              help="Max pullback distance from EMA in ATR units. Tighter = stricter entry."),
    ParamSpec("TREND_STOP_ATR_MULT", "Stop (ATR)", "ratio", min=0.5, max=10.0,
              help="Stop loss distance in ATR units. 3x is CTA standard."),
    ParamSpec("TREND_TP_ATR_MULT", "TP (ATR)", "ratio", min=1.0, max=20.0,
              help="Take profit distance in ATR units. Should exceed stop for positive R:R."),
    ParamSpec("TREND_TRAIL_ATR_MULT", "Trail (ATR)", "ratio", min=0.5, max=10.0,
              help="Trailing stop distance in ATR units."),
]

MOMENTUM_SPECS: List[ParamSpec] = [
    ParamSpec("MOMENTUM_RSI_PERIOD", "RSI Period", "int", min=2, max=50),
    ParamSpec("MOMENTUM_RSI_BULL", "RSI Bull Level", "int", min=50, max=90,
              help="RSI above this = bullish momentum."),
    ParamSpec("MOMENTUM_RSI_BEAR", "RSI Bear Level", "int", min=10, max=50,
              help="RSI below this = bearish momentum."),
    ParamSpec("MOMENTUM_MACD_FAST", "MACD Fast", "int", min=5, max=30),
    ParamSpec("MOMENTUM_MACD_SLOW", "MACD Slow", "int", min=10, max=60),
    ParamSpec("MOMENTUM_MACD_SIGNAL", "MACD Signal", "int", min=3, max=20),
    ParamSpec("MOMENTUM_EMA_FAST", "EMA Fast", "int", min=3, max=50),
    ParamSpec("MOMENTUM_EMA_SLOW", "EMA Slow", "int", min=10, max=100),
    ParamSpec("MOMENTUM_BB_PERIOD", "BB Period", "int", min=5, max=50),
    ParamSpec("MOMENTUM_BB_STD", "BB Std Dev", "float", min=1.0, max=4.0),
    ParamSpec("MOMENTUM_ADX_GATE", "ADX Gate", "int", min=0, max=50,
              help="Min ADX to allow signals (filters choppy markets). 0 disables."),
    ParamSpec("MOMENTUM_VOTE_THRESHOLD", "Score Threshold", "int", min=30, max=100,
              help="Weighted score (0-100) needed to trigger. 60 is default."),
]

FUNDING_SPECS: List[ParamSpec] = [
    ParamSpec("FUNDING_THRESHOLD", "Funding Threshold", "pct", min=0.00001, max=0.01,
              help="Min funding rate per interval (hourly on HL). 0.03% = ~33% APR."),
    ParamSpec("FUNDING_HIGH_THRESHOLD", "High Threshold", "pct", min=0.00001, max=0.02,
              help="Funding rate for HIGH confidence signals."),
    ParamSpec("FUNDING_SETTLEMENT_WINDOW", "Settlement Window", "seconds", min=60, max=7200,
              help="Only enter within N seconds before the next hourly funding settlement."),
    ParamSpec("FUNDING_PERSISTENCE_PERIODS", "Persistence Periods", "int", min=1, max=10,
              help="Funding must stay elevated for N consecutive periods."),
    ParamSpec("FUNDING_NORMALIZATION_EXIT", "Normalization Exit", "pct", min=0.0, max=0.001,
              help="Close position when |funding| drops below this."),
    ParamSpec("FUNDING_POSITION_SIZE", "Position Size", "usd", min=25, max=1000,
              help="Base USD size (dynamic sizing still applies)."),
]

BREAKOUT_SPECS: List[ParamSpec] = [
    ParamSpec("BREAKOUT_ATR_MULT", "ATR Multiplier", "ratio", min=0.5, max=5.0,
              help="Breakout threshold = N * ATR(20) on 15m candles."),
    ParamSpec("BREAKOUT_SQUEEZE_BARS", "Squeeze Bars Min", "int", min=1, max=20,
              help="BB must be inside Keltner for at least this many bars."),
    ParamSpec("BREAKOUT_VOLUME_MULT", "Volume Multiplier", "ratio", min=1.0, max=5.0,
              help="Breakout candle volume must exceed N * 20-period average."),
    ParamSpec("BREAKOUT_LOOKBACK_CANDLES", "Lookback Candles", "int", min=10, max=200),
]

PAIRS_SPECS: List[ParamSpec] = [
    ParamSpec("PAIRS_ZSCORE_ENTRY", "Z-Score Entry", "float", min=1.0, max=5.0,
              help="Open pair when |z-score| exceeds this. 2σ is standard."),
    ParamSpec("PAIRS_ZSCORE_EXIT", "Z-Score Exit", "float", min=-2.0, max=2.0,
              help="Close pair when z-score crosses this (mean reversion)."),
    ParamSpec("PAIRS_ZSCORE_STOP", "Z-Score Stop", "float", min=2.0, max=6.0,
              help="Stop out when spread blows out past this z-score."),
    ParamSpec("PAIRS_LOOKBACK_HOURS", "Lookback Hours", "int", min=12, max=240),
    ParamSpec("PAIRS_CANDLE_COUNT", "Candle Count", "int", min=24, max=500),
    ParamSpec("PAIRS_MIN_CORRELATION", "Min Correlation", "float", min=0.0, max=1.0,
              help="Reject pair if rolling correlation drops below this."),
    ParamSpec("PAIRS_POSITION_SIZE_PER_LEG", "Size Per Leg", "usd", min=25, max=500),
]

CASCADE_V2_SPECS: List[ParamSpec] = [
    ParamSpec("CASCADE_V2_WINDOW_MINUTES", "Window", "minutes", min=15, max=240,
              help="Rolling window for liquidation aggregation."),
    ParamSpec("CASCADE_V2_MIN_EVENT_USD", "Min Event Size", "usd", min=1_000, max=1_000_000,
              help="Ignore liquidation events smaller than this."),
    ParamSpec("CASCADE_V2_THRESHOLD_BTC_USD", "BTC Threshold", "usd_millions", min=500_000, max=50_000_000,
              help="USD liquidated in one direction to trigger BTC cascade."),
    ParamSpec("CASCADE_V2_THRESHOLD_ETH_USD", "ETH Threshold", "usd_millions", min=250_000, max=20_000_000),
    ParamSpec("CASCADE_V2_THRESHOLD_DEFAULT_USD", "Default Threshold", "usd_millions", min=100_000, max=10_000_000,
              help="Threshold for DOGE, SOL, XRP, SUI, AVAX, LINK."),
    ParamSpec("CASCADE_V2_IMBALANCE_RATIO", "Imbalance Ratio", "ratio", min=1.5, max=10.0,
              help="Dominant side must exceed subdominant by this factor."),
    ParamSpec("CASCADE_V2_ACCELERATION_THRESHOLD", "Acceleration", "ratio", min=0.5, max=5.0,
              help="Last 15min vs hourly-average ratio confirming cascade is ongoing."),
    ParamSpec("HYPEDEXER_POLL_INTERVAL", "Poll Interval", "seconds", min=10, max=300,
              help="How often to fetch fresh liquidation data from HypeDexer."),
]

# Shared risk / infra parameters — appended to every strategy's view
# so users don't have to hunt for them.
RISK_SPECS: List[ParamSpec] = [
    ParamSpec("POSITION_SIZE_USD", "Base Position Size", "usd", min=25, max=500,
              help="Base size before vol/conviction scalars."),
    ParamSpec("MIN_POSITION_SIZE_USD", "Min Position Size", "usd", min=10, max=200,
              help="Skip-trade floor — sizes below this return 0."),
    ParamSpec("MAX_POSITION_SIZE_USD", "Max Position Size", "usd", min=50, max=2000),
    ParamSpec("MAX_LEVERAGE", "Max Leverage", "int", min=1, max=20),
    ParamSpec("MAX_CONCURRENT_POSITIONS", "Max Concurrent", "int", min=1, max=20),
    ParamSpec("MAX_DAILY_LOSS_USD", "Max Daily Loss (abs)", "usd", min=10, max=10000),
    ParamSpec("MAX_DAILY_LOSS_PCT", "Max Daily Loss (%)", "pct", min=0.005, max=0.25),
    ParamSpec("MAX_TOTAL_EXPOSURE_MULT", "Max Exposure Mult", "ratio", min=1.0, max=10.0),
    ParamSpec("MAX_NET_DIRECTIONAL_POSITIONS", "Max Net Directional", "int", min=1, max=10),
    ParamSpec("TRADE_COOLDOWN_DEFAULT", "Trade Cooldown", "seconds", min=0, max=3600),
    ParamSpec("NATIVE_STOP_WIDEN_MULT", "Native Stop Widen", "ratio", min=1.0, max=3.0),
]


STRATEGY_SPECS: Dict[str, List[ParamSpec]] = {
    "trend_follower": TREND_FOLLOWER_SPECS,
    "momentum": MOMENTUM_SPECS,
    "funding_carry": FUNDING_SPECS,
    "volatility_breakout": BREAKOUT_SPECS,
    "pairs_reversion": PAIRS_SPECS,
    "liquidation_cascade_v2": CASCADE_V2_SPECS,
}


def get_specs_for(strategy: str) -> List[ParamSpec]:
    """Return strategy-specific specs followed by shared risk specs."""
    strategy_specs = STRATEGY_SPECS.get(strategy, [])
    return list(strategy_specs) + RISK_SPECS


def get_spec_by_key(strategy: str, config_key: str) -> Optional[ParamSpec]:
    """Look up a spec by its config attribute name."""
    for spec in get_specs_for(strategy):
        if spec.config_key == config_key:
            return spec
    return None


# ---------------------------------------------------------------------------
# Cross-field invariants
# ---------------------------------------------------------------------------
#
# Per-row min/max catches "30 is too high for ADX_THRESHOLD" but not
# "EMA_FAST > EMA_SLOW" — a combination that's individually legal but
# semantically broken. These checks run at edit-apply time with a tentative
# config dict (the would-be-mutated state) and return an error message
# if any invariant is violated.
#
# Each check takes (cfg, proposed_key, proposed_value) where cfg is a
# read-only view of the CURRENT config, and proposed_* describes the
# single field the user just edited. The function resolves the effective
# value (proposed or current) for any key it cares about.


def _resolve(cfg: Any, proposed_key: str, proposed_value: Any, key: str) -> Any:
    """Return the effective value for `key` given a tentative edit."""
    if key == proposed_key:
        return proposed_value
    return getattr(cfg, key, None)


def check_invariants(
    cfg: Any,
    strategy: str,
    proposed_key: str,
    proposed_value: Any,
) -> Optional[str]:
    """Check strategy-specific invariants. Returns error message or None.

    Called ONCE per edit (not on every keystroke) because these checks
    involve reading multiple config values.
    """

    # Helper to read the effective value for another field
    def eff(k: str) -> Any:
        return _resolve(cfg, proposed_key, proposed_value, k)

    # ---- Momentum strategy ----
    if strategy == "momentum":
        if eff("MOMENTUM_EMA_FAST") >= eff("MOMENTUM_EMA_SLOW"):
            return "EMA Fast must be < EMA Slow"
        if eff("MOMENTUM_MACD_FAST") >= eff("MOMENTUM_MACD_SLOW"):
            return "MACD Fast must be < MACD Slow"
        if eff("MOMENTUM_RSI_BEAR") >= eff("MOMENTUM_RSI_BULL"):
            return "RSI Bear Level must be < RSI Bull Level"

    # ---- Trend follower ----
    if strategy == "trend_follower":
        if eff("TREND_EMA_FAST") >= eff("TREND_EMA_SLOW"):
            return "EMA Fast must be < EMA Slow"
        if eff("TREND_ADX_EXIT") >= eff("TREND_ADX_THRESHOLD"):
            return "ADX Exit must be < ADX Threshold (else you'd exit as soon as you enter)"
        # TP should exceed stop — otherwise R:R is negative
        if eff("TREND_TP_ATR_MULT") <= eff("TREND_STOP_ATR_MULT"):
            return "TP (ATR mult) must be > Stop (ATR mult) for positive R:R"

    # ---- Funding carry ----
    if strategy == "funding_carry":
        if eff("FUNDING_HIGH_THRESHOLD") <= eff("FUNDING_THRESHOLD"):
            return "High Threshold must be > Funding Threshold"
        if eff("FUNDING_NORMALIZATION_EXIT") >= eff("FUNDING_THRESHOLD"):
            return "Normalization Exit must be < Funding Threshold (else you'd exit immediately)"

    # ---- Pairs reversion ----
    if strategy == "pairs_reversion":
        if eff("PAIRS_ZSCORE_STOP") <= eff("PAIRS_ZSCORE_ENTRY"):
            return "Z-Score Stop must be > Z-Score Entry"
        # Exit z can be negative or 0, but must be below entry in absolute terms
        if abs(eff("PAIRS_ZSCORE_EXIT")) >= eff("PAIRS_ZSCORE_ENTRY"):
            return "|Z-Score Exit| must be < Z-Score Entry"

    # ---- Liquidation cascade v2 ----
    if strategy == "liquidation_cascade_v2":
        btc = eff("CASCADE_V2_THRESHOLD_BTC_USD")
        eth = eff("CASCADE_V2_THRESHOLD_ETH_USD")
        default = eff("CASCADE_V2_THRESHOLD_DEFAULT_USD")
        # BTC is the biggest, ETH mid, alts smallest — enforce ordering so
        # nobody accidentally makes BTC more sensitive than DOGE
        if not (btc >= eth >= default):
            return (
                "Thresholds must be ordered: BTC >= ETH >= Default "
                f"(got BTC={btc:.0f}, ETH={eth:.0f}, Default={default:.0f})"
            )

    # ---- Shared risk invariants ----
    # These apply regardless of active strategy.
    if eff("MIN_POSITION_SIZE_USD") > eff("POSITION_SIZE_USD"):
        return "Min Position Size cannot exceed Base Position Size"
    if eff("POSITION_SIZE_USD") > eff("MAX_POSITION_SIZE_USD"):
        return "Base Position Size cannot exceed Max Position Size"
    if eff("MIN_POSITION_SIZE_USD") > eff("MAX_POSITION_SIZE_USD"):
        return "Min Position Size cannot exceed Max Position Size"

    return None
