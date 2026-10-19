"""Market prices from Yahoo Finance, plus a seeded sample series for offline use."""

from __future__ import annotations

import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

from pf.models import Bar, Series

_DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "cache"
CACHE = Path(os.environ.get("PRICE_CACHE_DIR", _DEFAULT_CACHE))


class PriceStore:
    def load(self, symbol: str, start: date, end: date) -> Series:
        raise NotImplementedError


class MemoryStore(PriceStore):
    def __init__(self, series: dict[str, Series]) -> None:
        self.series = series

    def load(self, symbol: str, start: date, end: date) -> Series:
        if symbol not in self.series:
            raise LookupError(f"No price history for {symbol}.")
        return _slice(self.series[symbol], start, end)


class YahooStore(PriceStore):
    def load(self, symbol: str, start: date, end: date) -> Series:
        full = _read_cache(symbol)
        stale = full is None or (
            end > full[1] and (date.today() - full[1]).days > 4
        )
        if stale:
            try:
                full_series = _download(symbol)
                _write_cache(full_series)
                full = (full_series, full_series.bars[-1].date)
            except LookupError:
                if full is None:
                    raise
        assert full is not None
        return _slice(full[0], start, end)


def synthetic_series(symbol: str, start: date, end: date) -> Series:
    """A repeatable random walk so the chart can be tried without a download."""

    rng = random.Random(_seed(symbol))
    price = 40.0 + (_seed(symbol) % 70)
    bars: list[Bar] = []
    cursor = start
    paid_month: tuple[int, int] | None = None
    while cursor <= end:
        if cursor.weekday() < 5:
            price = max(1.0, price * (1.0 + rng.gauss(0.00030, 0.009)))
            dividend = 0.0
            key = (cursor.year, cursor.month)
            if cursor.month in (3, 6, 9, 12) and paid_month != key:
                dividend = round(price * 0.004, 4)
                paid_month = key
            bars.append(Bar(date=cursor, close=round(price, 4), dividend=dividend))
        cursor += timedelta(days=1)
    if not bars:
        raise LookupError(f"No sample sessions for {symbol} in that window.")
    return Series(symbol=symbol, bars=tuple(bars), listed_on=bars[0].date, delisted_on=None)


def _seed(symbol: str) -> int:
    total = 0
    for char in symbol:
        total = (total * 33 + ord(char)) % 1_000_003
    return total or 1


def _slice(full: Series, start: date, end: date) -> Series:
    bars = tuple(bar for bar in full.bars if start <= bar.date <= end)
    last = full.bars[-1].date if full.bars else None
    delisted = full.delisted_on
    if delisted is None and last is not None and last < end - timedelta(days=7):
        if last < date.today() - timedelta(days=10):
            delisted = last
    listed = full.listed_on
    return Series(symbol=full.symbol, bars=bars, listed_on=listed, delisted_on=delisted)


def _cache_path(symbol: str) -> Path:
    return CACHE / f"{symbol.upper()}.json"


def _read_cache(symbol: str) -> tuple[Series, date] | None:
    path = _cache_path(symbol)
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    bars = tuple(
        Bar(
            date=date.fromisoformat(row["d"]),
            close=float(row["c"]),
            dividend=float(row.get("v", 0)),
            split=float(row.get("s", 0)),
        )
        for row in payload["bars"]
    )
    if not bars:
        return None
    series = Series(
        symbol=payload["symbol"],
        bars=bars,
        listed_on=date.fromisoformat(payload["listed_on"]),
        delisted_on=None,
    )
    return series, bars[-1].date


def _write_cache(series: Series) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": series.symbol,
        "listed_on": series.listed_on.isoformat() if series.listed_on else series.bars[0].date.isoformat(),
        "bars": [
            {"d": bar.date.isoformat(), "c": bar.close, "v": bar.dividend, "s": bar.split}
            for bar in series.bars
        ],
    }
    path = _cache_path(series.symbol)
    path.write_text(json.dumps(payload))


def _download(symbol: str) -> Series:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise LookupError("yfinance is not installed.") from exc
    try:
        frame = yf.Ticker(symbol).history(period="max", auto_adjust=False, actions=True)
    except Exception as exc:
        raise LookupError(f"Could not download {symbol}: {exc}") from exc
    if frame is None or frame.empty:
        raise LookupError(f"No price history for {symbol}.")
    bars: list[Bar] = []
    for ts, row in frame.iterrows():
        close = float(row.get("Close") or 0)
        if close <= 0:
            continue
        when = _as_date(ts)
        dividend = float(row.get("Dividends") or 0)
        split = float(row.get("Stock Splits") or 0)
        bars.append(Bar(date=when, close=close, dividend=dividend, split=split))
    if not bars:
        raise LookupError(f"No usable closes for {symbol}.")
    bars.sort(key=lambda bar: bar.date)
    dedup: dict[date, Bar] = {}
    for bar in bars:
        dedup[bar.date] = bar
    ordered = tuple(dedup[day] for day in sorted(dedup))
    return Series(symbol=symbol.upper(), bars=ordered, listed_on=ordered[0].date, delisted_on=None)


def _as_date(value: object) -> date:
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    ts = value
    tz_convert = getattr(ts, "tz_convert", None)
    if tz_convert is not None and getattr(ts, "tzinfo", None) is not None:
        ts = tz_convert("America/New_York")
    as_date = getattr(ts, "date", None)
    if callable(as_date):
        return as_date()
    raise LookupError(f"Unrecognized price date {value!r}.")
