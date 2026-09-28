"""Agentic RISC artifact verification. Verify the original bytes and exact artifact type."""
from .crypto import verify

from .wire import WIRE_VERSION


def verify_artifact(obj, public, kid, typ):
    verify(obj, public, kid, typ)
