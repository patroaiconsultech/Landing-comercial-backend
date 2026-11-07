"""R3-A, PROPOSAL_ONLY. PostgreSQL authority; no schema auto-creation.
Public sessions cannot read or write administrator resources.
"""
from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from commercial.security import Secrets, decrypt, encrypt
from dashboard.auth import active_staff, aware, now, require_csrf, audit as staff_audit
from dashboard.config import Config, get_db, require_enabled
from dashboard.models import Staff
from .models import Diagnosis, DiagnosisAudit, DiagnosisEvent, DiagnosisRate, DiagnosisTurn
from .budget import BudgetUnavailable, budget_settings, reserve_amount_micro_usd
from .policy import MAX_TURNS, can_review, canonical, init_digest, issued_token, mac, secure_equal, turn_digest
from .schemas import CompleteRequest, Registration, ReviewRequest, TurnRequest

router=APIRouter(tags=["PatroAI Diagnosis"])
RETENTION_DAYS=30
SESSION_HOURS=3
LEASE_SECONDS=90

def fail(code:str,status:int=403):
    raise HTTPException(status_code=status,detail=code)

def enabled()->bool:
    # Two independent release checks: schema feature and legally approved notice.
    return os.getenv("PATROAI_DIAGNOSIS_ENABLED")=="true" and os.getenv(
        "PATROAI_DIAGNOSIS_PRIVACY_APPROVED")=="true"

def public_config()->Config:
    if not enabled(): fail("FEATURE_DISABLED",503)
    try:
        budget_settings()
    except BudgetUnavailable:
        fail("AI_BUDGET_UNAVAILABLE",503)
    return require_enabled()

def require_origin(request:Request,cfg:Config)->None:
    if request.headers.get("origin")!=cfg.origin:
        fail("FORBIDDEN")

def keys(cfg:Config)->Secrets:
    return Secrets(cfg.handoff_key,cfg.session_key)

def audited(db:Session,row:Diagnosis,event:str,staff_id:UUID|None=None)->None:
    db.add(DiagnosisAudit(diagnosis_id=row.id,event=event,staff_id=staff_id,happened_at=now()))

def pg(db:Session)->None:
    if db.get_bind().dialect.name!="postgresql":
        fail("POSTGRES_REQUIRED",503)

