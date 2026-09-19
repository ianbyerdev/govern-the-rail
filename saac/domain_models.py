"""Strict domain proposals; the common Effect envelope remains p,s,o,r,x,c."""
from typing import Literal
from pydantic import Field
from .models import StrictModel, Proposal, require


class DomainProposal(StrictModel):
    principal_id: str = 'institution.demo'
    agent_id: str = 'agent.demo'
    harness_id: str = 'mock-harness'
    model_id: str = 'deterministic-proposer'
    grant_id: str = 'G-DEMO'
    ttl_seconds: int = Field(default=120, ge=1, le=3600)
    actor_risk_observed: dict[str, int] = Field(default_factory=dict)


class OrderProposal(DomainProposal):
    operation: Literal['order.submit'] = 'order.submit'
    socket_id: str = 'OMS-17'
    instrument: str = 'XYZ'
    book: str = 'CASH-1'
    side: Literal['buy'] = 'buy'
    quantity: int = Field(default=2000, ge=1, le=100000)
    limit_price_cents: int = Field(default=4127, ge=1, le=1000000)
    route: str = 'BROKER-A/session17'
    time_in_force: Literal['DAY'] = 'DAY'


class CancelProposal(DomainProposal):
    operation: Literal['order.cancel'] = 'order.cancel'
    socket_id: str = 'OMS-17'
    order_id: str


class ReferralProposal(DomainProposal):
    operation: Literal['referral.release'] = 'referral.release'
    socket_id: str = 'REFERRAL-EGRESS-1'
    patient: str = 'P-104'
    recipient: str = 'north-clinic'
    documents: list[str] = Field(default_factory=lambda: [f'doc-{n}' for n in range(1, 7)], min_length=1, max_length=10)
    purpose: str = 'referral'


class JobScope(StrictModel):
    principal_id: str = 'institution.demo'
    agent_id: str = 'agent.child'
    resources: list[str] = Field(default_factory=lambda: ['workspace:demo'])
    operations: list[str] = Field(default_factory=lambda: ['job.run'])
    network: list[str] = Field(default_factory=list)
    ceilings: dict[str, int] = Field(default_factory=lambda: {'job_starts': 1, 'job_slots': 1})
    exp: int = Field(ge=1)


class JobProposal(DomainProposal):
    operation: Literal['job.run'] = 'job.run'
    socket_id: str = 'RUNTIME-1'
    script: str = 'audit'
    workspace: str = 'demo'
    network: list[str] = Field(default_factory=list)
    argv: list[str] = Field(default_factory=list)


class DelegateProposal(DomainProposal):
    operation: Literal['job.delegate'] = 'job.delegate'
    socket_id: str = 'RUNTIME-1'
    delegation: JobScope


class DomainPolicy(StrictModel):
    version: int = Field(default=1, ge=1)
    limits: dict[str, int]
    per_action: dict[str, int]
    approval_above: dict[str, int] = Field(default_factory=dict)
    max_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    resources: list[str]
    routes: list[str] = Field(default_factory=list)
    purposes: list[str] = Field(default_factory=list)
    recipients: list[str] = Field(default_factory=list)
    price_min_cents: int = Field(default=4000, ge=1)
    price_max_cents: int = Field(default=4300, ge=1)


PROPOSALS = {'payment': Proposal, 'dispatch': Proposal, 'order.submit': OrderProposal,
             'order.cancel': CancelProposal, 'referral.release': ReferralProposal,
             'job.run': JobProposal, 'job.delegate': DelegateProposal}


def parse_proposal(value):
    if isinstance(value, (Proposal, DomainProposal)):
        value = value.model_dump()  # Revalidate even model_copy-created input.
    from .swarm.models import PROPOSALS as SWARM_PROPOSALS
    model = {**PROPOSALS, **SWARM_PROPOSALS}.get(value.get('operation', 'payment'))
    require(model is not None, 'UNMEDIATED_OPERATION', 'No registered profile mediates this operation.')
    return model.model_validate(value)
