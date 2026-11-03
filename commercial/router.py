"""HTTP candidate. Deliberately unavailable until independent operational gates."""
from __future__ import annotations
import json
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .schemas import HandoffSubmission
from .service import DuplicateConflict, DuplicateExpired, HandoffService, StoreUnavailable

router = APIRouter()
MAX_HANDOFF_BYTES = 49152

class GateError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code

def _error(status: int, code: str, retry_after: str | None = None) -> JSONResponse:
    headers = {"Cache-Control":"no-store"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return JSONResponse(
        {"error": {"code": code, "request_id": uuid.uuid4().hex}},
        status_code=status, headers=headers)

def require_release_gate():
    """FAIL CLOSED. Operational AO-01/DB/LGPD/rate-limiter approvals missing.
    A future integration must replace this dependency with independently reviewed
    checks. Setting an environment variable is deliberately insufficient.
    """
    raise GateError(503, "FEATURE_DISABLED")

def require_distributed_rate_limit():
    """No in-memory fallback; the real distributed limiter is not implemented."""
    raise GateError(503, "FEATURE_DISABLED")

def get_service() -> HandoffService:
    """Production credentials and PostgreSQL must never be synthesized."""
    raise GateError(503, "PERSISTENCE_UNAVAILABLE")

async def _read_body(request: Request) -> bytes | None:
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise ValueError("Bad content-length")
        if int(length) > MAX_HANDOFF_BYTES:
            return None
    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_HANDOFF_BYTES:
            return None
        chunks.extend(chunk)
    return bytes(chunks)

@router.post("/v1/public/handoffs", status_code=201)
async def submit_handoff(
    request: Request, response: Response,
    release: Any = Depends(require_release_gate),
    throttle: Any = Depends(require_distributed_rate_limit),
    service: HandoffService = Depends(get_service),
):
    # Must not store content or return a receipt before a transaction commits.
    ctype = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if ctype != "application/json":
        return _error(415, "UNSUPPORTED_MEDIA_TYPE")
    origin = request.headers.get("origin")
    if origin:
        o = urlsplit(origin)
        if o.scheme not in ("https", "http") or o.netloc.lower() != request.headers.get("host", "").lower():
            return _error(403, "FORBIDDEN")
    raw_key = request.headers.get("idempotency-key", "")
    try:
        parsed_key = uuid.UUID(raw_key)
        if parsed_key.version != 4 or str(parsed_key) != raw_key.lower():
            raise ValueError()
    except (ValueError, AttributeError):
        return _error(400, "BAD_REQUEST")
    try:
        raw = await _read_body(request)
    except ValueError:
        return _error(400, "BAD_REQUEST")
    if raw is None:
        return _error(413, "PAYLOAD_TOO_LARGE")
    try:
        content = json.loads(raw)
    except (ValueError, UnicodeError):
        return _error(400, "BAD_REQUEST")
    if not isinstance(content, dict):
        return _error(422, "VALIDATION_FAILED")
    try:
        data = HandoffSubmission.model_validate(content)
    except ValidationError:
        return _error(422, "VALIDATION_FAILED")
    try:
        receipt, replayed = service.submit(data, parsed_key)
    except DuplicateConflict:
        return _error(409, "IDEMPOTENCY_CONFLICT")
    except DuplicateExpired:
        # Expiration is distinct from a mismatched payload. NEVER create a fresh key here.
        return _error(409, "IDEMPOTENCY_EXPIRED")
    except (StoreUnavailable, OSError):
        return _error(503, "PERSISTENCE_UNAVAILABLE")
    # Unexpected exception may have occurred before/after commit; do not claim success.
    response.headers["Idempotency-Replayed"] = str(replayed).lower()
    response.headers["Cache-Control"] = "no-store"
    return receipt.model_dump(mode="json")

def require_staff():
    """No native session adapter yet. Admin routes are NOT mounted or exposed."""
    raise GateError(401, "UNAUTHENTICATED")
