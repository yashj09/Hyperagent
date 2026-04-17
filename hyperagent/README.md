# HyperAgent

**Autonomous Trading Terminal for Hyperliquid**

HyperAgent is an interactive terminal-based trading agent that watches the market in real-time, detects trading opportunities using 5 different strategies, executes trades autonomously, manages risk with trailing stop-losses, and explains every decision with AI — all from a single terminal interface.

You pick a strategy. You hit Start. It trades by itself.

---

## What It Does

Every second, HyperAgent:

1. **Watches** — Pulls live prices, funding rates, open interest, and orderbook depth from Hyperliquid mainnet
2. **Scans** — Reads 28+ whale wallets to find where their liquidation prices cluster
3. **Thinks** — Runs the active strategy's logic: scoring cascades, counting indicator votes, measuring orderbook imbalance, or calculating funding carry
4. **Decides** — Only trades when the signal passes confidence thresholds AND risk checks
5. **Executes** — Places market orders on Hyperliquid testnet instantly, sets native TP/SL as safety nets
6. **Protects** — Monitors every position every 2 seconds with a trailing stop-loss that locks in profits
7. **Explains** — When AI is on, Claude Haiku writes why the agent made each trade

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     HYPERAGENT TUI (Textual)                      │
│     [Dashboard]      [Strategy Config]      [Trade Journal]       │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  STRATEGIES                    CORE                                │
│  ┌────────────────────┐       ┌──────────────────────────────┐    │
│  │ Liquidation Cascade │       │ Dual Client                  │    │
│  │ Momentum Flip       │──────>│  Mainnet Info (read-only)    │    │
│  │ Funding Sniper      │       │  Testnet Exchange (trading)  │    │
│  │ Volatility Breakout │       └──────────────────────────────┘    │
│  │ Orderbook Imbalance │       ┌──────────────────────────────┐    │
│  └─────────┬──────────┘       │ Risk Manager                 │    │
│            │                   │  Trailing stop (software)    │    │
│  ┌─────────▼──────────┐       │  Native TP/SL (exchange)     │    │
│  │ AI Wrapper          │       │  Daily loss limits           │    │
│  │ (Claude Haiku via   │       │  Position sizing             │    │
│  │  AWS Bedrock)       │       └──────────────────────────────┘    │
│  └────────────────────┘                                            │
│                                                                    │
│  SCANNER                       TUI                                 │
│  ┌────────────────────┐       ┌──────────────────────────────┐    │
│  │ Liquidation Scanner │       │ Market Ticker                │    │
│  │ 28+ whale addresses │       │ Liquidation Heatmap          │    │
│  │ ThreadPool scanning │       │ Cascade Score Gauge          │    │
│  │ Cluster detection   │       │ AI Reasoning Panel           │    │
│  └────────────────────┘       │ Positions + Trailing Stop    │    │
│                                │ Scrollable Trade Log         │    │
│                                └──────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5 Trading Strategies

| Strategy | What It Does | Speed |
|----------|-------------|-------|
| **Liquidation Cascade** | Scans whale wallets, maps liquidation price clusters, scores cascade probability (proximity + density + momentum + funding), trades the cascade direction | Every 1s |
| **Momentum Flip** | 6-indicator voting (RSI, MACD, EMA, Bollinger Bands, 6h/12h momentum) — 4/6 majority triggers entry | Every 1s |
| **Funding Sniper** | Trades against overcrowded positions to collect funding payments. Near risk-free carry trade | Every 1s |
| **Volatility Breakout** | Catches sudden 5-min candle spikes, rides momentum with tight trailing stop | Every 1s |
| **Orderbook Imbalance** | Reads L2 depth — when one side is 1.3x+ heavier, trades the imbalance direction | Every 1s |

**AI Layer**: Any strategy can be wrapped with Claude Haiku (AWS Bedrock) for natural language trade explanations. Toggle with `a` key.

---

## Quick Start

```bash
cd hyperagent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env — add your Hyperliquid testnet private key

# Run
python3 app.py
```

### Get testnet USDC
1. Go to https://app.hyperliquid-testnet.xyz
2. Connect wallet
3. Get testnet USDC from faucet
4. Export private key → paste in `.env` as `TESTNET_PRIVATE_KEY`

