from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Scope(StrictModel):
    principal_id: str = "institution.demo"
    agent_id: str = "agent.child"
    resources: list[str] = Field(default_factory=lambda: ["beneficiary:acme"])
    routes: list[str] = Field(default_factory=lambda: ["local-ach"])
    currencies: list[str] = Field(default_factory=lambda: ["USD"])
    purposes: list[str] = Field(default_factory=lambda: ["invoice"])
    operations: list[str] = Field(default_factory=lambda: ["payment"])
    max_amount_cents: int = Field(default=5000, ge=1, le=100000000)
    exp: int = Field(ge=1)


class Proposal(StrictModel):
    amount_cents: int = Field(default=7500, ge=1, le=100000000)
    beneficiary: str = Field(default="acme", min_length=1, max_length=120)
    currency: str = "USD"
    route: str = "local-ach"
    purpose: str = "invoice"
    principal_id: str = "institution.demo"
    agent_id: str = "agent.demo"
    harness_id: str = "mock-harness"
    model_id: str = "deterministic-proposer"
    grant_id: str = "G-DEMO"
    socket_id: str = "PAYMENTS-1"
    operation: str = "payment"
    ttl_seconds: int = Field(default=120, ge=1, le=3600)
    actor_risk_observed: dict[str, int] = Field(default_factory=lambda: {"used_cents": 0, "reserved_cents": 0, "limit_cents": 50000})
    delegation: Scope | None = None


class Effect(StrictModel):
    p: str
    s: str
    o: str
    r: str
    x: dict
    c: dict


class PolicyConfig(StrictModel):
    version: int = Field(default=1, ge=1)
    session_limit_cents: int = Field(default=50000, ge=1, le=100000000)
    per_action_cents: int = Field(default=20000, ge=1, le=100000000)
    approval_above_cents: int = Field(default=10000, ge=0, le=100000000)
    max_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    allow_attenuation: bool = False
    beneficiaries: list[str] = Field(default_factory=lambda: ["beneficiary:acme", "beneficiary:community"])
    currencies: list[str] = Field(default_factory=lambda: ["USD"])
    routes: list[str] = Field(default_factory=lambda: ["local-ach"])
    purposes: list[str] = Field(default_factory=lambda: ["invoice", "research"])


class SAACError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def require(condition: bool, code: str, message: str):
    if not condition:
        raise SAACError(code, message)