def take_quota(db:Session,cfg:Config,category:str,scope:str,limit:int,period:int,amount:int=1)->None:
    """PostgreSQL, transaction-scoped advisory locking, fail-closed across replicas."""
    pg(db)
    t=now()
    window=int(t.timestamp())//period
    key=mac(cfg.session_key,"diag-quota-v1",f"{category}:{scope}:{window}".encode())
    lock=int.from_bytes(key[:8],"big",signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"),{"key":lock})
    row=db.get(DiagnosisRate,key)
    if row is None:
        row=DiagnosisRate(key_digest=key,window_started_at=t,count=0)
        db.add(row)
        db.flush()
    if amount <= 0 or amount > limit or row.count > limit-amount:
        fail("AI_DAILY_BUDGET_EXHAUSTED" if category=="ai-budget-microusd" else "RATE_LIMITED",429)
    row.count+=amount

def client_scope(request:Request)->str:
    # Never trust attacker-supplied X-Forwarded-For or spoofable browser headers.
    # Reverse proxy IP behavior must be validated in STAGING before enabling.
    return request.client.host if request.client else "unknown"

def require_request_key(value:str|None)->UUID:
    try:
        uid=UUID(value or "")
        if uid.version!=4 or str(uid)!=value.lower(): raise ValueError
        return uid
    except (ValueError,AttributeError):
        fail("INVALID_IDEMPOTENCY_KEY",400)

def require_public_session(request:Request,cfg:Config,db:Session,diagnosis_id:UUID)->Diagnosis:
    raw=request.headers.get("authorization","")
    if not raw.startswith("Bearer "): fail("UNAUTHENTICATED",401)
    token=raw[7:]
    if len(token)!=43 or not all(c.isalnum() or c in "-_" for c in token):
        fail("UNAUTHENTICATED",401)
    stamp=mac(cfg.session_key,"diag-session-record-v1",token.encode("ascii"))
    row=db.get(Diagnosis,diagnosis_id)
    if (row is None or not secure_equal(row.session_digest,stamp)
            or now()>=aware(row.session_expires_at)
            or now()>=aware(row.retention_due_at)
            or row.status=="revoked"):
        fail("UNAUTHENTICATED",401)
    return row

def public_result(row:Diagnosis,token:str|None=None)->dict:
    out={"diagnosis_id":str(row.id),"status":row.status,
         "version":row.version,"turn_count":row.turn_count,
         "created_at":row.created_at.isoformat()}
    if token is not None: out["session_token"]=token
    return out

@router.get("/v1/public/diagnoses/status")
def feature_status():
    try:
        budget_settings()
        budget_ready=True
    except BudgetUnavailable:
        budget_ready=False
    return {"available":enabled() and budget_ready and os.getenv("CHAT_ENABLED")=="true"
            and bool(os.getenv("OPENAI_API_KEY","").strip())
            and os.getenv("PATROAI_DASHBOARD_ENABLED")=="true"}

@router.post("/v1/public/diagnoses",status_code=201)
def register(payload:Registration,request:Request,
             idempotency_key:str|None=Header(None,alias="Idempotency-Key"),
             cfg:Config=Depends(public_config),db:Session=Depends(get_db)):
    require_origin(request,cfg)
    request_id=require_request_key(idempotency_key)
    token=issued_token(cfg.session_key,request_id)
    kd=mac(cfg.session_key,"diag-register-key-v1",str(request_id).encode())
    pd=init_digest(cfg.session_key,payload)
    sd=mac(cfg.session_key,"diag-session-record-v1",token.encode())
    t=now()
    pg(db)
    try:
        # An already-committed replay does not consume additional registration quota.
        existing=db.scalar(select(Diagnosis).where(Diagnosis.init_key_digest==kd))
        if existing is not None:
            if not secure_equal(existing.init_payload_digest,pd):
                fail("IDEMPOTENCY_CONFLICT",409)
            if existing.status=="revoked": fail("CONSENT_REVOKED",410)
            if now()>=aware(existing.session_expires_at): fail("SESSION_EXPIRED",410)
            data=public_result(existing,token)
            db.rollback()  # read-only transaction; explicitly close it
            return data
        # New registration quota, PostgreSQL enforced across replicas.
        take_quota(db,cfg,"register-ip",client_scope(request),5,3600)
        take_quota(db,cfg,"register-global","all",300,86400)
        claimed=db.execute(insert(Diagnosis).values(
            id=uuid4(),init_key_digest=kd,init_payload_digest=pd,session_digest=sd,
            session_expires_at=t+timedelta(hours=SESSION_HOURS),
            contact_ciphertext=None,consent_ai=True,consent_contact=True,
            consent_notice_version=payload.consent.notice_version,consent_accepted_at=t,
            status="registered",version=1,turn_count=0,created_at=t,
            last_activity_at=t,retention_due_at=t+timedelta(days=RETENTION_DAYS)
        ).on_conflict_do_nothing(index_elements=[Diagnosis.init_key_digest])
          .returning(Diagnosis.id)).scalar_one_or_none()
        row=db.get(Diagnosis,claimed) if claimed else db.scalar(
            select(Diagnosis).where(Diagnosis.init_key_digest==kd))
        if row is None: fail("PERSISTENCE_UNAVAILABLE",503)
        if not secure_equal(row.init_payload_digest,pd):
            fail("IDEMPOTENCY_CONFLICT",409)
        if row.status=="revoked": fail("CONSENT_REVOKED",410)
        if not claimed and now()>=aware(row.session_expires_at):
            fail("SESSION_EXPIRED",410)
        if claimed:
            # Contact encrypted AFTER id assigned, but inside same uncommitted transaction.
            info={"name":payload.name,"phone":payload.phone,"company":payload.company}
            row.contact_ciphertext=encrypt(keys(cfg),row.id,"diag-r3-contact",canonical(info))
            audited(db,row,"DIAGNOSIS_REGISTERED")
        db.commit()
        return public_result(row,token)
    except Exception:
        db.rollback()
        raise

def completed_history(db:Session,cfg:Config,diagnosis_id:UUID):
    """Only DB-stored, committed messages; never accept browser history."""
    from app import ChatHistory
    history=[]
    turns=db.scalars(select(DiagnosisTurn).where(
        DiagnosisTurn.diagnosis_id==diagnosis_id,DiagnosisTurn.status=="complete"
    ).order_by(DiagnosisTurn.finished_at,DiagnosisTurn.id)).all()
    for item in turns[-3:]:
        user=decrypt(keys(cfg),item.id,"diag-r3-user",item.user_ciphertext).decode()
        ai=decrypt(keys(cfg),item.id,"diag-r3-ai",item.assistant_ciphertext).decode()
        history.extend((ChatHistory(role="user",content=user[:1200]),
                        ChatHistory(role="assistant",content=ai[:1200])))
    return history

async def model_reply(message:str,history,language:str)->str:
    from app import DIAGNOSIS_PROMPT, call_provider, enabled as provider_enabled
    if not provider_enabled(): fail("AI_UNAVAILABLE",503)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(call_provider,message,history,language,
                              instructions=DIAGNOSIS_PROMPT.format(language=language)),
            timeout=20)
    except (RuntimeError,asyncio.TimeoutError):
        fail("AI_UNAVAILABLE",502)

