"""API and static UI.

Run from the repo root:

    uvicorn pf.server:app --app-dir src --port 8765
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from pf.cpi import adjust_path, level
from pf.decide import lasting, outlook
from pf.engine import ReplayResult, replay
from pf.metrics import describe
from pf.models import Flow, Forward, Plan, RebalanceRule, Regime, Series, Sleeve, validate_plan
from pf.montecarlo import Projection, project
from pf.prices import PriceStore, YahooStore, synthetic_series

STATIC = Path(__file__).resolve().parent / "static"
STORE: PriceStore = YahooStore()

app = FastAPI(title="Later Ledger")


class SleeveIn(BaseModel):
    symbol: str
    drip: bool = True
    expense_ratio: float = 0.0

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("expense_ratio")
    @classmethod
    def fee_range(cls, value: float) -> float:
        if not 0 <= value <= 0.05:
            raise ValueError("Yearly fund cost must be between 0% and 5%.")
        return value


class RegimeIn(BaseModel):
    date: date
    weights: dict[str, float]


class FlowIn(BaseModel):
    amount: float
    frequency: Literal["once", "weekly", "biweekly", "monthly", "quarterly", "yearly"]
    start: date
    end: date | None = None
    allocation: Any = "targets"

    @field_validator("allocation")
    @classmethod
    def clean_allocation(cls, value: Any) -> Any:
        if value is None or value == "targets":
            return "targets"
        if isinstance(value, dict):
            return {str(key).strip().upper(): float(weight) for key, weight in value.items()}
        raise ValueError("Allocation must be 'targets' or a map of symbols to weights.")


class RebalanceIn(BaseModel):
    mode: Literal["never", "monthly", "quarterly", "yearly", "threshold"] = "yearly"
    threshold: float = 0.05


class ForwardIn(BaseModel):
    years: int = 30
    annual_contribution: float = 0
    annual_withdrawal: float = 0
    trials: int = 500


class PlanIn(BaseModel):
    start: date
    end: date
    listing_policy: Literal["skip", "park"] = "skip"
    sleeves: list[SleeveIn]
    regimes: list[RegimeIn] = Field(default_factory=list)
    flows: list[FlowIn]
    rebalance: RebalanceIn = Field(default_factory=RebalanceIn)
    forward: ForwardIn | None = None
    benchmark: str | None = None

    @field_validator("benchmark")
    @classmethod
    def clean_benchmark(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class OutlookIn(BaseModel):
    mix: Literal["stocks", "balanced", "bonds", "custom"]
    years: int = 30
    starting: float = 0
    annual_contribution: float = 0
    annual_withdrawal: float = 0
    glide: bool = False
    trials: int = 500
    source: Literal["market", "sample"] = "market"
    stock_weight: float = 0.6
    stock_symbol: str = "VFINX"
    bond_symbol: str = "VBMFX"
    bond_source: Literal["treasury", "fund"] = "treasury"


class LastingIn(BaseModel):
    mix: Literal["stocks", "balanced", "bonds", "custom"]
    years: int = 30
    starting: float = 0
    annual_spend: float = 0
    inflation: bool = True
    source: Literal["market", "sample"] = "market"
    certainty: float = 0.9
    stock_weight: float = 0.6
    stock_symbol: str = "VFINX"
    bond_symbol: str = "VBMFX"
    bond_source: Literal["treasury", "fund"] = "treasury"


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/symbol/{symbol}")
def describe_symbol(symbol: str) -> dict[str, Any]:
    cleaned = symbol.strip().upper()
    if not cleaned or len(cleaned) > 12:
        raise HTTPException(status_code=400, detail="Enter a ticker symbol.")
    try:
        series = STORE.load(cleaned, date(1900, 1, 1), date.today())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not series.bars:
        raise HTTPException(status_code=404, detail=f"No price history for {cleaned}.")
    return {
        "symbol": cleaned,
        "listed_on": series.listed_on.isoformat() if series.listed_on else series.bars[0].date.isoformat(),
        "delisted_on": series.delisted_on.isoformat() if series.delisted_on else None,
        "last": series.bars[-1].date.isoformat(),
    }


@app.post("/api/outlook")
def outlook_route(body: OutlookIn) -> dict[str, Any]:
    if not 50 <= body.trials <= 2000:
        raise HTTPException(status_code=400, detail="Use between 50 and 2000 shuffles.")
    try:
        return outlook(
            body.mix,
            body.years,
            body.starting,
            body.annual_contribution,
            body.annual_withdrawal,
            body.glide,
            body.trials,
            body.source,
            STORE,
            stock_weight=body.stock_weight,
            stock_symbol=body.stock_symbol,
            bond_symbol=body.bond_symbol,
            bond_source=body.bond_source,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/lasting")
def lasting_route(body: LastingIn) -> dict[str, Any]:
    try:
        return lasting(
            body.mix,
            body.years,
            body.starting,
            body.annual_spend,
            body.inflation,
            body.source,
            STORE,
            certainty=body.certainty,
            stock_weight=body.stock_weight,
            stock_symbol=body.stock_symbol,
            bond_symbol=body.bond_symbol,
            bond_source=body.bond_source,
        )
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/backtest")
def backtest(body: PlanIn) -> dict[str, Any]:
    plan, forward = _parse(body)
    try:
        series = {sleeve.symbol: STORE.load(sleeve.symbol, plan.start, plan.end) for sleeve in plan.sleeves}
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _execute(
        plan,
        series,
        forward,
        "market",
        body.benchmark,
        lambda symbol: STORE.load(symbol, plan.start, plan.end),
    )


@app.post("/api/preview")
def preview(body: PlanIn) -> dict[str, Any]:
    plan, forward = _parse(body)
    series = {}
    for sleeve in plan.sleeves:
        try:
            series[sleeve.symbol] = synthetic_series(sleeve.symbol, plan.start, plan.end)
        except LookupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _execute(
        plan,
        series,
        forward,
        "sample",
        body.benchmark,
        lambda symbol: synthetic_series(symbol, plan.start, plan.end),
    )


def _parse(body: PlanIn) -> tuple[Plan, Forward | None]:
    regimes = tuple(
        Regime(
            date=regime.date,
            weights={symbol.strip().upper(): float(weight) for symbol, weight in regime.weights.items()},
        )
        for regime in sorted(body.regimes, key=lambda item: item.date)
    )
    flows = []
    for flow in body.flows:
        allocation = None if flow.allocation == "targets" else flow.allocation
        flows.append(
            Flow(
                amount=flow.amount,
                frequency=flow.frequency,
                start=flow.start,
                end=flow.end,
                allocation=allocation,
            )
        )
    plan = Plan(
        start=body.start,
        end=body.end,
        sleeves=tuple(
            Sleeve(symbol=sleeve.symbol, drip=sleeve.drip, expense_ratio=sleeve.expense_ratio)
            for sleeve in body.sleeves
        ),
        flows=tuple(flows),
        regimes=regimes,
        listing_policy=body.listing_policy,
        rebalance=RebalanceRule(mode=body.rebalance.mode, threshold=body.rebalance.threshold),
    )
    forward = None
    if body.forward is not None:
        forward = Forward(
            years=body.forward.years,
            annual_contribution=body.forward.annual_contribution,
            annual_withdrawal=body.forward.annual_withdrawal,
            trials=body.forward.trials,
        )
    try:
        validate_plan(plan, forward)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return plan, forward


def _execute(
    plan: Plan,
    series: dict[str, Series],
    forward: Forward | None,
    source: str,
    benchmark: str | None,
    load_symbol,
) -> dict[str, Any]:
    try:
        result = replay(plan, series)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    projection = project(result.annual_returns, result.ending_value, forward) if forward else None
    bench = _benchmark(plan, benchmark, load_symbol) if benchmark else None
    return _payload(result, projection, source, forward, bench)


def _benchmark(plan: Plan, symbol: str, load_symbol):
    try:
        series = {symbol: load_symbol(symbol)}
    except LookupError as exc:
        return {"error": str(exc), "result": None, "symbol": symbol}
    bench = Plan(
        start=plan.start,
        end=plan.end,
        sleeves=(Sleeve(symbol, drip=True),),
        flows=tuple(
            Flow(flow.amount, flow.frequency, flow.start, flow.end, {symbol: 1.0}) for flow in plan.flows
        ),
        regimes=(),
        listing_policy="skip",
        rebalance=RebalanceRule("never"),
    )
    try:
        validate_plan(bench)
        result = replay(bench, series)
    except ValueError as exc:
        return {"error": str(exc), "result": None, "symbol": symbol}
    return {"error": None, "result": result, "symbol": symbol}


def _payload(
    result: ReplayResult,
    projection: Projection | None,
    source: str,
    forward: Forward | None,
    benchmark: dict[str, Any] | None = None,
) -> dict[str, Any]:
    montecarlo: dict[str, Any]
    if forward is None:
        montecarlo = {"unavailable": "No forward horizon was requested."}
    elif projection is None:
        count = len(result.annual_returns)
        montecarlo = {
            "unavailable": (
                f"The bootstrap needs at least five full years of returns and a positive "
                f"ending balance or a forward contribution. This window has {count} usable year"
                f"{'' if count == 1 else 's'}."
            )
        }
    else:
        montecarlo = {
            "years": projection.years,
            "p10": [round(value, 2) for value in projection.p10],
            "p50": [round(value, 2) for value in projection.p50],
            "p90": [round(value, 2) for value in projection.p90],
            "deposited": [round(value, 2) for value in projection.deposited],
            "success_rate": None if projection.success_rate is None else round(projection.success_rate, 4),
            "trials": projection.trials,
            "annual_return_count": projection.annual_return_count,
            "mean_annual_return": round(projection.mean_annual_return, 4),
            "annual_volatility": round(projection.annual_volatility, 4),
            "method": projection.method,
        }
    adjusted, base = adjust_path(result.points, result.xirr_flows, result.ending_value)
    when = {
        "each session": "each market day",
        "week-end": "the end of each week",
        "month-end": "the end of each month",
    }.get(result.sampling, result.sampling)
    real_ending = None if not adjusted else adjusted[-1][0]
    real_net = None if not adjusted else adjusted[-1][1]
    bench_values, bench_real, bench_error = _align_benchmark(result.points, benchmark, base)
    return {
        "source": source,
        "sampling": result.sampling,
        "sessions": result.sessions,
        "caption": (
            f"Each point is the value at {when}. The account starts empty, and money goes in only on the dates you chose. "
            "Taxes are not included. A yearly fund cost is included only where you set one."
        ),
        "stats": {
            "ending_value": round(result.ending_value, 2),
            "net_contributions": round(result.net_contributions, 2),
            "market_gain": round(result.market_gain, 2),
            "real_ending_value": None if real_ending is None else round(real_ending, 2),
            "real_net_contributions": None if real_net is None else round(real_net, 2),
            "real_market_gain": None if real_ending is None or real_net is None else round(real_ending - real_net, 2),
            "xirr": None if result.xirr is None else round(result.xirr, 6),
            "twr_cagr": None if result.twr_cagr is None else round(result.twr_cagr, 6),
            "skipped": round(result.skipped, 2),
            "max_drawdown": None if result.max_drawdown is None else round(result.max_drawdown, 4),
            "drawdown_peak": result.drawdown_peak.isoformat() if result.drawdown_peak else None,
            "drawdown_trough": result.drawdown_trough.isoformat() if result.drawdown_trough else None,
        },
        "points": [
            {
                "date": point.date.isoformat(),
                "value": round(point.value, 2),
                "net_contributions": round(point.net_contributions, 2),
                "real_value": round(real[0], 2),
                "real_net_contributions": round(real[1], 2),
                "cash": round(point.cash, 2),
                "sleeves": {symbol: round(value, 2) for symbol, value in point.sleeves.items()},
            }
            for point, real in zip(result.points, adjusted, strict=True)
        ],
        "sleeves": [
            {
                "symbol": report.symbol,
                "shares": round(report.shares, 4),
                "value": round(report.value, 2),
                "dividends_reinvested": round(report.dividends_reinvested, 2),
                "dividends_cash": round(report.dividends_cash, 2),
                "skipped": round(report.skipped, 2),
                "invested": round(report.invested, 2),
                "listed_on": report.listed_on.isoformat() if report.listed_on else None,
                "delisted_on": report.delisted_on.isoformat() if report.delisted_on else None,
            }
            for report in result.sleeve_reports
        ],
        "notes": result.notes,
        "metrics": describe(result.annual_returns),
        "benchmark": None
        if benchmark is None
        else {
            "symbol": benchmark["symbol"],
            "error": benchmark["error"],
            "values": [None if value is None else round(value, 2) for value in bench_values],
            "real_values": [None if value is None else round(value, 2) for value in bench_real],
        },
        "montecarlo": montecarlo,
    }


def _align_benchmark(points, benchmark, base):
    if benchmark is None:
        return [], [], None
    if benchmark["error"] or benchmark["result"] is None:
        return [], [], benchmark["error"]
    dates = [point.date for point in benchmark["result"].points]
    values = []
    real_values = []
    for point in points:
        index = bisect_right(dates, point.date) - 1
        value = None if index < 0 else benchmark["result"].points[index].value
        values.append(value)
        if value is None or not base:
            real_values.append(None)
        else:
            real_values.append(value * base / level(point.date))
    return values, real_values, None


app.mount("/static", StaticFiles(directory=STATIC), name="static")
