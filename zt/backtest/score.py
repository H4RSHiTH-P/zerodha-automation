"""Instrument score: `_normalize` and `_calculate_score` from performance.py, unchanged."""

from zt.backtest.metrics import Metrics


def normalize(value, min_value, max_value) -> float:
    value = max(min(value, max_value), min_value)
    return (value - min_value) / (max_value - min_value)


def score(metrics: Metrics) -> float:
    sharpe_ratio_score = normalize(metrics.sharpe_ratio, 0, 4)
    profit_factor_score = normalize(metrics.profit_factor, 1, 3)
    recovery_factor_score = normalize(metrics.recovery_factor, 0, 10)
    expectancy_rate_score = normalize(metrics.expectancy_rate, 0, 100)
    win_rate_score = normalize(metrics.win_rate, 0.4, 0.7)
    drawdown_score = 1 - normalize(metrics.max_drawdown_percentage, 0, 0.25)
    return (
        sharpe_ratio_score * 0.30
        + profit_factor_score * 0.20
        + recovery_factor_score * 0.20
        + expectancy_rate_score * 0.15
        + win_rate_score * 0.05
        + drawdown_score * 0.10
    )
