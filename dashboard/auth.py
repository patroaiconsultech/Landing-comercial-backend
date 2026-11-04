"""Native password + mandatory TOTP, opaque revocable sessions and CSRF.
Account provisioning/recovery MUST be a separately audited offline procedure.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import Cookie, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import Config, get_db, require_enabled
from .models import AuthWindow, LoginChallenge, Staff, StaffSession, StaffAudit

COOKIE="__Host-patroai_admin_session"
def now(): return datetime.now(timezone.utc)
def aware(dt):
    if dt.tzinfo is None: return dt.replace(tzinfo=timezone.utc)
    return dt
def stamp(key: bytes, prefix: str, token: str) -> bytes:
    return hmac.new(key, (prefix+token).encode(), hashlib.sha256).digest()
def password_digest(password: str, salt: bytes) -> bytes:
    if len(password)<12 or len(password)>1024:
        raise ValueError("Bad password length")
    return hashlib.scrypt(password.encode("utf-8"),salt=salt,n=2**14,r=8,p=1,dklen=32)
# Use a fixed dummy digest for unknown users to avoid a quick account enumeration path.
_DUMMY_SALT = b"\x9f"*16
_DUMMY_HASH = hashlib.scrypt(b"not-a-real-operator-password",salt=_DUMMY_SALT,n=2**14,r=8,p=1,dklen=32)

def seal_totp(key:bytes, staff_id, secret:bytes) -> bytes:
    nonce=os.urandom(12)
    return nonce+AESGCM(key).encrypt(nonce,secret,("patroai-totp:"+str(staff_id)).encode())
def unseal_totp(key:bytes, staff_id, data:bytes) -> bytes:
    return AESGCM(key).decrypt(data[:12],data[12:],("patroai-totp:"+str(staff_id)).encode())

def totp_for_step(secret:bytes, step:int)->str:
    digest=hmac.new(secret,struct.pack(">Q",step),hashlib.sha1).digest()
    offset=digest[-1]&15
    val=(int.from_bytes(digest[offset:offset+4],"big")&0x7fffffff)%1000000
    return f"{val:06d}"
def matching_totp_step(secret:bytes, code:str, timestamp:int|None=None)->int|None:
    if len(code)!=6 or not code.isascii() or not code.isdigit(): return None
    current=(int(time.time()) if timestamp is None else timestamp)//30
    for step in (current-1,current,current+1):
        if hmac.compare_digest(totp_for_step(secret,step),code):
            return step
    return None

def audit(db:Session,event:str,request:Request,staff_id=None,handoff_id=None):
    rid=request.headers.get("x-request-id", "")
    if len(rid)>40 or not rid.replace("-","").isalnum():
        rid=uuid4().hex
    db.add(StaffAudit(staff_id=staff_id,handoff_id=handoff_id,event=event,
                      request_id=rid or uuid4().hex,occurred_at=now()))

def check_origin(request:Request,cfg:Config):
    if request.headers.get("origin")!=cfg.origin:
        raise HTTPException(status_code=403,detail="FORBIDDEN")

def throttle_lock(db:Session,key_digest:bytes):
    # Serialize login counters across PostgreSQL workers/replicas.
    if db.get_bind().dialect.name=="postgresql":
        lock_num=int.from_bytes(key_digest[:8],"big",signed=True)
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"),{"key":lock_num})

def throttle(db:Session,key:bytes,fail:bool) -> bool:
    """5 failures per 15min, keyed for existing/unknown accounts alike."""
    throttle_lock(db,key)
    row=db.get(AuthWindow,key)
    t=now()
    if row is None:
        row=AuthWindow(key_digest=key,window_started_at=t,failures=0)
        db.add(row);db.flush()
    elif t-aware(row.window_started_at)>=timedelta(minutes=15):
        row.window_started_at=t;row.failures=0
    locked=row.failures>=5
    if fail:
        row.failures+=1
    return locked

def password_phase(db:Session,cfg:Config,request:Request,email:str,password:str) ->str:
    normalized=email.strip().casefold()
    identity_key=stamp(cfg.session_key,"login-account:",normalized)
    throttle_lock(db,identity_key)
    row=db.get(AuthWindow,identity_key)
    t=now()
    if row is None:
        row=AuthWindow(key_digest=identity_key,window_started_at=t,failures=0)
        db.add(row);db.flush()
    elif t-aware(row.window_started_at)>=timedelta(minutes=15):
        row.window_started_at=t;row.failures=0
    if row.failures>=5:
        db.commit()
        raise HTTPException(status_code=429,detail="RATE_LIMITED")
    staff=db.scalar(select(Staff).where(Staff.email==normalized))
    salt=staff.password_salt if staff is not None else _DUMMY_SALT
    expected=staff.password_digest if staff is not None else _DUMMY_HASH
    try:
        digest=password_digest(password,salt)
        correct=hmac.compare_digest(digest,expected)
    except ValueError:
        correct=False
    if not (staff and staff.active and correct):
        row.failures+=1
        audit(db,"LOGIN_FAILED",request)
        db.commit()
        raise HTTPException(status_code=401,detail="INVALID_CREDENTIALS")
    row.failures=0
    token=secrets.token_urlsafe(32)
    db.add(LoginChallenge(token_digest=stamp(cfg.session_key,"challenge:",token),
        staff_id=staff.id,created_at=t,expires_at=t+timedelta(minutes=5)))
    audit(db,"PASSWORD_VERIFIED",request,staff.id)
    db.commit()
    return token

def mfa_phase(db:Session,cfg:Config,request:Request,challenge:str,code:str) ->str:
    digest=stamp(cfg.session_key,"challenge:",challenge)
    ch=db.scalar(select(LoginChallenge).where(LoginChallenge.token_digest==digest).with_for_update())
    if not ch or now()>aware(ch.expires_at) or ch.failures>=5:
        raise HTTPException(status_code=401,detail="INVALID_CHALLENGE")
    staff=db.scalar(select(Staff).where(Staff.id==ch.staff_id).with_for_update())
    if not staff or not staff.active:
        raise HTTPException(status_code=401,detail="INVALID_CHALLENGE")
    try:
        seed=unseal_totp(cfg.totp_key,staff.id,staff.totp_ciphertext)
        step=matching_totp_step(seed,code)
    except (ValueError,KeyError):
        step=None
    if step is None or step<=staff.last_totp_step:
        ch.failures+=1
        audit(db,"MFA_FAILED",request,staff.id)
        db.commit()
        raise HTTPException(status_code=401,detail="INVALID_CHALLENGE")
    staff.last_totp_step=step
    db.delete(ch)
    token=secrets.token_urlsafe(32)
    t=now()
    db.add(StaffSession(token_digest=stamp(cfg.session_key,"session:",token),
        staff_id=staff.id,created_at=t,seen_at=t,expires_at=t+timedelta(hours=12)))
    audit(db,"LOGIN_OK",request,staff.id)
    db.commit()
    return token

def active_staff(request:Request, db:Session=Depends(get_db), cfg:Config=Depends(require_enabled)):
    token=request.cookies.get(COOKIE,"")
    if not token or len(token)>128:
        raise HTTPException(status_code=401,detail="UNAUTHENTICATED")
    sess=db.get(StaffSession,stamp(cfg.session_key,"session:",token))
    t=now()
    if not sess or sess.revoked_at or t>=aware(sess.expires_at) or t-aware(sess.seen_at)>timedelta(minutes=30):
        raise HTTPException(status_code=401,detail="UNAUTHENTICATED")
    staff=db.get(Staff,sess.staff_id)
    if not staff or not staff.active or staff.role not in ("analyst","manager"):
        raise HTTPException(status_code=401,detail="UNAUTHENTICATED")
    sess.seen_at=t
    db.commit()
    return staff

def csrf_token(cfg:Config,session_token:str)->str:
    return base64.urlsafe_b64encode(stamp(cfg.session_key,"csrf:",session_token)).decode().rstrip("=")
def require_csrf(request:Request,cfg:Config):
    check_origin(request,cfg)
    raw=request.cookies.get(COOKIE,"")
    provided=request.headers.get("x-csrf-token","")
    if not raw or not hmac.compare_digest(csrf_token(cfg,raw),provided):
        raise HTTPException(status_code=403,detail="FORBIDDEN")

def set_cookie(response:Response,token:str):
    response.set_cookie(COOKIE,token,secure=True,httponly=True,samesite="lax",path="/",max_age=43200)
def clear_cookie(response:Response):
    response.delete_cookie(COOKIE,secure=True,httponly=True,samesite="lax",path="/")
