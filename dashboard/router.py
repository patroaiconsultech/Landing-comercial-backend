"""Administrative API. All endpoints disabled unless explicitly configured.
No public bootstrap, registration, recovery or handoff activation here.
"""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hmac
import hashlib
import json
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import and_, or_, select, update, text, func
from sqlalchemy.orm import Session
from commercial.models import Handoff
from commercial.security import Secrets, decrypt
from .models import Staff, StaffSession
from .config import Config, get_db, require_enabled
from .auth import (
    aware, COOKIE, active_staff, audit, check_origin, clear_cookie, csrf_token,
    mfa_phase, password_phase, require_csrf, set_cookie, stamp, now
)

router=APIRouter(prefix="/v1/admin",tags=["PatroAI Admin"])
class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid")
class PasswordLogin(Strict):
    email:EmailStr
    password:str=Field(min_length=1,max_length=1024)
class MFAChallenge(Strict):
    challenge_id:str=Field(min_length=20,max_length=128)
    code:str=Field(min_length=6,max_length=6)
class StatusRequest(Strict):
    status:Literal["in_review","closed"]

def error(status:int,code:str):
    raise HTTPException(status_code=status,detail=code)

def ensure_manager(staff:Staff):
    if staff.role not in ("manager","super_admin"): error(403,"FORBIDDEN")

def ensure_super_admin(staff:Staff):
    if staff.role!="super_admin": error(403,"FORBIDDEN")

@router.post("/auth/password",status_code=202)
def login_password(payload:PasswordLogin,request:Request,db:Session=Depends(get_db),cfg:Config=Depends(require_enabled)):
    check_origin(request,cfg)
    token=password_phase(db,cfg,request,str(payload.email),payload.password)
    return {"challenge_id":token,"expires_in":300}

@router.post("/auth/mfa")
def login_mfa(payload:MFAChallenge,request:Request,response:Response,
              db:Session=Depends(get_db),cfg:Config=Depends(require_enabled)):
    check_origin(request,cfg)
    token=mfa_phase(db,cfg,request,payload.challenge_id,payload.code)
    set_cookie(response,token)
    return {"authenticated":True,"csrf_token":csrf_token(cfg,token)}

@router.get("/auth/session")
def current_session(request:Request,staff:Staff=Depends(active_staff),cfg:Config=Depends(require_enabled)):
    return {"authenticated":True,"role":staff.role,"staff_id":str(staff.id),"csrf_token":csrf_token(cfg,request.cookies[COOKIE])}

@router.post("/auth/logout")
def logout(request:Request,response:Response,staff:Staff=Depends(active_staff),
           db:Session=Depends(get_db),cfg:Config=Depends(require_enabled)):
    require_csrf(request,cfg)
    token=request.cookies.get(COOKIE,"")
    row=db.get(StaffSession,stamp(cfg.session_key,"session:",token))
    if row:
        row.revoked_at=now()
        audit(db,"SESSION_REVOKED",request,staff.id)
        db.commit()
    clear_cookie(response)
    return {"ok":True}

def summary(h:Handoff):
    return {"handoff_id":str(h.id),"status":h.status,"received_at":h.received_at.isoformat(),"version":h.version}

def encode_cursor(cfg:Config,h:Handoff,filter_status:str|None):
    obj={"t":aware(h.received_at).isoformat(),"i":str(h.id),"s":filter_status or ""}
    encoded=base64.urlsafe_b64encode(json.dumps(obj,separators=(",",":")).encode()).decode().rstrip("=")
    signature=hmac.new(cfg.session_key,("page:"+encoded).encode(),"sha256").hexdigest()
    return encoded+"."+signature

def decode_cursor(cfg:Config,cursor:str,filter_status:str|None):
    try:
        if len(cursor)>512: raise ValueError
        encoded,mac=cursor.split(".",1)
        expected=hmac.new(cfg.session_key,("page:"+encoded).encode(),"sha256").hexdigest()
        if not hmac.compare_digest(mac,expected): raise ValueError
        obj=json.loads(base64.urlsafe_b64decode(encoded+"="*((-len(encoded))%4)))
        if obj["s"]!=(filter_status or ""): raise ValueError
        t=datetime.fromisoformat(obj["t"])
        if t.tzinfo is None: raise ValueError
        return t,UUID(obj["i"])
    except Exception:
        error(400,"BAD_CURSOR")

@router.get("/handoffs")
def list_handoffs(request:Request,limit:int=Query(25,ge=1,le=50),
                  status:Literal["new","in_review","closed"]|None=None,
                  cursor:str|None=None,staff:Staff=Depends(active_staff),
                  db:Session=Depends(get_db),cfg:Config=Depends(require_enabled)):
    q=select(Handoff).where(Handoff.retention_due_at>now(),
             Handoff.transcript_provenance=="user_submitted")
    if status: q=q.where(Handoff.status==status)
    if cursor:
        t,i=decode_cursor(cfg,cursor,status)
        q=q.where(or_(Handoff.received_at<t,
                    and_(Handoff.received_at==t,Handoff.id<i)))
    rows=db.scalars(q.order_by(Handoff.received_at.desc(),Handoff.id.desc()).limit(limit+1)).all()
    items=rows[:limit]
    next_cur=encode_cursor(cfg,items[-1],status) if len(rows)>limit else None
    audit(db,"HANDOFF_LIST",request,staff.id)
    db.commit()
    return {"items":[summary(h) for h in items],"next_cursor":next_cur}

