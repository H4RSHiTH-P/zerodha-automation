"""'Proven profitable': threshold table, stability over calendar blocks, lucky-trade cap -> verdict."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from zt.backtest import metrics as M
from zt.backtest.engine import BacktestResult
from zt.backtest.score import score as _score
from zt.config import Performance

BLOCK_DAYS = 30
MIN_BLOCK_TRADES = 8


@dataclass(frozen=True)
class Verdict:
    verdict: str  # 'pass' | 'fail' | 'no_trades'
    reasons: tuple[str, ...]
    metrics: M.Metrics | None
    score: float | None


def stability(result: BacktestResult, equity: float, cfg: Performance) -> list[str]:
    """Consecutive 30-calendar-day blocks ending at the window end, equity carried forward."""
    if not result.trading_dates:
        return ["no data"]
    end = result.trading_dates[-1]
    n_blocks = max(1, cfg.historic_duration // BLOCK_DAYS)
    reasons = []
    carried = equity
    for i in range(n_blocks, 0, -1):
        block_start = end - timedelta(days=BLOCK_DAYS * i - 1)
        block_end = end - timedelta(days=BLOCK_DAYS * (i - 1))
        trades = [t for t in result.trades if block_start <= t.exit_ts.date() <= block_end]
        dates = [d for d in result.trading_dates if block_start <= d <= block_end]
        label = f"block {n_blocks - i + 1} ({block_start}..{block_end})"
        m = M.compute(trades, dates, carried)
        if m is None or m.total_positions < MIN_BLOCK_TRADES:
            reasons.append(f"{label}: {len(trades)} trades < {MIN_BLOCK_TRADES}")
        else:
            if m.profit_factor < cfg.stability_profit_factor:
                reasons.append(f"{label}: profit factor {m.profit_factor:.2f} < {cfg.stability_profit_factor}")
            if m.sharpe_ratio < cfg.stability_sharpe_ratio:
                reasons.append(f"{label}: sharpe {m.sharpe_ratio:.2f} < {cfg.stability_sharpe_ratio}")
        carried += sum(t.pnl for t in trades)
    return reasons


def verdict(result: BacktestResult, equity: float, cfg: Performance) -> Verdict:
    m = M.compute(result.trades, result.trading_dates, equity)
    if m is None:
        return Verdict("no_trades", (), None, None)
    reasons = []
    for name, value, threshold, worse in [
        ("total positions", m.total_positions, cfg.min_positions, "less"),
        ("profit factor", m.profit_factor, cfg.min_profit_factor, "less"),
        ("sharpe ratio", m.sharpe_ratio, cfg.min_sharpe_ratio, "less"),
        ("recovery factor", m.recovery_factor, cfg.min_recovery_factor, "less"),
        ("max drawdown", m.max_drawdown_percentage, cfg.max_drawdown_percentage, "more"),
        ("expectancy", m.expectancy_rate, cfg.min_expectancy_rate, "less_equal"),
    ]:
        failed = {"less": value < threshold, "more": value > threshold, "less_equal": value <= threshold}[worse]
        if failed:
            reasons.append(f"{name} {value:.4g} is {worse.replace('_', ' or ')} than {threshold}")
    reasons += stability(result, equity, cfg)
    if m.max_single_profit > 0.25 * m.net_profit_loss:
        reasons.append(f"lucky trade: best trade {m.max_single_profit:.0f} > 25% of net {m.net_profit_loss:.0f}")
    s = _score(m)
    if not reasons and s < cfg.min_score:
        reasons.append(f"score {s:.3f} < {cfg.min_score}")
    return Verdict("fail" if reasons else "pass", tuple(reasons), m, s)
