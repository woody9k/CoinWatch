# CoinWatch — Spec v0.3

> Autonomous memecoin trading agent with UI, alerting, and AI oversight.
> Initial target: BobCoin (`BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump`) on Solana / pump.fun. Designed to add users, chains, and inference providers without a rewrite.
>
> v0.2 adds multi-user roles, audit logging, pluggable inference, and a chain adapter. v0.3 names the product CoinWatch everywhere and adds the v1 safety surface a new trader actually needs. This is still a single-operator app on boblab. Those seams exist so a later multi-user or multi-chain deployment is a fill-in, not a redesign.

---

## 1. Goals

1. Monitor any supported coin 24/7 — price, volume, curve or pool progress, holder activity
2. Execute trades automatically based on configurable algorithmic strategies
3. Escalate to an LLM for judgment calls and daily digests, through a swappable inference provider
4. Deliver real-time alerts via Signal
5. Expose a web UI for status, charts, manual overrides, and strategy config
6. Run reliably on boblab Mac (battery-backed, always-on)
7. Authorize every mutation with a user, a role, and an explicit permission — even while there is only one operator
8. Record an audit trail for trades, config changes, wallet access, and inference calls
9. Keep chain-specific code behind an adapter so Ethereum (or another chain) can be added later

**Not a goal right now:** production multi-tenancy, horizontal scale, SSO, or a second live chain. The interfaces below are the cheap part. The implementations stay single-process, SQLite, and Solana-only until we choose otherwise.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                        WEB UI                            │
│  Dashboard · Charts · Trade Log · Strategy Config · Bots │
└──────────────────┬───────────────────────────────────────┘
                   │ REST + WebSocket  (session, permission-checked)
┌──────────────────▼───────────────────────────────────────┐
│                    API SERVER (FastAPI)                   │
│  /coins  /trades  /strategies  /bots  /alerts  /status   │
│  /users  /audit                                              │
│  AuthN session  ·  AuthZ permission check on every write │
└───────┬──────────────────┬───────────────────────────────┘
        │                  │
┌───────▼──────┐   ┌───────▼──────────────────────────────┐
│  FAST LAYER  │   │            SLOW LAYER                 │
│  Algo Engine │   │  Task prompts (digest, escalation,    │
│  – poll      │   │  strategy review)                     │
│  – triggers  │   │           │                           │
│  – execution │   │  InferenceProvider                    │
└───────┬──────┘   │  openai · anthropic · lmstudio        │
        │          └───────┬──────────────────────────────┘
        │                  │
┌───────▼──────────────────▼──────────────────────────────┐
│  DATA LAYER                                              │
│  SQLite: domain rows + append-only audit_events          │
│  Structured JSON logs (no secrets)                       │
└───────┬─────────────────────────────────────────────────┘
        │
