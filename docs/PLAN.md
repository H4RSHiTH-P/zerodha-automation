# Zerodha intraday bot: rebuild plan (three services + strategy lab)

## 1. Context

**Why.** The current repo (`src/main`, ~1,650 lines, Python 3.11, kiteconnect 5.0.1) is a single-process script: `trade.py` logs in via a request token pasted on stdin, pulls "active" MidCap/SmallCap tickers from an undocumented TickerTape endpoint, backtests each over 90 days of Kite 3m/5m candles (`Performance.categorize`), subscribes survivors on the websocket, builds 1m candles from ticks on the websocket thread (`Chart.generate_candle`), aggregates 3m/5m by wall-clock polling in a busy loop (`Chart.aggregate_candles`), and logs signals (`Performance.signal`). Nothing is persisted until an xlsx dump at exit. The owner wants three services (ticks → candles → DB; discovery → backtest → subscribe; signal → notification, later automated orders) with no compromise on correctness because real money is at stake.

**What is correct and is reused verbatim:** the indicator maths (`SMA`, `RSI`, `SAR` in `strategy.py`), the Heikin Ashi formula (`Candle.convert`), `Candle.aggregate`, the scoring weights (`_normalize`, `_calculate_score`) and the charge formulas (`performance.py:296-308, 339-351`).

**Defects found (file:line) and where this plan fixes them.** Verified by an adversarial audit (3 refuters per finding); the full list is in §18.

| Defect | Location | Fix |
|---|---|---|
| Entry filled at the signal candle's own open (one-candle lookahead) | `performance.py:270` | realistic fill = next raw open + slippage; manual fill = worse of next open and first-1m close (§8) |
| Stop detected on extremes but filled at that candle's open; checked on high and low regardless of side | `performance.py:283, 332-337` | intrabar stop fill at min/max(stop, open), per side, checked from the entry bar (§8) |
| Second entry on the next candle with no signal (`<= max_pyramiding` with 1) | `performance.py:288` | `max_pyramiding=0`: entries only when flat (§8) |
| Short margin sign error (adds cash on shorts) | `performance.py:311` | margin = amount/leverage both sides (§8) |
| Day counter counts candles, Sharpe mis-scaled | `performance.py:266, 108` | distinct dates; daily Sharpe (§8) |
| Stability compares trade count to day count; shadowed loop variable | `performance.py:143, 153` | calendar blocks with equity carried forward (§8) |
| `AttributeError` when an interval has no trades | `performance.py:459` | `verdict='no_trades'` (§8) |
| `is_invalid` typo | `performance.py:206` | class removed (§14) |
| Backtest on Kite candles, live on tick-built candles | `performance.py:240` | EOD reconciliation report + nightly signal-diff with a per-instrument pause rule (§8, §16) |
| Stop derived from HA close, not fill price | `strategy.py:181, 191` | stop from fill / broker average price (§8, §10) |
| Square-off literals 15:25/15:27 fire after the close and after MIS auto square-off; each matches one interval only | `strategy.py:169, 173, 197` | replaced by the Pine's last-bar rule plus per-instrument cutoffs (§8, §13) |
| Post-stop cooldown (`stop_loss_position`) never synced to broker-side closes | `strategy.py:165-172` | cooldown dropped (not in the Pine); runner state driven by the reconciler (§10) |
| `threshold()` races `evaluate()` across threads | `strategy.py:203` | single engine thread; stop logic in engine/monitor (§10) |
| Mixed percent/ratio units (`variation_percent` holds a ratio) | `strategy.py:177` | `Params.variation`, documented as a fraction (§8) |
| Blocking Kite REST on the reactor thread | `chart.py:59` via `kite.py:55` | `on_ticks` = `put_nowait` only (§7) |
| Class-level dicts shared across threads | `chart.py:20-26` | worker-owned state, DB tables (§7) |
| Late loop skips a 3m/5m slot forever | `chart.py:162` | boundary-driven, 09:15-anchored buckets (§7) |
| Late ticks mutate candles up to 5 minutes back | `chart.py:108-113` | never applied after close; counted (§7) |
| Busy loop, hard-coded past end time | `trade.py:57` | blocking `q.get(timeout)`; no end date (§7) |
| `KeyError` on unknown TickerTape ticker | `trade.py:48` | explicit reject reasons (§8) |
| Access token logged in plaintext | `kite.py:26, 46` | token file 0600 + redaction; purge `logs/` (§13) |
| stdin login, no token persistence | `kite.py:35` | `/start <request_token>` + `zt login` (§13) |
| Import-time `datetime.now()` defaults | `candle.py:21-22` | fields dropped (§14) |
| Handler accumulation per logger call | `logger_util.py` | one JSON handler per process (§13) |
| History window "yesterday" = calendar day (Monday targets Sunday) | `date_util.py:47` | holiday-aware `prev_trading_day` (§8) |

**Real-data evidence (why the backtest must change).** The repo's own `Strategy`/`SMA`/`RSI`/`SAR` classes were run unchanged on 58 trading days of real NSE 5-minute candles (25 Jun to 16 Sep 2026) for the 30 stocks TickerTape listed as "active" on 16 Sep 2026, with the `.env` charge model and thresholds, 5x leverage, 10% of equity per trade. The execution loop was replicated from `performance.py:264-372`, then re-run with executable fills (signal on close → fill at next open; stop at stop level or gap open; one entry per signal; no entries after 14:55; forced exit at the 15:15 open).

| Model | Net-profitable stocks | Pass `.env` filters | Total net P&L |
|---|---|---|---|
| Legacy (current code) | 28 / 30 | 10 / 30 | +11,45,213 |
| Fixed (executable fills) | 5 / 30 | 0 / 30 | -1,52,106 |

Of the 10 stocks the legacy filter selects (PAYTM, WELCORP, ATHERENERG, MCX, CUPID, PINELABS, RAYMOND, NETWEB, IFCI, PCJEWELLER), 7 lose money under executable fills; the other 3 make small profits that fail every threshold. Roughly half of all legacy trades are the automatic add-on entries. Conclusion: the reported edge is the lookahead, not the strategy. The rebuild makes an executable fill model the default gate, expects few or no survivors until the strategy is revised, and therefore puts a strategy lab (§9) between Service 2 and Service 3.

**Owner decisions (16 Sep 2026).**
- Notifications: Telegram bot (WhatsApp later behind the same notifier interface, not designed now).
- Runtime: Mac today; Linux laptop / VPS / cloud VM later. Portable, minimal spend, cost table with a cheaper alternative for every paid item.
- Process model: three processes + shared DB; Redis only if it costs nothing extra (the design needs none).
- Backtest: fix all four execution defects; realistic fills are the default, legacy fills kept as a switch for comparison.
- Strategy research phase comes before Service 3 (16 Sep); moved ahead of Service 1 as well on 18 Sep: no live service is built until a strategy passes the §9 gate.
- 18 Sep: `src/main` deleted (kept in git history). The Pine scripts in `resources/pine/` (v1 raw candles → v2 Heikin Ashi → v3 relative-volume activity filter → v4 EMA/Supertrend/VWAP/RSI-band/1.5% stop) are the specification; the engine implements TradingView's execution semantics and its parity target is the TradingView Strategy Tester trade list, not the old Python loop. There is no `legacy` fill model.

## 2. Verified facts that constrain the design (web research, 16 Sep 2026)

**Kite Connect account and limits**
- Data (websocket + historical) requires the ₹500/month "Connect" plan per API key; the free "Personal" plan has no market data. The ₹2,000 historical add-on was abolished in Feb 2025.
- Rate limits: quote 1 req/s (≤500 instruments per call), historical 3 req/s, orders 10 req/s, everything else 10 req/s; 400 orders/min, 5,000 orders/day, 25 modifications per order. All caps are per API key / client, shared across our three processes.
- Historical caps per request: `minute` 60 days, `3minute`/`5minute` 100 days (one older staff post says 90; chunk at ≤60 to be safe). Today's candles are written with an unguaranteed delay and Zerodha says the endpoint is not for live strategies. The 09:15 one-minute candle's open comes from the pre-open price, so it never matches the first tick.
- Access token dies at 06:00 the next day; the request token lives a few minutes; one manual browser login per day (TOTP automation is disallowed by Zerodha). A Kite web session coexists with the Kite app session.

**Websocket (KiteTicker, pykiteconnect)**
- Only `full` mode carries `exchange_timestamp` and `last_trade_time` (second precision). After `subscribe` the default mode is `quote`; call `set_mode(full)` immediately and accept that a few early ticks may lack `exchange_timestamp`.
- Ticks are throttled snapshots (1 to 3 per second per liquid stock), not every trade. Zerodha builds its own minute candles from the same snapshot stream, so tick-built candles are close to Kite's; volume differs (Zerodha sums per-tick volume, not `volume_traded` deltas). The `ohlc` block in a tick is day-level, never the current bar.
- `ticker.py` returns naive local-time datetimes: run every process with `TZ=Asia/Kolkata` and attach the zone before persisting.
- All callbacks run on the Twisted reactor thread; blocking there causes 1006 disconnect loops. Subscriptions are re-sent automatically after reconnect. The client's ping/pong dead-connection detection is broken upstream (GitHub #229/#237, open as of 5.2.2), so a silent-dead socket never fires `on_close`: Service 1 needs its own watchdog. The reactor cannot be restarted in-process: one process = one ticker for its lifetime; on a dead feed, close and exit, let the supervisor restart. Call `subscribe`/`set_mode` from other threads only via `reactor.callFromThread`; before the first `on_connect` the socket object is `None`.
- kiteconnect 5.0.1 (installed) → 5.2.2 (15 Sep 2026): additive changes only (`market_protection`, `place_autoslice_order`, `algo_id`, NCO divisor). Upgrade in Phase 0.

**Orders and regulation (matters for §12)**
- Since 1 Apr 2026 every API order must come from a static IP whitelisted in the Kite Connect developer console (Profile → IP Whitelist; up to two IPs; one change per calendar week). Data endpoints and the websocket work from any IP.
- `market_protection` is mandatory for MARKET and SL-M API orders (`-1` = auto band: 2% under ₹100, 1% for ₹100 to 500, 0.5% above). Hard cap 10 orders/second; order slicing ≤10 pieces; iceberg counts as one order.
- Kite Publisher / basket (`POST https://kite.zerodha.com/connect/basket` with `api_key` + `data` JSON, user confirms in Kite) is explicitly manual trading per Zerodha: no static IP, no market protection, no algo tagging. A basket needs an HTML form auto-submitted from a browser; a plain GET returns 403; iOS universal links do not intercept it; the JS popup fails in iOS webviews. The return lands on the app's registered redirect URL with `status` and `request_token`, exactly like the login flow.
- Order updates for orders placed anywhere arrive on the websocket (`on_order_update`); postback URLs are per-app only.
- `validity=TTL` with `validity_ttl` minutes auto-cancels stale entries; `tag` (≤20 alphanumeric) ties orders to signal ids.

**MIS and market mechanics**
- Auto square-off: 15:25 for normal stocks, 15:12 for Closing Auction Session (CAS) stocks (F&O-underlying names, whose continuous trading ends 15:15 since 3 Aug 2026). Charge ₹50 + GST per squared-off order. The client remains responsible; positions stuck at circuits convert to CNC or short delivery (auction penalty, 120% block).
- MIS is blocked for Trade-to-Trade (BE/BZ), GSM/ASM and other flagged names; Zerodha publishes a machine-readable "Stocks allowed for MIS" Google Sheet (id `1XwWNCASDmrXfx5LtFNna0Kmkt5vHtqkjICvVcUZaQhw`, gviz CSV export) with per-stock leverage, and the blocked list changes intraday. Margins should be read from `order_margins`, not assumed 5x (VaR+ELM on mid/small caps often exceeds 20%).
- 2026 intraday charges: brokerage min(₹20, 0.03%) per executed order; STT 0.025% on sell; NSE 0.00307% both sides; SEBI ₹10/crore; stamp 0.003% on buy; GST 18% on brokerage + exchange + SEBI. Keep them in config; verify the `.env` values against these.
- NSE 2026 weekday holidays remaining after today: 02 Oct, 20 Oct, 10 Nov, 24 Nov, 25 Dec (Muhurat 08 Nov is a Sunday). NSE's holiday JSON needs browser headers and its terms forbid automated collection: ship the list in code.

**Discovery source**
- TickerTape `https://analyze.api.tickertape.in/homepage/stocks`: `universe` ∈ {LargeCap, MidCap, SmallCap} (anything else → 400), `type` ∈ {active, gainers, losers}, `count` up to the whole universe (100/100/500), `offset` does not paginate, no auth or headers needed, served via CloudFront, real-time turnover in `active` only. Item fields: `sid, name, slug, sector, ticker, marketCap, price, change, turnover`.
- Mapping pitfalls: TickerTape strips NSE series suffixes (SME names exist on Kite only as `-SM`), lists BSE-only stocks, and companies get renamed (ZOMATO → ETERNAL). Kite's instrument CSVs are public (`https://api.kite.trade/instruments/NSE`, `/NFO`, `/BFO`, no auth); key by `exchange + tradingsymbol`, refresh daily at 08:30.

**Notification channel**
- Telegram Bot API: free, `sendMessage` with inline keyboard (callback data ≤64 bytes), `answerCallbackQuery` must be called on every button press, `editMessageText` has no time window for bot messages, long-poll `getUpdates` (no inbound port), ≤1 message/second per chat. Deep link `t.me/<bot>?start=<param>` carries ≤64 chars.
- ntfy.sh (free, public topics) and Pushover (₹450 one-time, priority-2 nag-until-ack) are the "must act" fallbacks.

**Storage on this machine**
- SQLite WAL with several processes needs SQLite ≥ 3.51.3 (WAL-reset corruption bug fixed 13 Mar 2026). This Mac's venv Python (Homebrew python@3.11) links Homebrew SQLite **3.50.4** → `brew upgrade sqlite` (current formula ≥3.53) and re-check; if the venv still reports < 3.51.3, rebuild it on Homebrew `python@3.12`. Assert the version at startup. SQLite WAL must not live on a Docker Desktop macOS bind mount or a network filesystem.
- Already installed here: Docker 28, PostgreSQL 16 (Homebrew). Redis is not installed. Redis pub/sub is at-most-once; Streams are at-least-once; neither is needed on one host.

## 3. Decisions