@router.post("/v1/public/diagnoses/{diagnosis_id}/turns")
async def add_turn(diagnosis_id:UUID,payload:TurnRequest,request:Request,
                   cfg:Config=Depends(public_config),db:Session=Depends(get_db)):
    require_origin(request,cfg)
    pg(db)
    dh=turn_digest(cfg.session_key,payload)
    with db.begin():
        row=require_public_session(request,cfg,db,diagnosis_id)
        row=db.scalar(select(Diagnosis).where(Diagnosis.id==row.id).with_for_update())
        old=db.get(DiagnosisTurn,payload.turn_id)
        if old:
            if old.diagnosis_id!=row.id or not secure_equal(old.request_digest,dh):
                fail("IDEMPOTENCY_CONFLICT",409)
            if old.status=="complete":
                reply=decrypt(keys(cfg),old.id,"diag-r3-ai",old.assistant_ciphertext).decode()
                return {**public_result(row),"turn_id":str(old.id),"reply":reply,"replayed":True}
            if old.status=="pending" and aware(old.lease_until)>now():
                fail("IN_FLIGHT",409)
        if row.status not in ("registered","in_progress"):
            fail("INVALID_STATUS",409)
        if row.version!=payload.expected_version: fail("VERSION_CONFLICT",409)
        if row.turn_count>=MAX_TURNS: fail("TURN_LIMIT",409)
        ongoing=db.scalar(select(DiagnosisTurn.id).where(
            DiagnosisTurn.diagnosis_id==row.id,DiagnosisTurn.status=="pending",
            DiagnosisTurn.lease_until>now()).limit(1))
        if ongoing: fail("IN_FLIGHT",409)
        take_quota(db,cfg,"turn-session",str(diagnosis_id),5,60)
        take_quota(db,cfg,"ai-global","all",100,86400)
        hist=completed_history(db,cfg,row.id)
        # Conservative atomic liability reservation, in the same PostgreSQL
        # transaction as the pending turn. Never refund ambiguous provider costs.
        try:
            model,budget_limit=budget_settings()
            liability=reserve_amount_micro_usd(payload.message,hist,payload.language,model)
        except BudgetUnavailable:
            fail("AI_BUDGET_UNAVAILABLE",503)
        take_quota(db,cfg,"ai-budget-microusd","all",budget_limit,86400,liability)
        if old:
            old.status="pending"
            old.lease_until=now()+timedelta(seconds=LEASE_SECONDS)
        else:
            db.add(DiagnosisTurn(id=payload.turn_id,diagnosis_id=row.id,request_digest=dh,
                user_ciphertext=encrypt(keys(cfg),payload.turn_id,"diag-r3-user",payload.message.encode()),
                assistant_ciphertext=None,language=payload.language,status="pending",
                expected_version=row.version,created_at=now(),
                lease_until=now()+timedelta(seconds=LEASE_SECONDS)))
    # DB transaction committed. Provider call is outside DB locks/transaction.
    try:
        answer=await model_reply(payload.message,hist,payload.language)
    except Exception:
        with db.begin():
            item=db.get(DiagnosisTurn,payload.turn_id)
            if item and item.status=="pending": item.status="failed"
        raise
    with db.begin():
        row=db.scalar(select(Diagnosis).where(Diagnosis.id==diagnosis_id).with_for_update())
        item=db.get(DiagnosisTurn,payload.turn_id)
        if (not row or not item or item.status!="pending"
            or row.status not in ("registered","in_progress")
            or row.version!=payload.expected_version):
            fail("VERSION_CONFLICT",409)
        item.assistant_ciphertext=encrypt(keys(cfg),item.id,"diag-r3-ai",answer.encode())
        item.status="complete"
        item.finished_at=now()
        row.turn_count+=1
        row.version+=1
        row.status="in_progress"
        row.last_activity_at=now()
        if row.turn_count==1:
            db.add(DiagnosisEvent(diagnosis_id=row.id,kind="diagnosis.started",created_at=now()))
        audited(db,row,"DIAGNOSIS_TURN_COMMITTED")
    # Return only after COMMIT. Outbox event and turn commit atomically.
    return {**public_result(row),"turn_id":str(item.id),"reply":answer,"replayed":False}

