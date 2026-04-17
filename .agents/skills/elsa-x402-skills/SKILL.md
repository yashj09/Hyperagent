---
name: elsa-x402-skills
description: Interact with the Elsa X402 blockchain API — portfolio analytics, token trading, staking, and transaction management via pay-per-use micropayments on Base network.
---

# Elsa X402 API Skill

Use this skill whenever the user wants to interact with the Elsa X402 API to execute blockchain operations, query portfolio data, trade tokens, manage staking positions, or monitor transactions.

## When to use

- User asks to check wallet balance, portfolio, or P&L
- User wants to swap tokens, create/cancel limit orders, or get swap quotes
- User wants to view staking positions or find yield opportunities
- User needs to monitor a pipeline or submit a signed transaction
- User asks about gas prices or transaction history
- Any blockchain operation routed through the Elsa x402 API

## Setup

All API calls require an X402-enabled HTTP client. The interceptor automatically handles micropayments on Base network.

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

const client = withPaymentInterceptor(
  axios.create({ baseURL: 'https://x402-api.heyelsa.ai' }),
  walletClient
);
```

### Payment token selection

| Path prefix | Payment token |
|---|---|
| `/api/` | USDC (default) |
| `/api/elsa/` | ELSA token |

All endpoints are available under both prefixes with identical behavior.

---

## Instructions

When the user makes a request, identify the relevant endpoint(s) below and generate the corresponding API call. Always include required parameters. Use `dry_run: true` for execute endpoints unless the user explicitly confirms real execution.

### Pipeline pattern

All `execute_*` and `claim_*` endpoints return a `pipeline_id`. Always follow this flow after calling them:

1. Call execute endpoint → receive `pipeline_id`
2. Poll `POST /api/get_transaction_status` with `{ pipeline_id }`
3. When a task has `status: "sign_pending"`, sign `tx_data` with the user's wallet
4. Submit the tx hash via `POST /api/submit_transaction_hash`
5. Continue polling until overall status is `"success"`

```typescript
// Generic pipeline handler
async function runPipeline(pipelineId: string) {
  while (true) {
    const { data } = await client.post('/api/get_transaction_status', { pipeline_id: pipelineId });

    for (const task of data.tasks) {
      if (task.status === 'sign_pending') {
        const hash = await walletClient.sendTransaction(task.tx_data);
        await client.post('/api/submit_transaction_hash', {
          task_id: task.task_id,
          tx_hash: hash,
          status: 'submitted'
        });
      }
    }

    if (data.status === 'success' || data.status === 'failed') break;
    await new Promise(r => setTimeout(r, 2000));
  }
}
```

---

## API Reference

### Portfolio & Analytics

#### Search Token — `POST /api/search_token` · $0.001
Search for tokens across all blockchains by symbol or address.

```typescript
const { data } = await client.post('/api/search_token', {
  symbol_or_address: 'USDC', // required
  limit: 5                   // optional
});
// data.result.results: [{ symbol, name, address, chain, priceUSD }]
```

#### Get Token Price — `POST /api/get_token_price` · $0.002
Real-time price for a token on a specific chain.

```typescript
const { data } = await client.post('/api/get_token_price', {
  token_address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', // required
  chain: 'base'                                                  // required
});
```

#### Get Balances — `POST /api/get_balances` · $0.005
All token balances for a wallet across chains.

```typescript
const { data } = await client.post('/api/get_balances', {
  wallet_address: '0x...' // required
});
// data.balances: [{ asset, balance, balance_usd, chain }]
```

#### Get Portfolio — `POST /api/get_portfolio` · $0.01
Comprehensive portfolio including DeFi positions and staking.

```typescript
const { data } = await client.post('/api/get_portfolio', {
  wallet_address: '0x...' // required
});
// data: { wallet_address, total_value_usd, chains, portfolio: { balances, defi_positions, staking_positions } }
```

#### Analyze Wallet — `POST /api/analyze_wallet` · $0.02
Wallet behavior patterns and risk analysis.

```typescript
const { data } = await client.post('/api/analyze_wallet', {
  wallet_address: '0x...' // required
});
```

#### Get P&L Report — `POST /api/get_pnl_report` · $0.015
Profit and loss analysis over a time period.

```typescript
const { data } = await client.post('/api/get_pnl_report', {
  wallet_address: '0x...',  // required
  time_period: '30_days'    // required
});
```

---

### Trading

#### Get Swap Quote — `POST /api/get_swap_quote` · $0.01
Optimal routing and pricing for a swap (read-only, no execution).

```typescript
const { data } = await client.post('/api/get_swap_quote', {
  from_chain: 'base',
  from_token: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', // USDC on Base
  from_amount: '100',
  to_chain: 'base',
  to_token: '0x4200000000000000000000000000000000000006',   // WETH on Base
  wallet_address: '0x...',
  slippage: 2.0
});
// data.quote: { from_amount, estimatedOutput, price_impact, gas_estimate, route }
```

#### Execute Swap — `POST /api/execute_swap` · $0.02
Execute a real token swap. Returns a pipeline — follow the pipeline pattern above.

```typescript
const { data } = await client.post('/api/execute_swap', {
  from_chain: 'base',
  from_token: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
  from_amount: '100',
  to_chain: 'base',
  to_token: '0x4200000000000000000000000000000000000006',
  wallet_address: '0x...',
  slippage: 2.0,
  dry_run: false  // set true to preview without executing
});
// data.pipeline_id — use with runPipeline()
```

#### Create Limit Order — `POST /api/create_limit_order` · $0.05
Create a limit order via CoW Protocol. Returns a pipeline.

```typescript
const { data } = await client.post('/api/create_limit_order', {
  from_chain: 'ethereum',
  from_token: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', // USDC on Ethereum
  from_amount: '1000',
  to_token: '0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2',   // WETH on Ethereum
  limit_price: '0.0003',
  wallet_address: '0x...',
  valid_for_hours: 24,
  dry_run: false
});
```

#### Get Limit Orders — `POST /api/get_limit_orders` · $0.002
View all open limit orders for a wallet.

```typescript
const { data } = await client.post('/api/get_limit_orders', {
  wallet_address: '0x...' // required
});
```

#### Cancel Limit Order — `POST /api/cancel_limit_order` · $0.01
Cancel a pending limit order. Returns a pipeline.

```typescript
const { data } = await client.post('/api/cancel_limit_order', {
  order_id: '0x123...',
  wallet_address: '0x...',
  dry_run: false
});
```

---

### Staking & Yield

#### Get Stake Balances — `POST /api/get_stake_balances` · $0.005
View all active staking positions.

```typescript
const { data } = await client.post('/api/get_stake_balances', {
  wallet_address: '0x...' // required
});
// data: { total_staked_usd, stakes: [{ protocol, token, staked_amount, apy }] }
```

#### Get Yield Suggestions — `POST /api/get_yield_suggestions` · $0.02
Discover yield opportunities based on current wallet holdings.

```typescript
const { data } = await client.post('/api/get_yield_suggestions', {
  wallet_address: '0x...' // required
});
```

---

### Transaction Management

#### Get Transaction History — `POST /api/get_transaction_history` · $0.003

```typescript
const { data } = await client.post('/api/get_transaction_history', {
  wallet_address: '0x...', // required
  limit: 10                // optional
});
```

#### Get Transaction Status — `POST /api/get_transaction_status` · $0.001
Poll pipeline status. Task statuses: `pending` → `sign_pending` → `success` / `failed`.

```typescript
const { data } = await client.post('/api/get_transaction_status', {
  pipeline_id: 'pip_123456789' // required
});
// data: { pipeline_id, status, tasks: [{ task_id, type, status, tx_hash?, tx_data? }] }
```

#### Submit Transaction Hash — `POST /api/submit_transaction_hash` · $0.005
After signing a `sign_pending` task, submit the tx hash to advance the pipeline.

```typescript
const { data } = await client.post('/api/submit_transaction_hash', {
  task_id: 'task_def456',          // required — from get_transaction_status
  tx_hash: '0x...',                // required — signed tx hash from RPC
  status: 'submitted',             // required — 'submitted' | 'rejected'
  error: 'optional rejection msg'  // optional — only when status is 'rejected'
});
```

#### Get Gas Prices — `POST /api/get_gas_prices` · $0.001

```typescript
const { data } = await client.post('/api/get_gas_prices', {
  chain: 'base' // required
});
```

---

### Free Endpoints

#### Health Check — `GET /health` · FREE

```typescript
const { data } = await axios.get('https://x402-api.heyelsa.ai/health');
// data: { status: 'healthy', service: { name, version }, payment: { protocol, network, recipient } }
```

---

## Common token addresses (Base network)

| Token | Address |
|---|---|
| USDC | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| WETH | `0x4200000000000000000000000000000000000006` |

## Error handling

Always wrap API calls in try/catch. A `402 Payment Required` response means the X402 interceptor needs a funded wallet on Base mainnet with sufficient USDC (or ELSA for `/api/elsa/` paths).

```typescript
try {
  const { data } = await client.post('/api/get_portfolio', { wallet_address });
} catch (err: any) {
  if (err.response?.status === 402) {
    // Wallet lacks funds for X402 payment
  }
}
```