| Decision | Choice | Reason |
|---|---|---|
| DB | One SQLite file (`data/zt.db`), WAL, local disk; startup guard `sqlite_version ≥ 3.51.3`; 17 tables | one main writer plus light writers on one host is SQLite's sweet spot; versions < 3.51.3 corrupt under exactly this pattern and the venv has 3.50.4. Switch to PostgreSQL 16 (already installed) only if a service ever moves to another machine |
| Bus | The DB: `candle_events` outbox + `PRAGMA data_version` polling (1 s in Service 3, 2 s in Service 1) | rows are durable, replayable and exactly-once-able; Redis adds a server for no benefit on one host; candle cadence is minutes |
| Process model | `zt ingest`, `zt discover`, `zt signal`: three supervised processes; ingest re-executes itself daily after EOD | reactor cannot restart and the token changes daily; `os.execv` gives a fresh reactor with no supervisor tricks |
| Fill model | one knob `FILL_MODEL ∈ {realistic, manual}`, default `realistic`; recommended `manual` while orders are placed by hand; every card prints the model | owner: explicit and switchable, realistic default |
| Notification | Telegram via a `Notifier` protocol; ntfy.sh only for Sev-1 escalation with symbol and reason, never quantities | owner's choice; ntfy is free and ~30 lines |
| Discovery source | TickerTape `active` (MidCap, SmallCap by default, configurable), validated; on failure keep the last good snapshot and report | owner's choice; a failed discovery means no new trades, not a loss, so a second discovery engine protects nothing |
| Time source | `exchange_timestamp` for bucketing; host clock (NTP-checked at start) for boundaries; `TZ=Asia/Kolkata` asserted; skew measured both ways | KiteTicker returns naive local datetimes with second precision |
| Config/secrets | `.env` keeps existing key names (incl. `KITE_API_SECRET_TOKEN`) for money/time parameters; operational knobs are code constants; daily token only in `data/session.json` (0600); log redaction filter | `KITE_API_ACCESS_TOKEN` currently sits in `.env` and in three `logs/session_*` directories |
| Supervision | launchd LaunchAgents (Mac) / systemd (Linux) / Docker Compose optional; exit 3 = restart, 78 = misconfiguration (no restart), 0 only on operator stop | portable, restart-on-crash |
| Login | static page in a separate one-file public GitHub Pages repo as the app's redirect URL: login redirect (`redirect_params=flow=login`) → Telegram deep link `/start <request_token>`; CLI paste fallback | manual TOTP login is mandated; works from the phone, zero servers |
| Orders (manual phase) | Telegram card + optional one-tap basket page; basket entry validity **IOC** (fallback TTL 2 min, never DAY); positions and orders read only from Kite (`product='MIS'`); reconciler enforces order-book hygiene | Publisher/basket is manual per Zerodha; resting DAY limits are the largest money leak in a manual loop |
| Orders (future) | kiteconnect 5.2.2, static IP, LIMIT-first executor with SL-M at the broker, priority order budget | market_protection mandatory since 1 Apr 2026; 5.0.1 cannot send it |
| Kite REST budget | discover: historical 2 req/s, quote 1 req/s; ingest: historical 1 req/s (recovery backfill only); signal: 2 req/s with a 3 s deadline per ENTRY | the 3 req/s historical cap is per key across processes |
| Historical chunking | ≤60 days per call for every interval | removes the 90-vs-100-day ambiguity for one extra call |
| Empty-minute policy | no 1m row when no trade tick; 3m/5m built from existing 1m rows; `gap_minutes` informational; `degraded=1` only for outage windows, skew windows, late-tick breaches or partial first minutes | Kite history omits empty minutes, so a 2-of-3-minute bar is a normal backtest input; blocking it live would silently diverge from the gate |
| Candle immutability | rows are written once, at the boundary, complete; no in-progress persistence | Service 3's audit trail must equal what it acted on |
| Kite reconciliation | EOD only (`zt report` at 15:45, one 1m call per token) + nightly signal-diff; no intraday reconciliation table | Kite candles come from the same snapshot stream and are not ground truth |
| Package | `zt/` with console script `zt`; `src/main` deleted in Phase 0 (18 Sep), Pine scripts kept in `resources/pine/` | one entrypoint, verifiable steps |

## 4. Architecture

```
                         ┌──────────────── data/zt.db (SQLite WAL) ─────────────────┐
                         │ instruments watchlist candles candle_events quotes history│
                         │ discovery_snapshots backtest_runs backtest_trades signals │
                         │ outbox positions broker_orders heartbeats kv reports      │
                         └──▲────────────▲─────────────────▲─────────────────────────┘
 Kite WS (full mode)        │            │                 │
 ┌──────────────────────────┴──┐  ┌──────┴───────────┐  ┌──┴────────────────────────┐
 │ zt ingest  (Service 1)      │  │ zt discover (S2) │  │ zt signal  (Service 3)    │
 │ reactor thread: on_ticks →  │  │ single thread    │  │ engine thread (1 Hz)      │
 │   q.put_nowait only         │  │ 08:30 refdata +  │  │   runners, gates, cards   │
 │ worker thread (main):       │  │   corp-action +  │  │ poller thread (getUpdates)│
 │   builder/aggregator/HA,    │  │   carried-pos chk│  │ sender thread (outbox)    │
 │   boundary commit, quotes,  │  │ 09:30..13:30 +   │  │ reconciler (5 s / 15 s)   │
 │   watchlist diff, backfill, │  │   /discover:     │  │   positions+orders MIS    │
 │   watchdog, tick recorder,  │  │   discover→map→  │  │ position monitor (1 s)    │
 │   checkpoint                │  │   backtest→score │  │ (executor: future)        │
 │ execv daily 16:00           │  │ 15:45 EOD+report │  │                           │
 └─────────────────────────────┘  └──────────────────┘  └──────────┬────────────────┘
   data/ticks/YYYY-MM-DD.ndjson(.gz)   data/session.json (0600)     Telegram Bot API (+ ntfy Sev-1)
                                        <pages-repo>/kite.html (login redirect + basket POST)
```

**Data flow.** Ticks → S1 worker → complete raw+HA rows + `candle_events` at each boundary, `quotes` upserted ≤1/s → S3 wakes on `data_version`, consumes `candle_events` with `seq > watermark`, runs indicators + `Strategy`, inserts `signals` + `outbox` + advances `kv.watermark` in one transaction → sender delivers by priority, poller edits cards on callbacks → reconciler polls Kite `positions()/orders()/order_trades()` (product MIS) into `positions`/`broker_orders` → those drive STOP/EXIT/DEADLINE and hygiene cards. S2 writes `watchlist`; S1 subscribes; S3 warm-starts a runner.

**Tables used as queues**

| Producer → consumer | Table | Cursor / ack |
|---|---|---|
| S1 → S3 candle closed | `candle_events` (`seq` AUTOINCREMENT) | `kv['signal.watermark']` advanced in the same transaction as the `signals` insert |
| S2 → S1, S3 subscribe/unsubscribe | `watchlist.status` | S1 in-memory `subscribed` set diffed every 2 s; S3 `runners` dict |
| S3 → Telegram | `outbox` (`queued → sending → sent|dropped`, priority) | `attempt_id` committed before the HTTP call; `message_ref` after |
| S3 → S1 pause | `watchlist.status='paused'` | S1 keeps the subscription, S3 stops ENTRY |
| all → S3 health | `heartbeats` | age check every 5 s |

**Contracts.** `candle_events` row: `{"seq":18231,"instrument_token":408065,"interval":"3m","ts":1789531380,"closed_at":1789531562}`. Candle row (raw and `ha` share the PK except `kind`; every row complete): `{"instrument_token":408065,"interval":"3m","kind":"raw","ts":1789531380,"open":…,"high":…,"low":…,"close":…,"volume":18420,"tick_count":47,"source":"tick","gap_minutes":1,"degraded":0,"late_ticks":0,"closed_at":1789531562}`. `signals.basis_json` records exactly what the decision saw: raw and HA candle, SMA/RSI/SAR, quote (ltp, bid, ask, circuit limits, age, spread), strategy params, fill model, backtest run id, sizing (day-start equity, leverage, qty, margin, available) and gate values. Telegram `callback_data`: `a|<signal_id>` Placed, `s|<signal_id>` Skip, `f|<position_id>` Flat, `p|<position_id>` stop placed. Kite order `tag` (≤20 chars): `Z{E|S|X}{token}{HHMM}{child}`, e.g. `ZE40806512330`. Tick log line (top of book only): `{"r":recv_ts,"t":token,"m":mode,"x":exchange_ts,"lt":last_trade_ts,"p":ltp,"v":volume_traded,"q":last_qty,"b1":[bid,qty],"a1":[ask,qty],"lc":lower_circuit,"uc":upper_circuit}`.

## 5. Data model

Timestamps are epoch seconds; `ts` = bucket start, as Kite labels candles. Every connection: `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=10000; foreign_keys=ON`; writers use `BEGIN IMMEDIATE`; one connection per thread. Checkpoint ownership: `wal_autocheckpoint=0` everywhere; ingest runs `wal_checkpoint(PASSIVE)` every 60 s in every state; discover runs `wal_checkpoint(TRUNCATE)` after bulk jobs when ingest is not streaming; ingest reports `wal_mb` in its heartbeat. S2 bulk inserts are chunked at 2,000 rows so S1's boundary commit never waits.

```sql
-- zt/migrations/001_init.sql
CREATE TABLE schema_version(v INTEGER NOT NULL);

CREATE TABLE instruments(
  instrument_token INTEGER PRIMARY KEY, tradingsymbol TEXT NOT NULL, name TEXT, exchange TEXT NOT NULL,
  series TEXT NOT NULL DEFAULT 'EQ', tick_size REAL NOT NULL, lot_size INTEGER NOT NULL,
  mis_allowed INTEGER NOT NULL DEFAULT 0, mis_leverage REAL, is_cas INTEGER NOT NULL DEFAULT 0,
  corp_action_date TEXT, tt_sid TEXT, as_of TEXT NOT NULL);
CREATE UNIQUE INDEX ix_instruments_sym ON instruments(exchange, tradingsymbol);

CREATE TABLE watchlist(                       -- S2 -> S1/S3; one row per instrument per day
  instrument_token INTEGER NOT NULL, trade_date TEXT NOT NULL,
  interval TEXT NOT NULL CHECK(interval IN ('3m','5m')),
  status TEXT NOT NULL CHECK(status IN ('active','paused','closed')),
  backtest_run_id INTEGER NOT NULL, leverage REAL NOT NULL, is_cas INTEGER NOT NULL, band_pct REAL,
  added_at INTEGER NOT NULL, closed_at INTEGER, reason TEXT,
  PRIMARY KEY(instrument_token, trade_date));

CREATE TABLE candles(                         -- live, complete rows only; today + 15 days
  instrument_token INTEGER NOT NULL, interval TEXT NOT NULL CHECK(interval IN ('1m','3m','5m')),
  kind TEXT NOT NULL CHECK(kind IN ('raw','ha')), ts INTEGER NOT NULL,
  open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
  volume INTEGER NOT NULL DEFAULT 0, tick_count INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL CHECK(source IN ('tick','kite','partial')),   -- aggregate: partial > kite > tick
  gap_minutes INTEGER NOT NULL DEFAULT 0, degraded INTEGER NOT NULL DEFAULT 0, late_ticks INTEGER NOT NULL DEFAULT 0,
  closed_at INTEGER NOT NULL,
  PRIMARY KEY(instrument_token, interval, kind, ts)) WITHOUT ROWID;

CREATE TABLE candle_events(                   -- S1 -> S3 outbox, one row per completed raw candle
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument_token INTEGER NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL, closed_at INTEGER NOT NULL,
  UNIQUE(instrument_token, interval, ts));

CREATE TABLE quotes(                          -- latest tick per instrument, upserted <= 1/s
  instrument_token INTEGER PRIMARY KEY, exchange_ts INTEGER, recv_ts INTEGER NOT NULL,
  last_price REAL NOT NULL, volume_traded INTEGER, bid REAL, bid_qty INTEGER, ask REAL, ask_qty INTEGER,
  lower_circuit REAL, upper_circuit REAL);

CREATE TABLE history(                         -- Kite candles ending yesterday; raw + canonical HA chain
  instrument_token INTEGER NOT NULL, interval TEXT NOT NULL CHECK(interval IN ('1m','3m','5m')),
  kind TEXT NOT NULL CHECK(kind IN ('raw','ha')),   -- 1m is raw only (manual fill model)
  ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume INTEGER,
  PRIMARY KEY(instrument_token, interval, kind, ts)) WITHOUT ROWID;

CREATE TABLE discovery_snapshots(id INTEGER PRIMARY KEY, run_at INTEGER NOT NULL, source TEXT NOT NULL,
  ok INTEGER NOT NULL, candidates_json TEXT NOT NULL, rejects_json TEXT NOT NULL, error TEXT);

CREATE TABLE backtest_runs(
  id INTEGER PRIMARY KEY, instrument_token INTEGER NOT NULL, tradingsymbol TEXT NOT NULL, interval TEXT NOT NULL,
  fill_model TEXT NOT NULL CHECK(fill_model IN ('legacy','realistic','manual')),
  window_from INTEGER NOT NULL, window_to INTEGER NOT NULL, params_hash TEXT NOT NULL, code_version TEXT NOT NULL,
  candles INTEGER, trading_days INTEGER, metrics_json TEXT, score REAL,
  verdict TEXT NOT NULL CHECK(verdict IN ('pass','fail','no_trades','error')), reasons_json TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE(instrument_token, interval, fill_model, window_from, window_to, params_hash, code_version));

CREATE TABLE backtest_trades(run_id INTEGER NOT NULL, n INTEGER NOT NULL, side INTEGER NOT NULL, qty INTEGER NOT NULL,
  signal_ts INTEGER NOT NULL, entry_ts INTEGER NOT NULL, entry_price REAL NOT NULL, stop_price REAL NOT NULL,
  exit_ts INTEGER NOT NULL, exit_price REAL NOT NULL, exit_reason TEXT NOT NULL, charges REAL NOT NULL, pnl REAL NOT NULL,
  PRIMARY KEY(run_id, n)) WITHOUT ROWID;

CREATE TABLE signals(
  id TEXT PRIMARY KEY,                        -- '{token}-{interval}-{candle_ts}-{kind}' | 'STOP-{position_id}' | 'DEADLINE-{position_id}'
  instrument_token INTEGER NOT NULL, tradingsymbol TEXT NOT NULL, interval TEXT NOT NULL, candle_ts INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('ENTRY','EXIT','STOP','DEADLINE')), side INTEGER NOT NULL,
  ref_price REAL NOT NULL, limit_price REAL, chase_limit REAL, stop_price REAL, qty INTEGER NOT NULL, margin REAL,
  status TEXT NOT NULL CHECK(status IN ('new','sent','acked','resting','rejected','ack_unconfirmed','filled','skipped','expired','undelivered')),
  basis_json TEXT NOT NULL, position_id INTEGER, outbox_id INTEGER, broker_order_id TEXT,
  created_at INTEGER NOT NULL, expires_at INTEGER, acked_at INTEGER, filled_at INTEGER, note TEXT,
  UNIQUE(instrument_token, interval, candle_ts, kind));                                      -- exactly-once key
CREATE INDEX ix_signals_status ON signals(status, created_at);

CREATE TABLE outbox(                          -- every Telegram message: cards, reminders, alerts
  id INTEGER PRIMARY KEY, priority INTEGER NOT NULL,           -- 0 exit-class/Sev-1, 1 entry, 2 alert, 3 info
  kind TEXT NOT NULL, ref TEXT, text TEXT NOT NULL, buttons_json TEXT, loud INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','sending','sent','dropped')), attempts INTEGER NOT NULL DEFAULT 0,
  attempt_id TEXT, expires_at INTEGER, message_ref TEXT, created_at INTEGER NOT NULL, sent_at INTEGER, last_error TEXT);
CREATE INDEX ix_outbox_queue ON outbox(status, priority, id);

CREATE TABLE positions(                       -- only from Kite (manual phase); product MIS only
  id INTEGER PRIMARY KEY, instrument_token INTEGER NOT NULL, tradingsymbol TEXT NOT NULL, trade_date TEXT NOT NULL,
  product TEXT NOT NULL DEFAULT 'MIS', side INTEGER NOT NULL, qty INTEGER NOT NULL, avg_entry REAL NOT NULL,
  stop_price REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','closing','closed')),
  source TEXT NOT NULL CHECK(source IN ('kite','adopted')),
  entry_signal_id TEXT, exit_signal_id TEXT, broker_stop_order_id TEXT, broker_stop_qty INTEGER,
  exit_reason TEXT CHECK(exit_reason IN ('EXIT','DEADLINE','STOP')), exit_card_ref TEXT, exit_repeats INTEGER NOT NULL DEFAULT 0,
  stop_card_ref TEXT, opened_at INTEGER NOT NULL, closed_at INTEGER, avg_exit REAL, realized_pnl REAL,
  UNIQUE(tradingsymbol, trade_date, opened_at));

CREATE TABLE broker_orders(                   -- mirror of orders() for MIS orders in watched/tracked symbols
  order_id TEXT PRIMARY KEY, tradingsymbol TEXT NOT NULL, transaction_type TEXT NOT NULL, order_type TEXT NOT NULL,
  product TEXT NOT NULL, status TEXT NOT NULL, status_message TEXT, qty INTEGER NOT NULL, filled_qty INTEGER NOT NULL,
  price REAL, trigger_price REAL, tag TEXT, placed_at INTEGER NOT NULL, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  role TEXT CHECK(role IN ('ENTRY','STOP','EXIT','UNKNOWN')), signal_id TEXT, hygiene_card_ref TEXT);
CREATE INDEX ix_broker_orders_open ON broker_orders(status, tradingsymbol);

CREATE TABLE heartbeats(service TEXT PRIMARY KEY, ts INTEGER NOT NULL, state TEXT NOT NULL, details_json TEXT);
CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at INTEGER NOT NULL);
-- kv keys: signal.watermark, session_valid, session_generation, halted, away_until, self_exit_override,
--          day_start_equity, day_realized_pnl, telegram.offset, blind_since
CREATE TABLE reports(date TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('recon','signal_diff','slippage','discovery','lab')),
  json TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(date, kind));
```

