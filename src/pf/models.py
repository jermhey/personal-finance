"""Plan inputs and replay outputs. The engine stays free of the web framework."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

Frequency = Literal["once", "weekly", "biweekly", "monthly", "quarterly", "yearly"]
ListingPolicy = Literal["skip", "park"]
RebalanceMode = Literal["never", "monthly", "quarterly", "yearly", "threshold"]

_SYMBOL = re.compile(r"^[A-Z0-9.^=-]{1,12}$")


@dataclass(frozen=True)
class Bar:
    date: date
    close: float
    dividend: float = 0.0
    split: float = 0.0


@dataclass(frozen=True)
class Series:
    symbol: str
    bars: tuple[Bar, ...]
    listed_on: date | None
    delisted_on: date | None = None

    def __post_init__(self) -> None:
        if any(b.close <= 0 for b in self.bars):
            raise ValueError(f"{self.symbol} has a non-positive close.")
        dates = [b.date for b in self.bars]
        if dates != sorted(dates):
            raise ValueError(f"{self.symbol} bars must be sorted by date.")


@dataclass(frozen=True)
class Sleeve:
    symbol: str
    drip: bool = True
    expense_ratio: float = 0.0


@dataclass(frozen=True)
class Regime:
    """Target weights from this date until the next regime. Weights are fractions of the account."""

    date: date
    weights: dict[str, float]


@dataclass(frozen=True)
class Flow:
    """A deposit (positive) or withdrawal (negative).

    allocation is None when the flow should follow the target weights in effect
    on its scheduled date. Otherwise it is an explicit fraction per symbol.
    """

    amount: float
    frequency: Frequency
    start: date
    end: date | None = None
    allocation: dict[str, float] | None = None


@dataclass(frozen=True)
class RebalanceRule:
    mode: RebalanceMode = "yearly"
    threshold: float = 0.05


@dataclass(frozen=True)
class Plan:
    start: date
    end: date
    sleeves: tuple[Sleeve, ...]
    flows: tuple[Flow, ...]
    regimes: tuple[Regime, ...] = ()
    listing_policy: ListingPolicy = "skip"
    rebalance: RebalanceRule = field(default_factory=RebalanceRule)


@dataclass(frozen=True)
class Forward:
    years: int = 30
    annual_contribution: float = 0.0
    annual_withdrawal: float = 0.0
    trials: int = 500


def validate_plan(plan: Plan, forward: Forward | None = None) -> None:
    if plan.end < plan.start:
        raise ValueError("End date is before the start date.")
    if (plan.end - plan.start).days > 365 * 80:
        raise ValueError("Use a horizon of 80 years or less.")
    if not plan.sleeves:
        raise ValueError("Add at least one holding.")
    if len(plan.sleeves) > 15:
        raise ValueError("Use 15 holdings or fewer.")
    symbols = [s.symbol for s in plan.sleeves]
    if any(_SYMBOL.match(sym) is None for sym in symbols):
        raise ValueError("Symbols use letters, digits, and . ^ = - only.")
    if any(not 0 <= sleeve.expense_ratio <= 0.05 for sleeve in plan.sleeves):
        raise ValueError("Yearly fund cost must be between 0% and 5%.")
    if len(set(symbols)) != len(symbols):
        raise ValueError("Each holding can appear once.")
    if len(plan.regimes) > 24:
        raise ValueError("Use 24 allocation changes or fewer.")
    seen_regime: set[date] = set()
    for regime in plan.regimes:
        if regime.date in seen_regime:
            raise ValueError(f"Two allocations share the date {regime.date.isoformat()}.")
        seen_regime.add(regime.date)
        unknown = set(regime.weights) - set(symbols)
        if unknown:
            raise ValueError(f"Allocation mentions {', '.join(sorted(unknown))}, which is not a holding.")
        if any(w < -1e-9 for w in regime.weights.values()):
            raise ValueError("Weights cannot be negative.")
        total = sum(regime.weights.values())
        if total > 1 + 1e-4:
            raise ValueError(
                f"Weights on {regime.date.isoformat()} add up to more than 100%."
            )
    if not plan.flows:
        raise ValueError("Add at least one deposit or withdrawal.")
    if len(plan.flows) > 20:
        raise ValueError("Use 20 cash flows or fewer.")
    for flow in plan.flows:
        if flow.frequency != "once" and flow.end is not None and flow.end < flow.start:
            raise ValueError("A cash flow ends before it starts.")
        if flow.allocation is not None:
            unknown = set(flow.allocation) - set(symbols)
            if unknown:
                raise ValueError(
                    f"A cash flow mentions {', '.join(sorted(unknown))}, which is not a holding."
                )
            if any(w < -1e-9 for w in flow.allocation.values()):
                raise ValueError("Cash-flow weights cannot be negative.")
            if sum(flow.allocation.values()) > 1 + 1e-4:
                raise ValueError("A cash-flow split adds up to more than 100%.")
    if plan.rebalance.mode == "threshold" and not (0 < plan.rebalance.threshold < 1):
        raise ValueError("Drift threshold must be between 0 and 100 percentage points.")
    if forward is not None:
        if not 1 <= forward.years <= 60:
            raise ValueError("Forward projection must be between 1 and 60 years.")
        if not 50 <= forward.trials <= 2000:
            raise ValueError("Use between 50 and 2000 trials.")
        if forward.annual_contribution < 0 or forward.annual_withdrawal < 0:
            raise ValueError("Forward contribution and withdrawal are entered as positive amounts.")


def add_months(day: date, months: int, anchor_day: int | None = None) -> date:
    """Advance by calendar months, keeping the original day-of-month when it exists."""

    cursor = day.month - 1 + months
    year = day.year + cursor // 12
    month = cursor % 12 + 1
    basis = day.day if anchor_day is None else anchor_day
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(basis, last))


def scheduled_dates(flow: Flow, horizon_start: date, horizon_end: date) -> list[date]:
    """Dates the investor intends, before they are snapped to a trading session."""

    end = horizon_end if flow.end is None else min(flow.end, horizon_end)
    if flow.frequency == "once":
        if horizon_start <= flow.start <= horizon_end:
            return [flow.start]
        return []
    if flow.start > end:
        return []

    dates: list[date] = []
    if flow.frequency in ("weekly", "biweekly"):
        step = timedelta(days=7 if flow.frequency == "weekly" else 14)
        cursor = flow.start
        if cursor < horizon_start:
            jumps = (horizon_start - cursor).days // step.days
            cursor = cursor + step * jumps
            if cursor < horizon_start:
                cursor += step
        while cursor <= end:
            if cursor >= horizon_start:
                dates.append(cursor)
            cursor += step
        return dates

    step_months = {"monthly": 1, "quarterly": 3, "yearly": 12}[flow.frequency]
    anchor_day = flow.start.day
    cursor = flow.start
    guard = 0
    while cursor < horizon_start and guard < 5000:
        cursor = add_months(cursor, step_months, anchor_day)
        guard += 1
    while cursor <= end and guard < 10000:
        if cursor >= horizon_start:
            dates.append(cursor)
        nxt = add_months(cursor, step_months, anchor_day)
        if nxt <= cursor:
            break
        cursor = nxt
        guard += 1
    return dates
