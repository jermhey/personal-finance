from datetime import date

from fastapi.testclient import TestClient

from pf import server


def test_preview_covers_thirty_years_and_returns_a_graph():
    client = TestClient(server.app)
    body = {
        "start": "1994-01-03",
        "end": "2024-01-02",
        "listing_policy": "skip",
        "rebalance": {"mode": "yearly", "threshold": 0.05},
        "sleeves": [
            {"symbol": "AAA", "drip": True},
            {"symbol": "BBB", "drip": False},
        ],
        "regimes": [
            {"date": "1994-01-03", "weights": {"AAA": 0.7, "BBB": 0.3}},
            {"date": "2008-01-02", "weights": {"AAA": 0.4, "BBB": 0.6}},
        ],
        "flows": [
            {
                "amount": 500,
                "frequency": "monthly",
                "start": "1994-01-03",
                "end": "2024-01-02",
                "allocation": "targets",
            },
            {
                "amount": 10000,
                "frequency": "once",
                "start": "2000-01-03",
                "end": None,
                "allocation": {"AAA": 1},
            },
        ],
        "forward": {
            "years": 30,
            "annual_contribution": 6000,
            "annual_withdrawal": 20000,
            "trials": 80,
        },
    }
    response = client.post("/api/preview", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"] == "sample"
    assert payload["sampling"] == "month-end"
    assert len(payload["points"]) >= 350
    assert payload["points"][0]["date"] < payload["points"][-1]["date"]
    assert "value" in payload["points"][-1]
    assert "AAA" in payload["points"][-1]["sleeves"]
    assert payload["stats"]["ending_value"] > 0
    assert payload["montecarlo"]["success_rate"] is not None
    assert len(payload["montecarlo"]["p50"]) == 31
    assert date.fromisoformat(payload["points"][0]["date"]).year == 1994
    assert payload["points"][0]["real_value"] > payload["points"][0]["value"]
    assert payload["stats"]["max_drawdown"] is not None
    assert payload["metrics"]["sharpe"] is not None
    assert payload["metrics"]["worst_year"] is not None


def test_benchmark_tracks_the_same_deposits():
    client = TestClient(server.app)
    response = client.post(
        "/api/preview",
        json={
            "start": "2020-01-02",
            "end": "2020-06-30",
            "sleeves": [{"symbol": "AAA", "drip": True, "expense_ratio": 0}],
            "flows": [
                {
                    "amount": 100,
                    "frequency": "monthly",
                    "start": "2020-01-02",
                    "end": "2020-06-30",
                    "allocation": {"AAA": 1},
                }
            ],
            "benchmark": "BBB",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    bench = payload["benchmark"]
    assert bench["symbol"] == "BBB"
    assert bench["error"] is None
    assert len(bench["values"]) == len(payload["points"])
    assert bench["values"][-1] > 0


def test_unknown_symbol_is_a_clear_404(monkeypatch):
    class Boom:
        def load(self, symbol, start, end):
            raise LookupError(f"No price history for {symbol}.")

    monkeypatch.setattr(server, "STORE", Boom())
    response = TestClient(server.app).get("/api/symbol/ZZZZQA")
    assert response.status_code == 404
    assert "ZZZZQA" in response.json()["detail"]


def test_outlook_and_lasting_run_on_sample_prices():
    client = TestClient(server.app)
    outlook = client.post(
        "/api/outlook",
        json={
            "mix": "balanced",
            "years": 20,
            "starting": 10000,
            "annual_contribution": 5000,
            "annual_withdrawal": 0,
            "glide": False,
            "trials": 80,
            "source": "sample",
        },
    )
    assert outlook.status_code == 200, outlook.text
    fan = outlook.json()["montecarlo"]
    assert fan is not None
    assert len(fan["p50"]) == 21
    assert "not a promise" in outlook.json()["plain"]

    lasting = client.post(
        "/api/lasting",
        json={
            "mix": "stocks",
            "years": 30,
            "starting": 1000000,
            "annual_spend": 40000,
            "inflation": True,
            "source": "sample",
        },
    )
    assert lasting.status_code == 200, lasting.text
    body = lasting.json()
    assert body["tested"] > 5
    assert 0 <= body["lasted"] <= body["tested"]
    assert len(body["periods"]) == body["tested"]
    assert "cost of living" in body["plain"]


def test_treasury_presets_report_certainty_from_1928():
    from pf.decide import lasting

    body = lasting(
        "balanced",
        30,
        1_000_000,
        40_000,
        True,
        "market",
        None,
        certainty=0.9,
    )
    assert body["tested"] > 50
    assert body["history_start"].startswith("1928")
    assert 0 < body["spend_at_certainty"] < 1_000_000
    assert "10-year Treasury" in body["detail"]
    assert "Social Security" in body["plain"]