---

## Keybindings

| Key | Action |
|-----|--------|
| `d` | Dashboard |
| `s` | Strategy Config |
| `j` | Trade Journal |
| `a` | Toggle AI |
| `q` | Quit |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TESTNET_PRIVATE_KEY` | Yes | Hyperliquid testnet wallet private key (0x...) |
| `AWS_ACCESS_KEY_ID` | For AI | AWS credentials for Bedrock |
| `AWS_SECRET_ACCESS_KEY` | For AI | AWS secret key |
| `AWS_DEFAULT_REGION` | For AI | AWS region (default: us-east-1) |

---

## Data Flow

```
Price Feed (1s)  ──> Prices, Funding, OI ──> Shared State
Scanner (30s)    ──> Whale positions ──> Liquidation Clusters ──> Shared State
Strategy (1s)    ──> Read State ──> Generate Signal ──> Execute Trade
Risk Monitor (2s)──> Check Trailing Stops ──> Close on Breach
Dashboard (1s)   ──> Read State ──> Render All Panels
```

---

## What Makes It Unique

1. **Liquidation Cascade Predictor** — First-ever strategy that scans whale wallets to predict liquidation cascades before they happen. Uses Hyperliquid's public `liquidationPx` API that nobody else exploits
2. **Strategy platform, not a single bot** — 5 strategies in a dropdown. Adding a new one is one file implementing `generate_signal()`
3. **AI reasoning on any strategy** — Claude Haiku explains every trade in plain English
4. **Dual-network architecture** — Real mainnet data, safe testnet execution
5. **Interactive TUI** — Tabs, dropdowns, toggles, scrollable logs, keyboard shortcuts. Not a script — a terminal app
6. **Production risk management** — Double stop-loss, per-coin dedup, daily loss limits, position sizing

---

## Tech Stack

| Library | Purpose |
|---------|---------|
| Textual 8.2.3 | Interactive TUI framework |
| hyperliquid-python-sdk 0.23.0 | Exchange API |
| ta | Technical analysis indicators (RSI, MACD, EMA, BB) |
| anthropic + boto3 | Claude Haiku via AWS Bedrock |
| numpy | Numerical computation |
| Python 3.9+ | Runtime |

---

## Project Structure

```
hyperagent/
├── app.py                          # Textual App — entry point + background workers
├── config.py                       # All tunable constants
├── core/
│   ├── client.py                   # Dual Hyperliquid client (mainnet read + testnet trade)
│   ├── risk.py                     # Risk manager (trailing stop + native TP/SL + limits)
│   └── state.py                    # Shared AgentState dataclass
├── strategies/
│   ├── base.py                     # Abstract BaseStrategy interface
│   ├── cascade.py                  # Liquidation Cascade Predictor
│   ├── momentum.py                 # 6-signal Momentum Flip
│   ├── funding_sniper.py           # Funding Rate Sniper
│   ├── volatility_breakout.py      # Volatility Breakout
│   ├── orderbook_imbalance.py      # Orderbook Imbalance
│   └── ai_wrapper.py               # Claude Haiku reasoning wrapper
├── scanner/
│   ├── liquidation_scanner.py      # Mainnet whale position scanner
│   └── whale_addresses.py          # 28+ whale addresses
├── tui/
│   ├── styles.tcss                 # Textual CSS (dark trading theme)
│   ├── screens/
│   │   ├── dashboard.py            # Live dashboard (6 panels)
│   │   ├── strategy_config.py      # Strategy selector + AI toggle + params
│   │   └── trade_journal.py        # Trade history table + stats
│   └── widgets/
│       ├── market_ticker.py        # Live price ticker bar
│       ├── heatmap.py              # Liquidation heatmap with density bars
│       ├── cascade_gauge.py        # Cascade score gauge (0-100)
│       └── positions_panel.py      # Active positions with trailing stop viz
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Built for

**Elsa Agentic Hyperthon** — Track 1: Hyperliquid Perp Agent

> *"Build an autonomous perp trading agent on Hyperliquid testnet. Pick your strategy: momentum flip, funding-rate arb, or vault copy-trader. Ship with a working stop-loss that fires on camera."*

We built all of them — and then some.
