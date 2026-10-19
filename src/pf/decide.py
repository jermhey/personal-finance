"""Two decisions that reuse a mix's own past years.

Outlook shuffles those years into a range.
Lasting walks every past window of the same length.
"""

from __future__ import annotations

import random
from datetime import date

from pf.cpi import level
from pf.engine import replay
from pf.models import Flow, Forward, Plan, RebalanceRule, Regime, Sleeve
from pf.montecarlo import project
from pf.prices import PriceStore, synthetic_series

STOCK = "VFINX"
BOND = "VBMFX"

PRESETS: dict[str, dict] = {
    "stocks": {
        "label": "Mostly stocks",
        "detail": "A long-lived US stock fund",
        "weights": ((STOCK, 1.0),),
        "glide_to": 0.6,
    },
    "balanced": {
        "label": "A balanced mix",
        "detail": "60% in a stock fund and 40% in a bond fund",
        "weights": ((STOCK, 0.6), (BOND, 0.4)),
        "glide_to": 0.4,
    },
    "bonds": {
        "label": "Mostly bonds",
        "detail": "20% in a stock fund and 80% in a bond fund",
        "weights": ((STOCK, 0.2), (BOND, 0.8)),
        "glide_to": None,
    },
}


def outlook(
    mix: str,
    years: int,
    starting: float,
    annual_contribution: float,
    annual_withdrawal: float,
    glide: bool,
    trials: int,
    source: str,
    store: PriceStore | None,
) -> dict:
    spec = _preset(mix)
    _check_years(years, 1, 60, "Looking ahead")
    _check_money(starting, annual_contribution, annual_withdrawal)
    start_weight = dict(spec["weights"]).get(STOCK, 0.0)
    glide_to = spec["glide_to"]
    use_glide = bool(glide and glide_to is not None and abs(glide_to - start_weight) > 1e-9)
    if use_glide:
        stock, _, _ = _mix_returns(((STOCK, 1.0),), source, store)
        bond, _, _ = _mix_returns(((BOND, 1.0),), source, store)
        stock_map = dict(stock)
        bond_map = dict(bond)
        shared = sorted(set(stock_map) & set(bond_map))
        pairs = [(stock_map[year], bond_map[year]) for year in shared]
        history_start = date(shared[0], 1, 1) if shared else None
        history_end = date(shared[-1], 12, 31) if shared else None
        projection = _glide(
            pairs,
            start_weight,
            float(glide_to),
            starting,
            annual_contribution,
            annual_withdrawal,
            years,
            trials,
        )
        count = len(pairs)
    else:
        annual, history_start, history_end = _mix_returns(spec["weights"], source, store)
        projection = project(
            annual,
            starting,
            Forward(
                years=years,
                annual_contribution=annual_contribution,
                annual_withdrawal=annual_withdrawal,
                trials=trials,
            ),
        )
        count = len(annual)
        pairs = [(rate, 0.0) for _year, rate in annual]
    return _outlook_payload(
        mix,
        spec,
        use_glide,
        start_weight,
        glide_to,
        history_start,
        history_end,
        count,
        pairs,
        projection,
        trials,
    )