┌───────▼─────────────────────────────────────────────────┐
│  CHAIN ADAPTER                                           │
│  SolanaPumpAdapter now · EvmAdapter later                │
└─────────────────────────────────────────────────────────┘
```

Cross-cutting rules:

- The engine, the API, and the UI all go through the same service functions. Permission checks and audit writes live there, not in the React app.
- Chain code and inference vendor SDKs are not imported outside their adapters.
- A system user (`actor = system`) is the principal for scheduled trades and crons. Human clicks use the signed-in user.

---

## 3. Identity, roles, and permissions

Build the authorization model now. Ship it with one seeded admin. Do not build signup, orgs, billing, or SSO.

### 3.1 What exists in v1

- Local users in SQLite. Passwords hashed (argon2). No shared default password in the repo.
- Server-side sessions (httpOnly cookie). The API does not trust a role sent by the browser.
- One seeded admin, created from env on first boot (`COINWATCH_ADMIN_USER`, `COINWATCH_ADMIN_PASSWORD`).
- Every bot, strategy, trade, wallet, and settings change stores `created_by` / `updated_by`.
- A user can be disabled. Disabled users fail the next request. Existing bots keep running as `system` until an admin pauses them.

### 3.2 Roles

Roles are bundles of permissions. Users have one role in v1 (the schema allows more later).

| Role | Intent |
|------|--------|
| `admin` | Users, wallets, global limits, inference config, everything below |
| `trader` | Run bots, edit strategies, place manual trades, invoke inference |
| `viewer` | Read dashboards, coins, trades, alerts |
| `auditor` | Viewer, plus the full audit log |

`auditor` exists so a later read-only reviewer does not need admin. With one operator, that operator is `admin`.

### 3.3 Permissions

Checks are explicit strings, enforced in the service layer before any side effect. Adding a permission later is a new string plus a role mapping, not a new access style.

| Permission | Allows |
|------------|--------|
| `coins.read` / `coins.manage` | View tracked coins / add or remove them |
| `ticks.read` | Price and volume history |
| `bots.read` / `bots.control` | See bots / start, pause, stop |
| `strategies.read` / `strategies.write` | View YAML / create and edit strategies |
| `trades.read` / `trades.execute` | History / manual and engine orders |
| `wallets.read` / `wallets.manage` | Balances / add, remove, assign hot wallets |
| `alerts.read` / `alerts.manage` | Alert log / Signal config and test send |
| `inference.invoke` | Ask the model (digest on demand, escalation) |
| `settings.manage` | RPC, limits, inference provider |
| `users.read` / `users.manage` | List users / create, disable, change role |
| `audit.read` | Query the audit log |

Engine orders use `trades.execute` as the `system` user. That user is not a login. Wallet private keys are never returned by `wallets.read`.

UI hides controls the role cannot use. The API still rejects the call. A 403 is an audit event.

---

## 4. Fast layer — algo trading engine

**Language:** Python 3.12
**Key libs:** `solana-py`, `solders`, `httpx`, `apscheduler` for the Solana adapter. The engine itself depends on the chain port, not on those libraries.

### 4.1 Coin monitor

Polls every 2–5 seconds per tracked coin for cheap state, and every 30–60 seconds for expensive state.

Cheap (every poll), via the chain adapter:

- Current price in the chain's native asset and in USD
- Market cap
- Bonding curve or pool progress, when the venue has one
- Buy/sell volume (last 1m, 5m, 15m)

Expensive (slower poll):

- Holder count
- Dev or deployer wallet activity (flag if they sell)

Solana source of truth: bonding-curve account over RPC. pump.fun's HTTP API is a convenience and will change. Holder and dev scans stay on the slow cadence so a free RPC tier survives.

### 4.2 Strategy engine

Strategies are YAML-configured rules. Triggers are edge-triggered: a condition arms an action once, then waits until it clears or a cooldown expires. A level condition must not buy on every poll. When two rules match the same tick, the more protective action wins (`sell_all` over `sell_pct` over `buy`).

```yaml
strategy: default
chain: solana
coin: <contract_address>
rules:
  - trigger: mcap_usd > 10000
    action: buy
    amount_native: 0.05
  - trigger: price_change_pct_5m > 20
    action: sell_pct
    pct: 50
  - trigger: price_change_pct_5m < -15
    action: stop_loss
  - trigger: curve_pct > 95
    action: sell_all
  - trigger: dev_sold == true
    action: sell_all
    note: "dev rug flag"
