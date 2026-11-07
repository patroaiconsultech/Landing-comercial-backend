"""R3-A proposal-only focused contract and security tests; no Railway writes."""
from __future__ import annotations
import os
from uuid import uuid4
import pytest
from pydantic import ValidationError
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient

from diagnosis.schemas import Registration, TurnRequest
from diagnosis.policy import can_review, init_digest, issued_token, turn_digest, MAX_TURNS
from diagnosis.models import Diagnosis, DiagnosisTurn, DiagnosisEvent, DiagnosisAudit, DiagnosisRate
from commercial.security import Secrets, encrypt, decrypt

KEY=b"x"*32
def consent():
    return {"ai_processing":True,"commercial_contact":True,
            "notice_version":"patroai-diagnosis-v1"}
def reg(**extra):
    return Registration.model_validate(
        {"name":"Interessado Teste","phone":"+5551999999999","consent":consent(), **extra})

def test_registration_accepts_valid_explicit_separate_consent():
    obj=reg(company="Negócio teste")
    assert obj.name=="Interessado Teste"
    assert obj.consent.ai_processing and obj.consent.commercial_contact

@pytest.mark.parametrize("field",["ai_processing","commercial_contact"])
def test_each_consent_is_required_and_never_coerced(field):
    payload={"name":"Pessoa teste","phone":"+5551999999999","consent":consent()}
    for value in [False, "true", None]:
        payload["consent"][field]=value
        with pytest.raises(ValidationError):
            Registration.model_validate(payload)
        payload["consent"][field]=True

@pytest.mark.parametrize("phone",["5511999999999","+55abc","+012345678","010","",
                                    "+55 11 99999 9999"])
def test_bad_phone_fail_closed(phone):
    with pytest.raises(ValidationError):reg(phone=phone)

def test_exact_notice_version_and_extra_fields():
    obj={"name":"Test test","phone":"+5551999999999","consent":consent()}
    obj["consent"]["notice_version"]="not-real"
    with pytest.raises(ValidationError):Registration.model_validate(obj)
    obj["consent"]=consent()
    obj["super_admin"]=True
    with pytest.raises(ValidationError):Registration.model_validate(obj)

def test_init_idempotency_and_token_secrecy():
    key1=uuid4();key2=uuid4()
    assert issued_token(KEY,key1)==issued_token(KEY,key1)
    assert issued_token(KEY,key1)!=issued_token(KEY,key2)
    assert issued_token(KEY,key1)!=issued_token(b"z"*32,key1)
    assert len(issued_token(KEY,key1))==43
    assert init_digest(KEY,reg())==init_digest(KEY,reg())
    assert init_digest(KEY,reg())!=init_digest(KEY,reg(name="Outro interessado"))

def test_turn_ids_payload_binding():
    id1=uuid4()
    item=TurnRequest(turn_id=id1,expected_version=1,message="Quero automatizar atendimento")
    same=TurnRequest(turn_id=id1,expected_version=1,message=item.message)
    changed=TurnRequest(turn_id=id1,expected_version=1,message="Outro texto")
    assert turn_digest(KEY,item)==turn_digest(KEY,same)
    assert turn_digest(KEY,item)!=turn_digest(KEY,changed)
    with pytest.raises(ValidationError):
        TurnRequest(turn_id=id1,expected_version=1,message=" ")
    with pytest.raises(ValidationError):
        TurnRequest(turn_id=id1,expected_version=0,message="oi")
    assert MAX_TURNS==7

def test_review_transitions_are_explicit_and_fail_closed():
    assert can_review("completed","in_review")
    assert can_review("in_review","qualified")
    assert can_review("in_review","disqualified")
    assert can_review("qualified","contacted")
    for a,b in [("registered","qualified"),("in_progress","contacted"),
                ("completed","contacted"),("disqualified","qualified"),
                ("revoked","in_review"),("anything","super_admin")]:
        assert not can_review(a,b)

def test_ciphertext_is_bound_to_record_and_purpose():
    keys=Secrets(b"a"*32,b"b"*32)
    id1=uuid4();id2=uuid4()
    blob=encrypt(keys,id1,"diag-r3-contact",b'{"name":"exemplo"}')
    assert b"exemplo" not in blob
    assert decrypt(keys,id1,"diag-r3-contact",blob)==b'{"name":"exemplo"}'
    with pytest.raises(InvalidTag):decrypt(keys,id2,"diag-r3-contact",blob)
    with pytest.raises(InvalidTag):decrypt(keys,id1,"diag-r3-user",blob)

def test_schema_has_no_legacy_overrides():
    tables={c.__tablename__ for c in (
        Diagnosis,DiagnosisTurn,DiagnosisEvent,DiagnosisAudit,DiagnosisRate)}
    assert tables=={
        "patroai_diagnosis","patroai_diagnosis_turn","patroai_diagnosis_event",
        "patroai_diagnosis_audit","patroai_diagnosis_rate"}

def test_feature_default_off(monkeypatch):
    from diagnosis.router import enabled
    monkeypatch.delenv("PATROAI_DIAGNOSIS_ENABLED",raising=False)
    monkeypatch.delenv("PATROAI_DIAGNOSIS_PRIVACY_APPROVED",raising=False)
    assert not enabled()
    monkeypatch.setenv("PATROAI_DIAGNOSIS_ENABLED","true")
    assert not enabled()
    monkeypatch.setenv("PATROAI_DIAGNOSIS_PRIVACY_APPROVED","true")
    assert enabled()

def test_route_disabled_does_not_touch_database(monkeypatch):
    from app import app
    monkeypatch.delenv("PATROAI_DIAGNOSIS_ENABLED",raising=False)
    monkeypatch.delenv("PATROAI_DIAGNOSIS_PRIVACY_APPROVED",raising=False)
    with TestClient(app) as c:
        r=c.get("/v1/public/diagnoses/status")
        assert r.status_code==200 and r.json()=={"available":False}
        r=c.post("/v1/public/diagnoses",json={
            "name":"Test Person","phone":"+5551999999999","consent":consent()},
            headers={"Origin":"https://landing-comercial-frontend-staging.up.railway.app",
                     "Idempotency-Key":str(uuid4())})
        assert r.status_code==503
        assert r.json()=={"detail":"FEATURE_DISABLED"}


def test_proposed_alembic_chain_is_additive_and_downgrade_forbidden():
    import importlib.util
    from pathlib import Path
    module=Path(__file__).resolve().parents[1]/"alembic/versions/0003_patroai_diagnosis_r3a.py"
    spec=importlib.util.spec_from_file_location("r3a_migration",module)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision=="0002_patroai_super_admin_r2"
    class Recorder:
        def __init__(self):self.tables=[];self.indices=[]
        def create_table(self,name,*args,**kwargs):self.tables.append(name)
        def create_index(self,name,*args,**kwargs):self.indices.append(name)
    rec=Recorder()
    mod.op=rec
    mod.upgrade()  # record DDL calls only, never touch DB
    assert set(rec.tables)=={
        "patroai_diagnosis","patroai_diagnosis_turn","patroai_diagnosis_event",
        "patroai_diagnosis_audit","patroai_diagnosis_rate"}
    assert "patroai_admin_staff" not in rec.tables
    with pytest.raises(RuntimeError,match="destructive downgrade"):
        mod.downgrade()
