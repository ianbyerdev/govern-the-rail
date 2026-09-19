"""Local wire encoding: sorted compact UTF-8 JSON; integers, never floats."""
import base64
import hashlib
import json
import os
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from .models import SAACError


def canonical(value) -> bytes:
    def check(v):
        if isinstance(v, float):
            raise ValueError("Floating point is not part of the SAAC toy wire")
        if isinstance(v, dict):
            if any(not isinstance(k, str) for k in v):
                raise ValueError("JSON keys must be strings")
            for x in v.values(): check(x)
        elif isinstance(v, (list, tuple)):
            for x in v: check(x)
    check(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class Signer:
    def __init__(self, path: Path, kid: str):
        self.kid = kid
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            self.key = Ed25519PrivateKey.from_private_bytes(path.read_bytes())
        else:
            self.key = Ed25519PrivateKey.generate()
            data = self.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f: f.write(data)
        self.public = b64(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def sign(self, payload: dict) -> dict:
        body = {**payload, "kid": self.kid}
        return {**body, "sig": b64(self.key.sign(canonical(body)))}


def verify(obj: dict, public: str, kid: str, typ: str):
    try:
        if not isinstance(obj, dict):
            raise ValueError("A signed object is required")
        if obj.get("kid") != kid or obj.get("typ") != typ:
            raise ValueError("Incorrect signer or artifact type")
        body = {k: v for k, v in obj.items() if k != "sig"}
        Ed25519PublicKey.from_public_bytes(unb64(public)).verify(unb64(obj["sig"]), canonical(body))
    except (KeyError, ValueError, TypeError, InvalidSignature) as e:
        raise SAACError("INVALID_SIGNATURE", "A valid signature from the expected authority is required.") from e
