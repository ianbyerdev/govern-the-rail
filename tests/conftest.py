import pytest
from agentic_risc.service import RISCService


@pytest.fixture
def svc(tmp_path):
    return RISCService(tmp_path, clock=lambda: 1800000000)
