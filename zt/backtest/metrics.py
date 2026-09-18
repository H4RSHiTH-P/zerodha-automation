"""Performance metrics. Same formulas as the original `_calculate_metrics`, with two corrections:
trading days are distinct candle dates (not candles), and the Sharpe ratio is computed on daily P&L."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np

from zt.backtest.engine import Trade


@dataclass(frozen=True)
class Metrics:
    win_positions: int
    loss_positions: int
    total_positions: int
    win_rate: float
    loss_rate: float
    average_profit: float
    average_loss: float
    gross_profit: float
    gross_loss: float
    net_profit_loss: float
    profit_factor: float
    expectancy_rate: float
    max_drawdown_percentage: float
    sharpe_ratio: float
    recovery_factor: float
    max_single_profit: float
    trading_days: int

    def as_dict(self) -> dict:
        return asdict(self)


def daily_pnl(trades: list[Trade], dates: list[date]) -> np.ndarray:
    by_day = {d: 0.0 for d in dates}
    for t in trades:
        by_day[t.exit_ts.date()] = by_day.get(t.exit_ts.date(), 0.0) + t.pnl
    return np.array([by_day[d] for d in sorted(by_day)])


def compute(trades: list[Trade], dates: list[date], equity: float) -> Metrics | None:
    if not trades:
        return None
    pnl = np.array([t.pnl for t in trades])
    total = len(pnl)
    wins, losses = int(np.sum(pnl > 0)), int(np.sum(pnl < 0))
    win_rate, loss_rate = wins / total, losses / total
    average_profit = float(np.mean(pnl[pnl > 0])) if wins else 0.0
    average_loss = float(np.mean(pnl[pnl < 0])) if losses else 0.0
    gross_profit = float(np.sum(pnl[pnl > 0]))
    gross_loss = float(abs(np.sum(pnl[pnl < 0])))
    net = gross_profit - gross_loss

    curve = equity + np.concatenate([[0.0], np.cumsum(pnl)])  # starts at the initial equity
    drawdown = np.maximum.accumulate(curve) - curve
    max_drawdown = float(np.max(drawdown))

    returns = daily_pnl(trades, dates) / equity
    sharpe = (
        float(np.mean(returns) / np.std(returns) * np.sqrt(252)) if len(returns) > 1 and np.std(returns) > 0 else 0.0
    )

    return Metrics(
        win_positions=wins,
        loss_positions=losses,
        total_positions=total,
        win_rate=float(win_rate),
        loss_rate=float(loss_rate),
        average_profit=average_profit,
        average_loss=average_loss,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit_loss=float(net),
        profit_factor=float(gross_profit / gross_loss)
        if gross_loss > 0
        else (float("inf") if gross_profit > 0 else 0.0),
        expectancy_rate=float(win_rate * average_profit - loss_rate * abs(average_loss)),
        max_drawdown_percentage=max_drawdown / equity,
        sharpe_ratio=sharpe,
        recovery_factor=float(net / max_drawdown) if max_drawdown > 0 else 0.0,
        max_single_profit=float(np.max(pnl)),
        trading_days=len(dates),
    )
