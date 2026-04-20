import os
from dotenv import load_dotenv

load_dotenv()

MAINNET_API_URL = "https://api.hyperliquid.xyz"
TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"

TESTNET_PRIVATE_KEY = os.getenv("TESTNET_PRIVATE_KEY", "")

MONITORED_ASSETS = ["BTC", "ETH", "SOL", "DOGE", "XRP", "SUI", "AVAX", "LINK"]

SCAN_INTERVAL_SECONDS = 30
MAX_ADDRESSES_PER_SCAN = 200
SCANNER_WORKERS = 10

# Legacy cascade (kept for scanner compatibility)
CASCADE_PROXIMITY_PCT = 0.02
CASCADE_DENSITY_THRESHOLD = 1
CASCADE_CLUSTER_WIDTH_PCT = 0.005
CASCADE_SIGNAL_THRESHOLD = 55
CASCADE_HIGH_CONFIDENCE = 75

# --- Trend Follower (replaces Cascade as primary strategy) ---
# ATR multipliers widened per code review: CTA convention is 4x ATR.
# 2x was causing whipsaws on pullback entries (you enter close to EMA,
# a normal retracement takes you out before the trend resumes).
TREND_ADX_PERIOD = 14
TREND_ADX_THRESHOLD = 25
TREND_ADX_EXIT = 20
TREND_EMA_FAST = 21
TREND_EMA_SLOW = 55
TREND_CANDLE_INTERVAL = "4h"
TREND_CANDLE_COUNT = 100
TREND_PULLBACK_ATR_MULT = 0.5
TREND_STOP_ATR_MULT = 3.0  # was 2.0 — room to breathe on 4h trend trades
TREND_TP_ATR_MULT = 6.0    # was 4.0 — maintain 2:1 R:R with wider stop
TREND_TRAIL_ATR_MULT = 2.0 # was 1.5 — let winners run

# --- Momentum (enhanced weighted scoring) ---
MOMENTUM_RSI_PERIOD = 8
MOMENTUM_RSI_BULL = 60
MOMENTUM_RSI_BEAR = 40
MOMENTUM_MACD_FAST = 14
MOMENTUM_MACD_SLOW = 23
MOMENTUM_MACD_SIGNAL = 9
MOMENTUM_EMA_FAST = 7
MOMENTUM_EMA_SLOW = 26
MOMENTUM_BB_PERIOD = 20
MOMENTUM_BB_STD = 2
MOMENTUM_VOTE_THRESHOLD = 60
MOMENTUM_ADX_GATE = 20
MOMENTUM_CANDLE_INTERVAL = "1h"
MOMENTUM_CANDLE_COUNT = 100
MOMENTUM_HTF_INTERVAL = "4h"
MOMENTUM_HTF_CANDLE_COUNT = 50

# --- Funding Sniper (research-calibrated thresholds) ---
FUNDING_THRESHOLD = 0.0003
FUNDING_HIGH_THRESHOLD = 0.0008
FUNDING_SETTLEMENT_WINDOW = 1800
FUNDING_PERSISTENCE_PERIODS = 2
FUNDING_NORMALIZATION_EXIT = 0.0001
FUNDING_POSITION_SIZE = 200

# --- Pairs Reversion (cointegration-based) ---
PAIRS_ZSCORE_ENTRY = 2.0
PAIRS_ZSCORE_EXIT = 0.0
PAIRS_ZSCORE_STOP = 3.5
PAIRS_LOOKBACK_HOURS = 48
PAIRS_CANDLE_COUNT = 72
PAIRS_CANDLE_INTERVAL = "1h"
PAIRS_MIN_CORRELATION = 0.70
PAIRS_POSITION_SIZE_PER_LEG = 50

# --- Volatility Breakout (squeeze-based) ---
BREAKOUT_ATR_MULT = 1.5
BREAKOUT_SQUEEZE_BARS = 3
BREAKOUT_VOLUME_MULT = 1.5
BREAKOUT_CANDLE_INTERVAL = "15m"
BREAKOUT_LOOKBACK_CANDLES = 40