```

Built-in strategy templates:

- **sniper** — buy in first 60s, sell at 2x or stop-loss at -30%
- **momentum** — ride volume spikes, sell on reversal
- **graduation_play** — accumulate near graduation, sell when the coin leaves the bonding curve
- **hold_and_manage** — manual entry, auto stop-loss only

Rules are evaluated by a small expression parser. They are never passed to `eval`.

### 4.3 Execution

- Hot wallet: one dedicated wallet per chain. Solana key material in `.env` on boblab. A later EVM wallet is a separate key, same pattern.
- Pre-flight check: confirm native balance before any trade
- Slippage: configurable per strategy (default 10%, hard cap in settings)
- Landing: wait for a chain-appropriate confirmation, then log. On Solana that is `confirmed`, not `finalized`.
- Priority fees are part of the Solana adapter, not a later polish item
- Hard limits: max single trade, max daily loss — both configurable, both checked before submit
- Paper mode: the adapter can fill against the last tick with no chain write. Phase 2 starts here. Live sends are an explicit bot setting.
- Global kill switch: rejects new orders even if the process stays up. Pausing is audited.

Before graduation, Solana buys and sells are pump.fun program instructions. After graduation they are the venue the coin listed on (PumpSwap or Raydium). The adapter owns that branch. Graduation is curve progress, not a fixed USD market cap.

### 4.4 Escalation to the slow layer

Escalate when:

- Unusual volume spike not explained by a trigger
- Dev or deployer wallet moves
- Coin is approaching graduation or a pool migration
- Daily P&L crosses a threshold
- Manual "ask" button in the UI (`inference.invoke`)

Default policy: log the model response. A bot may act on it only when `act_on_inference: true` is set. That flag is `settings.manage` to change and is audited.

---

## 5. Slow layer — pluggable inference

The slow layer is a set of tasks with fixed prompt templates and structured outputs. Vendor SDKs sit behind one port. OpenClaw can still deliver cron and Signal. It is not the inference implementation.

### 5.1 Provider port

```text
InferenceProvider.complete(task, messages, response_schema) -> InferenceResult
```

`InferenceResult` carries text, parsed object, provider, model, latency, token counts, and a content hash. It does not carry API keys.

| Provider id | Use |
|-------------|-----|
| `openai` | Hosted OpenAI chat completions |
| `anthropic` | Hosted Anthropic messages |
| `lmstudio` | Local OpenAI-compatible server (`LMSTUDIO_BASE_URL`) |

Selection is config, not code: `INFERENCE_PROVIDER=openai|anthropic|lmstudio`, plus a model name per task (`INFERENCE_MODEL_DIGEST`, `INFERENCE_MODEL_ESCALATION`, `INFERENCE_MODEL_REVIEW`). LM Studio model names will not match the hosted ones, so the model is per task.

A provider that is down fails the task, writes an audit row, and does not block the fast layer. Trading never waits on a completion except the explicit escalation path, which has a timeout and falls back to "no advice."

### 5.2 Tasks

**Daily digest (scheduled).** Every morning at 8am ET the digest task receives a structured summary:

- Each tracked coin: price, 24h change, trades made, P&L
- Hot wallets: native balance, total deployed
- Anything flagged for review

The model writes a plain-English read. CoinWatch sends it to Signal. The send is audited as `system`.

**Escalation (on demand).** The fast layer posts coin state. The model may be given social context later. It returns `HOLD`, `SELL`, or `BUY_MORE` plus a rationale, validated against the response schema. The fast layer logs it and acts only if the bot allows it.

**Strategy review (weekly).** The model reviews trade history, identifies what worked, and suggests YAML tweaks. Suggestions are stored and sent to Signal. They are not applied automatically.

---

## 6. Audit and logging

Two different streams. Operational logs explain how the process ran. The audit log explains who changed what. Neither stores secrets, seed phrases, private keys, or raw API keys.

### 6.1 Operational logs

- Structured JSON to stdout, rotated daily (launchd or pm2 captures the files)
- Fields on every line: `ts`, `level`, `logger`, `event`, `request_id`, `chain`, `coin`
- `request_id` is created at the API edge and passed into engine work spawned by that request. Cron work generates its own.
- Inference logs record provider, model, task, latency, and token counts. Full prompts are off by default (`LOG_INFERENCE_PROMPTS=false`) because they contain positions.

### 6.2 Audit log

Append-only `audit_events`. No updates, no deletes from the app. If a write to the domain row succeeds, the audit insert is in the same transaction.

| Field | Meaning |
|-------|---------|
| `id` | — |
| `ts` | UTC |
| `actor_type` | `user` or `system` |
| `actor_id` | user id, or `system` |
| `action` | `trade.submit`, `bot.pause`, `strategy.update`, `user.disable`, `inference.complete`, `auth.denied`, … |
| `entity_type` / `entity_id` | bot, trade, strategy, wallet, user, settings |
| `result` | `ok` or `denied` or `error` |
| `before` / `after` | JSON snapshots of the changed fields, redacted |
| `request_id` | joins to logs |
| `detail` | short machine-readable reason (rule id, tx signature, provider error) |

Events that must be audited: login success and failure, permission denied, every settings change, wallet add/remove, strategy and bot lifecycle, every order attempt (including paper and rejected), kill switch, inference task start and result, alert send.

Strategy decisions that do not trade (condition false, cooldown, paper skip) go to a `decisions` table, not the audit log. They are high volume. Audit stays for actions a person would investigate.

---

## 7. Signal alerts

**Transport:** `signal-cli` on boblab (0.14.0), account linked to `+14075150936` (407-515-0936). The previous Google Voice number is retired. OpenClaw may send on this account. CoinWatch can also shell out to `signal-cli` for engine alerts. One account, one recipient to start. The alert sender is an interface so another channel can be added later without touching trade code.

Alert types and urgency:

| Event | Urgency | Channel |
|-------|---------|---------|
| Trade executed | Low | Silent push |
| Stop-loss triggered | High | Loud alert |
| Dev sold | Critical | Loud + message |
| Coin graduating | High | Loud alert |
| Daily digest | Low | Morning message |
| Escalation response | Medium | Standard |

Alerts include: coin name, chain, event, current price and market cap, action taken (if any), wallet balance remaining. They do not include keys or full wallet addresses beyond a short suffix.

**Before first run:** send one manual test alert to `+14075150936` and confirm it arrives on the phone.

---

## 8. Chain adapters

The engine talks to a `ChainAdapter`. Solana pump.fun is the only implementation we build now. Ethereum is a second implementation later, not a fork of the bot.

```text
ChainAdapter
  id                  # "solana", "ethereum", …
  native_symbol       # SOL, ETH
  get_coin_state(coin) -> CoinState
  get_balance(wallet) -> Balance
  execute(order) -> TxResult     # paper or live
  confirm(tx_sig) -> Confirmation
