# zerodha-automation

Intraday trading bot for Zerodha (NSE equities, MIS). The strategy is defined by the Pine scripts in
`resources/pine/`; this repository turns them into a backtest engine, a strategy lab, and later the live
services (tick ingest, discovery, signals). The full design and the order of work are in [`docs/PLAN.md`](docs/PLAN.md).

**Status:** foundations and the backtest engine are done; the strategy lab is next. No live service exists yet.

## Layout

```
zt/                 the package and the `zt` command
  core/             clock (IST), NSE holidays, SQLite access, JSON logging with secret redaction
  kite/             daily session, instrument master, historical candles, tick recorder
  candles/          candle model, Heikin Ashi
  strategy/         indicators, Pine v2..v4 entry/exit conditions, bar builder
  backtest/         engine (TradingView execution semantics), presets, charges, metrics, score, filters, store
  migrations/       database schema
tests/unit/         pytest suite
resources/pine/     the strategy as Pine Script, v1..v4 (the specification)
docs/PLAN.md        design, decisions, evidence, phases
data/, logs/        runtime files, not tracked
```

## Setup (macOS)

```sh
brew install sqlite                 # >= 3.51.3 is required; the venv's Python links Homebrew's SQLite
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install
cp .env.example .env                # then fill in KITE_API_KEY and KITE_API_SECRET_TOKEN
.venv/bin/zt migrate
```

On Linux the `pysqlite3-binary` wheel is picked up automatically, but at the time of writing it bundles SQLite 3.51.1
while the runtime guard requires 3.51.3 (multi-process WAL fix). Until the wheel catches up, a Linux host needs a
newer SQLite built from source or the Docker image described in `docs/PLAN.md`. Tests do not need it.

## Daily login

Kite access tokens expire at 06:00 every day and Zerodha requires a manual TOTP login. Once a day:

```sh
zt login                            # prints the login URL
zt login <request_token>            # paste the request_token from the redirect URL
zt status
```

The token is written to `data/session.json` (mode 0600) and never to `.env` or the logs.

## Commands

| Command | What it does |
|---|---|
| `zt migrate` | create or upgrade the database |
| `zt login [request_token]` | print the login URL, or exchange a request token for today's session |
| `zt status` | session, token and database state |
| `zt backtest SYMBOL [--interval 3m,5m] [--rules v2\|v3\|v4\|realistic\|manual] [--compare] [--trades] [--no-charges]` | fetch and cache Kite history, run the engine, print metrics and verdict |
| `zt record --symbols A,B --minutes 30` | record live ticks to `data/ticks/<date>.ndjson` |

`--compare` runs every preset side by side. `--no-charges` reproduces TradingView's zero-cost numbers for
parity checks; the default applies Zerodha's charges and 5 bps slippage.

## Development

```sh
.venv/bin/pytest                    # unit tests
.venv/bin/ruff check . && .venv/bin/ruff format .
.venv/bin/pre-commit run --all-files
```

CI runs the same checks on Linux for Python 3.11 and 3.12.
