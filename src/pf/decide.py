"""Two decisions that reuse a mix's own past years.

Outlook shuffles those years into a range.
Lasting walks every past window of the same length.

Market presets use yearly US large-stock returns and 10-year Treasury returns.
A custom mix can use those Treasury returns or a bond fund the person names.
Sample mode stays on synthetic fund prices so the tools can be tried offline.
"""

from __future__ import annotations

import random
import re
from datetime import date

from pf.cpi import level
from pf.engine import replay
from pf.models import Flow, Forward, Plan, RebalanceRule, Regime, Sleeve
from pf.montecarlo import project
from pf.prices import PriceStore, synthetic_series
from pf.rates import SP500, TBOND

STOCK = "VFINX"
BOND = "VBMFX"
_SYMBOL = re.compile(r"^[A-Z0-9.^=-]{1,12}$")

PRESETS: dict[str, dict] = {
    "stocks": {
        "label": "Mostly stocks",
        "stock_weight": 1.0,
        "glide_to": 0.6,
        "detail": "US large stocks, including dividends, yearly from 1928. Shifting toward bonds uses 10-year Treasuries.",
        "sample_detail": "A long-lived US stock fund. These are sample prices, not market history.",
    },
    "balanced": {
        "label": "A balanced mix",
        "stock_weight": 0.6,
        "glide_to": 0.4,
        "detail": "60% US large stocks, with dividends, and 40% in 10-year Treasury bonds. The yearly record runs from 1928.",
        "sample_detail": "60% in a stock fund and 40% in a bond fund. These are sample prices, not market history.",
    },
    "bonds": {
        "label": "Mostly bonds",
        "stock_weight": 0.2,
        "glide_to": None,
        "detail": "20% US large stocks and 80% in 10-year Treasury bonds. The yearly record runs from 1928.",
        "sample_detail": "20% in a stock fund and 80% in a bond fund. These are sample prices, not market history.",
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
    stock_weight: float = 0.6,
    stock_symbol: str = STOCK,
    bond_symbol: str = BOND,
    bond_source: str = "treasury",
) -> dict:
    _check_years(years, 1, 60, "Looking ahead")
    _check_money(starting, annual_contribution, annual_withdrawal)
    history = _history(mix, stock_weight, stock_symbol, bond_symbol, bond_source, source, store, glide)
    use_glide = bool(
        glide and history["glide_to"] is not None and abs(history["glide_to"] - history["stock_weight"]) > 1e-9
    )
    if use_glide and history["pairs"]:
        projection = _glide(
            history["pairs"],
            history["stock_weight"],
            float(history["glide_to"]),
            starting,
            annual_contribution,
            annual_withdrawal,
            years,
            trials,
        )
        count = len(history["pairs"])
    else:
        use_glide = False
        projection = project(
            history["annual"],
            starting,
            Forward(
                years=years,
                annual_contribution=annual_contribution,
                annual_withdrawal=annual_withdrawal,
                trials=trials,
            ),
        )
        count = len(history["annual"])
    return _outlook_payload(
        mix,
        history,
        use_glide,
        count,
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
    certainty: float = 0.9,
    stock_weight: float = 0.6,
    stock_symbol: str = STOCK,
    bond_symbol: str = BOND,
    bond_source: str = "treasury",
) -> dict:
    _check_years(years, 1, 50, "The stretch")
    if starting < 0 or annual_spend < 0:
        raise ValueError("The starting amount and the yearly spending are zero or more.")
    if not 0.5 <= certainty <= 1:
        raise ValueError("Certainty must be between 50% and 100%.")
    history = _history(mix, stock_weight, stock_symbol, bond_symbol, bond_source, source, store, False)
    annual = history["annual"]
    base = {
        "mix": mix,
        "mix_label": history["label"],
        "detail": history["detail"],
        "inflation": inflation,
        "horizon": years,
        "certainty": certainty,
        "history_start": history["start"].isoformat() if history["start"] else None,
        "history_end": history["end"].isoformat() if history["end"] else None,
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
            success_rate=None,
            spend_at_certainty=None,
            periods=[],
            plain="",
        )
        return base
    periods = _roll(annual, starting, annual_spend, years, inflation)
    lasted_count = sum(1 for period in periods if period["ok"])
    endings = sorted(period["ending"] for period in periods)
    shortest = min(period["lasted"] for period in periods)
    median = _median(endings)
    success = lasted_count / len(periods)
    affordable = _spend_for_certainty(annual, starting, years, inflation, certainty)
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
        f"That is a historical certainty of {success:.0%}. "
        f"Spending of about ${affordable:,.0f} a year would have lasted in at least {certainty:.0%} of those periods. "
        f"{spend_line} Spending comes out at the start of each year, then what remains earns that year's return. "
        f"{short_line} The bars are money left, in {dollars}. "
        "This check does not include Social Security, a pension, or taxes."
    )
    base.update(
        unavailable=None,
        tested=len(periods),
        lasted=lasted_count,
        shortest=shortest,
        median_ending=round(median, 2),
        success_rate=round(success, 4),
        spend_at_certainty=round(affordable, 2),
        periods=periods,
        plain=plain,
    )
    return base