```

`CoinState` is chain-neutral: price in native units, price in USD, market cap, volume windows, optional `curve_pct`, optional holder count, dev-sold flag. Fields a chain does not have stay null. Strategies must tolerate nulls (a rule on `curve_pct` does not fire on a Uniswap pool unless that adapter defines an equivalent).

What stays chain-specific inside the adapter:

- Account layout, instruction building, priority fees, confirmation depth
- Venue migration (pump.fun curve to PumpSwap/Raydium)
- Address validation and explorer links

Wallets, coins, and trades all carry `chain`. A bot binds one coin on one chain. Cross-chain inventory is out of scope.

Adding Ethereum later means: a new adapter, a new wallet row, native-asset pricing, and whatever venue we actually want (a specific DEX or router). It does not mean new tables for users, audit, strategies, or alerts.

---

## 9. Web UI

**Stack:** React + Vite + TailwindCSS + shadcn/ui
**Charts:** Recharts
**Served:** built static files from FastAPI, or Nginx on boblab. Not a Vite dev server as the always-on process. Local network access.

The UI sends the session cookie and renders from the permissions on `/status` (or `/me`). It is not a second authorization system.

### 9.1 Dashboard (home)

- Portfolio summary: total deployed, total P&L, active coins
- Active bot status cards (running / paused / stopped)
- Recent trades feed (last 10)
- Alert log

### 9.2 Coin view

Per-coin page with:

- Price chart (candlestick or line, multiple timeframes)
- Bonding curve progress bar when the chain reports `curve_pct`
- Volume bars (1m, 5m, 15m)
- Holder count over time
- My position: entry, current value, unrealized P&L
- Trade history for this coin
- Active strategy (editable inline, `strategies.write`)
- Manual trade panel (buy / sell with amount input, `trades.execute`)
- "Ask" button → escalation (`inference.invoke`) and the logged response

### 9.3 Bot management

- List all bots (chain, coin, strategy, status, P&L)
- Start / pause / stop controls
- Per-bot trade log
- Strategy editor (YAML with syntax highlight)
- Backtest view (if historical data available)

### 9.4 Wallet management

- Hot wallet balances per chain (never the secret)
- Allocation across coins
- Add / remove tracked wallets (`wallets.manage`)

### 9.5 Strategy library

- Saved strategy templates
- Clone and customize
- Performance history per strategy

### 9.6 Settings

- Signal alert config + test
- RPC endpoint config per chain (Helius or Alchemy key stays server-side)
- Global limits (max daily loss, max per trade)
- Inference provider, per-task model, and a one-shot connectivity check
- User list and role changes (`users.manage`)
- Audit log viewer (`audit.read`)

### 9.7 Future UI features (nice-to-have)

- Multiple human users beyond the seeded admin (the API already allows it)
- Wallet tracker (watch any address on a supported chain)
- Trending coins feed from pump.fun
- Sniper queue (queued targets with auto-buy on launch)
- Social feed integration (pump.fun comments, Twitter mentions)
- Leaderboard (top coins, top wallets)

---

## 10. Data model (SQLite)

SQLite in WAL mode. The engine and the API may be separate processes. Domain writes and their audit rows share a transaction.

```sql
users         (id, username, password_hash, role, disabled_at, created_at)
sessions      (id, user_id, expires_at, created_at)

