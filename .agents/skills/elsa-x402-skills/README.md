# elsa-x402-skills

A reusable agent skill that enables interaction with the [Elsa X402 API](https://x402.heyelsa.ai/docs) — execute real blockchain transactions, query portfolio data, trade tokens, and manage staking via pay-per-use micropayments on Base network.

Works with any [skills.sh](https://skills.sh)-compatible agent: Claude Code, Cursor, GitHub Copilot, Cline, Windsurf, Goose, and more.

## What this skill does

When installed, your agent knows how to:

- Query wallet balances, portfolio value, P&L reports, and token prices
- Get swap quotes and execute token swaps across chains
- Create, view, and cancel limit orders via CoW Protocol
- View staking positions and discover yield opportunities
- Monitor and advance multi-step transaction pipelines
- Check gas prices and transaction history

## Installation

```bash
# Install into a specific project
npx skills add elsa-x402-skills

# Or install globally
npx skills add elsa-x402-skills -g
```

The CLI will prompt you to select your agent.

## Prerequisites

Your project needs the following packages:

```bash
npm install x402-axios axios viem
```

And a funded Base mainnet wallet (USDC or ELSA) to pay for API calls.

## Setup

```typescript
import { withPaymentInterceptor } from 'x402-axios';
import axios from 'axios';
import { createWalletClient, http } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { base } from 'viem/chains';

const walletClient = createWalletClient({
  account: privateKeyToAccount(process.env.PRIVATE_KEY as `0x${string}`),
  chain: base,
  transport: http('https://mainnet.base.org')
});

// Pay with USDC (default)
const client = withPaymentInterceptor(
  axios.create({ baseURL: 'https://x402-api.heyelsa.ai' }),
  walletClient
);

// Pay with ELSA token — use /api/elsa/ endpoints instead
```

## Payment

All API calls are metered via [X402](https://x402.org) micropayments on Base network. Costs range from $0.001 to $0.05 per call depending on the endpoint. The `GET /health` endpoint is free.

| Payment token | Base URL prefix |
|---|---|
| USDC (default) | `https://x402-api.heyelsa.ai/api/` |
| ELSA token | `https://x402-api.heyelsa.ai/api/elsa/` |

## API coverage

| Category | Endpoints |
|---|---|
| Portfolio & Analytics | `search_token`, `get_token_price`, `get_balances`, `get_portfolio`, `analyze_wallet`, `get_pnl_report` |
| Trading | `get_swap_quote`, `execute_swap`, `create_limit_order`, `get_limit_orders`, `cancel_limit_order` |
| Staking & Yield | `get_stake_balances`, `get_yield_suggestions` |
| Transaction Management | `get_transaction_history`, `get_transaction_status`, `submit_transaction_hash`, `get_gas_prices` |
| Free | `GET /health` |

## Pipeline system

Execute endpoints (`execute_swap`, `create_limit_order`, etc.) return a `pipeline_id`. The skill guides the agent through the full pipeline flow:

```
execute endpoint → pipeline_id
     ↓
get_transaction_status (poll)
     ↓
sign tx_data when status = "sign_pending"
     ↓
submit_transaction_hash
     ↓
poll until status = "success"
```

## Example prompts

Once the skill is installed, you can ask your agent things like:

- _"Check my portfolio for 0x742d35Cc..."_
- _"What's the current price of USDC on Base?"_
- _"Swap 100 USDC to WETH on Base with 2% slippage"_
- _"Show my staking positions and suggest yield opportunities"_
- _"Get a swap quote for 500 USDC → ETH on Base"_

## Links

- [Elsa X402 API Docs](https://x402.heyelsa.ai/docs)
- [X402 Protocol](https://x402.org)
- [Base Network](https://base.org)
- [skills.sh](https://skills.sh)
- [Skills CLI](https://github.com/vercel-labs/skills)