def _history(mix, stock_weight, stock_symbol, bond_symbol, bond_source, source, store, want_pairs: bool) -> dict:
    if mix != "custom":
        spec = _preset(mix)
        weight = spec["stock_weight"]
        if source == "sample":
            annual, start, end = _mix_returns(_fund_weights(weight), source, store)
            pairs = _sample_pairs(source, store) if want_pairs else []
            detail = spec["sample_detail"]
        else:
            annual, start, end = _embedded(weight)
            pairs = _embedded_pairs()
            detail = spec["detail"]
        return {
            "label": spec["label"],
            "detail": detail,
            "stock_weight": weight,
            "glide_to": spec["glide_to"],
            "annual": annual,
            "pairs": pairs,
            "start": start,
            "end": end,
        }
    if not 0 <= stock_weight <= 1:
        raise ValueError("The stock share must be between 0% and 100%.")
    stock_symbol = _clean_symbol(stock_symbol)
    glide_to = None if stock_weight <= 0.2 else max(0.2, stock_weight - 0.2)
    if source == "sample" or bond_source == "fund":
        bond_symbol = _clean_symbol(bond_symbol)
        if stock_symbol == bond_symbol and 0 < stock_weight < 1:
            raise ValueError("Use a different fund for the bonds, or put the whole mix in one fund.")
        annual, start, end = _mix_returns(
            _fund_weights(stock_weight, stock_symbol, bond_symbol), source, store
        )
        pairs = _two_pairs(stock_symbol, bond_symbol, source, store) if want_pairs and 0 < stock_weight < 1 else []
        if stock_weight >= 1:
            detail = f"All in {stock_symbol}."
        elif stock_weight <= 0:
            detail = f"All in {bond_symbol}."
        else:
            detail = f"{stock_weight:.0%} in {stock_symbol} and {1 - stock_weight:.0%} in {bond_symbol}."
        if source == "sample":
            detail += " These are sample prices, not market history."
    else:
        if bond_source != "treasury":
            raise ValueError("Choose Treasury bonds or a bond fund.")
        if stock_weight <= 0:
            annual, start, end = _embedded(0.0)
            pairs = []
            detail = "All in 10-year Treasury bonds, yearly from 1928."
        elif stock_weight >= 1:
            annual, start, end = _mix_returns(((stock_symbol, 1.0),), source, store)
            pairs = []
            detail = f"All in {stock_symbol}."
        else:
            stock_annual, _, _ = _mix_returns(((stock_symbol, 1.0),), source, store)
            annual, start, end = _blend(stock_annual, stock_weight)
            pairs = _blend_pairs(stock_annual) if want_pairs else []
            detail = (
                f"{stock_weight:.0%} in {stock_symbol} and {1 - stock_weight:.0%} in 10-year Treasury bonds. "
                "Treasury results include the price change, not just the yield."
            )
    return {
        "label": "My mix",
        "detail": detail,
        "stock_weight": stock_weight,
        "glide_to": glide_to,
        "annual": annual,
        "pairs": pairs,
        "start": start,
        "end": end,
    }