`002_execution.sql` (Phase 6, future) adds `orders(client_tag PK, kite_order_id UNIQUE, signal_id, position_id, role, child, variety, order_type, transaction_type, qty, price, trigger_price, validity, validity_ttl, local_state, kite_status, filled_qty, avg_price, modifications, paper, submit_attempted_at, submit_confirmed_at, last_update_at, status_message)`, `order_events(id, client_tag, kite_order_id, ts, source IN ('ws','poll','place','modify','cancel','trades'), kite_status, payload_json)`, `fills(trade_id PK, client_tag, ts, qty, price)`, plus `signals.status 'done'`, `positions.source 'paper','auto'`, `quotes` depth columns and `kv.exec_stage`.

**Idempotency keys:** `candles` PK; `candle_events UNIQUE(token, interval, ts)`; `signals UNIQUE(token, interval, candle_ts, kind)` plus `kv.signal.watermark` in the same transaction; `outbox.attempt_id` committed before the HTTP call; `backtest_runs UNIQUE(…, params_hash, code_version)`; `watchlist` PK per day; `positions` matched to the broker by `(tradingsymbol, trade_date, product='MIS')`; `broker_orders.order_id` PK; `orders.client_tag` PK (future); `fills.trade_id` PK.

**Files outside the DB:** `data/session.json` (0600, atomic write: `{"date","access_token","user_id","login_at","generation"}`); `data/ticks/YYYY-MM-DD.ndjson` append-flushed every second, gzipped at 16:00, 15-day retention (~10 MB/day for 20 instruments); `logs/<service>.jsonl` rotated daily, 14 days; nightly `VACUUM INTO data/backup/zt-YYYY-MM-DD.db` (7 kept). Holidays live in code (`zt/core/holidays.py`).

**Sizes:** 20 instruments × 375 min × 3 intervals × 2 kinds ≈ 45k candle rows/day (~4 MB), pruned to 15 days; history 50 candidates × (3m+5m, raw+HA) × 90 days ≈ 1.3 M rows (~100 MB) plus 1m raw for names that reach the manual stage; pruned to instruments seen in 30 days.

## 6. Phase 0: Foundations and goldens (no behaviour change yet)

**Target layout**
```
zerodha-automation/
  pyproject.toml
  zt/
    cli.py  config.py
    core/      clock.py  holidays.py  db.py  log.py  ratelimit.py
    kite/      session.py  ticker.py  history.py  refdata.py
    candles/   model.py  builder.py  aggregator.py  heikin_ashi.py  recorder.py
    strategy/  indicators.py  rules.py  runner.py
    backtest/  engine.py  rules.py  charges.py  metrics.py  filters.py  score.py
    discovery/ tickertape.py  mapping.py
    notify/    base.py  telegram.py  ntfy.py  cards.py
    execution/ reconcile.py  monitor.py   (future: state.py gateway.py ladder.py risk.py paper.py executor.py)
    services/  ingest.py  discover.py  signal.py
    lab/       sweep.py  walkforward.py  report.py
    migrations/ 001_init.sql  (002_execution.sql future)
  deploy/launchd/*.plist  deploy/systemd/*.service  deploy/docker-compose.yml  deploy/Dockerfile
  tests/unit  tests/golden  tests/fixtures/{ticks,history}
  docs/runbook.md
  data/ (gitignored): zt.db  session.json  ticks/  backup/     logs/
<separate one-file public repo, GitHub Pages>: kite.html   # login redirect (flow=login) + single-use basket POST
```

**Steps**
- **0.1 Parity fixture (replaces the legacy goldens).** Export the TradingView Strategy Tester "List of Trades" for `scalping_strategy_v2.pine` on 2 symbols × 3m/5m over the same 90-day window as the Kite fixture (`tests/fixtures/tradingview/`), plus the Kite candles for those windows (`tests/fixtures/history/`). Verify: `engine.run` with the `v2` preset, zero slippage and zero charges reproduces the same entry/exit bars and prices except where TradingView's and Kite's candles differ (report the differing bars). Record 30 minutes of live ticks with `zt record`.
- **0.2 Environment.** `brew upgrade sqlite`; confirm `python -c "import sqlite3; print(sqlite3.sqlite_version)"` ≥ 3.51.3 in the venv (else rebuild the venv on Homebrew `python@3.12`); `pip install kiteconnect==5.2.2`; create `pyproject.toml` (package `zt`, console script `zt`; deps `kiteconnect==5.2.2`, `requests>=2.32`, `python-dotenv>=1.0`, `numpy>=1.26`, `pysqlite3-binary; sys_platform=="linux"`; optional extra `[export]` pandas+openpyxl; dev pytest, hypothesis). Drop `python-dateutil` (zoneinfo).
- **0.3 Skeleton.** `config.py` (frozen dataclass from env; existing key names kept; `KITE_API_ACCESS_TOKEN` removed), `core/db.py` (version guard → exit 78, migrations, WAL pragmas), `core/log.py` (one JSON handler per process + `RedactFilter`), `core/clock.py` + `core/holidays.py` (IST, trading-day helpers, `session_end/last_entry/self_exit/square_off(is_cas)`), NTP check, `core/ratelimit.py` (token buckets per process), `kite/session.py`, `zt login|status|migrate`. Delete the three `logs/session_*` directories (they contain access tokens). Verify: `pytest` (tz assert, redaction, idempotent migrations, guard fails on 3.50.4); `zt login <token>` writes a 0600 file; `grep -r <token> logs/` is empty.
- **0.4 Move the verbatim pieces.** `SMA/RSI/SAR` → `zt/strategy/indicators.py`; `Candle.convert/aggregate` → `zt/candles/heikin_ashi.py`, `zt/candles/model.py` (drop `created_on/updated_on`, add `volume, tick_count`); the entry/exit conditions → pure functions in `zt/strategy/rules.py` transcribed from the Pine (v3/v4 filters as `Params` switches, default off = v2); charge blocks → `zt/backtest/charges.py::round_trip()`. Verify: indicator parity against independent references (SMA/HA 1e-9, RSI 1e-6 Wilder, EMA/ATR/Supertrend/VWAP per the Pine reference implementations); `round_trip` equals the 2026 Zerodha formula on 200 random inputs.

## 7. Phase 1: Service 1, ticks to candles (`zt ingest`)

**Modules:** `zt/kite/ticker.py` (TickerBridge), `zt/candles/builder.py` (MinuteBuilder), `zt/candles/aggregator.py`, `zt/candles/heikin_ashi.py`, `zt/candles/recorder.py`, `zt/kite/history.py`, `zt/services/ingest.py`.

**Reactor boundary**
```python
class TickerBridge:
    def __init__(self, api_key, token, q, liveness):
        self.q = q  # queue.Queue() UNBOUNDED: a drop would silently corrupt a candle
        self.kws = KiteTicker(api_key, token, reconnect=True, reconnect_max_tries=300, reconnect_max_delay=30)
        self.pending, self.connected = set(), False  # tokens requested before the socket is open
        self.kws.on_ticks = lambda ws, ticks: self.q.put_nowait(("ticks", time.time(), ticks))  # ONLY this
        self.kws.on_message = lambda ws, payload, is_binary: liveness.touch()  # heartbeats land here
        self.kws.on_connect = self._on_connect  # first open: subscribe the pending union + set_mode(FULL)
        self.kws.on_close = lambda *a: None  # never stop() here
        self.kws.on_error = lambda ws, code, reason: self.q.put_nowait(("error", time.time(), (code, reason)))
        self.kws.on_reconnect = lambda ws, n: self.q.put_nowait(("reconnect", time.time(), n))
        self.kws.on_noreconnect = lambda ws: liveness.fatal("noreconnect")  # -> exit 3
        self.kws.on_order_update = lambda ws, data: liveness.touch()  # order state comes from orders() polling in S3

    def subscribe(self, tokens) -> bool:  # called from the worker thread
        self.pending |= set(tokens)
        if not self.connected:
            return False  # worker retries on its next 2 s tick
        reactor.callFromThread(self.kws.subscribe, list(tokens))
        reactor.callFromThread(self.kws.set_mode, self.kws.MODE_FULL, list(tokens))
        return True
```
One `KiteTicker` per process lifetime; `close()` for intentional disconnects; `stop()` never (the process re-executes instead). Queue depth is reported in the heartbeat; > 5,000 logs `ingest.backlog`, > 50,000 exits 3.

**Watchdog.** Worker checks every 5 s: during 09:00–15:35 on a trading day, `now − liveness.last_message > 20 s` ⇒ log `ws.silent`, heartbeat `state=restarting`, open an outage window, `kws.close()`, `os._exit(3)`. Supervisor restarts in ≤10 s; recovery backfills and closes the window.

**Worker loop** (main thread, owns the DB connection and all candle state):
```
next_boundary = ceil_minute(now) + GRACE(2.0 s)
loop:
  item = q.get(timeout=max(0, next_boundary - now))  (Empty -> None)
  if item: dispatch(item)                # ticks -> builder + recorder buffer; reconnect/error -> outage window
  if now >= next_boundary: on_boundary(next_boundary - GRACE); next_boundary += 60     # the ONLY candle write
  every 1 s: upsert quotes (dirty tokens), append-flush the tick log, skew sample
  every 2 s (on data_version change): apply_watchlist()
  every 5 s: heartbeat, watchdog; every 60 s: wal_checkpoint(PASSIVE), in every state
```