@router.get("/v1/public/diagnoses/{diagnosis_id}/progress")
def progress(diagnosis_id:UUID,request:Request,
             cfg:Config=Depends(public_config),db:Session=Depends(get_db)):
    pg(db)
    row=require_public_session(request,cfg,db,diagnosis_id)
    messages=[]
    turns=db.scalars(select(DiagnosisTurn).where(
        DiagnosisTurn.diagnosis_id==row.id,DiagnosisTurn.status=="complete"
    ).order_by(DiagnosisTurn.finished_at,DiagnosisTurn.id)).all()
    try:
        for item in turns:
            messages.append({"role":"user","content":decrypt(
                keys(cfg),item.id,"diag-r3-user",item.user_ciphertext).decode()})
            messages.append({"role":"assistant","content":decrypt(
                keys(cfg),item.id,"diag-r3-ai",item.assistant_ciphertext).decode()})
    except Exception:
        fail("DATA_UNAVAILABLE",503)
    return {**public_result(row),"messages":messages}

@router.post("/v1/public/diagnoses/{diagnosis_id}/complete")
def complete(diagnosis_id:UUID,payload:CompleteRequest,request:Request,
             cfg:Config=Depends(public_config),db:Session=Depends(get_db)):
    require_origin(request,cfg)
    pg(db)
    with db.begin():
        row=require_public_session(request,cfg,db,diagnosis_id)
        row=db.scalar(select(Diagnosis).where(Diagnosis.id==row.id).with_for_update())
        if row.status=="completed":
            return public_result(row)
        if row.status!="in_progress": fail("INVALID_STATUS",409)
        if row.version!=payload.expected_version: fail("VERSION_CONFLICT",409)
        if db.scalar(select(DiagnosisTurn.id).where(DiagnosisTurn.diagnosis_id==row.id,
                     DiagnosisTurn.status=="pending",DiagnosisTurn.lease_until>now()).limit(1)):
            fail("IN_FLIGHT",409)
        row.status="completed"
        row.version+=1
        row.last_activity_at=now()
        db.add(DiagnosisEvent(diagnosis_id=row.id,kind="diagnosis.completed",created_at=now()))
        audited(db,row,"DIAGNOSIS_COMPLETED")
    return public_result(row)

@router.delete("/v1/public/diagnoses/{diagnosis_id}")
def revoke(diagnosis_id:UUID,request:Request,cfg:Config=Depends(public_config),
           db:Session=Depends(get_db)):
    require_origin(request,cfg)
    pg(db)
    with db.begin():
        row=require_public_session(request,cfg,db,diagnosis_id)
        row=db.scalar(select(Diagnosis).where(Diagnosis.id==row.id).with_for_update())
        # Irreversibly remove personal content; maintain minimal event/audit tombstone.
        for turn in db.scalars(select(DiagnosisTurn).where(
                DiagnosisTurn.diagnosis_id==row.id).with_for_update()):
            turn.user_ciphertext=b""
            turn.assistant_ciphertext=None
            turn.status="failed" if turn.status=="pending" else turn.status
        db.query(DiagnosisEvent).filter(DiagnosisEvent.diagnosis_id==row.id).delete(
            synchronize_session=False)
        row.contact_ciphertext=None
        row.status="revoked"
        row.version+=1
        row.session_expires_at=now()
        audited(db,row,"DIAGNOSIS_CONSENT_REVOKED")
    return {"revoked":True}

def admin_row(row:Diagnosis)->dict:
    return {**public_result(row),"consent_version":row.consent_notice_version,
            "consent_accepted_at":row.consent_accepted_at.isoformat(),
            "last_activity_at":row.last_activity_at.isoformat()}

@router.get("/v1/admin/diagnoses")
def list_admin(request:Request,limit:int=Query(25,ge=1,le=50),
               offset:int=Query(0,ge=0,le=10000),
               staff:Staff=Depends(active_staff),cfg:Config=Depends(public_config),
               db:Session=Depends(get_db)):
    pg(db)
    with db.begin():
        rows=db.scalars(select(Diagnosis).where(
            Diagnosis.status!="revoked",Diagnosis.retention_due_at>now()
        ).order_by(Diagnosis.created_at.desc(),Diagnosis.id.desc()).offset(offset).limit(limit+1)).all()
        # Record access even for an empty list, without leaking PII.
        staff_audit(db,"DIAGNOSIS_LIST",request,staff.id)
    return {"items":[admin_row(x) for x in rows[:limit]],
            "next_offset":offset+limit if len(rows)>limit else None}

@router.get("/v1/admin/diagnoses/{diagnosis_id}")
def get_admin(diagnosis_id:UUID,request:Request,response:Response,
              staff:Staff=Depends(active_staff),cfg:Config=Depends(public_config),
              db:Session=Depends(get_db)):
    pg(db)
    with db.begin():
        row=db.get(Diagnosis,diagnosis_id)
        if not row or row.status=="revoked" or aware(row.retention_due_at)<=now():
            fail("NOT_FOUND",404)
        try:
            contact=json.loads(decrypt(keys(cfg),row.id,"diag-r3-contact",row.contact_ciphertext))
            messages=[]
            turns=db.scalars(select(DiagnosisTurn).where(
                DiagnosisTurn.diagnosis_id==row.id,
                DiagnosisTurn.status=="complete").order_by(
                DiagnosisTurn.finished_at,DiagnosisTurn.id)).all()
            for turn in turns:
                messages.append({"role":"user","content":decrypt(
                    keys(cfg),turn.id,"diag-r3-user",turn.user_ciphertext).decode()})
                messages.append({"role":"assistant","content":decrypt(
                    keys(cfg),turn.id,"diag-r3-ai",turn.assistant_ciphertext).decode()})
        except Exception:
            fail("DATA_UNAVAILABLE",503)
        audited(db,row,"DIAGNOSIS_DETAIL_READ",staff.id)
    response.headers["ETag"]=f'"v{row.version}"'
    return {**admin_row(row),"contact":contact,"messages":messages}

@router.patch("/v1/admin/diagnoses/{diagnosis_id}/status")
def change_admin_status(diagnosis_id:UUID,payload:ReviewRequest,request:Request,response:Response,
                        if_match:str|None=Header(None,alias="If-Match"),
                        staff:Staff=Depends(active_staff),cfg:Config=Depends(public_config),
                        db:Session=Depends(get_db)):
    if staff.role not in ("manager","super_admin"): fail("FORBIDDEN")
    require_csrf(request,cfg)
    if not if_match: fail("PRECONDITION_REQUIRED",428)
    if not (if_match.startswith('"v') and if_match.endswith('"')
            and if_match[2:-1].isdigit()): fail("BAD_ETAG",400)
    expected=int(if_match[2:-1])
    pg(db)
    with db.begin():
        row=db.scalar(select(Diagnosis).where(Diagnosis.id==diagnosis_id).with_for_update())
        if not row or row.status=="revoked" or aware(row.retention_due_at)<=now():
            fail("NOT_FOUND",404)
        if row.version!=expected: fail("VERSION_CONFLICT",409)
        if not can_review(row.status,payload.status): fail("INVALID_STATUS_TRANSITION",409)
        row.status=payload.status
        row.version+=1
        row.last_activity_at=now()
        audited(db,row,"DIAGNOSIS_REVIEW_CHANGED",staff.id)
    response.headers["ETag"]=f'"v{row.version}"'
    return admin_row(row)