def _embedded(stock_weight: float):
    years = sorted(set(SP500) & set(TBOND))
    annual = [
        (year, stock_weight * SP500[year] + (1 - stock_weight) * TBOND[year])
        for year in years
    ]
    return annual, date(years[0], 1, 1), date(years[-1], 12, 31)


def _embedded_pairs():
    years = sorted(set(SP500) & set(TBOND))
    return [(SP500[year], TBOND[year]) for year in years]


def _blend(stock_annual, stock_weight: float):
    stock_map = dict(stock_annual)
    years = sorted(set(stock_map) & set(TBOND))
    if not years:
        raise ValueError("That stock fund does not overlap the Treasury record.")
    annual = [
        (year, stock_weight * stock_map[year] + (1 - stock_weight) * TBOND[year])
        for year in years
    ]
    return annual, date(years[0], 1, 1), date(years[-1], 12, 31)


def _blend_pairs(stock_annual):
    stock_map = dict(stock_annual)
    years = sorted(set(stock_map) & set(TBOND))
    return [(stock_map[year], TBOND[year]) for year in years]


def _fund_weights(stock_weight: float, stock: str = STOCK, bond: str = BOND):
    if stock_weight >= 1 - 1e-9:
        return ((stock, 1.0),)
    if stock_weight <= 1e-9:
        return ((bond, 1.0),)
    return ((stock, stock_weight), (bond, 1 - stock_weight))


def _sample_pairs(source: str, store: PriceStore | None):
    stock, _, _ = _mix_returns(((STOCK, 1.0),), source, store)
    bond, _, _ = _mix_returns(((BOND, 1.0),), source, store)
    return _aligned_pairs(stock, bond)


def _two_pairs(stock_symbol: str, bond_symbol: str, source: str, store: PriceStore | None):
    stock, _, _ = _mix_returns(((stock_symbol, 1.0),), source, store)
    bond, _, _ = _mix_returns(((bond_symbol, 1.0),), source, store)
    return _aligned_pairs(stock, bond)


def _aligned_pairs(stock, bond):
    stock_map = dict(stock)
    bond_map = dict(bond)
    years = sorted(set(stock_map) & set(bond_map))
    return [(stock_map[year], bond_map[year]) for year in years]


def _roll(annual, starting, annual_spend, years, inflation):
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
    return periods


def _spend_for_certainty(annual, starting, years, inflation, certainty) -> float:
    def success(spend: float) -> float:
        periods = _roll(annual, starting, spend, years, inflation)
        if not periods:
            return 0.0
        return sum(1 for period in periods if period["ok"]) / len(periods)

    if success(0) < certainty:
        return 0.0
    low = 0.0
    high = max(starting, 1.0)
    while success(high) >= certainty and high < starting * 5:
        high *= 1.5
    for _step in range(28):
        mid = (low + high) / 2
        if success(mid) >= certainty:
            low = mid
        else:
            high = mid
    return low


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) % 2:
        return values[len(values) // 2]
    return (values[len(values) // 2 - 1] + values[len(values) // 2]) / 2


def _preset(mix: str) -> dict:
    if mix not in PRESETS:
        raise ValueError("Choose a preset, or build your own mix.")
    return PRESETS[mix]


def _clean_symbol(value: str) -> str:
    cleaned = (value or "").strip().upper()
    if _SYMBOL.match(cleaned) is None:
        raise ValueError("Symbols use letters, digits, and . ^ = - only.")
    return cleaned


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


def _outlook_payload(mix, history, use_glide, count, projection, trials):
    label = history["label"].lower()
    history_start = history["start"]
    history_end = history["end"]
    if history_start is None or history_end is None or count < 5:
        plain = (
            f"{history['label']} needs five full years of history before a range can be drawn. "
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
                f"The mix starts at {history['stock_weight']:.0%} stocks and ends near {float(history['glide_to']):.0%} stocks. "
                "Each shuffled year keeps that year's stock result and bond result together. "
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
        "mix_label": history["label"],
        "detail": history["detail"],
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
