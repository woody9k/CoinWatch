# CoinWatch

CoinWatch is a single-operator trading app for boblab. It watches coins, quotes orders before sending them, and can later run strategies. Product behavior is in `coinwatch-spec.md`. This file is how we build it. If they disagree on a safety rule, the spec wins. If they disagree on tooling or layout, this file wins.

v1 chain is Solana, first coin is BobCoin (`BXAdy9GqTt3HKxUfC8NibhS9oYSN2mpHc3AiGwmGpump`) on pump.fun. Do not implement a second chain, signup, orgs, or SSO.

## Stack

One repo. One Python package. Two processes that share it. A separate Vite app.

| Piece | Choice |
|-------|--------|
| Python | 3.12, managed by uv |
| API | FastAPI, Pydantic v2, pydantic-settings |
| Jobs | APScheduler inside the engine process |
| DB | SQLite in WAL mode, SQLAlchemy 2, Alembic |
| HTTP | httpx |
| Logs | structlog, JSON to stdout |
| Passwords | argon2 |
| Solana | solana-py and solders, imported only by the Solana adapter |
| Strategies | YAML via PyYAML. A small parser. Never `eval` or `exec`. |
| Inference | OpenAI SDK, Anthropic SDK, and an OpenAI-compatible LM Studio client, each behind the provider port |
| Alerts | `signal-cli` on boblab, account `+14075150936` |
| Web | React, TypeScript (strict), Vite, Tailwind CSS, shadcn/ui, Recharts, TanStack Query, React Router |
| Web package manager | pnpm |
| API port | 8420, also serves the built UI |

Paper mode is the default for every bot and every manual order. Live sends require an explicit flag and the kill switch to be off.

## Layout

```
src/coinwatch/
  main.py            # API entry: python -m coinwatch.api
  api/               # routes, schemas, session dependency
  engine/            # poll loop, scheduler, strategy runner
  services/          # the only place that mutates domain state
  db/                # engine, session, ORM models
  chains/            # ChainAdapter, SolanaPumpAdapter
  inference/         # InferenceProvider and three drivers
  alerts/            # AlertSender, SignalCliSender
  quoting/           # pre-trade quote and safety refusals
web/                 # Vite app
tests/               # pytest
alembic/
data/                # gitignored SQLite file
```

Entrypoints:

- `python -m coinwatch.api`
- `python -m coinwatch.engine`

The API and the engine both call `services`. Routes, the scheduler, and the React app do not contain trading rules.

Import rules:

- `services`, `db`, `quoting`, and `alerts` do not import FastAPI, solders, or a vendor LLM SDK.
- `api` does not import solders or a vendor LLM SDK.
- `chains/solana.py` is the only module that imports solana-py or solders.
- `inference/openai.py`, `inference/anthropic.py`, and `inference/lmstudio.py` are the only modules that import those SDKs.
- `web` talks to the API over HTTP. It does not contain a second copy of permissions, quote math, or P&L.

## Commands

Once the toolchain exists, use only these:

```
make standards-check   # required before any commit
make test
make api
make engine
make web
make migrate
```

`standards-check` runs, in order: `ruff check`, `ruff format --check`, `pyright`, `pytest`, `pnpm exec tsc --noEmit`, `pnpm exec eslint .` in `web/`. Do not add a second lint or format tool. Fix failures. Do not weaken a rule to get a green run.

There is no application code yet. The first implementation change creates this Makefile and the empty package so the command is real.

## Python

- ruff for lint and format. Line length 100. pyright in standard mode on `src/coinwatch`.
- Type hints on public functions. Prefer `X | None` over `Optional`.
- SQLAlchemy models and Pydantic schemas stay separate. Do not return ORM objects from routes.
- Sessions are short. A service opens a session, commits domain row and audit row together, and closes it.
- Settings come from the environment through pydantic-settings. No `os.environ` scattered through services.
- Raise typed errors (`NotAuthorized`, `StaleQuote`, `KillSwitchEngaged`, `QuoteRejected`). The API maps them to HTTP. The engine maps them to a decision row and a log line.

## TypeScript

- `"strict": true`. No `any` unless a comment says why a third-party type is wrong.
- Server state lives in TanStack Query. Do not mirror ticks into a second store.
- Components call the API client. Permission flags from `/me` only hide controls. The server still decides.

## Data and API

- Database file: `data/coinwatch.db`. Enable WAL on connect.
- Schema changes go through Alembic. No startup `create_all` on a database that has migrations.
- JSON API under `/api`. Cookie session, no token in `localStorage`.
- Mutations require header `X-CoinWatch-Request: 1` so a foreign site cannot ride the cookie.
- Error body: `{"error": {"code": "stale_quote", "message": "..."}}`. Codes are stable snake_case. Messages can change.
- WebSocket path is `/api/ws` for tick and trade events. Auth uses the same session.

Permission strings and roles are the ones in the spec. Check them inside the service, before any side effect, including paper orders. A denied check writes an audit event and does not touch the chain.

## Money path

These are not optional later.

- Quote first. A live or paper order carries expected out, minimum out, fee, price impact, and remaining native balance. Refuse if the last tick is older than 15 seconds, a sell simulation fails, the impact exceeds the strategy cap, or the wallet would keep less than the fee reserve (default 0.05 SOL).
- Show market cap and native liquidity as different numbers. Do not label curve liquidity as market cap.
- Record fee and price impact on the trade. Update the position in the same transaction.
- The system user is the actor for engine orders. It cannot log in.
- Private keys, seed phrases, RPC keys, and inference keys stay in `.env` or the process environment. They are not columns, logs, audit JSON, error messages, or API responses.
- Wallet responses return the public address only.
- Inference prompts are not logged unless `LOG_INFERENCE_PROMPTS=true`.

## Logging and audit

- structlog JSON. Every line has `ts`, `level`, `logger`, `event`, and `request_id` when one exists.
- Event names are dotted and lowercase: `order.rejected`, `poll.tick`.
- `audit_events` is append-only. Insert it in the same transaction as the change. Do not update or delete audit rows from app code.
- High-volume "rule did not fire" rows go to `decisions`, not `audit_events`.

## Tests

- pytest. Network is off by default.
- Chain and inference tests use fixtures or fakes. A test that calls a live RPC or a live model is marked `integration` and is not part of `standards-check`.
- Cover quote refusals, permission denials, paper fills updating positions, and audit inserts. Do not assert on log text.
- No secrets in fixtures. Use obviously fake keys.

## Config

Commit `.env.example` with empty values and one-line comments. Never commit `.env`.

Expected names include `COINWATCH_ADMIN_USER`, `COINWATCH_ADMIN_PASSWORD`, `DATABASE_URL`, `SOLANA_RPC_URL`, `SOLANA_HOT_WALLET_KEY`, `INFERENCE_PROVIDER`, `INFERENCE_MODEL_DIGEST`, `INFERENCE_MODEL_ESCALATION`, `INFERENCE_MODEL_REVIEW`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LMSTUDIO_BASE_URL`, `LOG_INFERENCE_PROMPTS`.

The admin password from the environment is a bootstrap secret. Do not log it. Do not put a real one in examples.

## Git

Do not commit `data/`, `.env`, `web/dist/`, `__pycache__`, or `.venv`. Do not skip hooks. Run `make standards-check` and fix it before committing.
