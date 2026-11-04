"""One-time, offline, two-signature first-manager provisioning (NOT an HTTP endpoint).
PROPOSAL_ONLY. Requires separately approved migration, DB access and two Ed25519 approvers.
Never pass a password/OTP/TOTP seed in command-line arguments or write them to files.
"""
from __future__ import annotations
import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.auth import password_digest,seal_totp,matching_totp_step,now
from dashboard.models import Staff,StaffAudit

def canonical(obj:dict)->bytes:
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
def verify_approvals(document:dict,public_keys:dict[str,bytes]):
    """Two different approved key holders sign the same payload."""
    details=document["payload"]
    if set(details)!={"email","role","expires_at","nonce"} or details["role"]!="manager":
        raise ValueError("Invalid approval payload")
    expires=datetime.fromisoformat(details["expires_at"].replace("Z","+00:00"))
    if expires.tzinfo is None or not datetime.now(timezone.utc)<expires:
        raise ValueError("Expired approval")
    if len(details["nonce"])<24: raise ValueError("Missing nonce")
    signatures=document.get("approvals",[])
    if len(signatures)!=2: raise ValueError("Exactly two independent approvals required")
    ids={s.get("approver_id") for s in signatures}
    if len(ids)!=2 or None in ids: raise ValueError("Approvers must differ")
    payload=canonical(details)
    for s in signatures:
        approver=s["approver_id"]
        if approver not in public_keys: raise ValueError("Unknown approver")
        Ed25519PublicKey.from_public_bytes(public_keys[approver]).verify(
            base64.b64decode(s["signature_b64"],validate=True),payload)
    return details

def main():
    parser=argparse.ArgumentParser(description="PatroAI first manager — controlled local tool")
    parser.add_argument("--approval",required=True,help="Two independently signed approvals JSON")
    args=parser.parse_args()
    if os.getenv("PATROAI_PROVISION_GATE")!="true":
        raise SystemExit("Provisioning gate disabled")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise SystemExit("Interactive protected terminal required")
    raw=os.environ.get("PATROAI_APPROVER_PUBLIC_KEYS_JSON","")
    if not raw: raise SystemExit("Approver public keys not configured")
    keys={k:base64.b64decode(v,validate=True) for k,v in json.loads(raw).items()}
    details=verify_approvals(json.loads(Path(args.approval).read_text()),keys)
    email=details["email"].strip().casefold()
    if "@" not in email: raise SystemExit("Invalid operator email")
    url=os.getenv("DATABASE_URL","")
    if url.startswith("postgres://"): url="postgresql+psycopg://"+url[11:]
    if url.startswith("postgresql://"): url="postgresql+psycopg://"+url[13:]
    if not url.startswith("postgresql+psycopg://"): raise SystemExit("Private PostgreSQL required")
    try:
        totp_key=bytes.fromhex(os.environ["PATROAI_ADMIN_TOTP_KEY"])
        if len(totp_key)!=32: raise ValueError
    except (KeyError,ValueError):
        raise SystemExit("TOTP encryption key missing or invalid") from None
    password=getpass.getpass("Password for initial manager (>=12 chars): ")
    if len(password)<12 or len(password)>1024: raise SystemExit("Invalid password length")
    confirm=getpass.getpass("Confirm password: ")
    if password!=confirm: raise SystemExit("Passwords differ")
    user_id=uuid.uuid4(); salt=os.urandom(16); pw_hash=password_digest(password,salt)
    seed=os.urandom(20)
    import base64 as b64
    print("Register this TOTP seed privately in a trusted authenticator:")
    print(b64.b32encode(seed).decode("ascii").rstrip("="))
    verified_step=matching_totp_step(seed,getpass.getpass("Confirm current TOTP code: "))
    if verified_step is None:
        raise SystemExit("MFA enrollment not confirmed; account NOT created")
    # DB role must have independently approved minimum insert permissions.
    engine=create_engine(url,pool_pre_ping=True)
    with Session(engine) as db,db.begin():
        # Distributed lock avoids a race between two bootstrap operators.
        db.execute(text("SELECT pg_advisory_xact_lock(7770001)"))
        if db.scalar(select(Staff.id).where(Staff.role=="manager").limit(1)):
            raise SystemExit("Initial manager already exists; refusing to change any role")
        if db.scalar(select(Staff.id).where(Staff.email==email).limit(1)):
            raise SystemExit("Account already exists")
        db.add(Staff(id=user_id,email=email,password_salt=salt,password_digest=pw_hash,
            totp_ciphertext=seal_totp(totp_key,user_id,seed),last_totp_step=verified_step,
            role="manager",active=True,created_at=now()))
        db.add(StaffAudit(staff_id=user_id,event="FIRST_MANAGER_PROVISIONED",
            request_id=uuid.uuid4().hex,occurred_at=now()))
    print("Account created with MFA confirmed. Use a NEW 30-second TOTP code to log in.")
    print("Securely remove the protected terminal transcript.")
    engine.dispose()

if __name__=="__main__": main()
