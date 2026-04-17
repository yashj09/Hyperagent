# HyperAgent

**Autonomous Trading Terminal for Hyperliquid**

An interactive Textual-based TUI that provides strategy-selectable autonomous trading on Hyperliquid testnet. Features a Liquidation Cascade Predictor as its star strategy, with a 6-signal Momentum Flip as a second option, and an optional AI reasoning layer powered by Claude Haiku via AWS Bedrock.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    HYPERAGENT TUI (Textual)                  │
│  [Dashboard]  [Strategy Config]  [Trade Journal]            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────────┐  │
│  │ Liquidation  │   │   Cascade    │   │  Testnet Trader  │  │
│  │ Scanner      │──>│  Detector    │──>│  (IOC + TP/SL)   │  │
│  │ (mainnet)    │   │  (scoring)   │   │  (testnet)       │  │
│  └─────────────┘   └──────────────┘   └─────────────────┘  │
│                                              │               │
│  ┌─────────────┐   ┌──────────────┐   ┌─────┴─────────┐    │
│  │  Momentum   │   │  AI Wrapper  │   │ Risk Manager  │    │
│  │  6-Signal   │   │  (Bedrock    │   │ (trailing SL  │    │
│  │  Voting     │   │   Haiku)     │   │  + native)    │    │
│  └─────────────┘   └──────────────┘   └───────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Strategies

### Liquidation Cascade Predictor (Star Strategy)
Scans 28+ whale wallets on Hyperliquid mainnet, maps their liquidation prices into clusters, and scores cascade probability using:
- **40% Proximity** — how close price is to a liquidation cluster
- **30% Density** — cluster notional vs total open interest
- **20% Momentum** — is price trending toward the cluster
- **10% Funding** — does funding rate confirm overcrowding

When score exceeds threshold: trades in the cascade direction (long cascade → SHORT, short cascade → LONG).

### Momentum Flip (6-Signal Voting)
Inspired by Blazefit's 20.6 Sharpe strategy:
- RSI(8), MACD(14/23/9), EMA crossover(7/26), Bollinger Bands(20/2), 6h momentum, 12h momentum
- 4/6 majority vote triggers entry

### AI Reasoning Layer (Optional)
Toggle with `a` key. Wraps any strategy with Claude Haiku (via AWS Bedrock) for natural language trade explanations.

## Quick Start

```bash
cd hyperagent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your testnet private key

# Run
python3 app.py
```

## Keybindings

| Key | Action |
|-----|--------|
| `d` | Switch to Dashboard |
| `s` | Switch to Strategy Config |
| `j` | Switch to Trade Journal |
| `a` | Toggle AI reasoning |
| `q` | Quit |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TESTNET_PRIVATE_KEY` | Yes | Hyperliquid testnet wallet private key (0x...) |
| `AWS_ACCESS_KEY_ID` | For AI | AWS credentials for Bedrock Claude Haiku |
| `AWS_SECRET_ACCESS_KEY` | For AI | AWS secret key |
| `AWS_DEFAULT_REGION` | For AI | AWS region (default: us-east-1) |

## Tech Stack

- **Textual 8.2.3** — Interactive terminal UI framework
- **hyperliquid-python-sdk 0.23.0** — Exchange API
- **ta** — Technical analysis indicators
- **anthropic + boto3** — Claude Haiku via AWS Bedrock
- **Python 3.9+**

## Data Flow

1. **Price Feed** (every 3s) — Fetches all asset prices from mainnet
2. **Scanner** (every 30s) — Scans whale addresses, extracts liquidation prices, clusters them
3. **Strategy** (every 30s) — Scores cascade probability or runs momentum voting, emits signals
4. **Trader** (on signal) — Executes on testnet with IOC orders, places native TP/SL
5. **Risk Monitor** (every 2s) — Checks trailing stops, fires on breach

## Project Structure

```
hyperagent/
├── app.py                  # Textual App entry point
├── config.py               # All configuration constants
├── core/
│   ├── client.py           # Dual Hyperliquid client (mainnet read + testnet trade)
│   ├── risk.py             # Risk manager (trailing stop + native TP/SL)
│   └── state.py            # Shared AgentState dataclass
├── strategies/
│   ├── base.py             # Abstract BaseStrategy interface
│   ├── cascade.py          # Liquidation Cascade Predictor
│   ├── momentum.py         # 6-signal Momentum Flip
│   └── ai_wrapper.py       # Claude Haiku reasoning wrapper
├── scanner/
│   ├── liquidation_scanner.py  # Mainnet whale position scanner
│   └── whale_addresses.py      # Whale address database
├── tui/
│   ├── styles.tcss         # Textual CSS stylesheet
│   ├── screens/            # Dashboard, Strategy Config, Trade Journal
│   └── widgets/            # Market ticker, Heatmap, Cascade gauge, Positions
├── requirements.txt
└── .env.example
```

## Built for

**Elsa Agentic Hyperthon** — Track 1: Hyperliquid Perp Agent
