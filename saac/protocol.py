"""SAAC artifact verification. Verify the original bytes and exact artifact type."""
from .crypto import verify

WIRE_VERSION = "0.3.0"


def verify_artifact(obj, public, kid, typ):
    verify(obj, public, kid, typ)