# --- Risk (research-calibrated) ---
POSITION_SIZE_USD = 50            # base per-trade size (pre-scalar)
MIN_POSITION_SIZE_USD = 25        # skip-trade floor; don't force a $25 trade
MAX_POSITION_SIZE_USD = 200       # was implicit $500 in risk.py — now aligned with daily loss limit
MAX_LEVERAGE = 5
SLIPPAGE_PCT = 0.01
TRAILING_STOP_PCT = 0.015
INITIAL_STOP_PCT = 0.020
TAKE_PROFIT_PCT = 0.035
NATIVE_STOP_WIDEN_MULT = 1.5      # native HL stops set 50% wider than software stops
                                   # (native are disaster-recovery only; software fires first)
MAX_CONCURRENT_POSITIONS = 5
MAX_DAILY_LOSS_USD = 100          # absolute fallback
MAX_DAILY_LOSS_PCT = 0.05         # 5% of account_value — preferred limit; uses whichever is stricter
MAX_TOTAL_EXPOSURE_MULT = 3.0     # gross notional cap = 3x account_value
MAX_NET_DIRECTIONAL_POSITIONS = 3 # max |longs - shorts| across all open positions
MIN_ORDER_SIZE_BTC = 0.001
TRADE_COOLDOWN_DEFAULT = 300

# Correlation groups — expanded per audit: DOGE + XRP share retail/meme beta;
# "large-cap L1" basket captures BTC/ETH/SOL co-moves.
CORRELATED_GROUPS = [
    ["BTC", "ETH", "SOL"],
    ["AVAX", "SUI", "LINK"],
    ["XRP", "DOGE"],
]

# --- Regime Detection ---
REGIME_UPDATE_INTERVAL = 300
REGIME_ADX_TRENDING = 25
REGIME_ADX_RANGING = 20

STOP_LOSS_POLL_INTERVAL = 3
PRICE_POLL_INTERVAL = 5
STRATEGY_POLL_INTERVAL = 15

# --- HypeDexer (third-party indexed data) ---
HYPEDEXER_API_KEY = os.getenv("HYPEDEXER_API_KEY", "")
HYPEDEXER_BASE_URL = "https://api.hypedexer.com"
HYPEDEXER_POLL_INTERVAL = 30       # poll liquidations every 30 seconds
HYPEDEXER_REQUEST_TIMEOUT = 10     # seconds per HTTP call

# --- Liquidation Cascade v2 (uses HypeDexer data) ---
# Rolling window tracking the last N minutes of liquidation events per coin/direction
CASCADE_V2_WINDOW_MINUTES = 60           # look at last hour of liquidations
CASCADE_V2_MIN_EVENT_USD = 10_000        # ignore sub-$10k "dust" liquidations
CASCADE_V2_FETCH_LIMIT = 500             # max events per poll (HypeDexer max 2000, but this is per poll)

# Thresholds — tuned per asset class because BTC liquidates far more in absolute $
# These are the "X dollars liquidated in one direction in the last hour" triggers
CASCADE_V2_THRESHOLD_BTC_USD = 5_000_000    # $5M BTC liquidated in one direction
CASCADE_V2_THRESHOLD_ETH_USD = 2_000_000    # $2M ETH
CASCADE_V2_THRESHOLD_DEFAULT_USD = 500_000  # $500k for other assets

# Directional imbalance multiplier: one side must dominate the other by this factor
# e.g. 3.0 means longs-liquidated must be 3x shorts-liquidated for a LONG-cascade signal
CASCADE_V2_IMBALANCE_RATIO = 3.0

# Acceleration check: more recent 15-min volume should exceed X% of the full hour's
# average-per-quarter. This confirms cascade is still unfolding (not fading).
CASCADE_V2_ACCELERATION_THRESHOLD = 1.3  # last 15 min > 130% of hourly average

# Exit timing — cascades burn out within 1-3 hours
CASCADE_V2_MAX_HOLD_SECONDS = 3 * 3600   # 3 hours max

AI_ENABLED_DEFAULT = False
AI_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
AI_MAX_TOKENS = 200
AWS_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

DASHBOARD_REFRESH_RATE = 1
LOG_MAX_LINES = 100
