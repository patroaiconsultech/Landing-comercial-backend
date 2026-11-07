"""PostgreSQL 18 integration candidates. NEVER point at STAGING/production.

Run only in disposable database named patroai_r3a_lab, after Alembic 0003.
These tests are CI-only evidence until actually executed by an authorized runner.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration_postgres

def _url():
    from sqlalchemy.engine import make_url
    raw = os.environ.get("PATROAI_R3A_LAB_DATABASE_URL", "")
    if not raw:
        pytest.skip("Isolated lab URL not provided")
    u=make_url(raw)
    assert u.drivername == "postgresql+psycopg"
    assert u.database == "patroai_r3a_lab", "Only an ephemeral, isolated lab is allowed"
    assert u.host in ("127.0.0.1","localhost"), "Never connect remote DB in integration tests"
    assert os.environ.get("PATROAI_R3A_LAB_ACK") == "EPHEMERAL_PG18_ONLY"
    return raw

@pytest.fixture
def db_url():
    return _url()

@pytest.fixture
def client(monkeypatch, db_url):
    from dashboard.config import session_factory
    from fastapi.testclient import TestClient
    from app import app
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("PATROAI_ADMIN_ORIGIN", "https://testserver")
    monkeypatch.setenv("PATROAI_DASHBOARD_ENABLED", "true")
    monkeypatch.setenv("PATROAI_ADMIN_SESSION_KEY", "11"*32)
    monkeypatch.setenv("PATROAI_ADMIN_TOTP_KEY", "22"*32)
    monkeypatch.setenv("PATROAI_HANDOFF_ENCRYPTION_KEY", "33"*32)
    monkeypatch.setenv("PATROAI_DIAGNOSIS_ENABLED", "true")
    monkeypatch.setenv("PATROAI_DIAGNOSIS_PRIVACY_APPROVED", "true")
    monkeypatch.setenv("PATROAI_DIAGNOSIS_DAILY_BUDGET_MICRO_USD", "1000000")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "ci-fake-never-call-network")
    monkeypatch.setenv("CHAT_ENABLED", "true")
    session_factory.cache_clear()
    with TestClient(app) as c:
        yield c
    session_factory.cache_clear()

def test_migration_chain_and_r2_untouched(db_url):
    from sqlalchemy import create_engine, text
    e=create_engine(db_url)
    try:
        with e.connect() as c:
            assert c.execute(text("SELECT current_database()")).scalar_one()=="patroai_r3a_lab"
            assert int(c.execute(text("SHOW server_version_num")).scalar_one())//10000==18
            assert c.execute(text("SELECT version_num FROM alembic_version")).scalar_one()=="0003_patroai_diagnosis_r3a"
            for name in ("patroai_diagnosis","patroai_diagnosis_turn","patroai_diagnosis_event",
                         "patroai_diagnosis_audit","patroai_diagnosis_rate"):
                assert c.execute(text("SELECT to_regclass(:name)"),{"name":name}).scalar_one() is not None
            assert c.execute(text("SELECT count(*) FROM patroai_admin_staff")).scalar_one()==0
    finally:
        e.dispose()

def test_register_retry_first_turn_revocation_and_unauthorized_dashboard(client, monkeypatch, db_url):
    from sqlalchemy import create_engine, text
    import diagnosis.router as r
    async def fake_reply(*args, **kwargs):
        return "Resposta sintética, sem provedor externo"
    monkeypatch.setattr(r, "model_reply", fake_reply)
    key=str(uuid4())
    payload={
        "name":"Cadastro sintético", "phone":"+5551999999999",
        "consent":{"ai_processing":True,"commercial_contact":True,
                   "notice_version":"patroai-diagnosis-v1"}
    }
    headers={"Origin":"https://testserver","Idempotency-Key":key}
    result=client.post("/v1/public/diagnoses",json=payload,headers=headers)
    assert result.status_code==201,result.text
    lead=result.json(); token=lead["session_token"]; did=lead["diagnosis_id"]
    again=client.post("/v1/public/diagnoses",json=payload,headers=headers)
    assert again.status_code==201 and again.json()["diagnosis_id"]==did
    changed={**payload,"name":"Outro cadastro"}
    conflict=client.post("/v1/public/diagnoses",json=changed,headers=headers)
    assert conflict.status_code==409

    unauth=client.get("/v1/admin/diagnoses")
    assert unauth.status_code in (401,403)
    turn_id=str(uuid4())
    auth={"Origin":"https://testserver","Authorization":"Bearer "+token}
    t=client.post(f"/v1/public/diagnoses/{did}/turns",
        json={"turn_id":turn_id,"expected_version":1,"message":"Qual o primeiro passo?","language":"pt"},
        headers=auth)
    assert t.status_code==200,t.text
    assert t.json()["version"]==2 and t.json()["turn_count"]==1
    dup=client.post(f"/v1/public/diagnoses/{did}/turns",
        json={"turn_id":turn_id,"expected_version":1,"message":"Qual o primeiro passo?","language":"pt"},
        headers=auth)
    assert dup.status_code==200,dup.text
    assert dup.json()["replayed"] is True
    end=client.post(f"/v1/public/diagnoses/{did}/complete",
        json={"expected_version":2},headers=auth)
    assert end.status_code==200 and end.json()["status"]=="completed"
    removed=client.delete(f"/v1/public/diagnoses/{did}",headers=auth)
    assert removed.status_code==200 and removed.json()=={"revoked":True}
    e=create_engine(db_url)
    try:
        with e.connect() as c:
            assert c.execute(text("SELECT contact_ciphertext IS NULL FROM patroai_diagnosis WHERE id=:id"),{"id":did}).scalar_one() is True
            assert c.execute(text("SELECT count(*) FROM patroai_diagnosis_rate")).scalar_one() > 0
            assert c.execute(text("SELECT count(*) FROM patroai_admin_staff")).scalar_one()==0
    finally:
        e.dispose()
