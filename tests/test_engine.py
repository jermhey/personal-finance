from datetime import date, timedelta

from pf.engine import replay
from pf.models import Bar, Flow, Forward, Plan, RebalanceRule, Regime, Series, Sleeve, validate_plan
from pf.montecarlo import project
from pf.prices import synthetic_series


def _series(symbol, rows, listed_on=None):
    bars = tuple(
        Bar(date=day, close=close, dividend=dividend, split=split)
        for day, close, dividend, split in rows
    )
    return Series(symbol=symbol, bars=bars, listed_on=listed_on or bars[0].date)


def _fixture_series():
    days = [
        date(2020, 1, 31),
        date(2020, 2, 29),
        date(2020, 3, 31),
        date(2020, 4, 30),
        date(2020, 5, 31),
        date(2020, 6, 30),
    ]
    prices_a = [100, 105, 103, 110, 108, 120]
    div_a = [0, 0, 2, 0, 0, 2]
    a = _series("FIXA", [(days[i], prices_a[i], div_a[i], 0) for i in range(6)])
    b_days = days[3:]
    b_prices = [50, 55, 52]
    b = _series("FIXB", [(b_days[i], b_prices[i], 0, 0) for i in range(3)], listed_on=date(2020, 4, 30))
    return {"FIXA": a, "FIXB": b}


def _fixture_plan(policy, drip=True):
    start, end = date(2020, 1, 31), date(2020, 6, 30)
    return Plan(
        start=start,
        end=end,
        sleeves=(Sleeve("FIXA", drip=drip), Sleeve("FIXB", drip=True)),
        flows=(
            Flow(200, "monthly", start, end, {"FIXA": 1}),
            Flow(100, "monthly", start, end, {"FIXB": 1}),
        ),
        listing_policy=policy,
        rebalance=RebalanceRule("never"),
    )


def test_skip_matches_spec_fixture():
    result = replay(_fixture_plan("skip"), _fixture_series())
    values = [round(point.value, 2) for point in result.points]
    assert values == [200.0, 410.0, 610.0, 951.46, 1245.98, 1668.81]
    assert round(result.net_contributions, 2) == 1500
    assert round(result.skipped, 2) == 300


def test_park_matches_spec_fixture():
    result = replay(_fixture_plan("park"), _fixture_series())
    values = [round(point.value, 2) for point in result.points]
    assert values == [300.0, 610.0, 910.0, 1251.46, 1575.98, 1980.81]
    assert round(result.net_contributions, 2) == 1800
    assert result.skipped == 0


def test_cash_dividends_trail_reinvestment_after_price_rises():
    drip = replay(_fixture_plan("skip", drip=True), _fixture_series())
    cash = replay(_fixture_plan("skip", drip=False), _fixture_series())
    assert drip.ending_value > cash.ending_value
    assert round(cash.ending_value, 2) == 1667.37


def test_split_adjusts_shares():
    days = [date(2020, 1, 2), date(2020, 1, 3)]
    series = {
        "FIXA": _series(
            "FIXA",
            [(days[0], 100, 0, 0), (days[1], 55, 0, 2)],
        )
    }
    plan = Plan(
        start=days[0],
        end=days[1],
        sleeves=(Sleeve("FIXA", drip=True),),
        flows=(Flow(100, "once", days[0], None, {"FIXA": 1}),),
        rebalance=RebalanceRule("never"),
    )
    result = replay(plan, series)
    assert result.sleeve_reports[0].shares == 2
    assert round(result.ending_value, 2) == 110


def test_yearly_rebalance_restores_weights():
    day1, day2 = date(2020, 12, 31), date(2021, 1, 4)
    series = {
        "FIXA": _series("FIXA", [(day1, 100, 0, 0), (day2, 200, 0, 0)]),
        "FIXB": _series("FIXB", [(day1, 100, 0, 0), (day2, 100, 0, 0)]),
    }
    plan = Plan(
        start=day1,
        end=day2,
        sleeves=(Sleeve("FIXA"), Sleeve("FIXB")),
        regimes=(Regime(day1, {"FIXA": 0.5, "FIXB": 0.5}),),
        flows=(Flow(200, "once", day1, None, None),),
        rebalance=RebalanceRule("yearly"),
    )
    result = replay(plan, series)
    by_symbol = {item.symbol: item for item in result.sleeve_reports}
    assert round(by_symbol["FIXA"].shares, 4) == 0.75
    assert round(by_symbol["FIXB"].shares, 4) == 1.5
    assert round(result.ending_value, 2) == 300