chains        (id, native_symbol)                          -- solana/SOL first
coins         (chain, address, name, symbol, created_at, graduated_at, status)
ticks         (chain, coin_address, ts, price_native, price_usd, mcap_usd,
               liquidity_native, curve_pct, volume_1m, volume_5m, volume_15m, holders)
wallets       (id, chain, label, public_address, created_by, created_at)
              -- secrets stay in env / a later secret store, not in this table

bots          (id, chain, coin_address, strategy_id, wallet_id, status,
               paper, act_on_inference, created_by, created_at)
strategies    (id, name, yaml_config, created_by, created_at, updated_by, updated_at)
positions     (bot_id, chain, coin_address, size, cost_native, realized_pnl_native, updated_at)
trades        (id, bot_id, chain, coin_address, side, amount_native, price_native,
               price_usd, mcap_usd, fee_native, price_impact_pct, tx_sig, paper, actor_id, ts)
price_alerts  (id, chain, coin_address, condition, threshold, enabled, created_by)
decisions     (id, bot_id, ts, rule, outcome, detail)      -- high volume, not audit
alerts        (id, chain, coin_address, type, message, sent_at, escalated)
digests       (id, ts, content, provider, model, sent_at)
inference_calls (id, task, provider, model, actor_id, latency_ms, status, ts)

audit_events  (id, ts, actor_type, actor_id, action, entity_type, entity_id,
               result, before_json, after_json, request_id, detail)
