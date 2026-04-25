# HyperAgent

[![PyPI version](https://img.shields.io/pypi/v/hyperliquidagent.svg)](https://pypi.org/project/hyperliquidagent/)
[![Python](https://img.shields.io/pypi/pyversions/hyperliquidagent.svg)](https://pypi.org/project/hyperliquidagent/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Autonomous Trading Terminal for Hyperliquid (testnet)**

HyperAgent is an interactive terminal-based trading agent that watches the market in real-time, detects trading opportunities using multiple strategies, executes trades autonomously, manages risk with trailing stop-losses, and explains every decision with AI — all from a single terminal interface.

You pick a strategy. You hit Start. It trades by itself.

## Install

```bash
pipx install hyperliquidagent
hyperagent
```

That's it. First run launches a short setup wizard (asks for your main wallet address and an agent-wallet private key — see [Safety](#safety) below), then drops you into the TUI.

Upgrade any time:

```bash
pipx upgrade hyperliquidagent
```

Don't have `pipx`? [Install it in 30 seconds](https://pipx.pypa.io/stable/installation/).

## Safety

- **Testnet only.** This version signs only on Hyperliquid testnet. It cannot touch your mainnet funds.
- **Agent-wallet only — you never paste your main private key.** HyperAgent uses Hyperliquid's native "agent wallet" feature. You generate an agent key on [app.hyperliquid-testnet.xyz/API](https://app.hyperliquid-testnet.xyz/API), sign one approval transaction with your main wallet, and paste only the agent key into the wizard. Hyperliquid's exchange enforces that agent keys **cannot withdraw funds** — only place and cancel trades.
- **Revocable any time.** If you ever want to cut off the bot, click "Revoke" on the same Hyperliquid page. No key rotation, no emails, one click.

---

## What It Does

Every second, HyperAgent:

1. **Watches** — Pulls live prices, funding rates, open interest, and orderbook depth from Hyperliquid mainnet
2. **Listens** — Polls HypeDexer's full liquidation firehose (Hyperliquid-wide) for ongoing cascade events
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
│  ┌─────────────────────┐      ┌──────────────────────────────┐    │
│  │ Trend Follower      │      │ Dual Client                  │    │
│  │ Momentum            │─────>│  Mainnet Info (read-only)    │    │
│  │ Funding Carry       │      │  Testnet Exchange (trading)  │    │
│  │ Volatility Breakout │      └──────────────────────────────┘    │
│  │ Pairs Reversion     │      ┌──────────────────────────────┐    │
│  │ Liquidation Cascade │      │ Risk Manager                 │    │
│  └─────────┬───────────┘      │  Trailing stop (software)    │    │
│            │                   │  Native TP/SL (exchange)     │    │
│  ┌─────────▼──────────┐       │  Daily loss limits           │    │
│  │ AI Wrapper          │       │  Position sizing             │    │
│  │ (Claude Haiku via   │       └──────────────────────────────┘    │
│  │  AWS Bedrock)       │                                            │
│  └────────────────────┘                                            │
│                                                                    │
│  LIQUIDATION DATA              TUI                                 │
│  ┌────────────────────┐       ┌──────────────────────────────┐    │
│  │ HypeDexer Client   │       │ Market Ticker                │    │
│  │ Full liquidation   │       │ Liquidation Stats (v2)       │    │
│  │ firehose (30s)     │       │ AI Reasoning Panel           │    │
│  │ Rolling aggregator │       │ Positions + Trailing Stop    │    │
│  └────────────────────┘       │ Scrollable Trade Log         │    │
│                                └──────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Trading Strategies

| Strategy | What It Does | Speed |
|----------|-------------|-------|
| **Trend Follower** | ADX(14) on 4h candles confirms trends, +DI/-DI for direction, EMA(21/55) confirms, pullback-to-EMA entries with ATR-based stops | Every 15s |
| **Momentum** | 6-signal weighted scoring (RSI, MACD+slope, EMA crossover, BB %B, volume-momentum, 4h confirmation) with ADX chop filter | Every 15s |
| **Funding Carry** | Research-calibrated funding arbitrage — requires >0.03% rate, settlement timing, trend filter, and funding persistence | Every 15s |
| **Volatility Breakout** | Detects Bollinger squeeze (BB inside Keltner), then trades the breakout with ATR-adaptive thresholds and volume confirmation | Every 15s |
| **Pairs Reversion** | Market-neutral stat-arb on BTC/ETH and SOL/AVAX — z-score on log price ratio, entry at 2σ, stop at 3.5σ | Every 15s |
| **Liquidation Cascade** | Polls HypeDexer's full liquidation firehose, trades in-cascade direction when dominant-side USD + 3× imbalance + 1.3× acceleration all confirm an ongoing cascade | Every 15s |

**AI Layer**: Any strategy can be wrapped with Claude Haiku (AWS Bedrock) for natural language trade explanations. Toggle with `a` key.

---

## Getting started (full walkthrough)

1. **Install**: `pipx install hyperliquidagent`
2. **Get testnet USDC**: go to [app.hyperliquid-testnet.xyz](https://app.hyperliquid-testnet.xyz), connect your wallet, hit the faucet.
3. **Generate an agent wallet**: go to [app.hyperliquid-testnet.xyz/API](https://app.hyperliquid-testnet.xyz/API), click **Generate**, sign the `approveAgent` transaction with your main wallet, copy the agent private key shown on screen.
4. **Run** `hyperagent`. The wizard asks for:
   - Network (testnet only in this version)
   - Your main wallet address (public, `0x…`)
   - The agent private key you just copied (hidden input)
   - Optional AWS Bedrock creds for AI explanations
   - Optional HypeDexer key for the Liquidation Cascade v2 strategy
5. **Trade**: pick a strategy, hit Start.

Config is saved to `~/.config/hyperagent/.env` with `chmod 600`.

To reconfigure later (rotate the agent key, add AWS creds, etc.):

```bash
hyperagent setup
```

### Run from source (contributors)

```bash
git clone https://github.com/yashj09/Hyperagent
cd hyperagent
python3 -m venv venv && source venv/bin/activate
pip install -e .
hyperagent
```

---

## Keybindings

| Key | Action |
|-----|--------|
| `d` | Dashboard |
| `s` | Strategy Config |
| `j` | Trade Journal |
| `n` | Analytics |
| `a` | Toggle AI |
| `k` | Kill all positions |
| `q` | Quit |

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HL_MAIN_ADDRESS` | Yes | Your main wallet address (0x…, public) |
| `HL_AGENT_PRIVATE_KEY` | Yes | Agent wallet private key from Hyperliquid — **never your main key** |
| `AWS_ACCESS_KEY_ID` | For AI | AWS credentials for Bedrock |
| `AWS_SECRET_ACCESS_KEY` | For AI | AWS secret key |
| `AWS_DEFAULT_REGION` | For AI | AWS region (default: us-east-1) |
| `HYPEDEXER_API_KEY` | For Cascade v2 | Required only for the Liquidation Cascade v2 strategy |
| `HYPERAGENT_ENV_FILE` | No | Override config file location |

---

## Data Flow

```
Price Feed (5s)    ──> Prices, Funding, OI ──> Shared State
Liq Poller (30s)   ──> HypeDexer firehose ──> Rolling Stats ──> Shared State
Strategy (15s)     ──> Read State ──> Generate Signal ──> Execute Trade
Risk Monitor (2s)  ──> Check Trailing Stops ──> Close on Breach
Dashboard (1s)     ──> Read State ──> Render All Panels
```

---

## What Makes It Unique

1. **Liquidation Cascade Trader** — Trades the full Hyperliquid liquidation firehose (via HypeDexer) when dominant-side USD, imbalance, and acceleration all confirm an ongoing cascade — catching the forced-flow move, not guessing at it
2. **Strategy platform, not a single bot** — 6 strategies in a dropdown. Adding a new one is one file implementing `generate_signal()`
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
│   ├── state.py                    # Shared AgentState dataclass
│   ├── hypedexer_client.py         # HypeDexer API client (liquidation firehose)
│   └── liquidation_aggregator.py   # Rolling per-coin liquidation stats
├── strategies/
│   ├── base.py                     # Abstract BaseStrategy interface
│   ├── trend_follower.py           # ADX-based CTA trend follower
│   ├── momentum.py                 # 6-signal weighted momentum
│   ├── funding_sniper.py           # Funding carry arbitrage
│   ├── volatility_breakout.py      # Bollinger/Keltner squeeze breakout
│   ├── pairs_reversion.py          # Market-neutral pairs stat-arb
│   ├── liquidation_cascade_v2.py   # Tradable cascade on HypeDexer firehose
│   └── ai_wrapper.py               # Claude Haiku reasoning wrapper
├── tui/
│   ├── styles.tcss                 # Textual CSS (dark trading theme)
│   ├── screens/
│   │   ├── dashboard.py            # Live dashboard
│   │   ├── strategy_config.py      # Strategy selector + AI toggle + params
│   │   └── trade_journal.py        # Trade history table + stats
│   └── widgets/
│       ├── market_ticker.py        # Live price ticker bar
│       ├── liquidation_stats.py    # Per-coin v2 liquidation stats panel
│       └── positions_panel.py      # Active positions with trailing stop viz
├── requirements.txt
├── .env.example
└── .gitignore
```

