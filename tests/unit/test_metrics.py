from datetime import datetime, timedelta

from tests.unit.factories import PERFORMANCE as CFG
from zt.backtest import filters, metrics
from zt.backtest.engine import BacktestResult, Trade
from zt.backtest.presets import PINE_V2
from zt.backtest.score import score
from zt.core.clock import IST

D0 = datetime(2026, 6, 1, 10, 0, tzinfo=IST)


def trade(n, pnl, day):
    ts = D0 + timedelta(days=day)
    return Trade(n, 1, 10, ts, ts, 100.0, 97.5, ts + timedelta(minutes=9), 100 + pnl / 10, "EXIT", 0.0, pnl)


def test_metrics_formulas_and_daily_sharpe():
    dates = [(D0 + timedelta(days=i)).date() for i in range(5)]
    m = metrics.compute([trade(1, -100, 0), trade(2, 300, 2), trade(3, -50, 2)], dates, 1000.0)
    assert (m.total_positions, m.win_positions, m.loss_positions, m.trading_days) == (3, 1, 2, 5)
    assert (m.gross_profit, m.gross_loss, m.net_profit_loss) == (300, 150, 150)
    assert m.profit_factor == 2 and m.max_drawdown_percentage == 0.1 and m.recovery_factor == 1.5
    assert m.average_loss == -75 and abs(m.expectancy_rate - (300 / 3 - 2 / 3 * 75)) < 1e-9
    daily = metrics.daily_pnl([trade(1, -100, 0), trade(2, 300, 2), trade(3, -50, 2)], dates)
    assert list(daily) == [-100, 0, 250, 0, 0]
    assert abs(m.sharpe_ratio - daily.mean() / daily.std() * 252**0.5) < 1e-9
    assert metrics.compute([], dates, 1000.0) is None


def result(trades, days=90):
    dates = [(D0 + timedelta(days=i)).date() for i in range(days)]
    return BacktestResult(PINE_V2, trades, days * 100, dates, D0, D0 + timedelta(days=days - 1))


def test_verdict_pass_fail_and_no_trades():
    winners = [trade(i + 1, 100 if i % 7 else -50, i) for i in range(90)]
    v = filters.verdict(result(winners), 100_000.0, CFG)
    assert v.verdict == "pass" and v.reasons == () and v.score > 0.9 and v.score == score(v.metrics)
    losers = [trade(i + 1, -100 if i % 7 else 50, i) for i in range(90)]
    f = filters.verdict(result(losers), 100_000.0, CFG)
    assert f.verdict == "fail" and any(r.startswith("profit factor") for r in f.reasons)
    assert filters.verdict(result([]), 100_000.0, CFG).verdict == "no_trades"


def test_stability_needs_every_block():
    only_recent = [trade(i + 1, 100 if i % 3 else -20, i) for i in range(60, 90)]  # last block only
    reasons = filters.stability(result(only_recent), 100_000.0, CFG)
    assert len(reasons) == 2 and all("trades < 8" in r for r in reasons)
    lucky = [trade(i + 1, 10, i) for i in range(89)] + [trade(90, 5000, 89)]
    assert any(r.startswith("lucky trade") for r in filters.verdict(result(lucky), 100_000.0, CFG).reasons)
