
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
