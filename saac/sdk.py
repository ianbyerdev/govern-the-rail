"""Actor-side SAAC client. No policy, ledger, private keys or effect implementation.

Canonical resource/effect resolution belongs to the authority and rail. Client
validation structures intent only; a compromised client cannot mint authority.
"""
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from .domain_models import parse_proposal
from .models import require


class Transport(Protocol):
    def authorize(self, proposal: dict, preview: bool = False, *, request_id: str | None = None) -> dict: ...
    def execute(self, proposal: dict, kappa: dict | None) -> dict: ...
    def reconcile(self, receipt: dict) -> dict: ...


@dataclass(frozen=True)
class Identity:
    principal_id: str = "institution.demo"
    agent_id: str = "agent.demo"
    grant_id: str = "G-DEMO"


class SAACClient:
    def __init__(self, transport: Transport, identity: Identity | None = None):
        self.transport, self.identity = transport, identity

    def propose(self, intent: dict, *, preview: bool = False, harness: str = "native-app", request_id: str | None = None):
        """Pass the same request_id on retries, including after a lost response.

        Omission starts a new logical request. A replay returns the original κ,
        never a refreshed expiry or a second allocation.
        """
        proposal = dict(intent)
        if self.identity is not None:
            for field in ("principal_id", "agent_id", "grant_id"):
                value = getattr(self.identity, field)
                require(field not in proposal or proposal[field] == value,
                        "IDENTITY_BINDING", "Intent cannot replace this client's identity context.")
                proposal[field] = value
        proposal["harness_id"] = harness  # Diagnostic provenance, never a grant.
        return self.transport.authorize(parse_proposal(proposal).model_dump(), preview,
                                        request_id=uuid4().hex if request_id is None else request_id)

    def execute(self, proposal: dict, kappa: dict | None):
        return self.transport.execute(proposal, kappa)

    def reconcile(self, receipt: dict):
        return self.transport.reconcile(receipt)


class HTTPTransport:
    """Use an httpx-compatible client with an actor credential and authority URL.

    The local HTTP profile exposes payments. Other packs use the same SDK through
    the operator workbench transport or the existing campaign-bound swarm API.
    """
    def __init__(self, client):
        self.client = client

    def _post(self, path, body, **kwargs):
        response = self.client.post(path, json=body, **kwargs)
        response.raise_for_status()
        return response.json()

    def authorize(self, proposal, preview=False, *, request_id=None):
        headers = {"Idempotency-Key": request_id} if request_id is not None else {}
        return self._post("/api/proposals?preview=" + str(preview).lower(), proposal, headers=headers)

    def execute(self, proposal, kappa):
        return self._post("/api/rail/execute", {"proposal": proposal, "kappa": kappa})

    def reconcile(self, receipt):
        return self._post("/api/receipts", {"receipt": receipt})
