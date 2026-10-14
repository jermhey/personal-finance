"""Annual bootstrap of a backtest's own time-weighted returns.

One draw per year keeps a 30- or 60-year projection small. The draws are the
annual returns the historical replay already produced, so the fan describes
that portfolio's past, repeated. It does not apply a new glide path.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from pf.models import Forward


@dataclass(frozen=True)
class Projection:
    years: list[int]
    p10: list[float]
    p50: list[float]
    p90: list[float]
    deposited: list[float]
    success_rate: float | None
    trials: int
    annual_return_count: int
    mean_annual_return: float
    annual_volatility: float
    method: str


def project(
    annual_returns: list[tuple[int, float]],
    start_value: float,
    forward: Forward,
    seed: int = 42,
) -> Projection | None:
    rates = [rate for _year, rate in annual_returns]
    if len(rates) < 5:
        return None
    if start_value <= 0 and forward.annual_contribution <= 0:
        return None
    mean = sum(rates) / len(rates)
    if len(rates) > 1:
        var = sum((rate - mean) ** 2 for rate in rates) / (len(rates) - 1)
        vol = math.sqrt(var)
    else:
        vol = 0.0

    rng = random.Random(seed)
    paths: list[list[float]] = []
    successes = 0
    for _ in range(forward.trials):
        wealth = start_value
        path = [wealth]
        ruined = False
        for _year in range(forward.years):
            if ruined:
                path.append(0.0)
                continue
            wealth += forward.annual_contribution
            wealth *= 1.0 + rng.choice(rates)
            wealth -= forward.annual_withdrawal
            if wealth <= 0:
                ruined = True
                wealth = 0.0
            path.append(wealth)
        if not ruined:
            successes += 1
        paths.append(path)

    p10: list[float] = []
    p50: list[float] = []
    p90: list[float] = []
    for index in range(forward.years + 1):
        column = sorted(path[index] for path in paths)
        p10.append(_percentile(column, 0.10))
        p50.append(_percentile(column, 0.50))
        p90.append(_percentile(column, 0.90))
    deposited = [
        start_value + year * forward.annual_contribution - year * forward.annual_withdrawal
        for year in range(forward.years + 1)
    ]
    success = None if forward.annual_withdrawal <= 0 else successes / forward.trials
    method = (
        f"Annual bootstrap of this backtest's time-weighted returns, "
        f"{forward.trials} trials, seed {seed}. Each year is drawn independently "
        f"from {len(rates)} historical years (mean {mean:.1%}, volatility {vol:.1%}). "
        "A contribution is added at the start of the year, the sampled return is applied, "
        "then the withdrawal is taken. Allocation changes inside the backtest are already "
        "inside those returns; this projection does not follow a new glide path."
    )
    return Projection(
        years=list(range(forward.years + 1)),
        p10=p10,
        p50=p50,
        p90=p90,
        deposited=deposited,
        success_rate=success,
        trials=forward.trials,
        annual_return_count=len(rates),
        mean_annual_return=mean,
        annual_volatility=vol,
        method=method,
    )


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * p
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[low]
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight
