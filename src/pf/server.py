"""API and static UI.

Run from the repo root:

    uvicorn pf.server:app --app-dir src --port 8765
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from pf.engine import ReplayResult, replay
from pf.models import Flow, Forward, Plan, RebalanceRule, Regime, Series, Sleeve, validate_plan
from pf.montecarlo import Projection, project
from pf.prices import PriceStore, YahooStore, synthetic_series

STATIC = Path(__file__).resolve().parent / "static"
STORE: PriceStore = YahooStore()

app = FastAPI(title="Portfolio replay")


class SleeveIn(BaseModel):
    symbol: str
    drip: bool = True

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value: str) -> str:
        return value.strip().upper()


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


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/backtest")
def backtest(body: PlanIn) -> dict[str, Any]:
    plan, forward = _parse(body)
    try:
        series = {sleeve.symbol: STORE.load(sleeve.symbol, plan.start, plan.end) for sleeve in plan.sleeves}
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _execute(plan, series, forward, "market")


@app.post("/api/preview")
def preview(body: PlanIn) -> dict[str, Any]:
    plan, forward = _parse(body)
    series = {}
    for sleeve in plan.sleeves:
        try:
            series[sleeve.symbol] = synthetic_series(sleeve.symbol, plan.start, plan.end)
        except LookupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _execute(plan, series, forward, "sample")


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
        sleeves=tuple(Sleeve(symbol=sleeve.symbol, drip=sleeve.drip) for sleeve in body.sleeves),
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


def _execute(plan: Plan, series: dict[str, Series], forward: Forward | None, source: str) -> dict[str, Any]:
    try:
        result = replay(plan, series)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    projection = project(result.annual_returns, result.ending_value, forward) if forward else None
    return _payload(result, projection, source, forward)


def _payload(
    result: ReplayResult, projection: Projection | None, source: str, forward: Forward | None
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
    return {
        "source": source,
        "sampling": result.sampling,
        "sessions": result.sessions,
        "caption": (
            f"Each point is the {result.sampling} value. The ledger stepped through "
            f"{result.sessions:,} sessions. The account starts empty. Income is added only on the "
            "dates in each stretch. Figures are pre-tax and have no fees."
        ),
        "stats": {
            "ending_value": round(result.ending_value, 2),
            "net_contributions": round(result.net_contributions, 2),
            "market_gain": round(result.market_gain, 2),
            "xirr": None if result.xirr is None else round(result.xirr, 6),
            "twr_cagr": None if result.twr_cagr is None else round(result.twr_cagr, 6),
            "skipped": round(result.skipped, 2),
        },
        "points": [
            {
                "date": point.date.isoformat(),
                "value": round(point.value, 2),
                "net_contributions": round(point.net_contributions, 2),
                "cash": round(point.cash, 2),
                "sleeves": {symbol: round(value, 2) for symbol, value in point.sleeves.items()},
            }
            for point in result.points
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
        "montecarlo": montecarlo,
    }


app.mount("/static", StaticFiles(directory=STATIC), name="static")
