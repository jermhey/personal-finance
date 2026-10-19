"""Year-by-year measures for people who want more than the headline result."""

from __future__ import annotations

import math

from pf.rates import TBILL


def describe(annual_returns: list[tuple[int, float]]) -> dict:
    """Sharpe and the range of yearly results.

    The Sharpe ratio is the average yearly result above that year's 3-month
    Treasury bill, divided by how much those extra results vary.
    """

    if len(annual_returns) < 2:
        return {"unavailable": "A few full years are needed before these numbers mean anything."}
    returns = [rate for _year, rate in annual_returns]
    mean = sum(returns) / len(returns)
    volatility = _stdev(returns, mean)
    best = max(annual_returns, key=lambda item: item[1])
    worst = min(annual_returns, key=lambda item: item[1])
    paired = [(rate, TBILL[year]) for year, rate in annual_returns if year in TBILL]
    sharpe = None
    if len(paired) >= 5:
        excess = [rate - bill for rate, bill in paired]
        excess_mean = sum(excess) / len(excess)
        excess_vol = _stdev(excess, excess_mean)
        if excess_vol > 1e-12:
            sharpe = excess_mean / excess_vol
    return {
        "unavailable": None,
        "sharpe": None if sharpe is None else round(sharpe, 2),
        "volatility": round(volatility, 4),
        "best_year": best[0],
        "best_return": round(best[1], 4),
        "worst_year": worst[0],
        "worst_return": round(worst[1], 4),
        "years": len(annual_returns),
        "tbill_years": len(paired),
    }


def _stdev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)
