from fastapi.testclient import TestClient
import pytest
from saac.api import create_app
from saac.lab import run_scenario, SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_lab_scenarios(tmp_path, scenario):
    result = run_scenario(tmp_path, scenario)
    assert result["verification"]["valid"]
    risk = result["view"]["risk"]
    assert risk["used_cents"] + risk["reserved_cents"] <= risk["limit_cents"]
    if scenario == "race":
        assert result["race"]["accepted"] == 10
        assert result["race"]["unsafe"]["accepted"] == 100
    if scenario in ("modify-up", "beneficiary", "audience", "expired", "alias-rebound", "stale", "pack", "expansion"):
        assert not result["view"]["payments"]


def test_actor_cannot_reach_any_operator_control(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    actor = {"Authorization": "Bearer "+app.state.actor_token}
    endpoints = [("GET", "/state"), ("GET", "/tape"), ("PUT", "/pack"), ("POST", "/resource"), ("POST", "/clock"),
                 ("POST", "/breaker"), ("POST", "/lab/race"), ("POST", "/runs/any/approval"), ("POST", "/runs/any/reconcile")]
    for method,path in endpoints:
        response = client.request(method, "/api/operator"+path, headers=actor, json={} if method != "GET" else None)
        assert response.status_code == 403, response.text
    assert client.post("/api/proposals", headers=actor, json={"principal_id": "other"}).status_code == 409
    assert client.post("/api/proposals", headers=actor, json={"amount_cents": 1.25}).status_code == 422
    assert client.get("/api/operator/state").status_code == 401
    assert client.get("/keys/riskbook.key").status_code == 404


def test_lab_reconciliation_endpoint(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    op = {"Authorization": "Bearer "+app.state.operator_token}
    lab = client.post("/api/operator/lab/receipt-loss", json={}, headers=op).json()
    result = client.post(f"/api/operator/lab-books/{lab['book_id']}/runs/{lab['run']['id']}/reconcile", json={}, headers=op)
    assert result.status_code == 200, result.text
    assert result.json()["view"]["risk"]["used_cents"] == 7500