```

Ticks are about 30k rows per coin per day at a 3s poll. Keep a retention job (raw ticks for 14 days, optional rollup after that). Index `(chain, coin_address, ts)`.

Positions update in the same transaction as the trade that changed them. P&L is not reconstructed by scanning trades at request time.

---

## 11. Seams we want, scale we do not

Keep these boundaries boring and real so a later production deploy is mostly hosting and a second implementation:

| Seam now | Later, only if we need it |
|----------|---------------------------|
| Service functions with permission checks | More roles, per-bot grants |
| `users` + sessions | SSO, multiple operators, invitations |
| SQLAlchemy + SQLite WAL | Postgres, same models |
| `ChainAdapter` with `SolanaPumpAdapter` | `EvmAdapter` for a chosen venue |
| `InferenceProvider` with three drivers | Another vendor, or a hosted gateway |
| Alert sender interface, Signal implementation | A second channel |
| Audit table in the same database | Export or a separate store |
| `.env` on boblab | A secret manager. Keys never move into SQLite either way |
| One FastAPI process plus the engine | Split workers, a queue between poll and submit |

Explicitly out of scope until we decide otherwise: Kubernetes, multi-region, per-tenant databases, a public internet signup, billing, and social login.

The API binds to localhost, or requires the session, before it is reachable on the LAN. Anything that can move funds is authenticated.

---

## 12. Infrastructure

**Host:** boblab Mac (battery-backed, always-on)
**Process manager:** launchd, or pm2 if that stays simpler
**Services:**

- `coinwatch-engine` — trading engine (always running)
- `coinwatch-api` — FastAPI server (port 8420), serves the built UI
- UI build is an artifact. Port 8421 is unused unless we split static hosting later.

**Repo:** `bobaisteel/coinwatch` on GitHub
**Secrets:** `.env` on boblab, never committed. Admin bootstrap password is rotated after first login.
**Logging:** structured JSON logs, rotated daily. Audit rows in SQLite.

---

## 13. Tech stack summary

Implementation standards, layout, and `make standards-check` live in `CLAUDE.md`. This table is the short version.

| Layer | Tech |
|-------|------|
| Language / packaging | Python 3.12, uv, one package, two processes |
| Trading engine | `python -m coinwatch.engine`, APScheduler, chain port |
| Solana adapter | solana-py, solders, pump.fun curve + program instructions |
| API server | FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite (WAL) |
| AuthZ | Roles and permission strings in the service layer |
| Web UI | React, TypeScript, Vite, Tailwind CSS, shadcn/ui, Recharts |
| Inference | Provider port: OpenAI, Anthropic, LM Studio |
| Alerts | Signal via signal-cli (`+14075150936`) |
| RPC | Alchemy free tier to start. Helius if we need staked landing. |
| Checks | ruff, pyright, pytest, TypeScript `tsc`, ESLint |
| Process mgmt | pm2 or launchd |
| Source control | GitHub (bobaisteel/coinwatch) |

---

## 14. Build phases

### Phase 1 — Foundation

- [ ] SQLite schema + migrations, including users, positions, decisions, inference_calls, audit_events, price_alerts
- [ ] Seeded admin, sessions, permission checks on write routes
- [ ] `ChainAdapter` + Solana poller (curve state, liquidity in the curve, store ticks) for BobCoin
- [ ] Stale-data flag: last tick older than 15s means the coin is not tradable
- [ ] FastAPI: `/coins`, `/ticks`, `/status`, `/me`, `/audit`
- [ ] Structured logs and audit writes for auth and coin changes
- [ ] Signal test alert to `+14075150936`
- [ ] Manual trade CLI in paper mode, then one live path behind the kill switch
- [ ] Order quote before submit: expected fill, slippage floor, fee, price impact, SOL left for fees

### Phase 2 — Trading engine

- [ ] Strategy engine (YAML rules → edge triggers → execution)
- [ ] Hot wallet integration (Solana only)
- [ ] Bot start / stop / pause lifecycle, audited
- [ ] Stop-loss and take-profit
- [ ] Trade and position logging
- [ ] Paper mode default until a strategy is intentionally armed live

### Phase 3 — UI

- [ ] Dashboard + coin view
- [ ] Bot management
- [ ] Charts (price, volume, bonding curve)
- [ ] Manual trade panel with the quote, a paper/live banner, and a Solscan link on each fill
- [ ] Coin identity: name, symbol, and full address. Market cap shown next to SOL sitting in the curve.
- [ ] Simple price alerts (above, below, curve %, dev sold) to Signal
- [ ] Fee-aware P&L in SOL and USD
- [ ] Trade CSV export
- [ ] Controls hidden and rejected according to role

### Phase 4 — Inference

- [ ] Provider port with OpenAI, Anthropic, and LM Studio drivers
- [ ] Daily digest task
- [ ] Escalation task, log-only by default
- [ ] "Ask" button → stored response
- [ ] Weekly strategy review, suggestions not auto-applied

### Phase 5 — Polish

- [ ] Strategy library + templates
- [ ] Backtesting
- [ ] Trending coins feed
- [ ] Wallet tracker
- [ ] Second chain adapter, only when we have a venue and a wallet we mean to use

---

## 15. RPC provider options

| Provider | Free tier | Paid entry | Notes |
|----------|-----------|------------|-------|
| **Public Solana RPC** | Unlimited (but rate-limited) | — | Unreliable, frequent 429s, no SLA. OK for dev, not for live orders. |
| **Alchemy** | 30M CU/month | ~$49/mo | Most generous free tier. Multi-chain, which matters when an EVM adapter exists. |
| **QuickNode** | 10M credits/month | ~$49/mo | Solid, multi-chain, good tooling. |
| **Helius** | 1M credits + 10 RPS | ~$49/mo | Solana-only specialist. Best transaction landing via staked connections. Deepest Solana-specific APIs. |
| **Chainstack** | 3M req/month | ~$25/mo | Cheaper entry, less Solana-specific tooling. |

**Recommendation:**

- **Phase 1:** Alchemy free tier. Polling one coin at 5s plus rare trades stays well under 30M CU/month. Alchemy also covers Ethereum later, so the RPC config is already per chain.
- **Upgrade to Helius Developer ($49/mo) when** live Solana landing matters. Staked connections help sniping. Helius pump.fun indexing is useful for a trending feed. It does not replace an EVM provider.

RPC URL and API key are per chain in settings, server-side only.

---

## 16. What v1.0 has to include

v1 is useful when a new trader can watch one coin, understand what a buy will do, and get out on purpose. Automatic strategies come after the numbers on screen match the chain. These are in Phases 1–3, not Phase 5.

**The address is the coin.** Names and tickers are copied constantly. Every coin page and alert shows the full mint address. Adding a second coin with the same symbol is allowed and visibly distinct.

**Market cap is not exit liquidity.** Pump.fun market cap is price times supply. The SOL actually sitting in the bonding curve is what a seller can hit. The coin page shows both, plus the estimated impact of selling the current position. A strategy may not spend more than a configured share of that liquidity in one order.

**Quote, then send.** Every live or paper order previews: native amount in, expected tokens out, minimum out at the slippage cap, venue fee, priority fee, price impact, and native balance left afterward. The submit is refused if the wallet would have less than a fee reserve (default 0.05 SOL) or if the last tick is older than 15 seconds. A failed sell simulation blocks live buys of that coin.

**Fills you can check.** Each trade stores the fee and the impact, shows P&L in native units and USD, and links the signature to an explorer. A CSV export of trades is in v1 so taxes are not a reconstruction project.

**Alerts before bots.** Price above, price below, curve progress, and dev-sold go to Signal without a strategy. That is the everyday loop while paper mode is still the default.

Still later: sniper queues, trending feeds, social posts, backtests, and a second chain. Those add surface area before the quote and the fill are trustworthy.

---

## Open questions

1. ✅ **Name:** CoinWatch
2. ✅ **First chain:** Solana / pump.fun. Next chain is unimplemented until we pick a venue.
3. ✅ **RPC for Phase 1:** Alchemy free tier. Helius if Solana landing becomes the problem.
4. ✅ **Signal:** `signal-cli` 0.14.0 on boblab, linked to `+14075150936`. Google Voice `+13526040428` is retired. Still need one successful test send.
5. ✅ **BobCoin:** `BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump`
6. **Inference default:** which provider is on for Phase 4 (OpenAI, Anthropic, or a local LM Studio). The port does not care. We should pick one before writing the digest prompt against a live key.

---

*Spec by Bobai · October 2026 · v0.3*
