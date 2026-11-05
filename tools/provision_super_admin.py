"""Offline, two-person signed PatroAI Commercial super_admin provisioning (R2).

Never an HTTP endpoint. Approval documents must be signed by two independently
authorized Ed25519 key holders. Neither the browser nor email address grants a role.
SOURCE_ONLY: requires R2 Alembic migration and its own human/AO-01 gates.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.auth import matching_totp_step, now, password_digest, seal_totp
from dashboard.models import Staff, StaffAudit

REVISION = "0002_patroai_super_admin_r2"

def canonical(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def verify_approvals(document: dict, public_keys: dict[str, bytes]) -> str:
    if set(document) != {"payload", "approvals"}:
        raise ValueError("Invalid approval document")
    details = document["payload"]
    if not isinstance(details, dict) or set(details) != {"email", "role", "expires_at", "nonce"}:
        raise ValueError("Invalid approval payload")
    if details["role"] != "super_admin":
        raise ValueError("Only an explicit super_admin approval is permitted")
    email = details["email"]
    if not isinstance(email, str) or not email or len(email)>254 or email != email.strip().casefold():
        raise ValueError("Canonical lowercase email required")
    from email_validator import validate_email
    validate_email(email, check_deliverability=False)
    try:
        expiry = datetime.fromisoformat(details["expires_at"].replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Invalid expiration") from None
    moment = datetime.now(timezone.utc)
    if expiry.tzinfo is None or not moment < expiry <= moment + timedelta(hours=24):
        raise ValueError("Approval must expire within 24 hours")
    if not isinstance(details["nonce"], str) or len(details["nonce"])<24:
        raise ValueError("Approval nonce invalid")
    approvals = document["approvals"]
    if not isinstance(approvals, list) or len(approvals)!=2:
        raise ValueError("Exactly two signatures required")
    ids=[a.get("approver_id") for a in approvals if isinstance(a, dict)]
    if len(ids)!=2 or len(set(ids))!=2 or any(i not in public_keys for i in ids):
        raise ValueError("Two registered approvers required")
    # Duplicating a single key under two IDs is not independence.
    if len({public_keys[i] for i in ids}) != 2:
        raise ValueError("Approver keys must differ")
    data=canonical(details)
    for approval in approvals:
        if set(approval)!={"approver_id","signature_b64"}:
            raise ValueError("Invalid signature fields")
        key=public_keys[approval["approver_id"]]
        if len(key)!=32:
            raise ValueError("Invalid Ed25519 public key")
        sig=base64.b64decode(approval["signature_b64"],validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(sig,data)
    return email

def main() -> None:
    parser=argparse.ArgumentParser(description="Two-person approved PatroAI super admin")
    parser.add_argument("--approval", required=True, help="Path to signed approval JSON (no secret keys)")
    args=parser.parse_args()
    if os.getenv("PATROAI_PROVISION_GATE")!="true":
        raise SystemExit("Provisioning disabled")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit("Protected interactive terminal required")
    raw=os.getenv("PATROAI_APPROVER_PUBLIC_KEYS_JSON","")
    try:
        parsed=json.loads(raw)
        if not isinstance(parsed,dict):
            raise ValueError
        keys={k:base64.b64decode(v,validate=True) for k,v in parsed.items()}
        if any(len(k)!=32 for k in keys.values()):
            raise ValueError
    except (ValueError,TypeError,KeyError):
        raise SystemExit("Public approver keys unavailable") from None
    document=json.loads(Path(args.approval).read_text(encoding="utf-8"))
    email=verify_approvals(document,keys)
    db_url=os.getenv("DATABASE_URL","")
    if db_url.startswith("postgres://"):
        db_url="postgresql+psycopg://"+db_url[11:]
    elif db_url.startswith("postgresql://"):
        db_url="postgresql+psycopg://"+db_url[13:]
    if not db_url.startswith("postgresql+psycopg://"):
        raise SystemExit("Explicit private PostgreSQL URL required")
    try:
        totp_key=bytes.fromhex(os.environ["PATROAI_ADMIN_TOTP_KEY"])
        if len(totp_key)!=32:
            raise ValueError
    except (KeyError,ValueError):
        raise SystemExit("Missing or invalid TOTP encryption key") from None

    password=getpass.getpass("New super admin password (12-1024 chars): ")
    if not 12<=len(password)<=1024 or password!=getpass.getpass("Confirm password: "):
        raise SystemExit("Invalid or mismatched password")
    salt=os.urandom(16)
    pw_digest=password_digest(password,salt)
    password=None
    user_id=uuid4()
    seed=os.urandom(20)
    print("Privately enroll the following seed in an authenticator; do not record the terminal:")
    print(base64.b32encode(seed).decode("ascii").rstrip("="))
    step=matching_totp_step(seed,getpass.getpass("Confirm current 6-digit MFA code: "))
    if step is None:
        raise SystemExit("MFA enrollment failed; account NOT created")
    # A long interactive enrollment must not outlive the signed approval.
    if verify_approvals(document, keys) != email:
        raise SystemExit("Signed approval no longer valid")
    engine=create_engine(db_url,pool_pre_ping=True)
    try:
        with Session(engine) as session, session.begin():
            if session.get_bind().dialect.name!="postgresql":
                raise RuntimeError("PostgreSQL required")
            session.execute(text("SELECT pg_advisory_xact_lock(7770003)"))
            revision=session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            if revision!=REVISION:
                raise RuntimeError("R2 migration not installed: refuse provisioning")
            if session.scalar(select(Staff.id).where(Staff.email==email)):
                raise RuntimeError("Account exists: refuse overwrite")
            session.add(Staff(
                id=user_id, email=email, role="super_admin", active=True,
                password_salt=salt, password_digest=pw_digest,
                totp_ciphertext=seal_totp(totp_key,user_id,seed),
                last_totp_step=step,created_at=now(),
            ))
            session.add(StaffAudit(
                staff_id=user_id,event="SUPER_ADMIN_PROVISIONED",
                request_id=uuid4().hex,occurred_at=now()
            ))
    finally:
        engine.dispose()
    print("Super admin provisioned with MFA; next login requires a NEW TOTP code.")
    print("Disable PATROAI_PROVISION_GATE immediately after approved operations.")

if __name__=="__main__":
    main()