def lasting(
    mix: str,
    years: int,
    starting: float,
    annual_spend: float,
    inflation: bool,
    source: str,
    store: PriceStore | None,
) -> dict:
    spec = _preset(mix)
    _check_years(years, 1, 50, "The stretch")
    if starting < 0 or annual_spend < 0:
        raise ValueError("The starting amount and the yearly spending are zero or more.")
    annual, history_start, history_end = _mix_returns(spec["weights"], source, store)
    base = {
        "mix": mix,
        "mix_label": spec["label"],
        "detail": spec["detail"],
        "inflation": inflation,
        "horizon": years,
        "history_start": history_start.isoformat(),
        "history_end": history_end.isoformat(),
        "years_used": len(annual),
    }
    if len(annual) < years:
        base.update(
            unavailable=(
                f"A {years}-year check needs {years} full years of this mix. "
                f"The history has {len(annual)}."
            ),
            tested=0,
            lasted=0,
            shortest=0,
            median_ending=0,
            periods=[],
            plain="",
        )
        return base
    periods = []
    for index in range(len(annual) - years + 1):
        window = annual[index : index + years]
        start_year = window[0][0]
        end_year = window[-1][0]
        wealth = starting
        paid = years
        for step, (year, rate) in enumerate(window):
            spend = annual_spend
            if inflation:
                spend = annual_spend * level(date(year, 1, 1)) / level(date(start_year, 1, 1))
            wealth -= spend
            if wealth <= 0:
                paid = step
                wealth = 0.0
                break
            wealth *= 1.0 + rate
        if inflation and wealth:
            wealth *= level(date(start_year, 1, 1)) / level(date(end_year, 1, 1))
        periods.append(
            {
                "start": start_year,
                "lasted": paid,
                "ending": round(wealth, 2),
                "ok": paid == years and wealth > 0,
            }
        )
    lasted_count = sum(1 for period in periods if period["ok"])
    endings = sorted(period["ending"] for period in periods)
    shortest = min(period["lasted"] for period in periods)
    median = endings[len(endings) // 2] if len(endings) % 2 else (
        endings[len(endings) // 2 - 1] + endings[len(endings) // 2]
    ) / 2
    spend_line = (
        "Spending rises with the cost of living."
        if inflation
        else "Spending stays the same number of dollars each year."
    )
    if shortest == years:
        short_line = "Every past period still had money at the end."
    elif shortest == 0:
        short_line = "The shortest run ran out in the first year."
    else:
        short_line = f"The shortest run ran out in year {shortest + 1}."
    dollars = "the dollars of the year it started" if inflation else "the dollars of the ending year"
    plain = (
        f"In {lasted_count} of {len(periods)} past {years}-year periods, money was still left at the end. "
        f"{spend_line} Spending comes out at the start of each year, then what remains earns that year's return. "
        f"{short_line} The bars are money left, in {dollars}."
    )
    base.update(
        unavailable=None,
        tested=len(periods),
        lasted=lasted_count,
        shortest=shortest,
        median_ending=round(median, 2),
        periods=periods,
        plain=plain,
    )
    return base


def _preset(mix: str) -> dict:
    if mix not in PRESETS:
        raise ValueError("Choose mostly stocks, a balanced mix, or mostly bonds.")
    return PRESETS[mix]


def _check_years(years: int, low: int, high: int, label: str) -> None:
    if not low <= years <= high:
        raise ValueError(f"{label} must be between {low} and {high} years.")


def _check_money(starting: float, contribution: float, withdrawal: float) -> None:
    if starting < 0 or contribution < 0 or withdrawal < 0:
        raise ValueError("Amounts are entered as zero or more.")


def _mix_returns(weights: tuple[tuple[str, float], ...], source: str, store: PriceStore | None):
    symbols = [symbol for symbol, _weight in weights]
    series, start, end = _load(symbols, source, store)
    weight_map = {symbol: weight for symbol, weight in weights}
    plan = Plan(
        start=start,
        end=end,
        sleeves=tuple(Sleeve(symbol, drip=True) for symbol in symbols),
        flows=(Flow(10_000, "once", start, None, weight_map),),
        regimes=(Regime(start, weight_map),),
        rebalance=RebalanceRule("yearly"),
    )
    result = replay(plan, series)
    return result.annual_returns, start, end


def _load(symbols: list[str], source: str, store: PriceStore | None):
    if source == "sample":
        start = date(1980, 1, 2)
        end = date(2024, 12, 31)
        return {symbol: synthetic_series(symbol, start, end) for symbol in symbols}, start, end
    if store is None:
        raise ValueError("Market prices are unavailable.")
    loaded = {symbol: store.load(symbol, date(1970, 1, 1), date.today()) for symbol in symbols}
    start = max((series.listed_on or series.bars[0].date) for series in loaded.values())
    end = min(series.bars[-1].date for series in loaded.values())
    if end < start:
        raise ValueError("These funds do not have overlapping prices.")
    return loaded, start, end


def _glide(pairs, start_weight, end_weight, starting, contribution, withdrawal, years, trials):
    if len(pairs) < 5 or (starting <= 0 and contribution <= 0):
        return None
    rng = random.Random(42)
    paths = []
    successes = 0
    for _trial in range(trials):
        wealth = starting
        path = [wealth]
        ruined = False
        for year in range(years):
            if ruined:
                path.append(0.0)
                continue
            wealth += contribution
            weight = start_weight if years == 1 else start_weight + (end_weight - start_weight) * year / (years - 1)
            stock, bond = rng.choice(pairs)
            wealth *= 1.0 + weight * stock + (1.0 - weight) * bond
            wealth -= withdrawal
            if wealth <= 0:
                ruined = True
                wealth = 0.0
            path.append(wealth)
        if not ruined:
            successes += 1
        paths.append(path)
    return _fan(paths, successes, starting, contribution, withdrawal, years, trials)


def _fan(paths, successes, starting, contribution, withdrawal, years, trials):
    p10, p50, p90 = [], [], []
    for index in range(years + 1):
        column = sorted(path[index] for path in paths)
        p10.append(_percentile(column, 0.10))
        p50.append(_percentile(column, 0.50))
        p90.append(_percentile(column, 0.90))
    deposited = [
        starting + year * contribution - year * withdrawal
        for year in range(years + 1)
    ]
    success = None if withdrawal <= 0 else successes / trials
    return {
        "years": list(range(years + 1)),
        "p10": p10,
        "p50": p50,
        "p90": p90,
        "deposited": deposited,
        "success_rate": success,
        "trials": trials,
    }


def _percentile(sorted_values: list[float], p: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (len(sorted_values) - 1) * p
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    if low == high:
        return sorted_values[low]
    weight = rank - low
    return sorted_values[low] * (1 - weight) + sorted_values[high] * weight


def _outlook_payload(mix, spec, use_glide, start_weight, glide_to, history_start, history_end, count, pairs, projection, trials):
    label = spec["label"].lower()
    if history_start is None or history_end is None or count < 5:
        plain = (
            f"{spec['label']} needs five full years of history before a range can be drawn. "
            f"This history has {count}."
        )
        fan = None
    elif projection is None:
        plain = "Enter an amount you already have, or an amount you will add each year."
        fan = None
    else:
        if use_glide:
            plain = (
                f"The paths shuffle {count} past years, from {history_start.year} through {history_end.year}. "
                f"The mix starts at {start_weight:.0%} stocks and ends near {float(glide_to):.0%} stocks. "
                "Each shuffled year uses that same past year for both funds. "
            )
        else:
            plain = (
                f"The paths shuffle {count} past years of {label}, from {history_start.year} through {history_end.year}. "
            )
        plain += (
            "The middle line is a typical shuffle. The top is a lucky one and the bottom is a rough one. "
            "Money you add goes in at the start of the year. Spending comes out after that year's result. "
            f"{trials} shuffles. This is a range from the past, not a promise about the future."
        )
        fan = _public_fan(projection)
    return {
        "mix": mix,
        "mix_label": spec["label"],
        "detail": spec["detail"],
        "glide": use_glide,
        "history_start": None if history_start is None else history_start.isoformat(),
        "history_end": None if history_end is None else history_end.isoformat(),
        "years_used": count,
        "plain": plain,
        "unavailable": None if fan else plain,
        "montecarlo": fan,
    }


def _public_fan(projection) -> dict:
    if isinstance(projection, dict):
        fan = projection
        success = fan["success_rate"]
        return {
            "years": fan["years"],
            "p10": [round(value, 2) for value in fan["p10"]],
            "p50": [round(value, 2) for value in fan["p50"]],
            "p90": [round(value, 2) for value in fan["p90"]],
            "deposited": [round(value, 2) for value in fan["deposited"]],
            "success_rate": None if success is None else round(success, 4),
            "trials": fan["trials"],
        }
    return {
        "years": projection.years,
        "p10": [round(value, 2) for value in projection.p10],
        "p50": [round(value, 2) for value in projection.p50],
        "p90": [round(value, 2) for value in projection.p90],
        "deposited": [round(value, 2) for value in projection.deposited],
        "success_rate": None if projection.success_rate is None else round(projection.success_rate, 4),
        "trials": projection.trials,
    }