**Candle builder (per tick):**
1. Append the tick to the recorder buffer (all ticks, all modes).
2. If `mode != 'full'` or `exchange_timestamp` missing → `counters.no_ets++`, update `quotes`, stop.
3. `ets = epoch(exchange_timestamp)` (naive local → Asia/Kolkata); `m = ets − ets % 60`; push `recv − ets` into the skew sample.
4. If `m < 09:15:00` or `m ≥ session_end(token)` (15:30, or 15:15 for `is_cas=1`) → quotes only.
5. Trade detection: `trade = volume_traded > prev_vol or last_trade_time > prev_ltt`; `vol_delta = max(0, volume_traded − prev_vol)`; the first tick after (re)subscribe sets the baseline and is not a trade.
6. Upsert `quotes[token]` (LTP, top of book, circuit limits), always.
7. If not a trade → stop (depth-only ticks never move OHLC; the tick's `ohlc` block is day-level and never used).
8. If `m < last_closed_minute[token]` → `late_ticks++` in the current 30-minute window, stop. **A closed minute is never modified.** More than 20 late ticks per instrument per 30-minute window ⇒ candles of that instrument closing in the rest of the window are `degraded=1`.
9. `bucket = open[token].setdefault(m, Bucket(lp))`; `source='partial'` if `m == subscribe_minute`; the 09:15 bucket's open is seeded from `tick['ohlc']['open']` (the pre-open price Kite uses); update H/L/C, `volume += vol_delta`, `tick_count++`.

**Clock skew.** Startup NTP check (`sntp -sS time.apple.com` on macOS, `chronyc tracking`/`timedatectl show` on Linux); |offset| > 1 s ⇒ exit 78. Live: rolling median of `recv − ets` per minute; outside `[−1, +3]` s for 3 consecutive minutes ⇒ log `clock.skew`, widen GRACE to 4 s, mark candles closed while out of range `degraded=1`.

**Boundary `on_boundary(B)`, one timer for all instruments:**
- Close every open 1m bucket with `m + 60 ≤ B`: raw row (`closed_at=now`); HA row via `Candle.convert(raw, *chain[token]['1m'])`; `candle_events` row. A minute with no trade ticks produces no row. `degraded=1` if the minute overlaps an outage window, a skew window, a late-tick breach, or `source='partial'`.
- For N ∈ {3, 5}: every bucket start `S = 09:15 + k·N min` with `S + N·60 ≤ B` not yet finalized: rows = 1m raw rows in `[S, S+N·60)`; none → nothing; else `Candle.aggregate(rows)` (plus volume/tick sums), `gap_minutes = N − len(rows)`, `source = partial > kite > tick` over components, `degraded = any(component degraded) or any missing minute inside an outage/skew window`; HA from the N-minute chain; raw+HA rows; `candle_events` row. Anchoring at 09:15 means a late worker never skips a slot. Session end (non-CAS: last 3m 15:27, last 5m 15:25; CAS: 15:12 / 15:10) is a normal boundary.
- All rows of one boundary commit in one `BEGIN IMMEDIATE` so S3 sees raw, HA and event atomically. There is no other candle write path.

**Outage windows.** Opened by `reconnect`/`error` items, the watchdog, and process start (from the last persisted `closed_at`); closed by the first trade tick after (re)connect. Missing minutes inside a window ⇒ `degraded`; outside ⇒ genuine no-trade minutes (`gap_minutes` only).

**Heikin Ashi seeding.** `history(kind='ha')` rows are canonical: S2 computes them once with `Candle.convert` when raw rows are first fetched and extends from the last stored HA row on every tail fetch; `engine.run`, S1's seed and S3's warm start read those rows and never recompute from a different start. Chain per (token, interval): last `history(kind='ha')` row before today → today's HA rows in `candles` → live. The 1m chain restarts daily and its HA rows are display-only (no signal uses 1m HA).

**Recovery (start, restart, new subscription).** For each active token: `bridge.subscribe` first (ticks flow into the current minute as `partial`), then backfill at ≤1 req/s: `historical_data(token, 09:15, now − 2 min, 'minute')` → 1m rows `source='kite'` only where no row exists (Kite's latest 1–2 bars are mutable); recompute 3m/5m only for completed buckets without rows; re-seed HA; emit `candle_events` in `ts` order so S3 advances indicator state. S3's ENTRY gate rejects `source ≠ 'tick'`, `degraded` and stale candles, so backfill can never trigger an entry.

**Subscribe/unsubscribe.** `watchlist` is the only interface: every 2 s (on `data_version` change) diff `rows WHERE trade_date=today AND status IN ('active','paused')` against the subscribed set; add → subscribe + backfill; `closed` → unsubscribe (S2 sets `closed` at 15:45 only; S3 refuses to close a row with an open position). `MAX_WATCHLIST=20`; S1 refuses > 100.

**Daily lifecycle.** Start → `waiting_token` until `session.json.date == today` and `profile()` OK (checked every 15 s; re-read on `kv.session_generation` change) → 09:00 create the single `KiteTicker`, connect → stream → 15:35 `close()`, final boundary → gzip tick log, prune → 16:00 `os.execv(sys.executable, sys.argv)`. Exit codes: 3 restart me, 78 misconfiguration, 0 only on SIGTERM. Heartbeat every 5 s with `state ∈ {waiting_token, connecting, streaming, idle, restarting}` and `{subscribed, queue_depth, last_msg_age, late_ticks, reconnects, skew_s, wal_mb}`.

**Reuse:** `Candle.convert`, `Candle.aggregate`, `Candle.create/update` verbatim; `Chart.historic_candle` → `zt/kite/history.py` with the limiter. Dropped: `Chart.generate_candle`, `aggregate_candles`, class dicts, flat-fill logic.

**Verify (step 1 done when):** tick→candle goldens pass (late tick, pre-open, quote-mode tick, silent minute, CAS end of day); one live day on 3 symbols gives continuous 1m/3m/5m rows and `zt report` shows |Δclose| ≤ 20 bps on > 95% of bars; `kill -9` at 11:00 mid-minute ⇒ back in ≤10 s, the crash minute is `source='kite'`, no duplicate `candle_events`; Wi-Fi off 60 s ⇒ watchdog exit 3 ⇒ recovery with degraded bars only inside the outage; subscribe before 09:00 does not crash; 16:00 re-exec observed.

## 8. Phase 2: Service 2, discovery, backtest, selection (`zt discover`)

**Modules:** `zt/discovery/{tickertape,mapping}.py`, `zt/kite/{history,refdata}.py`, `zt/backtest/{engine,rules,charges,metrics,filters,score}.py`, `zt/services/discover.py`.

**08:30 reference data.** `https://api.kite.trade/instruments/{NSE,NFO,BFO}` (no auth) → `instruments`; `series` = suffix after `-` (none = EQ); `is_cas = name ∈ NFO futures ∩ BFO futures`; MIS sheet gviz CSV → `mis_allowed, mis_leverage`, refreshed hourly (a watched name that disappears ⇒ `watchlist.status='paused', reason='mis_blocked'` + card; sheet failure → keep yesterday's values if < 3 days old, else `mis_allowed=0` for all). Non-trading day ⇒ all services idle.

**08:30 corporate-action gate.** For every token with cached `history`: compare the last raw close with `kc.quote()` `ohlc.close` and the instruments-dump `last_price`; |diff| > 5%, or a changed `tradingsymbol`/`name` ⇒ `instruments.corp_action_date = today`, delete that token's `history`, refetch cold; the backtest window starts at `corp_action_date`. Unit test: synthetic 1:2 split.

**08:30 carried-position check.** `positions()['net']` and `holdings()` for yesterday's watchlist symbols; anything non-zero ⇒ loud `POSITION_CARRIED` card.

**Discovery** (09:30 and every 30 min to 13:30, plus `/discover` or `zt discover --now`, ≤1 TickerTape call/min):
1. TickerTape `universe ∈ DISCOVERY_CAPS` (default MidCap, SmallCap), `type=active`, `count=100`/`500`, `offset=0`. Validate HTTP 200, `success==true`, non-empty `data['active']`, each item has `ticker` and numeric `turnover`; anything else = `DiscoveryError`. Re-sort by turnover desc client-side. Timeout 10 s, 2 retries. Store `sid` for cross-day joins.
2. On `DiscoveryError`: today's watchlist is unchanged, the last good snapshot stands, the summary says so.
3. Universe filters: `price ≥ ₹50`, `turnover ≥ ₹25 cr × min(1, session_fraction/0.25)`, `1% ≤ |pct_change| ≤ 8%`. Top `DISCOVERY_TOP_N=15` per cap bucket → mapping.

**Mapping and validation.** Exact match `instruments(exchange='NSE', tradingsymbol=ticker)`; match only via `-BE/-BZ/-SM/-ST` → reject `t2t_or_sme`; no NSE match → reject `not_on_nse`; `mis_allowed=0` → reject; `band_pct = (upper − lower)/(2 × prev_close) × 100 < 9.5` (from `kc.quote()`) → reject `narrow_band`; `mis_leverage < 2` → reject; `corp_action_date == today` → reject. Leverage = sheet value, verified with one `order_margins(mode='compact')` call for qty 1. The old `KeyError` becomes a `rejects_json` entry.

**Dedupe.** `backtest_runs UNIQUE(token, interval, fill_model, window_from, window_to, params_hash, code_version)`; `params_hash` = sha256 of strategy/charge/session parameters; `code_version` = sha256 of the file contents of `zt/backtest/`, `zt/strategy/`, `zt/candles/heikin_ashi.py`. A name already on today's watchlist is never re-evaluated intraday.

**History window and cache.** `window_to = prev_trading_day(today) 15:30` (holiday-aware); `window_from = max(window_to − PERFORMANCE_HISTORIC_DURATION days @ 09:15, corp_action_date)`. Fetch only the missing tail; chunk every call at ≤60 days; limiter historical 2 req/s, quote 1 req/s; 429/5xx backoff 1/2/4/8 s; 400/403 never retried; `TokenException` ⇒ `kv.session_valid=0` + alert. Store raw and canonical HA. For `is_cas=1` names, rows with `ts ≥ 15:15` are excluded until the day-one CAS dump settles what Kite emits. Today's candles are never written to `history`. Cost: 30 cold candidates × 2 intervals × 2 chunks = 120 calls ≈ 60 s.

**Backtest engine** (`engine.run(raw, rules, equity, leverage, charges, interval_s, m1=None) → BacktestResult`; pure: no I/O, clock or env). Execution follows TradingView's broker emulator, which is what the Pine scripts were tuned on: per bar (1) the order queued at the previous close fills at this bar's open, (2) the resting stop fills intrabar at the stop level or at the open when the bar gaps through it, (3) on the session's last bar the position is closed at that bar's close (`close_all(immediately=true)`), (4) the closed bar is evaluated: an exit is queued for the next open, or, when flat and not on the last bar, an entry. The stop on the fill bar is the provisional level from the signal bar's HA close and is re-anchored to the fill from the next bar, exactly as the Pine's two `strategy.exit` calls do.
```python
@dataclass(frozen=True)
class Params:  # zt/strategy/rules.py: strategy parameters, every v3/v4 filter a switch (default off = v2)
    sma_length = 50
    rsi_length = 15
    sar = (0.02, 0.02, 0.2)
    variation = 0.00075
    stop_pct = 2.5
    rsi_band = 0.0
    activity = False
    activity_sessions = 10
    activity_multiple = 1.5  # v3
    ema = False
    ema_length = 20
    supertrend = False
    st_length = 10
    st_multiplier = 3.0
    vwap = False
    exit_rule = "sar"  # v4


@dataclass(frozen=True)
class Rules:  # zt/backtest/presets.py: execution
    name: str
    params: Params
    fill = "realistic"
    slippage_bps = 5.0
    risk_fraction = 0.10
    last_entry: time | None = None
    self_exit: time | None = None
    sqoff_charge = 0.0


PINE_V2 = Rules("v2", Params(), slippage_bps=0)
PINE_V3 = Rules("v3", Params(activity=True), slippage_bps=0)
PINE_V4 = Rules(
    "v4",
    Params(activity=True, stop_pct=1.5, rsi_band=5, ema=True, supertrend=True, vwap=True, exit_rule="either"),
    slippage_bps=0,
)
REALISTIC = replace(PINE_V2, name="realistic", slippage_bps=5)
MANUAL = replace(REALISTIC, name="manual", fill="manual")
```

| Event | REALISTIC (default; = Pine + slippage + charges) | MANUAL |
|---|---|---|
| ENTRY signalled on *i* | fill `raw[i+1].open × (1 ± 5 bps)`; never on the session's last bar; optional `last_entry` cutoff | worse of `raw[i+1].open` and the close of the 1m candle at `ts(i+1)` (from `m1`), × (1 ± 5 bps) |
| Stop level | fill bar: `ha[i].close × (1 ∓ stop%)`; afterwards `fill × (1 ∓ stop%)` | same |
| Stop check | intrabar from the fill bar: long `low ≤ stop` → `min(stop, open)`; short `high ≥ stop` → `max(stop, open)`; a queued close executes at the open before the stop is checked | same |
| Trend-flip EXIT on *j* (SAR / Supertrend / either) | fill `raw[j+1].open ∓ 5 bps` | worse of `raw[j+1].open` and first-1m close, ∓ 5 bps |
| End of session | last bar: close at its close (Pine); with `self_exit` set: close at the next open after the first bar closing ≥ `self_exit`, and ₹59 if a position still reaches the last bar | same |
| Pyramiding | none (`position_size == 0` gate) | none |
| Sizing | `floor(equity × risk_fraction × leverage / fill)`; equity = initial + closed P&L | same |

Engine tests: "stopped on the fill bar", "gap through stop", "queued close beats the stop", "no entry on the last bar", "flat at the last close", "no pyramiding", "manual worse-of fill", "deadline". Charges: one `round_trip()` with the exact formulas and existing `ZERODHA_*` keys. Metrics (`_normalize`/`_calculate_score` verbatim): `trading_days = |{date(ts)}|`; Sharpe = `mean(daily_pnl/equity)/std × √252` over trading days; drawdown on the closed-trade equity curve; stability = three consecutive 30-calendar-day blocks with equity carried forward, each block `PF ≥ PERFORMANCE_STABILITY_PROFIT_FACTOR`, Sharpe ≥ `PERFORMANCE_STABILITY_SHARPE_RATIO`, trades ≥ 8; lucky-trade filter verbatim; zero trades → `verdict='no_trades'`.

**"Proven profitable"** (env keys kept; recommended new defaults, owner decision §17): `PERFORMANCE_MIN_POSITIONS=40` (was 200), `MIN_PROFIT_FACTOR=1.3` (1.7), `MIN_SHARPE_RATIO=1.0` daily-based (1.05), `MAX_DRAWDOWN_PERCENTAGE=0.10` (0.15), `MIN_RECOVERY_FACTOR=2` (6), `MIN_EXPECTANCY_RATE=0` ₹/trade after charges (100), stability PF ≥ 1.1 and Sharpe ≥ 0.5 per block, `MIN_SCORE=0.5` (0.6). Both intervals run; the higher passing score wins. When `FILL_MODEL=manual`, REALISTIC runs first as a pre-filter on the 3m/5m already fetched; 1m history is fetched only for names that pass, then MANUAL decides. `zt backtest SYMBOL --compare` runs every preset (v2, v3, v4, realistic, manual) and prints the gap; the daily loop runs only the gating model.

**Persistence and handoff.** Every run → `backtest_runs` + `backtest_trades`; pass → `INSERT watchlist(status='active', backtest_run_id, leverage, is_cas, band_pct)`. Ranking by score; `MAX_WATCHLIST=20`; no new rows after `LAST_SUBSCRIBE=13:30`. Each run ends with one info message (the discovery summary): candidates, passes, rejects by reason, TickerTape status, MIS-sheet status, corporate actions.

**Live feedback loop.** Per instrument, rolling live PF over the last 10 closed positions and the nightly signal-diff count; live PF < 0.8 after ≥10 trades, or ≥3 signal differences in a week ⇒ `paused` + card; owner resumes with `/resume SYM`. Slippage per fill (`avg_entry − ref_price` in bps, `sent_at → filled_at`) written to `reports(kind='slippage')`; `zt report --week` prints p50/p75/p95.

**Warm start.** No indicator state is pickled. S3 replays `history(raw, ha)` → SMA/RSI/SAR/Strategy, then today's `candles` in `candle_events` order. If the replayed state ends inside a position the runner is `carried` and emits no ENTRY until flat (preserves the `wait_position` intent).

**Cadence.** 08:30 refdata + corporate-action gate + carried-position check; 09:30 then every 30 min to 13:30 + on demand; hourly MIS sheet; 15:45 EOD: `watchlist.status='closed'`, history tail refresh for today's watched names (1m too when `FILL_MODEL=manual`), `zt report`, pruning, `wal_checkpoint(TRUNCATE)` if ingest is idle, `VACUUM INTO` backup at 16:05.

**Verify (step 2 done when):** the `v2` preset reproduces the TradingView trade list of §6 step 0.1 up to data differences; REALISTIC and MANUAL equal hand-built goldens incl. "stopped on the fill bar"; determinism (same input twice ⇒ identical hash); a reprice-legacy-trades test reproduces the executable-P&L column of §1 on the same trade set; second `zt backtest` run makes 0 Kite calls; TickerTape mocked 400 ⇒ snapshot `ok=0`, last good snapshot kept; mapping rejects `-BE/-SM`, BSE-only, non-MIS, narrow band; synthetic split ⇒ history deleted and window moved; running twice ⇒ no second run row; cold vs cached+tail fetch ⇒ identical `metrics_json`.

## 9. Phase 3: Strategy lab (before Service 3)

**Why it exists, and why it runs before Service 1.** §1 shows the existing rules lose money once fills are honest. The three services are infrastructure; this phase is where the edge has to be found and proven before a single Telegram signal is acted on. It needs only Kite historical candles and a correct engine, so it runs right after Phase 0 and the backtest core, before the tick ingest service is built (§14 order of work). It reuses `engine.run` and `Rules` unchanged (one implementation, no notebook fork), so whatever passes here is exactly what Service 2 scores and Service 3 signals.

**Data.** Kite `3minute` and `5minute` candles pulled in 60-day chunks back to at least 12 months for the candidate universe (recent TickerTape `active` snapshots across all three caps plus liquid MIS-allowed names), stored in `history`; 1m for the MANUAL fill model on finalists. Yahoo 5-minute data is only a sanity cross-check.

**Method** (`zt/lab/`; results in `backtest_runs` + `reports(kind='lab')`):
1. Baseline: Pine v2 (SMA 50 / RSI 15 / SAR 0.02,0.02,0.2 on Heikin Ashi; 2.5% stop; 0.075% gap filter) under REALISTIC and MANUAL, both intervals, slippage 0 / 5 / 10 bps; then v3 (activity filter) and v4 (EMA, Supertrend, VWAP, RSI band, 1.5% stop, exit rule) with each filter toggled individually so its contribution is measured.
2. Walk-forward: optimise on 60 trading days, test on the next 20, roll; report out-of-sample (OOS) metrics only. Grid: SMA {20, 50, 100}, RSI length {9, 14, 15, 21} and bands {40/60, 45/55, 50/50}, SAR step/max {0.01/0.1, 0.02/0.2, 0.03/0.3}, stop {1%, 1.5%, 2.5%, ATR×1.5}, gap filter {0, 0.075%, 0.15%}, entry window start {09:20, 09:30, 09:45}, last entry {13:30, 14:00, 14:15}.
3. Further candidates beyond v4, each an optional `Params` switch (default off): higher-timeframe trend filter (15-minute HA SMA slope), ATR-based stop and trailing stop, minimum candle range / turnover filter, one trade per direction per day, no entries in the first 15 minutes, post-stop cooldown (the old Python had one; the Pine does not), CAS-stock cutoff.
4. Robustness: per-window stability across ≥3 OOS windows, Monte Carlo trade reshuffling for the drawdown distribution, lucky-trade cap (already in the scorer), and the "signal-diff" between Kite candles and recorded tick-built candles from Phase 1 for the same days.

**Go / no-go gate for Phase 4 (recommended; owner may tighten).** OOS, net of charges and 5 bps slippage, per instrument: ≥100 trades, PF ≥ 1.3, max drawdown ≤10% of equity, positive expectancy in every OOS window, and ≥5 such instruments in the latest snapshot. Until this passes, Service 3 runs in paper mode only (cards go to a paper Telegram chat, never a live card). If nothing passes, revise the strategy rather than the gate.

**Deliverables.** `zt lab sweep --interval 3m --from 2025-09-01`, `zt lab walkforward`, `zt lab report`; a one-page markdown report per run; the chosen parameter set becomes the `params_hash` Service 2 uses.

## 10. Phase 4: Service 3, signals and manual execution (`zt signal`)

**Modules:** `zt/strategy/{indicators,rules,runner}.py`, `zt/services/signal.py`, `zt/notify/{base,telegram,ntfy,cards}.py`, `zt/execution/reconcile.py`, `zt/execution/monitor.py`.

**Threads** (each with its own DB connection): engine (1 Hz or on `data_version`), poller (`getUpdates(timeout=25)`, offset persisted in `kv.telegram.offset` after each batch), sender (woken by a `threading.Event` on outbox insert and every 1 s; a long poll can never delay an exit card), reconciler, monitor.

**Engine loop**
```
every 1 s (or on data_version change):
  events = SELECT * FROM candle_events WHERE seq > watermark ORDER BY seq
  for ev in events: runner.update(ha)                          # SMA/RSI/SAR + Strategy.evaluate, pure
  exits  = [decide_exit(r) for r in runners if r flipped and has an open broker position]   # first, no REST
  entries = rank_by_score([decide_entry(r, ev) for ...])       # gates 1-9, no REST yet
  for e in entries[:capacity]: enrich(e, deadline=3 s)        # gate 10: order_margins(); timeout -> skipped
  ONE transaction: INSERT OR IGNORE signals; INSERT outbox; UPDATE kv watermark = max(seq)
  monitor.tick(); reconciler.poll() if due; every 5 s heartbeat + health + daily-loss check
```
Exactly-once: `signals.UNIQUE(token, interval, candle_ts, kind)` and the watermark commit together; a crash before commit re-processes the same `seq` and produces the same row; a crash after commit leaves `outbox.status='queued'`, which the sender resumes.

**Sizing.** `day_start_equity` = `kc.margins()['equity']['net']` written to `kv` at the first valid session of the day (never a static env value); `available` = live balance cached 60 s; `qty = floor(day_start_equity × RISK_FRACTION(0.10) × leverage / ref_price)`, reduced until `order_margins(mode='compact')` ≤ `0.6 × available`; for names with `band_pct ≤ 10` notional is capped at available cash (a long stuck at a lower circuit converts to delivery); `ref_price` = `quotes.last_price` (≤3 s old); prices rounded to tick; suggested LIMIT = ref ± `ENTRY_LIMIT_BPS (10)`; `chase_limit` = ref ± `MAX_CHASE_BPS (25)`.

**ENTRY gate** (`decide()`: every item must hold; any unknown ⇒ no entry; failures logged `signal.suppressed{reason}`):
1. `watchlist.status='active'` and `backtest_runs.verdict='pass'` under `FILL_MODEL` (card prints the model).
2. candle `source='tick', degraded=0`.
3. `now − closed_at ≤ ENTRY_MAX_AGE_S (10)`: a restart backlog or backfilled candle advances state silently.
4. `ts + interval ≤ LAST_ENTRY` (14:15; 14:00 CAS) and not within `kv.away_until`.
5. no `positions.status IN ('open','closing')` for the token; no ENTRY for the token in `sent|acked|resting|ack_unconfirmed`; no `broker_orders` row for the symbol in `OPEN|TRIGGER PENDING|*PENDING` (mirror ≤5 s old, else unknown ⇒ no entry).
6. runner not `carried`; ≥1 tick-built candle of that interval since subscribe.
7. capacity: `open + reserved < MAX_OPEN_POSITIONS (3)`; candidates at one boundary ranked by score; daily loss: `kv.day_realized_pnl ≤ −DAILY_LOSS_LIMIT (0.02) × day_start_equity` ⇒ `kv.halted='1'` + one alert (exits unaffected).
8. `kv.halted != '1'`; `kv.session_valid='1'`; ingest heartbeat ≤15 s old; `quotes.recv_ts` ≤3 s old; if `kv.blind_since` was set today, every open position has a broker SL-M or is flat.
9. price band and liquidity: long `ltp ≥ lower_circuit × 1.03`, short `ltp ≤ upper_circuit × 0.97`; `spread_bps ≤ 20`; no short where `band_pct ≤ 5`.
10. `order_margins(product='MIS')` for this qty succeeded within 3 s.
EXIT (SAR flip), STOP and DEADLINE need only an open broker position, bypass every other gate and never expire.

**Telegram ENTRY card** (`sendMessage(parse_mode=HTML, disable_notification=False)`):
```
🟢 LONG  DIXON  3m HA   candle 12:33 (closed 12:36:02)   model: manual
Ref ₹14,512.30 (LTP 12:36:03)   LIMIT ≤ ₹14,526.80 (+0.1%)   skip above ₹14,548.60 (+0.25%)
Qty 34 · notional ₹4.93 L · margin ≈ ₹98.6 k (5×) of ₹1.80 L available
Stop ₹14,149.50 (−2.5% from your fill)   Exit: SAR flip on 3m HA / stop / 15:10 deadline
Valid until 12:38:02 · id 408065-3m-1789531380-ENTRY
[ ▶ Open in Kite ]   [ ✅ Placed ]   [ ❌ Skip ]
```
- Edited at +30/+60/+120 s with the current LTP; when LTP crosses `chase_limit` it becomes "⚠ moved — skip" and the basket button is removed. Expires at `SIGNAL_TTL_S=120` → "⌛ expired".
- `[▶ Open in Kite]` (only after the phone tests, §17): URL button to `<PAGES_URL>/kite.html#d=<base64 JSON>`; the page decodes the fragment locally, refuses expired ids, refuses a second submit for the same `signal_id` (`localStorage`), and auto-submits a hidden form POST to `https://kite.zerodha.com/connect/basket` with `api_key` and `data=[{variety:'regular', exchange:'NSE', tradingsymbol, transaction_type, quantity, order_type:'LIMIT', price, product:'MIS', validity:'IOC', readonly:true, tag:'ZE…'}]`. Validity is IOC so an unfilled entry never rests (fallback TTL 2 min; never DAY). Setup: log into Kite web on the phone each morning; set Telegram to open links externally. Without the page the card carries everything needed to place by hand.
- `[✅ Placed]` → `acked`; `[❌ Skip]` → `skipped`. Neither creates a position. Callbacks and commands accepted only from `TELEGRAM_CHAT_ID`.

**Broker is truth.** `reconcile.poll()` every 15 s (5 s while any ENTRY is `acked|resting`, any position open, or any `broker_orders` row non-terminal): `positions()['day']` and `orders()` filtered `product == 'MIS' and exchange == 'NSE'`; `order_trades()` for average fill. `acked` ENTRY whose order is `OPEN` → `resting` (card: "still OPEN after 60 s, cancel or re-price"); `REJECTED` → `rejected` with `status_message`; `acked` with no order after 90 s → `ack_unconfirmed` + reminder. A new MIS net position in a watched symbol → `positions(source='kite', avg_entry=real avg, stop_price = avg × (1 ∓ 2.5%))`, linked to an ENTRY only if acked ≤5 min ago and the side matches (→ `filled`); otherwise `source='adopted'`, auto-protected, card "you hold N SYM MIS with no matching signal, tracking it with stop ₹…". `qty`/`avg_entry` updated every poll (partial fills). Net qty 0 for two polls → `closed`, P&L card, `kv.day_realized_pnl` updated. CNC rows are never adopted.

**Runner ↔ broker sync.** Reconciled close without a SAR EXIT/DEADLINE signal ⇒ `runner.stopped_out()` (post-stop cooldown); reconciled open while the runner is flat ⇒ `runner.adopt(side)`; every divergence logged `reconcile.mismatch`.

**Order-book hygiene cards** (priority 0, no buttons, repeat every 60 s, self-edit to "✔ resolved"): entry-class order `OPEN` older than `SIGNAL_TTL_S` with no linked open position → "CANCEL STALE ORDER"; `TRIGGER PENDING` SL-M with broker net qty 0 → "CANCEL ORPHAN SL-M"; SL-M qty ≠ net qty → "RE-SIZE SL-M to N"; `positions.qty > signal.qty × 1.2` → "OVERSIZED"; net qty changes sign → "REVERSED", loud; protective SL-M `REJECTED` → `STOP_UNPROTECTED`, loud, repeats until a resting SL-M or flat.

**Protective stop card** (after `filled`/adopted): "🛡 Place protective stop: SL-M SELL {broker qty} SYM trigger ₹…" with basket link (`order_type:'SL-M', trigger_price`, `tag:'ZS…'`) and `[✅ Placed] [Skip]`; re-issued whenever the broker qty changes; a resting SL-M seen by the reconciler → `positions.broker_stop_order_id/qty`. Never bundled with the entry basket.

**Single exit card per position.** `positions.exit_card_ref` holds at most one live exit-class card; reasons rank STOP > DEADLINE > EXIT; a new reason edits in place; text shows the broker net qty, "cancel SL-M {order_id} first", and a basket link for exactly that qty; repeats every 60 s (STOP/EXIT) or 120 s (DEADLINE) until flat; after the 2nd unacked repeat the reason is also published to ntfy (`X-Priority: 5`, symbol and reason only) regardless of Telegram health; `STOP_UNACTIONED` after 10 repeats.

**Position monitor** (1 s, from `quotes`): stop hit requires two consecutive readings ≥1 s apart beyond the stop; if a broker SL-M is on file the card says "SL-M should have triggered, verify, then cancel it before selling". DEADLINE: silent reminder at `self_exit − 10 min`; loud reason at 15:10 (15:00 CAS); at 15:20 / 15:08 "AUTO SQUARE-OFF IMMINENT (₹59/order)"; at 15:26 / 15:13 `positions()` check → `POSITION_SURVIVED_SQUAREOFF`. `CIRCUIT_RISK` at 15:00 / 14:50 near a circuit. `/away HH:MM` halts entries and starts DEADLINE cards early. Manual mode cannot place an exit itself; an unanswered DEADLINE is the stated residual risk.

**DATA_BLIND (Sev-1).** (`kv.session_valid='0'` or ingest heartbeat > 30 s or `quotes` > 30 s stale for a held symbol) and any open position ⇒ `kv.blind_since`, loud card every 60 s "DATA BLIND, N open positions, verify SL-M / exit manually", ntfy P5 immediately; DEADLINE cards continue on the wall clock; entries stay blocked until every open position has a broker SL-M or is flat.

**Ops alerts** (money set only; in-memory dedupe): `service.down{ingest|discover}` (heartbeat > 30 s, 09:00–15:35; discover > 10 min; 5-min cooldown), `ingest.ws` (restarting/noreconnect/reconnects), `data.stale` (active instrument without a 1m row > 150 s, 09:20–15:00; entries suppressed for it), `token.invalid` (with login URL), plus the money states above (`DATA_BLIND`, `STOP_UNPROTECTED`, `STOP_UNACTIONED`, `ACK_UNCONFIRMED`, `CANCEL_STALE_ORDER`, `CANCEL_ORPHAN_SLM`, `RESIZE_SLM`, `OVERSIZED`, `REVERSED`, `CIRCUIT_RISK`, `POSITION_SURVIVED_SQUAREOFF`, `POSITION_CARRIED`, `DAILY_LOSS_HALT`). Diagnostics (skew, late ticks, backlog, WAL size) live in logs and `zt status`.

**Outbox sender.** Priority 0 > 1 > 2 > 3; 1 msg/s per chat; before each HTTP call the row is set `sending` with a fresh `attempt_id` in its own commit; on restart every `sending` row is re-sent with the banner "(re-sent, act once)" and the same signal id; retries 1/2/4/8/16/32 s, 429 honours `retry_after`. ENTRY whose `expires_at` passes undelivered → `dropped`/`undelivered`; exit-class never expire and refresh LTP on each retry. After 3 consecutive failures on a priority-0 item → ntfy.

**Commands:** `/status`, `/positions`, `/pause SYM`, `/resume SYM`, `/halt`, `/resume`, `/away HH:MM`, `/discover`, `/login [force]`, `/start <request_token>`. No `/mute`.

**Degraded modes:** ingest down without positions → no ENTRY + alert; with positions → DATA_BLIND; Telegram down → outbox retry, entries expire, exits retry forever, ntfy for priority 0; discover down → watchlist unchanged; SQLite busy → iteration skipped and logged; S3 crash → runners replayed from history + today's candles, watermark prevents re-emission, `sending` rows re-sent with banner.

`Notifier` protocol (the only new abstraction; WhatsApp later): `send(text, buttons, loud, priority) -> message_ref`, `edit(message_ref, text, buttons)`, `poll_actions() -> list[Action]`.

**Verify (step 4 done when):** exactly-once test (kill between candles ⇒ one row; kill between `sendMessage` and `sent` ⇒ one re-sent card with banner); table-driven gate test incl. capacity ranking and band gate; `FakeTelegram` end-to-end on a replayed day; stop S1 with an open paper position ⇒ DATA_BLIND card + ntfy ≤ 60 s; invalid bot token ⇒ entries expire `undelivered`, exits keep retrying; manual MIS position in Kite ⇒ adopted + protected; CNC position ⇒ ignored; resting DAY order ⇒ stale-order card; SL-M with flat position ⇒ orphan card; a STOP card leaves the sender ≤1 s while a 25 s long poll is outstanding.

## 11. Phase 5: One-tap page, supervision, cutover

- **Basket page** (separate one-file public GitHub Pages repo; register its URL as the Kite app's redirect URL): `flow=login` branch shows "Send to bot" (`https://t.me/<bot>?start=<request_token>`) and Copy; a basket return shows only "order submitted, close this tab"; single-use per `signal_id`; IOC validity. Phone tests: tap ⇒ Kite basket with `readonly`, `validity IOC` accepted (else TTL 2 min); reload ⇒ refused; expired id refused; SL-M card places a `TRIGGER PENDING` order the reconciler links.
- **Supervision.** macOS: `~/Library/LaunchAgents/com.zt.{ingest,discover,signal}.plist` with `RunAtLoad=true`, `KeepAlive={SuccessfulExit:false}`, `ThrottleInterval=10`, `EnvironmentVariables{TZ=Asia/Kolkata}`, `WorkingDirectory`, log paths; SIGTERM flushes and exits 0; runbook: `sudo pmset -a disablesleep 1` while positions can be open (a sleeping Mac is DATA_BLIND); agents run only while logged in. Linux: `deploy/systemd/zt-*.service` with `Restart=on-failure`, `RestartSec=5`, `StartLimitIntervalSec=0`, `SuccessExitStatus=78` excluded from restart, optional `WatchdogSec=60`. Docker Compose (Linux only): one image, three services, `restart: unless-stopped`, DB on a named volume, healthcheck on heartbeat age.
- **Runbook** (`docs/runbook.md`): daily login, sleep rule, residual manual-mode risk, CAS list weekly cross-check, day-one drills (§16), retention and backups.
- **Cutover.** Two clean shadow weeks (acceptance in §16). Verify: reboot ⇒ three agents up; `launchctl kickstart -k` ⇒ recovery; one unattended trading day with only `/start` typed by hand; fresh clone + `.env` + `zt migrate` boots all three.

## 12. Phase 6 (future scope): Service 3, automated execution

Ships behind `EXECUTION_MODE=manual|paper|one_share|sized|live` (with `002_execution.sql`); `manual` remains the fallback at all times. The executor lives in the `signal` process as `zt/execution/{state,gateway,ladder,risk,paper,executor}.py`, using the second of the three websocket connections per key for its own `on_order_update` and sub-second LTP on open positions.

**Prerequisites (verified at start; any failure ⇒ refuse `live`):** kiteconnect ≥ 5.2.2 (`market_protection`); egress IP equals a whitelisted IP (`curl ifconfig.me` at start, hourly); valid session; `EXPECTED_EGRESS_IP` set; paper history satisfies the gate for the requested stage (`kv.exec_stage`). Rate budget: exchange 10 OPS per client, Zerodha 400/min, 5,000/day, 25 modifications/order, rejected requests count. Order budget (`risk.py`, one token bucket of 8 requests/s with strict priority STOP/DEADLINE/kill > SAR exit > entry cancel > entry): exits are never delayed past the next second and never paused on 429; entries pause 10 s on 429; at most one active ladder per position and 2 requests/position/s; `algo_id` left unset (undocumented for sub-threshold users).

**Static IP steps.** (1) Provision a fixed public egress IP on the executor host only: Hetzner-class VPS IPv4 or an ISP static IP (~₹1,500/yr); Oracle Always-Free VMs are reclaimed after ~7 idle days; CGNAT/PaaS egress is rejected. (2) developers.kite.trade → Profile → IP Whitelist → primary and secondary (home ISP static, from day one) → Update; one change per calendar week, so do it ≥7 days before go-live, never on a trading morning. (3) The executor halts on egress mismatch; manual cards remain as fallback. Under 10 OPS no exchange registration is needed.

**Order state machine** (Kite is authoritative; local state moves only forward on a newer Kite event; every event appended to `order_events` in the same transaction as the `orders` update):

| Local state | Kite statuses | Action |
|---|---|---|
| `NEW` | — | row with `client_tag` committed; no API call yet |
| `SUBMITTING` | — | `place_order` in flight |
| `SUBMITTED` | `PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`, `OPEN PENDING`, `AMO REQ RECEIVED` | wait; > 10 s poll `order_history`; > 30 s alert `ORDER_STUCK`, treat as active (never re-place) |
| `OPEN` | `OPEN`, `MODIFIED`, `MODIFY VALIDATION PENDING`, `MODIFY PENDING` | ladder runs; `filled_qty > 0` = partial; no modify while a modify is pending |
| `TRIGGER_PENDING` | `TRIGGER PENDING` | healthy resting SL-M |
| `CANCEL_PENDING` | `CANCEL PENDING` | wait; never place a replacement until terminal |
| `COMPLETE` | `COMPLETE` | fills from `order_trades` (arbitrary chunks); ENTRY → place STOP |
| `CANCELLED` | `CANCELLED` | `filled_qty > 0` ⇒ partial position; else intent closed |
| `REJECTED` | `REJECTED` | parse `status_message` (IP, market protection, MIS-blocked, freeze qty, band, margin); 3 consecutive ⇒ halt |
| `UNKNOWN` | — | resolve by tag lookup before any other action on that signal |
| rule | any string containing `PENDING` or `REQ RECEIVED` | transient; any other unrecognised string ⇒ treat as active, alert `UNKNOWN_ORDER_STATUS`, never re-place |

Position machine: `FLAT → ENTERING → OPEN(stop_order) → EXITING → FLAT`; invariant before every action: `broker_net_qty == Σ entry fills − Σ exit fills`, else `RECONCILE_HALT` for the instrument (no new orders, adopt broker qty, loud alert).

**Idempotent placement and crash recovery (never double-place).** (1) `BEGIN IMMEDIATE; INSERT orders(client_tag, local_state='SUBMITTING', submit_attempted_at); COMMIT`. (2) `place_order(..., tag=client_tag)` (10 s timeout), the only call that can duplicate. (3) `UPDATE orders SET kite_order_id, local_state='SUBMITTED', submit_confirmed_at; COMMIT`. (4) Timeout/`NetworkException` ⇒ `UNKNOWN`; poll `orders()` at 2, 6, 15, 30, 60 s for `tag == client_tag`; found ⇒ adopt; not found after 60 s ⇒ `FAILED`, alert, no automatic re-place; a new intent needs a new tag (`child+1`) and a human `/retry`. (5) Startup: resolve every non-terminal row against `orders()` by tag/order_id; rebuild positions from `positions()` (MIS only); any open position without a live SL-M gets one immediately; only then process new signals; an ENTRY older than 10 s at recovery is not executed. (6) `signals.status='done'` once an entry intent exists; tags are per (token, candle).

**Entry price selection ("which price to trade").** Inputs: executor-socket depth (≤2 s old), `quotes` circuit limits. Pre-trade checks (all logged; any failure ⇒ `ENTRY_ABANDONED`): spread ≤ 20 bps; opposite-side top-5 depth ≥ 3 × qty; price within `[lower_circuit × 1.03, upper_circuit × 0.97]`; no shorts where the band ≤ 5%; `order_margins(mode='compact')` ≤ available × 0.6; risk gate; `now ≤ last_entry`; signal age ≤ 10 s. Order: marketable LIMIT, long `min(ask + 1 tick, ref × (1 + 10 bps))`, short symmetric; `validity='TTL', validity_ttl=2` as broker-side backstop (verify the API accepts 1–120 min; fallback `DAY` + self-cancel). MARKET is never used for entries (protected market orders can leave OPEN remainders).

**Stale-order ladder (entry; "order goes stale").** t=5 s not COMPLETE → modify to current ask + 2 ticks; t=10 s → +3; t=15 s → +4 (≤3 modifications); chase ceiling `ref × (1 + MAX_CHASE_BPS 25)` at every step, else cancel immediately; t=20 s → `cancel_order`, wait terminal; the filled part becomes the position (`partial`), the remainder is abandoned for this candle. Modify never sent while `MODIFY PENDING`.

**Partial fills.** Position qty = Σ `fills` for the entry tag(s); SL-M placed for the first fill and re-sized once per fill batch; after 6 modifications the SL-M is cancelled and re-placed for the full qty under the single-exit protocol; exits always target the reconciled broker qty.

**Quantity splitting ("split quantities to exit faster").** Entry: if `qty × price ≥ ₹5 L` or `qty > 30%` of visible opposite depth → up to 3 children at ask+1/+2/+3 ticks (50/30/20%), tags `…0/1/2`, each with its own ladder. Above the NSE freeze quantity → `place_autoslice_order` (≤10 slices; iceberg leg range 2–10 vs docs 2–50, verify live). Exit ladder: IOC LIMIT children sized to displayed depth levels 1–3, placed one per second in depth order (IOC returns the unfilled remainder as CANCELLED at once, zero resting risk) → the MARKET `market_protection=-1` remainder is sized only after every IOC child is terminal → if rejected (band/LPP), LIMIT at bid − 3 ticks re-priced every 2 s with no chase limit → still open at 20 s ⇒ alert `EXIT_STUCK` and repeat; exits are never abandoned. Emergency (STOP, DEADLINE, kill): a single MARKET `-1` for the whole qty per position, positions staggered one per second.

**Protective stop choice.** Both a broker SL-M (`trigger=stop`, `market_protection=-1`; survives executor/host/network death; may leave an OPEN remainder in a fast market) and a software stop (tick watcher → exit ladder; can chase) under a single-exit-path protocol. SL-M placed within 2 s of the first entry fill (alert if > 5 s). The watcher acts only when (a) the SL-M is not `TRIGGER PENDING` (rejected/cancelled), (b) it triggered and left an OPEN remainder > 5 s, or (c) a SAR/DEADLINE/kill exit fires. Protocol: cancel the SL-M → wait `CANCELLED` (or observe `COMPLETE` ⇒ nothing to do; `CANCEL PENDING` > 2 s ⇒ poll every 500 ms) → place the exit for `broker_qty − filled_by_stop`. Two live exit orders for the same quantity are never in the book. Stops are never widened.

**Reconciliation.** `on_order_update` on the executor socket → `order_events(source='ws')`; `orders()` every 3 s while any order is non-terminal else 30 s; `positions()` (MIS only) every 15 s; `order_trades()` on COMPLETE/partial. Poll wins over WS on conflict. Unknown MIS position → adopted, protected, alert; `AUTO_FLATTEN_UNKNOWN=true` flattens adopted MIS positions only at the deadline unless `/keep SYM`; CNC is never touched.

**Risk limits and kill switch** (before every placement and continuously):

| Control | Default | Breach |
|---|---|---|
| kill (`/kill`, `kv.halted`, file `data/KILL`) | — | cancel all open orders (priority budget), emergency MARKET exit per position staggered 1/s, block entries until `/resume` with typed confirmation |
| daily loss (realized + unrealized) | −2% of start-of-day equity | auto kill; resets next day only |
| max open positions | 3 | refuse entry |
| per-instrument notional | ≤ 10% equity × leverage; ≤ available cash where `band_pct ≤ 10` | cap qty |
| total margin used | ≤ 60% available | refuse entry |
| order budget | 8 req/s with exit priority, 60/min, 300/day | delay entries; exits never paused |
| consecutive rejections | 3 in 5 min | halt entries 15 min + alert; 3 in a day ⇒ halt for the day |
| circuit proximity | long ltp ≤ lower × 1.002 or short ≥ upper × 0.998 at 15:00/14:50 | `CIRCUIT_RISK`; no new entries in that name |
| data health | ingest heartbeat > 15 s or executor LTP > 3 s old | no entries; exits allowed (MARKET) |
| token invalid / egress mismatch / SDK < 5.2.2 | — | executor refuses; manual cards continue |
| 20-day live PF < 1.0 | rolling | automation halts; manual mode |
| any Sev-1 (double order, unreconciled position, stop not placed, exit stuck > 60 s) | — | `kv.exec_stage` demoted one level automatically |

**Latency budget (3m candle):** boundary + 2 s grace → S1 commit ≤ +2.3 s → S3 wake (250 ms poll in auto mode) ≤ +2.6 s → gates + margin call ≤ +2.9 s → `place_order` 150–400 ms → order at exchange ≈ +3.3 s (p95 target ≤ 5 s, alert at 8 s); ladder done ≤ +25 s. When the executor is live the gating `FILL_MODEL` returns to `realistic`.

**Rollout** (`PaperGateway` implements place/modify/cancel/orders/trades/positions against depth; same state machine, ladders and cards; rows `paper=1`):

| Stage | Duration | Go / no-go (all required) |
|---|---|---|
| paper | ≥ 15 trading days, ≥ 30 signals | 0 invariant violations, 0 duplicate tags, 0 unresolved `UNKNOWN`; signal→order p50 ≤ 3 s, p95 ≤ 6 s; every stop placed ≤ 2 s after fill; paper P&L/trade within 30% of the realistic backtest expectancy for the same days; recon divergence within §16 thresholds on ≥ 12 days |
| one_share | ≥ 10 days | same on real fills; 0 rejections not understood; SL-M accepted every time; slippage vs ref p50 ≤ 10 bps, p95 ≤ 25 bps; every position flat by 15:15/15:05; `kill -9` mid-order twice with no orphan; 429 drill shows exits unaffected |
| sized (25%) | ≥ 10 days | same; partial-fill and ladder paths exercised; daily-loss limit never breached by malfunction; kill switch drilled once |
| full | — | monthly review vs backtest expectancy; automatic demotion on any Sev-1 |

## 13. Cross-cutting

**Config (`zt/config.py`).** Frozen dataclass from environment, validated at start. Existing names kept: `KITE_API_KEY`, `KITE_API_SECRET_TOKEN`, all `PERFORMANCE_*`, all `ZERODHA_*`. Removed: `KITE_API_ACCESS_TOKEN`. Added: `ZT_DB=data/zt.db`, `ZT_SESSION_FILE=data/session.json`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `NTFY_TOPIC` (optional), `PAGES_URL` (optional; none ⇒ no basket button), `RISK_FRACTION=0.10`, `MAX_OPEN_POSITIONS=3`, `DAILY_LOSS_LIMIT=0.02`, `FILL_MODEL=realistic`, `SLIPPAGE_BPS=5`, `LAST_ENTRY=14:15`, `LAST_ENTRY_CAS=14:00`, `SELF_EXIT=15:10`, `SELF_EXIT_CAS=15:00`, `DISCOVERY_CAPS=MidCap,SmallCap`. Code constants: `MAX_WATCHLIST=20`, `DISCOVERY_TOP_N=15`, `LAST_SUBSCRIBE=13:30`, `SIGNAL_TTL_S=120`, `ENTRY_MAX_AGE_S=10`, `ENTRY_LIMIT_BPS=10`, `MAX_CHASE_BPS=25`, `WARMUP_LIVE_CANDLES=1`, `GRACE_S=2.0`. Future: `EXECUTION_MODE`, `EXPECTED_EGRESS_IP`, `AUTO_FLATTEN_UNKNOWN`. `zt status --config` prints effective values with secrets masked. `PERFORMANCE_EQUITY_BALANCE` is backtest equity only; live sizing uses `kv.day_start_equity` from `margins()`.

**Secrets.** Daily token only in `data/session.json` (0600, tmp + `os.replace`); `data/` gitignored. `RedactFilter` on the single log handler masks the API secret, bot token, current access token, and any 30+ char alphanumeric run following `token|secret|api_key`; unit-tested. Delete the three `logs/session_*` directories in Phase 0.

**Daily login helper.** Token expires 06:00; sessions are flushed ~07:30–08:30, so the prompt is sent at 08:30 after refdata: S3 sends `https://kite.zerodha.com/connect/login?v=3&api_key=…&redirect_params=flow%3Dlogin` if `session.json.date != today` (repeat every 10 min) → owner logs in with TOTP → Kite redirects to `PAGES_URL/kite.html` → page shows "Send to bot" only when `flow=login` → S3 receives `/start <token>`, refused when `kv.session_valid='1'` for today unless `/login force` was sent → `generate_session` immediately → writes `session.json` (`generation+1`), `kv.session_valid=1`, `kv.session_generation`, replies "logged in as UID until 06:00". CLI fallback `zt login <request_token>`. Every process re-reads the file when `kv.session_generation` changes and on `TokenException`.

**Time and calendar (`zt/core/clock.py`, `zt/core/holidays.py`).** `IST = ZoneInfo('Asia/Kolkata')`; every datetime tz-aware; DB epoch ints; startup asserts the process offset is +05:30 (supervisor sets `TZ`); NTP check; `Clock` protocol injected (real vs virtual for replay). Holidays: dict by year in code (2026 list in §2); startup warns if the current year has no entry; from 1 December S3 sends one info message if the next year is missing; if no active instrument ticks by 09:20 the day is reported once as "market closed?". Helpers: `is_trading_day`, `prev_trading_day`, `session_end(is_cas)`, `bucket_start(ts, n)`, `last_entry(is_cas)`, `self_exit(is_cas)`, `square_off(is_cas)` = 15:25 / 15:12.

**Logging.** One JSON-lines handler per process (`logs/<service>.jsonl`, daily rotation, 14 days) + stderr; fields `ts, level, service, event, token, symbol, interval, candle_ts, signal_id, order_tag, latency_ms`; enumerated event names (`signal.emitted`, `signal.suppressed`, `order.intent`, `order.state`, `reconcile.mismatch`, `ws.silent`, `clock.skew`, `ingest.backlog`, `refdata.corp_action`).

**Health.** `heartbeats` every 5 s; `zt status` prints heartbeat ages, watchlist, open positions, broker orders, queued outbox, last 20 signals, last discovery summary; `/status` returns the same.

**SQLite runtime (`zt/core/db.py`).** `try: import pysqlite3 as sqlite3 except ImportError: import sqlite3`; assert `sqlite_version >= 3.51.3` or exit 78. macOS: Homebrew sqlite upgrade (Phase 0); Linux: `pysqlite3-binary`. Never delete `-wal/-shm`; local disk only.

**Tests.** Unit (builder incl. skew/late-tick/outage flags and source precedence, aggregator, clock, charges, redaction, gates table-driven, sizing, corporate-action split), golden (ticks → candles; legacy trade-list parity both intervals; realistic and manual goldens incl. "stopped on entry bar"), property (fill invariants), replay, exactly-once restart incl. `stopped_out`/`adopt` and `sending` re-send, reconciler with fakes (MIS filter, side mismatch, stale order, orphan SL-M, reversed), state machine and order budget (future). CI: GitHub Actions on Linux with `pysqlite3-binary`.

## 14. Migration map (every existing file/function)

| Existing | Disposition | Target |
|---|---|---|
| `enum/interval.py` `Interval` | reuse (+ `seconds`) | `zt/candles/model.py` |
| `enum/market.py` `Market` | reuse verbatim | `zt/discovery/tickertape.py` |
| `enum/position.py` `Position` | reuse verbatim | `zt/strategy/rules.py` |
| `model/candle.py` `Candle` dataclass | refactor: drop `created_on/updated_on`, add `volume, tick_count` | `zt/candles/model.py` |
| `Candle.convert` (Heikin Ashi) | **reuse verbatim** | `zt/candles/heikin_ashi.py` |
| `Candle.generate/create/update/aggregate` | reuse (+ volume sums) | `zt/candles/model.py` |
| `Candle.display` | reuse for `[export]` only | `zt/candles/model.py` |
| `model/instrument.py` `Instrument` | drop (`pick_/track_candle_*`, `wait_position`, `is_valid` belonged to the busy loop) | `Watch` dataclass in `zt/services/ingest.py` |
| `model/metrics.py` `Metrics` | refactor: metric fields only; drop indicator/strategy/candle attachments (:39-45) | `zt/backtest/metrics.py` |
| `util/date_util.py` `precise_date_time`, `format_date_time` | refactor to zoneinfo | `zt/core/clock.py` |
| `DateUtil.current/market_start/market_end/session_date_time` | drop | — |
| `util/file_util.py` `generate_candle_xlsx` | reuse (reads DB) | `zt export --xlsx` (extra) |
| `FileUtil.generate_instrument_xlsx`, `extract_xlsx`, `find_repo_root` | drop | — |
| `util/logger_util.py` | drop | `zt/core/log.py` |
| `zerodha/kite.py` login block (19-47) | drop (token in logs) | `zt/kite/session.py`, `zt login` |
| `kite.py` ticker callbacks, `subscribe_/unsubscribe_instrument` | refactor: queue hand-off, `callFromThread`, pending buffer, watchlist-driven | `zt/kite/ticker.py`, `zt/services/ingest.py` |
| `zerodha/chart.py` `historic_candle` | reuse with limiter/chunking | `zt/kite/history.py` |
| `Chart.generate_candle` bucketing idea | refactor | `zt/candles/builder.py` |
| `Chart.aggregate_candles`, flat-fill, in-tick REST, class dicts | drop | — |
| `zerodha/strategy.py` `SMA, RSI, SAR` | **reuse verbatim** | `zt/strategy/indicators.py` |
| `Strategy.evaluate` | replaced by pure condition functions transcribed from the Pine (`long_entry`, `short_entry`, `exit_long`, `exit_short`) | `zt/strategy/rules.py` |
| `Strategy.threshold` (post-stop cooldown) | drop: not in the Pine; stop logic in engine/monitor | `zt/backtest/engine.py`, `zt/execution/monitor.py` |
| `performance.py` `_normalize`, `_calculate_score` | **reuse verbatim** | `zt/backtest/score.py` |
| `_calculate_metrics` | refactor (daily Sharpe, distinct days) | `zt/backtest/metrics.py` |
| `_calculate_stability` | rewrite (calendar blocks, carried equity) | `zt/backtest/filters.py` |
| `_calculate_leverage` | refactor (MIS sheet + `order_margins`) | `zt/discovery/mapping.py` |
| `categorize` fetch/convert | refactor | `zt/kite/history.py` |
| `categorize` execution loop (264-372) | rewrite as `engine.run` with TradingView execution semantics | `zt/backtest/engine.py` |
| `categorize` filter table (397-454) | reuse as data | `zt/backtest/filters.py` |
| charge blocks (296-308, 339-351) | extract verbatim into one function | `zt/backtest/charges.py` |
| `Performance.signal` | drop | `zt/strategy/runner.py` + `zt/services/signal.py` |
| `zerodha/trade.py` | drop | `zt/cli.py`, `zt/services/*` |
| `.env` `KITE_API_ACCESS_TOKEN` | drop | `data/session.json` |
| `.env` other keys | keep names | `zt/config.py` |

**Order of work (revised 18 Sep 2026, owner decision: prove the edge before building the live services):** Phase 0 (§6) → Phase 2a backtest core: history fetch/cache, `engine.run`, `Rules`, charges, metrics, score (§8 engine parts only) → Phase 3 strategy lab (§9, go/no-go gate) → Phase 1 Service 1 (§7) → Phase 2b discovery wiring and `zt discover` (§8 remainder) → Phase 4 Service 3 manual (§10) → Phase 5 page, supervision, cutover (§11) → Phase 6 automated execution (§12, future). The only Service 1 piece that runs from day one is the tick recorder (§6 step 0.1), which costs nothing and accumulates real tick data. `src/main` was deleted on 18 Sep (Phase 0); the migration map above records where each piece went. Indicative effort (one engineer): Phase 0 ≈ 2 days; Phase 1 ≈ 4 days + 2 live days; Phase 2 ≈ 4 days; Phase 3 open-ended (data-driven; budget ≥ 2 weeks); Phase 4 ≈ 5 days + 2 shadow days; Phase 5 ≈ 2 days + two shadow weeks; Phase 6 ≈ 2–3 weeks of code plus ≥ 35 trading days of staged rollout.

## 15. Cost (monthly, INR)

| Item | Now (Mac, manual) | Automated phase | Cheapest alternative |
|---|---|---|---|
| Kite Connect data plan (websocket + historical) | 500 | 500 | None; the free Personal plan has no data and cannot run this bot. Confirm the existing app is on this plan |
| Telegram bot | 0 | 0 | |
| ntfy.sh Sev-1 fallback | 0 | 0 | Pushover ₹450 one-time if nag-until-ack is wanted |
| GitHub Pages (login redirect + basket page) | 0 (separate public one-file repo; Pages on a private repo needs GitHub Pro) | 0 | Skip the page: `zt login <token>` from the browser URL bar |
| Database (SQLite), tick logs, backups | 0 | 0 | |
| Redis / PostgreSQL / WhatsApp / second API key | 0 (not used) | 0 | |
| Compute | 0 (this Mac, ≈ ₹100 electricity; sleep disabled while positions can be open) | Mac + ISP static IP ≈ 125 (₹1,500/yr), or Hetzner-class VPS ≈ 350–450 with fixed IPv4 | Oracle Cloud Always-Free ARM VM with reserved IP ≈ 0, only if kept loaded and monitored (idle VMs reclaimed after ~7 days) |
| **Total** | **500** | **625 (Mac + ISP IP) to 950 (VPS)** | |

## 16. Verification (end to end)

**Service 1 candles match Kite's.** (a) Offline: `Candle.aggregate` over 09:15-anchored buckets of one day of Kite 1m reproduces Kite's 3m/5m OHLC exactly (volume excluded). (b) EOD `zt report DATE` (15:45, one Kite 1m call per active token): per-token distributions of `d_close_bps`, `hl_outside`, HA-colour mismatches, minutes present on one side only (split into "inside outage window" and "genuine no-trade"), stored in `reports(kind='recon')`. Acceptance for go-live: on ≥4 of 5 shadow days, ≥95% of 3m/5m bars have `|d_close_bps| ≤ 20`, HA colour differs on < 2%, no genuine-no-trade minute exists on Kite's side that S1 lacks, and the 09:15 open is the only systematic mismatch. (c) Nightly signal-diff: re-run the strategy over today's tick-built 3m/5m and over Kite's 3m/5m; ≥3 differences on one instrument in a week ⇒ `paused` + owner review.

**Backtest determinism.** `engine.run` is pure (test monkeypatches `os.getenv`, `datetime.now`, `time` to raise); same fixture twice ⇒ identical hash; `v2` preset == TradingView trade list up to data differences; REALISTIC and MANUAL == hand-built goldens; hypothesis properties: entry fill ts > signal ts; MANUAL fill never better than REALISTIC; long stop fill ≤ stop; short ≥ stop; a stop breached on the fill bar exits on that bar; no entry closing after `last_entry`; no position after `self_exit + interval`; ≤1 open position per interval; Sharpe uses exactly `trading_days` returns; stability blocks are contiguous dates; cold fetch and cached+tail fetch give identical metrics.

**Signal round-trip to Telegram.** Temp-DB test inserting `candle_events` progressively with engine kill/recreate between events ⇒ one `signals` row per key and one `outbox` row; kill between `sendMessage` and `sent` ⇒ exactly one re-sent card with the banner; `FakeTelegram` asserts send → `answerCallbackQuery` → `editMessageText` sequences and 429 handling; a STOP card leaves the sender ≤1 s during a 25 s long poll. Reconciler with `FakeKite`: CNC ignored; side mismatch ⇒ adopted; resting entry ⇒ stale card; orphan SL-M ⇒ card; sign flip ⇒ REVERSED; broker close without EXIT ⇒ `stopped_out()`; adopted open ⇒ `adopt()`. Live drill (shadow day): a card arrives ≤3 s after the 3m boundary; press Placed then place 1 share MIS by hand ⇒ position row `source='kite'` with the real average price within 15 s and the protective-stop card follows; press Skip ⇒ no follow-ups; let one expire ⇒ "⌛ expired"; place a DAY limit far from LTP ⇒ stale-order card within 2 min.

**Day-one drills (recorded in the runbook before the first real position).** (1) `invalidate_access_token` while the socket streams: do ticks continue, does reconnect succeed, does DATA_BLIND fire? (2) A second `generate_session` the same day: does the first token or the ticker die? (3) Dump Kite 1m/3m/5m for one CAS name 15:00–15:35 and decide whether 15:15–15:30 rows stay excluded. (4) Phone tests: Telegram's external browser shares the Kite web cookie; the Connect `api_key` is accepted by the basket endpoint; the basket accepts `validity IOC`/`TTL`. (5) The 08:30 login prompt lands after the session flush. (6) The basket return page does not offer "Send to bot".

**Full paper day.** `zt replay data/ticks/<day>.ndjson.gz --db /tmp/replay.db --speed 100` drives the real builder, aggregator and engine on a virtual clock with `FakeTelegram`; asserts `candles` and `signals` equal the live day's rows (regression on every change; < 2 min). Live acceptance before removing `src/main`: two consecutive market weeks with zero duplicate `(token, interval, candle_ts, kind)`, zero missing 1m rows for active instruments outside genuine no-trade minutes, reconciliation within the thresholds above, zero hygiene cards left unresolved at 15:30, and every alert key fired at least once in a drill (kill S1 with a paper position, invalidate the token, block Telegram, unplug the network, place a stray DAY order and an orphan SL-M by hand).

## 17. Open decisions for the owner (recommendation first)

1. **Selection thresholds.** Recommended new defaults in §8 (min positions 40, PF 1.3, daily Sharpe 1.0, max DD 10%, recovery 2, expectancy > 0, score 0.5) versus the current `.env` (200, 1.7, 1.05, 15%, 6, 100, 0.6). Recommendation: adopt the new set; the old one was tuned against lookahead-inflated numbers.
2. **Risk fraction and limits.** `RISK_FRACTION=0.10`, `MAX_OPEN_POSITIONS=3`, `DAILY_LOSS_LIMIT=0.02`, all active from the first live day. Recommendation: keep.
3. **Gating fill model.** `realistic` default; `manual` prices the 20–60 s a human needs. Recommendation: `manual` until the executor is live.
4. **Shorts in mid/small caps.** Narrow-band names (< 9.5%) rejected, shorts blocked where the band ≤ 5%; a short stuck at an upper circuit still risks auction penalty. Recommendation: keep; revisit after the shadow weeks.
5. **One-tap basket page.** Needs the phone tests and a separate public repo. Recommendation: test on day 1 of Phase 5; if any test fails, skip it (the card already contains every order field).
6. **Manual protective SL-M card.** Recommendation: enable.
7. **Discovery filters and cutoff.** `price ≥ ₹50`, `1–8%` change, turnover ramp, last discovery 13:30, MidCap+SmallCap only. Recommendation: accept; `/discover` covers "at any point of time today" on demand; add LargeCap via `DISCOVERY_CAPS` if wanted.
8. **Host now.** Mac with `pmset -a disablesleep 1` versus a VM. Recommendation: Mac through Phase 5; move to a VM before paper mode so the static IP is fixed a week ahead.
9. **Slippage and cutoffs.** `SLIPPAGE_BPS=5`, `LAST_ENTRY 14:15/14:00`, `SELF_EXIT 15:10/15:00` are deliberately early for manual mode. Recommendation: after 20 manual fills set slippage to the measured p75; loosen cutoffs only after the one-share phase measures exit latency.
10. **Circuit-risk cash cap** for `band_pct ≤ 10`. Recommendation: accept.
11. **Retention.** Tick logs 15 days, live candles 15 days, `broker_orders`/`outbox` 30 days, backups 7. Recommendation: accept.
12. **Strategy lab gate.** §9 thresholds (≥100 OOS trades, PF ≥ 1.3, DD ≤ 10%, ≥5 instruments). Recommendation: accept; tighten rather than loosen.

## 18. Appendix: audited defect list

Method: 6 lenses × 4 finder rounds produced 102 raw findings, merged into 60 canonical defects; each was then sent to 3 independent refuters (correctness, impact, reproducibility) with majority rule. Outcome: **31 confirmed**, **2 refuted** (removed below), **27 not voted on** because the session's usage limit stopped the refuters (the items marked ⚠; every one of them is still addressed by the design, and the ENTRY re-emission, inverted `wait_position`, in-progress-candle and after-close items were each reported independently by 4 to 6 lenses and confirmed by the design panel's own code reading). Refuters lowered a few severities (token logging and the 15:25/15:27 square-off to medium; stability and equity-curve items to low); none were raised.

**Live signal path (`performance.py`, `strategy.py`)**
- ⚠ `performance.py:509`: the ENTRY signal is re-emitted on every `signal()` call and at every later boundary while a position is open (found by 6 lenses). Fix: exactly-once `signals` key + "entry only when flat" gate (§10).
- ⚠ `performance.py:526`: the `wait_position` update is inverted, so a position that was already open before subscription is announced as a fresh ENTRY. Fix: `carried` runner state (§8, §10).
- ⚠ `performance.py:517` / `trade.py:60`: a stop hit is detected on the tick thread but only surfaced at the next 3/5-minute boundary, at the boundary price, labelled "EXIT NONE". Fix: 1 s position monitor from `quotes`, single exit card (§10).
- `performance.py:522`: EXIT prints direction `NONE`, so the notification cannot say buy or sell. Fix: cards carry side and broker qty (§10).
- `performance.py:511`: ENTRY notification omits stop level and quantity. Fix: card fields + protective-stop card (§10).
- ⚠ `performance.py:486, 491`: replay of today's candles applies `evaluate()` but never `threshold()`, so reconstructed state ignores stops. Fix: stop logic lives in the engine; runner synced to broker via `stopped_out()`/`adopt()` (§8, §10).
- ⚠ `performance.py:494`, `instrument.py:43`: open-position state is not persisted; a restart orphans or re-announces the held position. Fix: broker-is-truth `positions` table (§10).
- `strategy.py:173`: no entry cutoff before MIS square-off (entries allowed on 15:18–15:24 candles). Fix: `LAST_ENTRY` per instrument (§8, §13).
- `strategy.py:181, 191`: stop level from the HA close, while `threshold()` compares raw LTP and the fill happens later. Fix: stop from fill / broker average (§8, §10).
- `strategy.py:196`: SAR exit uses strict inequality, so the reversal bar that makes a new extreme does not exit. Kept verbatim for parity; candidate for the strategy lab (§9).
- `strategy.py:112`: the Parabolic SAR reversal test uses the unclamped SAR (TradingView `pine_sar` semantics, not Wilder/TA-Lib). Kept verbatim; the indicator parity test in Phase 0 must target that reference, not TA-Lib (§6).
- ⚠ `strategy.py:205`: stop and exit signals ignore circuit locks, so the notified exit may be unexecutable. Fix: `CIRCUIT_RISK` monitor, band gates (§10).
- `strategy.py:197`: square-off depends solely on the 15:25/15:27 candle existing; no day-boundary reset. Fix: `self_exit`/DEADLINE per instrument (§8, §10).

**Backtest (`performance.py`)**
- `:270` lookahead fill; `:283, 332-337` stop fill at open, both sides checked; `:288` accidental pyramiding; `:311` short margin sign; `:266, 108` candles-as-days and Sharpe scaling; `:143, 153` stability trade-count vs days and shadowed variable (all in §1). Fix: `Rules` engine (§8).
- `:286`: an entry stopped out on its own candle is silently dropped from the ledger (evaluated on pre-signal price action). Fix: stop checked from the fill bar with the "stopped on entry bar" golden (§8).
- `:154`: the stability check passes days-per-split as `positions_per_day`, inflating block Sharpe by orders of magnitude. Fix: daily Sharpe (§8).
- `:99`: the equity curve omits the starting balance, so drawdown from initial equity and the first trade's return are wrong. Fix: metrics rewrite must prepend the starting equity point (§8).
- `:378`: `ZeroDivisionError` when Kite returns no candles (fresh IPO, bad ticker) aborts the whole pre-market run. Fix: `verdict='no_trades'`/`error` per run (§8).
- `:255`: the catch-all in `categorize` turns transient errors (429, `TokenException`, timeouts) into a permanent "invalid" verdict for the day. Fix: retry/backoff rules and `verdict='error'` (§8).
- `:201, 56`: unguarded quote/margin REST calls (1 req/s limit) abort the run. Fix: rate limiter and per-run error verdicts (§8).
- ⚠ `:57`: no MIS-eligibility check; a valid-scored stock may be untradeable intraday. Fix: MIS sheet + `order_margins` (§8).
- `:66`: intraday leverage rounded to the nearest integer can round up past the real leverage. Fix: sheet value verified by `order_margins` (§8).
- `:240`: unadjusted Kite candles across a split/bonus ex-date feed a price cliff into the indicators. Fix: 08:30 corporate-action gate (§8).
- `:294`: no spread or slippage modelled. Fix: `slippage_bps` in `Rules`, spread gate live (§8, §10).
- `:70`: env var read in a default argument at import time. Fix: `config.py` (§13).

**Candle construction (`chart.py`, `date_util.py`)**
- ⚠ `chart.py:146, 135` (5 lenses): the in-progress 3m/5m Kite candle from the mid-session backfill is stored as complete and never replaced, feeding a partial bar to SMA/SAR. Fix: backfill only completed buckets, `source` flags, boundary-only writes (§7).
- ⚠ `chart.py:55, 162, 179, 181` (5 lenses): no session gate; after 15:30 (and during pre-open) flat 3m/5m candles are fabricated at every boundary and the strategy evaluates them. Fix: session windows per instrument, no synthetic rows (§7).
- ⚠ `chart.py:80, 181`: feed outages are indistinguishable from no-trade minutes; missing minutes are fabricated as flat candles (unbounded by session time; a stale timestamp can spawn millions). Fix: outage windows, `degraded` flag, Kite backfill (§7).
- ⚠ `chart.py:182`: the missing-slot filler is a raw flat bar appended to the HA series without conversion and without advancing the chain. Fix: canonical HA chain (§7).
- ⚠ `chart.py:138`: the first-pass 1m/3m/5m backfill for every subscribed instrument fires in the same minute and trips the 3 req/s historical limit. Fix: per-process limiter and REST budget (§3, §7).
- `chart.py:63`: intraday backfill stops at the subscription minute instead of "now", leaving a hole. Fix: recovery backfill to `now − 2 min` (§7).
- ⚠ `chart.py:72`: an empty historical response raises `IndexError` inside the websocket callback and retries the REST call on every tick. Fix: no REST on the reactor thread (§7).
- ⚠ `chart.py:108, 162, 166`: 3m/5m aggregation snapshots the first instant of the boundary minute; late ticks then mutate 1m candles the aggregate already consumed. Fix: grace period, immutable closed minutes, late ticks counted not applied (§7).
- ⚠ `chart.py:44`: the `exchange_timestamp` guard checks key presence only; a `None` value crashes the tick thread. Fix: mode/field validation in the builder (§7).
- `chart.py:57`: the market-open gap before the first tick is neither backfilled nor produced. Fix: recovery backfill on subscribe (§7).
- ⚠ `date_util.py:26`: tick timestamps are relabelled as IST, not converted; on a non-IST host every tick is silently dropped. Fix: `TZ` assertion + zoneinfo conversion (§13).

**Websocket and process (`kite.py`, `trade.py`)**
- ⚠ `kite.py:53, 55`: any exception inside `on_ticks` tears down the websocket for all instruments and can loop on reconnect. Fix: `on_ticks` = `put_nowait` only (§7).
- ⚠ `kite.py:66`: a dead websocket is never detected; the main loop keeps synthesising candles and stops are never evaluated. Fix: watchdog + outage windows (§7).
- ⚠ `kite.py:98`: `subscribe()` before the first connect crashes; `subscribe`/`set_mode` are called from the main thread without `callFromThread`. Fix: pending buffer + `reactor.callFromThread` (§7).
- `kite.py:29, 31`: the token is validated once at startup, and only `TokenException` is caught. Fix: session file re-read on generation change and on `TokenException`; `token.invalid` alert (§13).
- ⚠ `trade.py:63`: one transient exception on the main loop ends the whole session for all instruments, with positions possibly open, no alert and no restart. Fix: supervised processes, DATA_BLIND, per-instrument error handling (§10, §11).
- `trade.py:40`: the TickerTape call has no timeout, status check or schema guard. Fix: validated discovery (§8).
- `trade.py:71`: the exit-time xlsx dump runs outside the `try` block and can mask the original exception. Dropped (`zt export` reads the DB).
- `logger_util.py:33`: the logger crashes at import unless the checkout directory is literally named `zerodha-automation`. Fix: `core/log.py` (§13).
- `logger_util.py:11`: the backtest trade ledger is logged at DEBUG, enabled only under a debugger. Fix: `backtest_trades` table (§5).
- ⚠ `file_util.py:46`: xlsx sheet names collide on duplicate instrument names. Fix: `zt export` keys by tradingsymbol.