def test_regime_change_rebalances_even_mid_year():
    day1, day2 = date(2020, 6, 1), date(2020, 6, 2)
    series = {
        "FIXA": _series("FIXA", [(day1, 100, 0, 0), (day2, 100, 0, 0)]),
        "FIXB": _series("FIXB", [(day1, 100, 0, 0), (day2, 100, 0, 0)]),
    }
    plan = Plan(
        start=day1,
        end=day2,
        sleeves=(Sleeve("FIXA"), Sleeve("FIXB")),
        regimes=(
            Regime(day1, {"FIXA": 1}),
            Regime(day2, {"FIXB": 1}),
        ),
        flows=(Flow(100, "once", day1, None, None),),
        rebalance=RebalanceRule("yearly"),
    )
    result = replay(plan, series)
    by_symbol = {item.symbol: item for item in result.sleeve_reports}
    assert by_symbol["FIXA"].shares == 0
    assert by_symbol["FIXB"].shares == 1


def test_buy_and_hold_ignores_later_regime_until_new_cash():
    day1, day2 = date(2020, 6, 1), date(2020, 6, 2)
    series = {
        "FIXA": _series("FIXA", [(day1, 100, 0, 0), (day2, 100, 0, 0)]),
        "FIXB": _series("FIXB", [(day1, 100, 0, 0), (day2, 100, 0, 0)]),
    }
    plan = Plan(
        start=day1,
        end=day2,
        sleeves=(Sleeve("FIXA"), Sleeve("FIXB")),
        regimes=(
            Regime(day1, {"FIXA": 1}),
            Regime(day2, {"FIXB": 1}),
        ),
        flows=(
            Flow(100, "once", day1, None, None),
            Flow(100, "once", day2, None, None),
        ),
        rebalance=RebalanceRule("never"),
    )
    result = replay(plan, series)
    by_symbol = {item.symbol: item for item in result.sleeve_reports}
    assert by_symbol["FIXA"].shares == 1
    assert by_symbol["FIXB"].shares == 1


def test_thirty_year_graph_is_monthly_and_bootstrap_runs():
    start, end = date(1994, 1, 3), date(2024, 1, 2)
    series = {"GROW": synthetic_series("GROW", start, end)}
    plan = Plan(
        start=start,
        end=end,
        sleeves=(Sleeve("GROW", drip=True),),
        regimes=(Regime(start, {"GROW": 1}),),
        flows=(Flow(100, "monthly", start, end, None),),
        rebalance=RebalanceRule("yearly"),
    )
    result = replay(plan, series)
    assert result.sampling == "month-end"
    assert len(result.points) >= 350
    assert result.sessions > 5000
    assert result.points[0].date.year == 1994
    assert result.points[-1].date.year >= 2023
    assert result.ending_value > result.net_contributions
    assert result.xirr is not None
    forward = project(result.annual_returns, result.ending_value, Forward(years=30, trials=80))
    assert forward is not None
    assert len(forward.p50) == 31
    assert forward.p90[-1] >= forward.p10[-1]


def test_final_weekend_deposit_uses_the_last_close():
    friday, saturday = date(2020, 1, 3), date(2020, 1, 4)
    series = {"FIXA": _series("FIXA", [(friday, 100, 0, 0)])}
    plan = Plan(
        start=friday,
        end=saturday,
        sleeves=(Sleeve("FIXA"),),
        flows=(Flow(100, "once", saturday, None, {"FIXA": 1}),),
        rebalance=RebalanceRule("never"),
    )
    result = replay(plan, series)
    assert round(result.ending_value, 2) == 100
    assert result.notes == []


