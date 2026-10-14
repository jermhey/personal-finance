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