@router.get("/handoffs/{handoff_id}")
def detail(handoff_id:UUID,request:Request,response:Response,staff:Staff=Depends(active_staff),
           db:Session=Depends(get_db),cfg:Config=Depends(require_enabled)):
    h=db.get(Handoff,handoff_id)
    if not h or aware(h.retention_due_at)<=now() or h.transcript_provenance!="user_submitted":
        error(404,"NOT_FOUND")
    try:
        # The same key that encrypts public handoffs is needed, never a browser secret.
        keys=Secrets(cfg.handoff_key,cfg.session_key) # independent keys; fingerprint unused here
        transcript=json.loads(decrypt(keys,h.id,"transcript",h.transcript_ciphertext))
        contact=json.loads(decrypt(keys,h.id,"contact",h.contact_ciphertext)) if h.contact_ciphertext else None
    except Exception:
        # Do not leak ciphertext or its content, and do not claim successful access.
        db.rollback()
        error(503,"DATA_UNAVAILABLE")
    audit(db,"TRANSCRIPT_READ",request,staff.id,h.id)
    db.commit()
    response.headers["ETag"]=f'"v{h.version}"'
    return {**summary(h),"selected_messages":transcript,"contact_preference":h.contact_preference,
            "contact":contact,"consent_notice_version":h.consent_version,
            "transcript_provenance":"user_submitted"}

@router.patch("/handoffs/{handoff_id}/status")
def change_status(handoff_id:UUID,payload:StatusRequest,request:Request,
                  response:Response,if_match:str|None=Header(None,alias="If-Match"),
                  staff:Staff=Depends(active_staff),db:Session=Depends(get_db),
                  cfg:Config=Depends(require_enabled)):
    ensure_manager(staff)
    require_csrf(request,cfg)
    if not if_match: error(428,"PRECONDITION_REQUIRED")
    if not if_match.startswith('"v') or not if_match.endswith('"') or not if_match[2:-1].isdigit():
        error(400,"BAD_ETAG")
    version=int(if_match[2:-1])
    h=db.get(Handoff,handoff_id)
    if not h or aware(h.retention_due_at)<=now() or h.transcript_provenance!="user_submitted":
        error(404,"NOT_FOUND")
    transitions={"new":{"in_review"},"in_review":{"closed"},"closed":set()}
    if payload.status not in transitions.get(h.status,set()):
        error(409,"INVALID_STATUS_TRANSITION")
    result=db.execute(update(Handoff).where(Handoff.id==h.id,Handoff.version==version,
                          Handoff.status==h.status).values(status=payload.status,version=Handoff.version+1))
    if result.rowcount!=1:
        db.rollback()
        error(409,"VERSION_CONFLICT")
    audit(db,"HANDOFF_STATUS_CHANGED",request,staff.id,h.id)
    db.commit()
    db.refresh(h)
    response.headers["ETag"]=f'"v{h.version}"'
    return summary(h)


# Role management is server-authoritative. Account creation and recovery are
# intentionally offline, independently signed operations, NOT browser requests.
@router.get("/staff")
def list_staff(request:Request,staff:Staff=Depends(active_staff),
               db:Session=Depends(get_db),cfg:Config=Depends(require_enabled)):
    ensure_super_admin(staff)
    rows=db.scalars(select(Staff).order_by(Staff.email)).all()
    audit(db,"STAFF_LIST",request,staff.id)
    db.commit()
    return {"items":[{"staff_id":str(row.id),"email":row.email,
                      "role":row.role,"active":row.active} for row in rows]}

@router.post("/staff/{staff_id}/deactivate")
def deactivate_staff(staff_id:UUID,request:Request,staff:Staff=Depends(active_staff),
                     db:Session=Depends(get_db),cfg:Config=Depends(require_enabled)):
    ensure_super_admin(staff)
    require_csrf(request,cfg)
    if staff.id==staff_id:
        error(409,"CANNOT_DISABLE_SELF")
    # Serialize deactivations: two concurrent requests cannot remove the
    # last active super_admin in separate replicas.
    if db.get_bind().dialect.name=="postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(7770002)"))
    target=db.scalar(select(Staff).where(Staff.id==staff_id).with_for_update())
    if target is None or not target.active:
        error(404,"NOT_FOUND")
    if target.role=="super_admin":
        count=db.scalar(select(func.count()).select_from(Staff).where(
            Staff.role=="super_admin",Staff.active.is_(True)))
        if count<=1:
            error(409,"LAST_SUPER_ADMIN")
    target.active=False
    db.execute(update(StaffSession).where(
        StaffSession.staff_id==target.id,
        StaffSession.revoked_at.is_(None)
    ).values(revoked_at=now()))
    audit(db,"STAFF_DEACTIVATED",request,staff.id)
    db.commit()
    return {"ok":True}
