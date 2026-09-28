from agentic_risc.service import RISCService
from agentic_risc.models import Proposal
from agentic_risc.crypto import verify


def test_complete_vertical_slice(tmp_path):
    service = RISCService(tmp_path, clock=lambda: 1800000000)
    run = service.authority.propose(Proposal())
    assert run["status"] == "authorized"
    assert service.view()["risk"]["reserved_cents"] == 7500
    verify(run["kappa"], service.issuer.public, "riskbook-1", "agentic-risc-kappa")
    result = service.execute(run["id"])
    assert result["status"] == "settled"
    verify(result["receipt"], service.socket_signer.public, "socket-1", "agentic-risc-receipt")
    assert service.view()["risk"] == {"used_cents": 7500, "reserved_cents": 0, "limit_cents": 50000, "available_cents": 42500}
    assert len(service.view()["payments"]) == 1
    assert result["events"][-1]["kind"] == "RESERVATION_SETTLED"
    duplicate = service.reconciler.accept(result["receipt"])
    assert not duplicate["applied"]


def test_api_vertical_slice_and_separation(tmp_path):
    from fastapi.testclient import TestClient
    from agentic_risc.api import create_app
    app = create_app(tmp_path)
    client = TestClient(app)
    actor = {"Authorization": "Bearer "+app.state.actor_token}
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/operator/state", headers=actor).status_code == 403
    assert client.put("/api/operator/pack", json={}, headers=actor).status_code == 403
    run = client.post("/api/proposals", json={"amount_cents": 7500}, headers=actor).json()
    assert run["status"] == "authorized"
    result = client.post(f"/api/runs/{run['id']}/execute", json={}, headers=actor)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "settled"
