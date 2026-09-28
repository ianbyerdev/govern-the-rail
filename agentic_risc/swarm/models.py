from typing import Literal
from pydantic import Field
from ..domain_models import DomainProposal
from ..models import StrictModel

OPERATIONS = ['artifact.read', 'artifact.write', 'egress.publish', 'resource.access', 'agent.spawn', 'agent.stop']
SOCKETS = {'artifact.read': 'ARTIFACT-1', 'artifact.write': 'ARTIFACT-1', 'egress.publish': 'EGRESS-1',
           'resource.access': 'RESOURCE-1', 'agent.spawn': 'SUPERVISOR-1', 'agent.stop': 'SUPERVISOR-1'}
BUDGETS = {'messages': 10000, 'read_bytes': 1000000, 'disclosed_bytes': 100000, 'agent_starts': 256, 'agent_slots': 64}

class ChildScope(StrictModel):
    principal_id: str = 'institution.demo'
    operations: list[str] = Field(default_factory=lambda: ['artifact.read'])
    resources: list[str] = Field(default_factory=lambda: ['artifact:team-a'])
    audiences: list[str] = Field(default_factory=lambda: ['ARTIFACT-1'])
    purposes: list[str] = Field(default_factory=lambda: ['research'])
    recipients: list[str] = Field(default_factory=lambda: ['team-a'])
    ceilings: dict[str, int] = Field(default_factory=lambda: {'messages': 1, 'read_bytes': 4096, 'disclosed_bytes': 4096, 'agent_starts': 1, 'agent_slots': 1})
    exp: int = Field(ge=1)

class ArtifactProposal(DomainProposal):
    operation: Literal['artifact.read', 'artifact.write'] = 'artifact.write'
    socket_id: str = 'ARTIFACT-1'
    resource: str = 'artifact:team-a'
    payload: str = Field(default='Synthetic research note.', max_length=4096)
    max_bytes: int = Field(default=4096, ge=1, le=4096)
    purpose: str = 'research'
    recipient: str = 'team-a'

class EgressProposal(DomainProposal):
    operation: Literal['egress.publish'] = 'egress.publish'
    socket_id: str = 'EGRESS-1'
    resource: str = 'endpoint:lab'
    payload: str = Field(default='SYNTHETIC confidential report.', max_length=4096)
    purpose: str = 'research'
    credential: str = 'mock-lab-key'

class AccessProposal(DomainProposal):
    operation: Literal['resource.access'] = 'resource.access'
    socket_id: str = 'RESOURCE-1'
    resource: str = 'resource:lab'
    credential: str = 'mock-lab-key'
    purpose: str = 'research'
    max_bytes: int = Field(default=256, ge=1, le=4096)

class SpawnProposal(DomainProposal):
    operation: Literal['agent.spawn'] = 'agent.spawn'
    socket_id: str = 'SUPERVISOR-1'
    resource: str = 'workspace:team-a'
    child_id: str = Field(pattern=r'^child-[a-zA-Z0-9_-]{1,80}$')
    scope: ChildScope
    program: Literal['logical','contained-proof'] = 'logical'

class StopProposal(DomainProposal):
    operation: Literal['agent.stop'] = 'agent.stop'
    socket_id: str = 'SUPERVISOR-1'
    resource: str = 'workspace:team-a'
    child_id: str = Field(pattern=r'^child-[a-zA-Z0-9_-]{1,80}$')

PROPOSALS = {'artifact.read': ArtifactProposal, 'artifact.write': ArtifactProposal, 'egress.publish': EgressProposal,
             'resource.access': AccessProposal, 'agent.spawn': SpawnProposal, 'agent.stop': StopProposal}
