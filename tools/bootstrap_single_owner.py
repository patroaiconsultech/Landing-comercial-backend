#!/usr/bin/env python3
"""PROPOSAL_ONLY: offline first-owner bootstrap, PatroAI Commercial STAGING.

This code must NOT be executed against Railway before a separate AO-01 patch
review, explicit human execution approval, and an isolated PostgreSQL rehearsal.
The existing dual-approval provisioner remains unchanged.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import getpass
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

TARGET_EMAIL = "daniel@patroai.com"
OWNER_ID = "daniel_graebin"
TARGET_ROLE = "super_admin"
TARGET_ENV = "STAGING"
TARGET_DATABASE = "railway"
TARGET_SYSTEM_ID = 7704322989484859451
TARGET_REVISION = "0002_patroai_super_admin_r2"
PURPOSE = "INITIAL_SINGLE_OWNER_BOOTSTRAP"
EVENT = "INITIAL_SINGLE_OWNER_BOOTSTRAP"
ADVISORY_LOCK = 7770003  # Matches ordinary R2 provisioner.
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX32 = re.compile(r"[0-9a-f]{32}\Z")
AO_REF = re.compile(r"[A-Za-z0-9_.:-]{8,80}\Z")
REQUIRED_PAYLOAD = frozenset({
    "purpose", "environment", "database", "system_identifier",
    "alembic_revision", "email", "role", "approver_id", "script_sha256",
    "issued_at", "expires_at", "nonce", "ao01_reference",
})

def canonical(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def script_sha256() -> str:
    return sha256(Path(__file__).read_bytes())

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class BootstrapAbort(ValueError):
    """Expected and deliberately redacted stop; safe to print the reason code."""
    pass

def fail(reason: str):
    raise BootstrapAbort(reason)

def decode_b64(value: str, expected_length: int | None = None) -> bytes:
    if not isinstance(value, str):
        fail("B64_TYPE_INVALID")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        fail("B64_INVALID")
    if expected_length is not None and len(raw) != expected_length:
        fail("B64_SIZE_INVALID")
    return raw

def parse_utc(value: str) -> datetime:
    if not isinstance(value, str):
        fail("DATE_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("DATE_INVALID")
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        fail("DATE_NOT_UTC")
    return parsed.astimezone(timezone.utc)

def validate_payload(payload: dict, *, now_at: datetime | None = None,
                     expected_script_sha: str | None = None) -> dict:
    if not isinstance(payload, dict) or set(payload) != REQUIRED_PAYLOAD:
        fail("PAYLOAD_FIELDS_INVALID")
    expected = {
        "purpose": PURPOSE, "environment": TARGET_ENV,
        "database": TARGET_DATABASE, "system_identifier": str(TARGET_SYSTEM_ID),
        "alembic_revision": TARGET_REVISION, "email": TARGET_EMAIL,
        "role": TARGET_ROLE, "approver_id": OWNER_ID,
    }
    if any(type(payload.get(key)) is not str or payload[key] != value
           for key, value in expected.items()):
        fail("PAYLOAD_TARGET_MISMATCH")
    code_hash = expected_script_sha or script_sha256()
    if (not isinstance(payload["script_sha256"], str)
            or not HEX64.fullmatch(payload["script_sha256"])
            or payload["script_sha256"] != code_hash):
        fail("CODE_HASH_MISMATCH")
    nonce = payload["nonce"]
    if not isinstance(nonce, str) or not HEX32.fullmatch(nonce):
        fail("NONCE_INVALID")
    ref = payload["ao01_reference"]
    if not isinstance(ref, str) or not AO_REF.fullmatch(ref):
        fail("AO01_REFERENCE_INVALID")
    issued = parse_utc(payload["issued_at"])
    expires = parse_utc(payload["expires_at"])
    moment = now_at or utc_now()
    if moment.tzinfo is None:
        fail("CLOCK_INVALID")
    if not (issued <= moment + timedelta(minutes=2)
            and moment < expires
            and timedelta(0) < expires - issued <= timedelta(hours=24)):
        fail("APPROVAL_EXPIRED_OR_INVALID_WINDOW")
    return payload

def verify_document(document: dict, public_key_b64: str, approved_sha: str,
                    ao_ref: str, *, now_at: datetime | None = None,
                    expected_script_sha: str | None = None) -> dict:
    """Signed payload plus separately pinned full-document hash (AO-01 external record)."""
    if not isinstance(document, dict) or set(document) != {"payload", "signature_b64"}:
        fail("DOCUMENT_FIELDS_INVALID")
    if not isinstance(approved_sha, str) or not HEX64.fullmatch(approved_sha):
        fail("AO01_DOCUMENT_HASH_NOT_CONFIGURED")
    if sha256(canonical(document)) != approved_sha:
        fail("AO01_DOCUMENT_HASH_MISMATCH")
    payload = validate_payload(document["payload"], now_at=now_at,
                               expected_script_sha=expected_script_sha)
    if payload["ao01_reference"] != ao_ref:
        fail("AO01_REFERENCE_MISMATCH")
    key_bytes = decode_b64(public_key_b64, 32)
    sig = decode_b64(document["signature_b64"], 64)
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(sig, canonical(payload))
    except (InvalidSignature, ValueError):
        fail("SIGNATURE_INVALID")
    return payload

def new_payload(ao01_reference: str, duration_minutes: int = 60,
                *, moment: datetime | None = None) -> dict:
    if not AO_REF.fullmatch(ao01_reference):
        fail("AO01_REFERENCE_INVALID")
    if not isinstance(duration_minutes, int) or not 1 <= duration_minutes <= 1440:
        fail("DURATION_INVALID")
    t = moment or utc_now()
    return {
        "purpose": PURPOSE, "environment": TARGET_ENV,
        "database": TARGET_DATABASE, "system_identifier": str(TARGET_SYSTEM_ID),
        "alembic_revision": TARGET_REVISION, "email": TARGET_EMAIL,
        "role": TARGET_ROLE, "approver_id": OWNER_ID,
        "script_sha256": script_sha256(),
        "issued_at": t.isoformat(), "expires_at": (t + timedelta(minutes=duration_minutes)).isoformat(),
        "nonce": os.urandom(16).hex(), "ao01_reference": ao01_reference,
    }

def write_new_json(path: Path, data: object):
    # Never clobber a signed approval or private material.
    with path.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write("\n")

def prepare(args):
    payload = new_payload(args.ao01_reference, args.minutes)
    write_new_json(Path(args.out), {"payload": payload})
    print("UNSIGNED_APPROVAL_WRITTEN=YES")
    print("SCRIPT_SHA256=" + payload["script_sha256"])
    print("NONCE=" + payload["nonce"])

def sign(args):
    unsigned = json.loads(Path(args.unsigned).read_text(encoding="utf-8"))
    if not isinstance(unsigned, dict) or set(unsigned) != {"payload"}:
        fail("UNSIGNED_DOCUMENT_INVALID")
    payload = validate_payload(unsigned["payload"])
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private_key = load_pem_private_key(
        Path(args.private_key).read_bytes(),
        password=getpass.getpass("Encrypted Ed25519 private key password: ").encode("utf-8"),
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        fail("PRIVATE_KEY_NOT_ED25519")
    signature = private_key.sign(canonical(payload))
    document = {"payload": payload, "signature_b64": base64.b64encode(signature).decode("ascii")}
    write_new_json(Path(args.out), document)
    print("SIGNED_APPROVAL_WRITTEN=YES")
    print("SIGNED_DOCUMENT_SHA256=" + sha256(canonical(document)))
    print("Never print or share private key/password/TOTP secret.")

def normalize_db_url(raw: str) -> str:
    if raw.startswith("postgres://"):
        return "postgresql+psycopg://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw

def environment_gate(env: dict):
    if env.get("RAILWAY_ENVIRONMENT_NAME") != TARGET_ENV:
        fail("WRONG_RAILWAY_ENVIRONMENT")
    if env.get("PATROAI_DASHBOARD_ENABLED") != "false":
        fail("DASHBOARD_MUST_BE_OFF")
    if env.get("PATROAI_PROVISION_GATE") == "true":
        fail("ORDINARY_PROVISION_GATE_MUST_BE_OFF")
    if env.get("PATROAI_SINGLE_OWNER_BOOTSTRAP_GATE") != "true":
        fail("SINGLE_OWNER_GATE_DISABLED")
    key = env.get("PATROAI_ADMIN_TOTP_KEY", "")
    try:
        decoded = bytes.fromhex(key)
    except ValueError:
        fail("INVALID_TOTP_KEY")
    if len(decoded) != 32:
        fail("INVALID_TOTP_KEY")
    return decoded

def database_url_gate(raw: str):
    # SQLAlchemy imported lazily; --prepare and --sign work on owner's Windows PC.
    from sqlalchemy.engine import make_url
    url = make_url(normalize_db_url(raw))
    if url.drivername != "postgresql+psycopg" or url.database != TARGET_DATABASE:
        fail("WRONG_DATABASE_URL")
    if not url.host:
        fail("DATABASE_HOST_MISSING")
    return url

def validate_database_identity(session):
    from sqlalchemy import text
    row = session.execute(text(
        "SELECT current_database(), system_identifier, current_schema(),"
        "current_setting('search_path') FROM pg_control_system()"
    )).one()
    database, system_id, schema, search_path = row
    if (database != TARGET_DATABASE or int(system_id) != TARGET_SYSTEM_ID
            or schema != "public" or "public" not in search_path):
        fail("DATABASE_IDENTITY_MISMATCH")
    revision = session.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one()
    if revision != TARGET_REVISION:
        fail("ALEMBIC_REVISION_MISMATCH")

def persist_first_owner(session, *, user_id: UUID, salt: bytes,
                        pw_hash: bytes, encrypted_totp: bytes, verified_step: int,
                        nonce: str, event_time: datetime):
    """Call ONLY inside Session.begin(); never commits itself.

    Table lock serializes with any INSERT into admin_staff, including old provisioners.
    A durable audit nonce blocks reuse even if staff is later deactivated/deleted.
    """
    from sqlalchemy import func, select, text
    from dashboard.models import Staff, StaffAudit

    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    session.execute(text("SET LOCAL statement_timeout = '30s'"))
    session.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": ADVISORY_LOCK})
    session.execute(text("LOCK TABLE public.patroai_admin_staff IN SHARE ROW EXCLUSIVE MODE"))
    validate_database_identity(session)
    if session.scalar(select(func.count()).select_from(Staff)) != 0:
        fail("ADMINISTRATOR_ALREADY_EXISTS")
    if session.scalar(select(func.count()).select_from(StaffAudit).where(
            StaffAudit.request_id == nonce)) != 0:
        fail("NONCE_ALREADY_USED")
    session.add(Staff(
        id=user_id, email=TARGET_EMAIL, role=TARGET_ROLE, active=True,
        password_salt=salt, password_digest=pw_hash,
        totp_ciphertext=encrypted_totp, last_totp_step=verified_step,
        created_at=event_time,
    ))
    session.add(StaffAudit(
        staff_id=user_id, event=EVENT, request_id=nonce, occurred_at=event_time,
    ))
    session.flush()

def execute(args):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        fail("INTERACTIVE_TTY_REQUIRED")
    totp_key = environment_gate(os.environ)
    url = database_url_gate(os.getenv("DATABASE_URL", ""))
    document = json.loads(Path(args.approval).read_text(encoding="utf-8"))
    approval_hash = os.getenv("PATROAI_SINGLE_OWNER_AO01_APPROVED_DOC_SHA256", "")
    owner_key = os.getenv("PATROAI_SINGLE_OWNER_PUBLIC_KEY_B64", "")
    ao_ref = os.getenv("PATROAI_SINGLE_OWNER_AO01_REFERENCE", "")
    payload = verify_document(document, owner_key, approval_hash, ao_ref)
    print("TARGET=STAGING/railway/daniel@patroai.com/super_admin")
    print("SCRIPT_SHA256=" + script_sha256())
    print("SIGNED_DOCUMENT_SHA256=" + approval_hash)
    print("EXTERNAL_OWNER_IDENTITY_AND_AO01_APPROVAL_MUST_BE_VERIFIED")
    confirmed = input("Type AUTORIZO_DANIEL_STAGING to confirm this authorized operation: ")
    if confirmed != "AUTORIZO_DANIEL_STAGING":
        fail("HUMAN_CONFIRMATION_MISSING")

    from dashboard.auth import matching_totp_step, now, password_digest, seal_totp
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import SQLAlchemyError
    password = getpass.getpass("New owner password (16-1024 chars): ")
    confirmation = getpass.getpass("Confirm password: ")
    if len(password) < 16 or len(password) > 1024 or password != confirmation:
        fail("PASSWORD_INVALID")
    salt = os.urandom(16)
    pw_hash = password_digest(password, salt)
    del password, confirmation

    user_id = uuid4()
    seed = os.urandom(20)
    print("ENROLL THIS TOTP SECRET PRIVATELY; DO NOT LOG THE TERMINAL:")
    print(base64.b32encode(seed).decode("ascii").rstrip("="))
    verified_step = matching_totp_step(seed, getpass.getpass("Confirm CURRENT six-digit MFA code: "))
    if verified_step is None:
        fail("MFA_NOT_CONFIRMED")
    # Check again after potentially lengthy interactive MFA enrollment.
    verify_document(document, owner_key, approval_hash, ao_ref)
    encrypted_seed = seal_totp(totp_key, user_id, seed)
    del seed
    engine = create_engine(url, pool_pre_ping=True)
    try:
        try:
            with Session(engine) as session, session.begin():
                environment_gate(os.environ)
                verify_document(document, owner_key, approval_hash, ao_ref)
                persist_first_owner(
                    session, user_id=user_id, salt=salt, pw_hash=pw_hash,
                    encrypted_totp=encrypted_seed, verified_step=verified_step,
                    nonce=payload["nonce"], event_time=now(),
                )
        except SQLAlchemyError:
            # Prevent SQLAlchemy connection/SQL details from appearing in terminal logs.
            fail("POSTGRESQL_OPERATION_FAILED_INSPECT_DATABASE")
        print("BOOTSTRAP_COMMITTED=YES")
        print("NEXT_LOGIN_REQUIRES_NEW_TOTP_STEP=YES")
        print("IMMEDIATELY_DISABLE_SINGLE_OWNER_BOOTSTRAP_GATE")
    finally:
        engine.dispose()

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="Create unsigned short-lived approval (no database)")
    p.add_argument("--ao01-reference", required=True)
    p.add_argument("--minutes", type=int, default=60)
    p.add_argument("--out", required=True)
    s = sub.add_parser("sign", help="Sign prepared approval on owner's computer")
    s.add_argument("--unsigned", required=True)
    s.add_argument("--private-key", required=True)
    s.add_argument("--out", required=True)
    e = sub.add_parser("execute", help="ONLY after independent AO-01 execution GO")
    e.add_argument("--approval", required=True)
    args = parser.parse_args(argv)
    try:
        {"prepare": prepare, "sign": sign, "execute": execute}[args.command](args)
    except BootstrapAbort as err:
        # All error codes come from literals in this audited script, never user data.
        print("ABORT=" + str(err), file=sys.stderr)
        raise SystemExit(2) from None
    except (ValueError, KeyError, OSError, json.JSONDecodeError):
        print("ABORT=INVALID_INPUT_OR_FILE", file=sys.stderr)
        raise SystemExit(2) from None

if __name__ == "__main__":
    main()
