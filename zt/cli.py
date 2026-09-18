"""`zt` command line: migrate, login, status, record, backtest."""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from zt import config
from zt.core import clock, db, log

EX_CONFIG = 78  # misconfiguration: supervisors must not restart


def cmd_migrate(cfg, args) -> int:
    conn = db.connect(cfg.db_path)
    applied = db.migrate(conn)
    print(f"{cfg.db_path}: schema version {db.schema_version(conn)}" + (f", applied {applied}" if applied else ""))
    return 0


def cmd_login(cfg, args) -> int:
    from zt.kite import session

    if not args.request_token:
        print("Open this URL, log in with TOTP, then run: zt login <request_token from the redirect URL>")
        print(session.login_url(cfg))
        return 0
    s = session.login(cfg, args.request_token)
    print(f"logged in as {s.user_id}; token saved to {cfg.session_file} (valid until 06:00 tomorrow)")
    return 0


def cmd_status(cfg, args) -> int:
    from zt.kite import session

    s = session.load(cfg.session_file)
    if s is None:
        print("session: none (run `zt login`)")
        return 1
    print(f"session: {s.date} user {s.user_id} generation {s.generation}" + ("" if s.is_today else " (STALE)"))
    if s.is_today:
        user = session.verify(session.client(cfg))
        print(f"kite: {'token accepted' if user else 'token REJECTED, log in again'}")
    print(f"sqlite: {db.sqlite3.sqlite_version}")
    return 0


def cmd_record(cfg, args) -> int:
    from zt.kite import recorder, refdata, session

    kc_session = session.load(cfg.session_file)
    if kc_session is None or not kc_session.is_today:
        print("no session for today; run `zt login` first", file=sys.stderr)
        return 1
    instruments = refdata.load("NSE", cfg.data_dir / "refdata")
    tokens = [refdata.token_for(s.strip(), instruments) for s in args.symbols.split(",") if s.strip()]
    n = recorder.record(cfg.api_key, kc_session.access_token, tokens, cfg.data_dir / "ticks", args.minutes)
    print(f"wrote {n} ticks to {cfg.data_dir / 'ticks'}")
    return 0


def cmd_backtest(cfg, args) -> int:
    from zt.backtest import engine, filters, store
    from zt.backtest.presets import PRESETS
    from zt.candles.model import Interval
    from zt.core import holidays
    from zt.kite import refdata, session

    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    token = refdata.token_for(args.symbol, refdata.load("NSE", cfg.data_dir / "refdata"))
    end = clock.at(holidays.prev_trading_day(clock.today()), clock.session_end(False))
    start = clock.at(end.date() - timedelta(days=args.days), clock.MARKET_OPEN)
    try:
        kc = session.client(cfg)
    except session.SessionError:
        kc = None
    equity, charges = cfg.performance.equity_balance, (None if args.no_charges else cfg.charges)
    names = list(PRESETS) if args.compare else [args.rules]
    print(
        f"{args.symbol} token {token}  window {start:%Y-%m-%d} .. {end:%Y-%m-%d}  equity {equity:,.0f}  "
        f"leverage {args.leverage}  charges {'off' if charges is None else 'on'}"
    )
    print(
        f"{'interval':8} {'rules':10} {'trades':>6} {'net':>12} {'PF':>6} "
        f"{'sharpe':>7} {'maxDD%':>7} {'score':>6}  verdict"
    )
    for abbr in args.interval.split(","):
        interval = Interval.from_abbreviation(abbr)
        raw = store.load_history(conn, token, interval, start, end, kc)
        m1 = None
        if "manual" in names:
            m1 = {c.date_time: c.close for c in store.load_history(conn, token, Interval.ONE_MINUTE, start, end, kc)}
        for name in names:
            rules = PRESETS[name]
            result = engine.run(raw, rules, equity, args.leverage, charges, interval.seconds, m1)
            v = filters.verdict(result, equity, cfg.performance)
            store.save_run(conn, token, args.symbol, interval, result, v, equity, args.leverage, charges)
            m = v.metrics
            if m is None:
                print(f"{abbr:8} {name:10} {0:6} {'':>12} {'':>6} {'':>7} {'':>7} {'':>6}  no_trades")
                continue
            print(
                f"{abbr:8} {name:10} {m.total_positions:6} {m.net_profit_loss:12,.0f} {m.profit_factor:6.2f} "
                f"{m.sharpe_ratio:7.2f} {m.max_drawdown_percentage * 100:7.2f} {v.score:6.3f}  {v.verdict}"
            )
            for r in v.reasons:
                print(f"{'':30}- {r}")
            if args.trades:
                for t in result.trades:
                    print(
                        f"{'':4}{t.n:4} {'LONG ' if t.side > 0 else 'SHORT'} {t.qty:5} "
                        f"{t.entry_ts:%Y-%m-%d %H:%M} @{t.entry_price:9.2f} -> {t.exit_ts:%H:%M} @{t.exit_price:9.2f} "
                        f"{t.exit_reason:8} pnl {t.pnl:9.2f}"
                    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zt")
    parser.add_argument("--env", default=".env")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="create or upgrade the database").set_defaults(fn=cmd_migrate)
    p = sub.add_parser("login", help="print the login URL, or exchange a request_token for today's session")
    p.add_argument("request_token", nargs="?")
    p.set_defaults(fn=cmd_login)
    sub.add_parser("status", help="session, token and database state").set_defaults(fn=cmd_status)
    p = sub.add_parser("record", help="record live ticks to data/ticks/<date>.ndjson")
    p.add_argument("--symbols", required=True, help="comma-separated NSE tradingsymbols")
    p.add_argument("--minutes", type=float, default=30)
    p.set_defaults(fn=cmd_record)
    p = sub.add_parser("backtest", help="run the engine on cached/fetched Kite history for one symbol")
    p.add_argument("symbol")
    p.add_argument("--interval", default="3m,5m", help="comma-separated: 1m,3m,5m")
    p.add_argument("--rules", default="realistic", help="v2 | v3 | v4 | realistic | manual")
    p.add_argument("--compare", action="store_true", help="run every preset")
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="calendar days back from yesterday (default PERFORMANCE_HISTORIC_DURATION)",
    )
    p.add_argument("--leverage", type=float, default=5.0)
    p.add_argument("--no-charges", action="store_true")
    p.add_argument("--trades", action="store_true", help="print every trade")
    p.set_defaults(fn=cmd_backtest)
    args = parser.parse_args(argv)

    try:
        clock.ensure_local_tz()
        cfg = config.load(args.env)
        db.check_version()
    except (RuntimeError, config.ConfigError, db.SqliteTooOld) as e:
        print(f"zt: {e}", file=sys.stderr)
        return EX_CONFIG
    log.setup(args.command, cfg.logs_dir, cfg.secrets)
    if getattr(args, "days", None) is None and hasattr(args, "days"):
        args.days = cfg.performance.historic_duration
    return args.fn(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
