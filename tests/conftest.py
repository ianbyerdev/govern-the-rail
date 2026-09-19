import pytest
from saac.service import SAACService


@pytest.fixture
def svc(tmp_path):
    return SAACService(tmp_path, clock=lambda: 1800000000)