def test_lump_sum_on_a_contribution_day_adds_to_that_day():
    day = date(2020, 6, 1)
    series = {"FIXA": _series("FIXA", [(day, 50, 0, 0)])}
    plan = Plan(
        start=day,
        end=day,
        sleeves=(Sleeve("FIXA"),),
        flows=(
            Flow(100, "once", day, None, {"FIXA": 1}),
            Flow(40, "monthly", day, day, {"FIXA": 1}),
        ),
        rebalance=RebalanceRule("never"),
    )
    result = replay(plan, series)
    assert round(result.ending_value, 2) == 140
    assert round(result.net_contributions, 2) == 140
    assert round(result.sleeve_reports[0].shares, 4) == 2.8


def test_withdrawal_larger_than_the_account_sells_what_exists():
    day1, day2 = date(2020, 6, 1), date(2020, 6, 2)
    series = {"FIXA": _series("FIXA", [(day1, 100, 0, 0), (day2, 100, 0, 0)])}
    plan = Plan(
        start=day1,
        end=day2,
        sleeves=(Sleeve("FIXA"),),
        flows=(
            Flow(100, "once", day1, None, {"FIXA": 1}),
            Flow(-500, "once", day2, None, {"FIXA": 1}),
        ),
        rebalance=RebalanceRule("never"),
    )
    result = replay(plan, series)
    assert round(result.ending_value, 2) == 0
    assert any("short" in note for note in result.notes)


def test_unallocated_income_is_not_a_cash_balance():
    day = date(2020, 6, 1)
    series = {
        "FIXA": _series("FIXA", [(day, 100, 0, 0)]),
        "FIXB": _series("FIXB", [(day, 100, 0, 0)]),
    }
    plan = Plan(
        start=day,
        end=day,
        sleeves=(Sleeve("FIXA"), Sleeve("FIXB")),
        regimes=(Regime(day, {"FIXA": 0.6, "FIXB": 0.2}),),
        flows=(Flow(100, "once", day, None, {"FIXA": 0.6, "FIXB": 0.2}),),
        rebalance=RebalanceRule("yearly"),
    )
    result = replay(plan, series)
    assert round(result.ending_value, 2) == 80
    assert round(result.net_contributions, 2) == 80
    assert result.points[0].cash == 0
    by_symbol = {item.symbol: item for item in result.sleeve_reports}
    assert round(by_symbol["FIXA"].value, 2) == 60
    assert round(by_symbol["FIXB"].value, 2) == 20


def test_weights_over_100_are_rejected():
    start = date(2020, 1, 2)
    plan = Plan(
        start=start,
        end=start + timedelta(days=10),
        sleeves=(Sleeve("AAA"),),
        regimes=(Regime(start, {"AAA": 1.2}),),
        flows=(Flow(10, "once", start, None, None),),
    )
    try:
        validate_plan(plan)
    except ValueError as exc:
        assert "100%" in str(exc)
    else:
        raise AssertionError("expected a validation error")


def test_expense_ratio_reduces_shares_on_a_flat_price():
    start = date(2020, 1, 2)
    end = date(2021, 1, 4)
    series = {"FIXA": _series("FIXA", [(start, 100, 0, 0), (end, 100, 0, 0)])}
    plan = Plan(
        start=start,
        end=end,
        sleeves=(Sleeve("FIXA", expense_ratio=0.01),),
        flows=(Flow(1000, "once", start, None, {"FIXA": 1}),),
        rebalance=RebalanceRule("never"),
    )
    result = replay(plan, series)
    assert 980 < result.ending_value < 1000
    assert any("fund cost" in note for note in result.notes)


def test_drawdown_uses_the_deepest_drop():
    days = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]
    series = {
        "FIXA": _series("FIXA", [(days[0], 100, 0, 0), (days[1], 50, 0, 0), (days[2], 80, 0, 0)])
    }
    plan = Plan(
        start=days[0],
        end=days[2],
        sleeves=(Sleeve("FIXA"),),
        flows=(Flow(100, "once", days[0], None, {"FIXA": 1}),),
        rebalance=RebalanceRule("never"),
    )
    result = replay(plan, series)
    assert result.max_drawdown is not None
    assert abs(result.max_drawdown - 0.5) < 1e-9
    assert result.drawdown_peak == days[0]
    assert result.drawdown_trough == days[1]
