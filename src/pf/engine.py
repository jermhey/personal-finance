"""Replay deposits, withdrawals, dividends, and allocation changes at historical closes.

Event order on each session:
1. Splits, then cash dividends on shares already held.
2. Sell a holding on its delist date.
3. Mark value before today's external cash (this is the return).
4. Apply today's deposits, parked cash, and withdrawals.
5. Invest parked cash once a holding has a price.
6. Rebalance when the rule says so.
7. Mark the portfolio that the graph records.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date

from pf.models import Flow, Plan, Regime, Series, scheduled_dates


@dataclass
class Point:
    date: date
    value: float
    net_contributions: float
    cash: float
    sleeves: dict[str, float]


@dataclass
class SleeveReport:
    symbol: str
    shares: float
    value: float
    dividends_reinvested: float
    dividends_cash: float
    skipped: float
    invested: float
    listed_on: date | None
    delisted_on: date | None


@dataclass
class ReplayResult:
    points: list[Point]
    sleeve_reports: list[SleeveReport]
    net_contributions: float
    ending_value: float
    market_gain: float
    xirr: float | None
    twr_cagr: float | None
    skipped: float
    sessions: int
    sampling: str
    annual_returns: list[tuple[int, float]]
    notes: list[str]
    xirr_flows: list[tuple[date, float]]


@dataclass
class _Book:
    shares: dict[str, float]
    parked: dict[str, float]
    free_cash: float = 0.0
    net: float = 0.0
    div_reinvested: dict[str, float] = field(default_factory=dict)
    div_cash: dict[str, float] = field(default_factory=dict)
    skipped: dict[str, float] = field(default_factory=dict)
    invested: dict[str, float] = field(default_factory=dict)
    last_regime: int = -1
    last_period: tuple[int, ...] | None = None


def replay(plan: Plan, series: dict[str, Series]) -> ReplayResult:
    symbols = [s.symbol for s in plan.sleeves]
    missing = [s for s in symbols if s not in series]
    if missing:
        raise ValueError(f"No price series for {', '.join(missing)}.")
    drip = {s.symbol: s.drip for s in plan.sleeves}
    sessions = _sessions(series, plan.start, plan.end)
    notes: list[str] = []
    if not sessions:
        notes.append("None of these holdings have a price inside the window.")
        return _empty(plan, series, notes)

    session_set = set(sessions)
    buys, parks, cash_in, withdraw_prorata, withdraw_sleeves, pre_skipped, dropped = _schedule_flows(
        plan, series, sessions, session_set
    )
    if dropped > 0:
        notes.append(
            f"${dropped:,.0f} was scheduled after the last available price and was left out."
        )

    book = _Book(
        shares={s: 0.0 for s in symbols},
        parked={s: 0.0 for s in symbols},
        div_reinvested={s: 0.0 for s in symbols},
        div_cash={s: 0.0 for s in symbols},
        skipped={s: pre_skipped.get(s, 0.0) for s in symbols},
        invested={s: 0.0 for s in symbols},
    )
    pointers = {s: 0 for s in symbols}
    last_close: dict[str, float | None] = {s: None for s in symbols}
    bars_by_date = {s: {b.date: b for b in series[s].bars} for s in symbols}
    delist_noted: set[str] = set()
    prev_value = 0.0
    growth: list[tuple[date, float]] = []
    marks: list[Point] = []
    xirr_flows: list[tuple[date, float]] = []
    shortfall_total = 0.0

    for day in sessions:
        px = _prices(day, symbols, series, pointers, last_close)
        for symbol in symbols:
            bar = bars_by_date[symbol].get(day)
            if bar and bar.split not in (0, 1) and book.shares[symbol]:
                book.shares[symbol] *= bar.split
        for symbol in symbols:
            bar = bars_by_date[symbol].get(day)
            price = px.get(symbol)
            if not bar or not bar.dividend or not price or book.shares[symbol] <= 0:
                continue
            gross = book.shares[symbol] * bar.dividend
            if drip[symbol]:
                book.shares[symbol] += gross / price
                book.div_reinvested[symbol] += gross
            else:
                book.free_cash += gross
                book.div_cash[symbol] += gross
        for symbol in symbols:
            meta = series[symbol]
            price = px.get(symbol)
            if meta.delisted_on == day and price and book.shares[symbol] > 0:
                book.free_cash += book.shares[symbol] * price
                book.shares[symbol] = 0.0
                px[symbol] = None
                if symbol not in delist_noted:
                    delist_noted.add(symbol)
                    notes.append(f"{symbol} was sold at the last close on {day.isoformat()}.")

        value_before, _ = _mark(book, px, symbols)
        if prev_value > 1e-6:
            growth.append((day, value_before / prev_value))

        external = 0.0
        for symbol, dollars in buys.get(day, {}).items():
            price = px.get(symbol)
            if price:
                book.shares[symbol] += dollars / price
                book.invested[symbol] += dollars
                external += dollars
            else:
                book.skipped[symbol] += dollars
        for symbol, dollars in parks.get(day, {}).items():
            book.parked[symbol] += dollars
            external += dollars
        if cash_in.get(day):
            book.free_cash += cash_in[day]
            external += cash_in[day]
        if external:
            book.net += external
            xirr_flows.append((day, -external))

        requested = withdraw_prorata.get(day, 0.0) + sum(withdraw_sleeves.get(day, {}).values())
        withdrawn = _withdraw(book, px, symbols, withdraw_prorata.get(day, 0.0), withdraw_sleeves.get(day, {}))
        if withdrawn:
            book.net -= withdrawn
            xirr_flows.append((day, withdrawn))
        if requested - withdrawn > 1:
            shortfall_total += requested - withdrawn

        for symbol in symbols:
            price = px.get(symbol)
            if book.parked[symbol] > 0 and price and series[symbol].delisted_on != day:
                book.shares[symbol] += book.parked[symbol] / price
                book.invested[symbol] += book.parked[symbol]
                book.parked[symbol] = 0.0
                if plan.rebalance.mode != "never":
                    book.last_period = None  # force the rule to trade into the new holding today

        _rebalance(book, plan, day, px, symbols)
        value_after, sleeve_values = _mark(book, px, symbols)
        cash = book.free_cash + sum(book.parked.values())
        marks.append(
            Point(
                date=day,
                value=value_after,
                net_contributions=book.net,
                cash=cash,
                sleeves=sleeve_values,
            )
        )
        prev_value = value_after

    if shortfall_total > 1:
        notes.append(
            f"Withdrawals were short ${shortfall_total:,.0f} in total because the account did not hold enough."
        )
    _listing_notes(plan, series, book.skipped, notes)
    points = _sample(marks)
    sampling = _sampling_label(marks)
    annual = _annual_returns(growth)
    ending = marks[-1].value
    if ending:
        xirr_flows.append((marks[-1].date, ending))
    xirr = _xirr(xirr_flows)
    cagr = _cagr(growth, marks[0].date, marks[-1].date)
    reports = []
    for symbol in symbols:
        price = last_close[symbol] if series[symbol].delisted_on is None else None
        if series[symbol].delisted_on is not None:
            value = 0.0
            shares = 0.0
        else:
            shares = book.shares[symbol]
            value = shares * price if price else 0.0
        reports.append(
            SleeveReport(
                symbol=symbol,
                shares=shares,
                value=value,
                dividends_reinvested=book.div_reinvested[symbol],
                dividends_cash=book.div_cash[symbol],
                skipped=book.skipped[symbol],
                invested=book.invested[symbol],
                listed_on=series[symbol].listed_on,
                delisted_on=series[symbol].delisted_on,
            )
        )
    return ReplayResult(
        points=points,
        sleeve_reports=reports,
        net_contributions=book.net,
        ending_value=ending,
        market_gain=ending - book.net,
        xirr=xirr,
        twr_cagr=cagr,
        skipped=sum(book.skipped.values()),
        sessions=len(marks),
        sampling=sampling,
        annual_returns=annual,
        notes=notes,
        xirr_flows=xirr_flows,
    )


def _empty(plan: Plan, series: dict[str, Series], notes: list[str]) -> ReplayResult:
    symbols = [s.symbol for s in plan.sleeves]
    return ReplayResult(
        points=[],
        sleeve_reports=[
            SleeveReport(
                symbol=s,
                shares=0,
                value=0,
                dividends_reinvested=0,
                dividends_cash=0,
                skipped=0,
                invested=0,
                listed_on=series[s].listed_on if s in series else None,
                delisted_on=series[s].delisted_on if s in series else None,
            )
            for s in symbols
        ],
        net_contributions=0,
        ending_value=0,
        market_gain=0,
        xirr=None,
        twr_cagr=None,
        skipped=0,
        sessions=0,
        sampling="each session",
        annual_returns=[],
        notes=notes,
        xirr_flows=[],
    )


def _sessions(series: dict[str, Series], start: date, end: date) -> list[date]:
    days: set[date] = set()
    for item in series.values():
        for bar in item.bars:
            if start <= bar.date <= end:
                days.add(bar.date)
    return sorted(days)


def _schedule_flows(
    plan: Plan,
    series: dict[str, Series],
    sessions: list[date],
    session_set: set[date],
) -> tuple[
    dict[date, dict[str, float]],
    dict[date, dict[str, float]],
    dict[date, float],
    dict[date, float],
    dict[date, dict[str, float]],
    dict[str, float],
    float,
]:
    symbols = [s.symbol for s in plan.sleeves]
    buys: dict[date, dict[str, float]] = {}
    parks: dict[date, dict[str, float]] = {}
    cash_in: dict[date, float] = {}
    withdraw_prorata: dict[date, float] = {}
    withdraw_sleeves: dict[date, dict[str, float]] = {}
    pre_skipped: dict[str, float] = {}
    dropped = 0.0

    def account(day: date) -> date | None:
        if day in session_set:
            return day
        i = bisect_left(sessions, day)
        if i < len(sessions):
            return sessions[i]
        # The scheduled day is inside the window, but the last close was a
        # few days earlier (weekend or holiday at the end date).
        if sessions and sessions[-1] < day and (day - sessions[-1]).days <= 7:
            return sessions[-1]
        return None

    for flow in plan.flows:
        explicit = flow.allocation is not None
        for scheduled in scheduled_dates(flow, plan.start, plan.end):
            trade = account(scheduled)
            if trade is None:
                dropped += abs(flow.amount)
                continue
            weights = _weights_for_flow(flow, scheduled, plan.regimes, symbols)
            if flow.amount >= 0:
                _place_deposit(
                    plan,
                    series,
                    symbols,
                    weights,
                    flow.amount,
                    scheduled,
                    trade,
                    buys,
                    parks,
                    cash_in,
                    pre_skipped,
                )
            elif explicit:
                _place_explicit_withdrawal(weights, -flow.amount, trade, withdraw_sleeves, withdraw_prorata)
            else:
                withdraw_prorata[trade] = withdraw_prorata.get(trade, 0.0) + (-flow.amount)
    return buys, parks, cash_in, withdraw_prorata, withdraw_sleeves, pre_skipped, dropped


def _weights_for_flow(
    flow: Flow, day: date, regimes: tuple[Regime, ...], symbols: list[str]
) -> dict[str, float]:
    if flow.allocation is not None:
        return {s: flow.allocation.get(s, 0.0) for s in symbols}
    current: Regime | None = None
    for regime in regimes:
        if regime.date <= day:
            current = regime
        else:
            break
    if current is None:
        return {s: 0.0 for s in symbols}
    return {s: current.weights.get(s, 0.0) for s in symbols}


def _place_deposit(
    plan: Plan,
    series: dict[str, Series],
    symbols: list[str],
    weights: dict[str, float],
    amount: float,
    scheduled: date,
    trade: date,
    buys: dict[date, dict[str, float]],
    parks: dict[date, dict[str, float]],
    cash_in: dict[date, float],
    pre_skipped: dict[str, float],
) -> None:
    for symbol in symbols:
        portion = amount * weights[symbol]
        if portion <= 0:
            continue
        meta = series[symbol]
        if _was_listed(meta, scheduled):
            own = _next_own_session(meta, scheduled, plan.end)
            slot = own or trade
            buys.setdefault(slot, {}).setdefault(symbol, 0.0)
            buys[slot][symbol] += portion
        elif plan.listing_policy == "park" and _can_eventually_trade(meta, plan.end):
            parks.setdefault(trade, {}).setdefault(symbol, 0.0)
            parks[trade][symbol] += portion
        else:
            pre_skipped[symbol] = pre_skipped.get(symbol, 0.0) + portion
    # The unallocated slice is income that was not saved into the account.
    # It is not a cash balance the investor already held.


def _was_listed(meta: Series, day: date) -> bool:
    if meta.listed_on is None or not meta.bars:
        return False
    if day < meta.listed_on:
        return False
    if meta.delisted_on is not None and day >= meta.delisted_on:
        return False
    return True


def _can_eventually_trade(meta: Series, horizon_end: date) -> bool:
    if meta.listed_on is None or not meta.bars:
        return False
    if meta.listed_on > horizon_end:
        return False
    if meta.delisted_on is not None and meta.delisted_on <= meta.listed_on:
        return False
    return True


def _next_own_session(meta: Series, day: date, end: date) -> date | None:
    for bar in meta.bars:
        if day <= bar.date <= end:
            return bar.date
    return None


def _place_explicit_withdrawal(
    weights: dict[str, float],
    amount: float,
    trade: date,
    withdraw_sleeves: dict[date, dict[str, float]],
    withdraw_prorata: dict[date, float],
) -> None:
    """Sell the named sleeves. Weight left unassigned is raised pro-rata, cash first."""

    allocated = 0.0
    for symbol, weight in weights.items():
        portion = amount * weight
        if portion <= 0:
            continue
        allocated += portion
        bucket = withdraw_sleeves.setdefault(trade, {})
        bucket[symbol] = bucket.get(symbol, 0.0) + portion
    residual = amount - allocated
    if residual > 1e-8:
        withdraw_prorata[trade] = withdraw_prorata.get(trade, 0.0) + residual


def _prices(
    day: date,
    symbols: list[str],
    series: dict[str, Series],
    pointers: dict[str, int],
    last_close: dict[str, float | None],
) -> dict[str, float | None]:
    px: dict[str, float | None] = {}
    for symbol in symbols:
        bars = series[symbol].bars
        i = pointers[symbol]
        while i < len(bars) and bars[i].date <= day:
            last_close[symbol] = bars[i].close
            i += 1
        pointers[symbol] = i
        meta = series[symbol]
        price = last_close[symbol]
        if price is None or meta.listed_on is None or day < meta.listed_on:
            px[symbol] = None
            continue
        if meta.delisted_on is not None and day > meta.delisted_on:
            px[symbol] = None
            continue
        if bars and day < bars[0].date:
            px[symbol] = None
            continue
        px[symbol] = price
    return px


def _mark(book: _Book, px: dict[str, float | None], symbols: list[str]) -> tuple[float, dict[str, float]]:
    sleeves: dict[str, float] = {}
    total = book.free_cash + sum(book.parked.values())
    for symbol in symbols:
        price = px.get(symbol)
        if price and book.shares[symbol] > 0:
            value = book.shares[symbol] * price
            sleeves[symbol] = value
            total += value
        else:
            sleeves[symbol] = 0.0
    return total, sleeves


def _withdraw(
    book: _Book,
    px: dict[str, float | None],
    symbols: list[str],
    prorata: float,
    by_sleeve: dict[str, float],
) -> float:
    got = 0.0
    if by_sleeve:
        for symbol, dollars in by_sleeve.items():
            got += _sell(book, px, symbol, dollars)
    if prorata > 0:
        got += _raise_prorata(book, px, symbols, prorata)
    return got


def _sell(book: _Book, px: dict[str, float | None], symbol: str, dollars: float) -> float:
    price = px.get(symbol)
    if not price or book.shares[symbol] <= 0 or dollars <= 0:
        return 0.0
    have = book.shares[symbol] * price
    sell = min(have, dollars)
    book.shares[symbol] -= sell / price
    if book.shares[symbol] < 1e-10:
        book.shares[symbol] = 0.0
    return sell


def _raise_prorata(book: _Book, px: dict[str, float | None], symbols: list[str], amount: float) -> float:
    take = min(book.free_cash, amount)
    book.free_cash -= take
    got = take
    need = amount - take
    if need <= 1e-8:
        return got
    holdings = []
    equity = 0.0
    for symbol in symbols:
        price = px.get(symbol)
        if price and book.shares[symbol] > 0:
            value = book.shares[symbol] * price
            holdings.append((symbol, price, value))
            equity += value
    if equity <= 0:
        return got
    if equity <= need + 1e-6:
        for symbol, _price, _value in holdings:
            book.shares[symbol] = 0.0
        return got + equity
    for symbol, price, value in holdings:
        sell = need * (value / equity)
        book.shares[symbol] -= sell / price
        if book.shares[symbol] < 1e-10:
            book.shares[symbol] = 0.0
        got += sell
    return got


def _rebalance(book: _Book, plan: Plan, day: date, px: dict[str, float | None], symbols: list[str]) -> None:
    mode = plan.rebalance.mode
    if mode == "never":
        return
    regime_i = _regime_index(plan.regimes, day)
    period = _period_key(day, mode)
    if regime_i == -1:
        book.last_regime = regime_i
        book.last_period = period
        return
    trigger = False
    if regime_i != book.last_regime and regime_i != -1:
        trigger = True
    if mode in ("monthly", "quarterly", "yearly") and period != book.last_period:
        trigger = True
    if mode == "threshold" and _drift(book, plan, day, px, symbols) > plan.rebalance.threshold:
        trigger = True
    # Parked cash that just became investable is signaled by clearing last_period.
    if book.last_period is None and mode in ("monthly", "quarterly", "yearly", "threshold"):
        trigger = True
    book.last_regime = regime_i
    book.last_period = period
    if not trigger:
        return
    _trade_to_targets(book, plan, day, px, symbols)


def _regime_index(regimes: tuple[Regime, ...], day: date) -> int:
    idx = -1
    for i, regime in enumerate(regimes):
        if regime.date <= day:
            idx = i
        else:
            break
    return idx


def _period_key(day: date, mode: str) -> tuple[int, ...]:
    if mode == "monthly":
        return (day.year, day.month)
    if mode == "quarterly":
        return (day.year, (day.month - 1) // 3)
    if mode == "yearly":
        return (day.year,)
    return (day.year, day.month, day.day)


def _drift(book: _Book, plan: Plan, day: date, px: dict[str, float | None], symbols: list[str]) -> float:
    targets, _cash_w, investable = _target_mix(book, plan, day, px, symbols)
    if investable <= 1:
        return 0.0
    worst = 0.0
    for symbol, weight in targets.items():
        price = px.get(symbol)
        current = (book.shares[symbol] * price / investable) if price else 0.0
        worst = max(worst, abs(current - weight))
    return worst


def _target_mix(
    book: _Book, plan: Plan, day: date, px: dict[str, float | None], symbols: list[str]
) -> tuple[dict[str, float], float, float]:
    raw = _regime_weights(plan.regimes, day, symbols)
    listed = [s for s in symbols if px.get(s)]
    listed_w = sum(raw[s] for s in listed)
    equity = 0.0
    for symbol in listed:
        price = px.get(symbol)
        if price:
            equity += book.shares[symbol] * price
    # free_cash here is only money already inside the account: dividends left
    # as cash, or proceeds from a sale. The gap in a percentage split is not cash.
    investable = equity + book.free_cash
    if listed_w <= 1e-12:
        return {}, 0.0, investable
    scale = 1.0 / listed_w
    return {s: raw[s] * scale for s in listed}, 0.0, investable


def _regime_weights(regimes: tuple[Regime, ...], day: date, symbols: list[str]) -> dict[str, float]:
    current: Regime | None = None
    for regime in regimes:
        if regime.date <= day:
            current = regime
        else:
            break
    if current is None:
        return {s: 0.0 for s in symbols}
    return {s: current.weights.get(s, 0.0) for s in symbols}


def _trade_to_targets(
    book: _Book, plan: Plan, day: date, px: dict[str, float | None], symbols: list[str]
) -> None:
    targets, cash_w, investable = _target_mix(book, plan, day, px, symbols)
    if investable <= 1 or not targets:
        return
    spent = 0.0
    for symbol, weight in targets.items():
        price = px[symbol]
        assert price is not None
        value = investable * weight
        book.shares[symbol] = value / price
        spent += value
    for symbol in symbols:
        if symbol not in targets and px.get(symbol):
            book.shares[symbol] = 0.0
    book.free_cash = investable - spent
    if book.free_cash < 0 and targets:
        symbol = max(targets, key=lambda s: book.shares[s] * (px[s] or 0))
        price = px[symbol]
        if price:
            book.shares[symbol] += book.free_cash / price
            book.free_cash = 0.0
    if cash_w == 0 and 0 < book.free_cash < 0.05:
        book.free_cash = 0.0


def _listing_notes(plan: Plan, series: dict[str, Series], skipped: dict[str, float], notes: list[str]) -> None:
    for sleeve in plan.sleeves:
        meta = series[sleeve.symbol]
        if meta.listed_on and meta.listed_on > plan.start:
            if plan.listing_policy == "skip" and skipped.get(sleeve.symbol, 0) > 1:
                notes.append(
                    f"{sleeve.symbol} is priced from {meta.listed_on.isoformat()}. "
                    f"${skipped[sleeve.symbol]:,.0f} of earlier income was not invested, "
                    "and is not counted as cash already held."
                )
            elif plan.listing_policy == "park":
                notes.append(
                    f"{sleeve.symbol} is priced from {meta.listed_on.isoformat()}. "
                    "Earlier deposits were held as cash and invested at the first close."
                )
            else:
                notes.append(f"{sleeve.symbol} is priced from {meta.listed_on.isoformat()}.")
        if not meta.bars:
            notes.append(f"{sleeve.symbol} has no prices in this window.")


def _sample(marks: list[Point]) -> list[Point]:
    if not marks:
        return []
    span = (marks[-1].date - marks[0].date).days
    if span <= 200:
        return marks
    buckets: dict[tuple[int, ...], Point] = {}
    for point in marks:
        if span <= 3 * 365:
            iso = point.date.isocalendar()
            key = (iso[0], iso[1])
        else:
            key = (point.date.year, point.date.month)
        buckets[key] = point
    points = list(buckets.values())
    if points[-1].date != marks[-1].date:
        points.append(marks[-1])
    return points


def _sampling_label(marks: list[Point]) -> str:
    if not marks:
        return "each session"
    span = (marks[-1].date - marks[0].date).days
    if span <= 200:
        return "each session"
    if span <= 3 * 365:
        return "week-end"
    return "month-end"


def _annual_returns(growth: list[tuple[date, float]]) -> list[tuple[int, float]]:
    grouped: dict[int, list[float]] = {}
    for day, factor in growth:
        grouped.setdefault(day.year, []).append(factor)
    annual: list[tuple[int, float]] = []
    years = sorted(grouped)
    for year in years:
        factors = grouped[year]
        if len(years) > 2 and len(factors) < 200:
            continue
        product = 1.0
        for factor in factors:
            product *= factor
        annual.append((year, product - 1.0))
    return annual


def _cagr(growth: list[tuple[date, float]], start: date, end: date) -> float | None:
    if len(growth) < 2:
        return None
    product = 1.0
    for _day, factor in growth:
        product *= factor
    days = (end - start).days
    if days <= 0 or product <= 0:
        return None
    return product ** (365.25 / days) - 1.0


def _xirr(cashflows: list[tuple[date, float]]) -> float | None:
    if len(cashflows) < 2:
        return None
    flows = sorted(cashflows)
    if not any(amount < 0 for _d, amount in flows) or not any(amount > 0 for _d, amount in flows):
        return None
    t0 = flows[0][0]

    def npv(rate: float) -> float:
        total = 0.0
        for day, amount in flows:
            years = (day - t0).days / 365.25
            total += amount / (1.0 + rate) ** years
        return total

    rate = 0.1
    for _ in range(60):
        value = npv(rate)
        slope = (npv(rate + 1e-5) - value) / 1e-5
        if abs(slope) < 1e-12:
            return None
        rate -= value / slope
        if rate <= -0.95:
            rate = -0.5
        if abs(value) < 1e-6:
            break
    if rate <= -0.95 or rate > 10:
        return None
    return rate
