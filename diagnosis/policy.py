"""R3-A policy primitives, free of DB and network side effects."""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
from uuid import UUID
from .schemas import Registration, TurnRequest

ADMIN_TRANSITIONS = {
    "completed": frozenset({"in_review"}),
    "in_review": frozenset({"qualified", "disqualified"}),
    "qualified": frozenset({"contacted"}),
}
MAX_TURNS = 7

def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")

def mac(key: bytes, label: str, value: bytes) -> bytes:
    return hmac.new(key, label.encode("ascii")+b":"+value, hashlib.sha256).digest()

def init_digest(key: bytes, registration: Registration) -> bytes:
    return mac(key, "diag-register-payload-v1",canonical(registration.model_dump(mode="json")))

def turn_digest(key: bytes, turn: TurnRequest) -> bytes:
    return mac(key, "diag-turn-payload-v1", canonical(
        {"message":turn.message,"language":turn.language,"expected_version":turn.expected_version}))

def issued_token(key: bytes, request_key: UUID) -> str:
    """Deterministic, secret-keyed response to initial idempotency-key replay.
    No raw token stored in PostgreSQL; 128-bit UUIDv4 possession required for replay.
    """
    raw=mac(key,"diag-public-token-v1",str(request_key).encode("ascii"))
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def secure_equal(a: bytes,b: bytes) -> bool:
    return hmac.compare_digest(a,b)

def can_review(current: str, target: str) -> bool:
    return target in ADMIN_TRANSITIONS.get(current, ())
