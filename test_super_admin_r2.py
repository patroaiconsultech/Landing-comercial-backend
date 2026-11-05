"""R2 source-level security regression tests. PostgreSQL migration needs separate rehearsal."""
from __future__ import annotations
import base64
import json
import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parent))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from commercial.models import Base
from dashboard.router import router, ensure_manager
from dashboard.models import Staff, StaffSession, StaffAudit
from dashboard.auth import COOKIE, csrf_token, stamp, now
from dashboard.config import Config, get_db, require_enabled
from tools.provision_super_admin import canonical, verify_approvals

class ApprovalContract(unittest.TestCase):
    def setUp(self):
        self.priv = [Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()]
        self.keys = {
            "approver-a": self.priv[0].public_key().public_bytes(
                encoding=serialization.Encoding.Raw,format=serialization.PublicFormat.Raw),
            "approver-b": self.priv[1].public_key().public_bytes(
                encoding=serialization.Encoding.Raw,format=serialization.PublicFormat.Raw),
        }
        self.payload={
            "email":"operator@example.org",
            "role":"super_admin",
            "expires_at":(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
            "nonce":"unique-approval-nonce-0123456789"
        }
    def doc(self):
        signatures=[
            {"approver_id":key,"signature_b64":base64.b64encode(p.sign(
                canonical(self.payload))).decode()}
            for key,p in zip(self.keys,self.priv)]
        return {"payload":self.payload.copy(),"approvals":signatures}
    def test_two_distinct_signatures_accepted(self):
        self.assertEqual(verify_approvals(self.doc(),self.keys),"operator@example.org")
    def test_one_signature_rejected(self):
        doc=self.doc();doc["approvals"].pop()
        with self.assertRaises(ValueError): verify_approvals(doc,self.keys)
    def test_same_key_two_names_rejected(self):
        doc=self.doc()
        keys={"approver-a":self.keys["approver-a"],"approver-b":self.keys["approver-a"]}
        with self.assertRaises(ValueError):verify_approvals(doc,keys)
    def test_unsigned_change_of_role_rejected(self):
        doc=self.doc();doc["payload"]["role"]="manager"
        with self.assertRaises(ValueError):verify_approvals(doc,self.keys)
    def test_unsigned_change_of_email_rejected(self):
        doc=self.doc();doc["payload"]["email"]="other@example.org"
        with self.assertRaises(Exception):verify_approvals(doc,self.keys)
    def test_expired_rejected(self):
        self.payload["expires_at"]=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        with self.assertRaises(ValueError):verify_approvals(self.doc(),self.keys)
    def test_noncanonical_email_rejected(self):
        self.payload["email"]="Operator@example.org"
        with self.assertRaises(ValueError):verify_approvals(self.doc(),self.keys)

class StaffRBAC(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine("sqlite+pysqlite://",connect_args={"check_same_thread":False},
                                  poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessionmaker=sessionmaker(self.engine, expire_on_commit=False)
        self.key=b"K"*32
        self.cfg=Config("https://testserver",self.key,b"T"*32,b"H"*32,"not-in-use")
        with self.sessionmaker() as db:
            self.users={}
            for role in ("analyst","manager","super_admin","super_admin"):
                user=Staff(id=uuid4(),email=f"{role}-{len(self.users)}@example.org",
                           password_salt=b"s"*16,password_digest=b"d"*32,
                           totp_ciphertext=b"cipher",last_totp_step=-1,role=role,
                           active=True,created_at=now())
                db.add(user)
                self.users[len(self.users)]=user
            db.commit()
        self.app=FastAPI()
        self.app.include_router(router)
        def getdb():
            with self.sessionmaker() as db: yield db
        self.app.dependency_overrides[get_db]=getdb
        self.app.dependency_overrides[require_enabled]=lambda:self.cfg
        self.client=TestClient(self.app,base_url="https://testserver")
    def tearDown(self):
        self.client.close(); self.engine.dispose()
    def authenticate(self,index):
        token=f"session-{index}"
        with self.sessionmaker() as db:
            db.add(StaffSession(
                token_digest=stamp(self.key,"session:",token),staff_id=self.users[index].id,
                created_at=now(),seen_at=now(),expires_at=now()+timedelta(hours=1)))
            db.commit()
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE,token)
        return {"Origin":"https://testserver","X-CSRF-Token":csrf_token(self.cfg,token)}
    def test_session_recognizes_scoped_superadmin(self):
        self.authenticate(2)
        res=self.client.get("/v1/admin/auth/session")
        self.assertEqual(res.status_code,200)
        self.assertEqual(res.json()["role"],"super_admin")
        self.assertEqual(res.json()["staff_id"],str(self.users[2].id))
    def test_manager_cannot_list_staff(self):
        self.authenticate(1)
        res=self.client.get("/v1/admin/staff")
        self.assertEqual(res.status_code,403)
    def test_analyst_cannot_list_staff(self):
        self.authenticate(0)
        self.assertEqual(self.client.get("/v1/admin/staff").status_code,403)
    def test_superadmin_can_list_staff_without_secrets(self):
        self.authenticate(2)
        res=self.client.get("/v1/admin/staff")
        self.assertEqual(res.status_code,200)
        data=res.json()["items"]
        self.assertEqual(len(data),4)
        self.assertNotIn("password",res.text)
        self.assertNotIn("totp",res.text)
    def test_superadmin_can_manage_handoff_status(self):
        self.assertIsNone(ensure_manager(self.users[2]))
        with self.assertRaises(Exception):
            ensure_manager(self.users[0])
    def test_manager_cannot_deactivate(self):
        headers=self.authenticate(1)
        res=self.client.post(f"/v1/admin/staff/{self.users[0].id}/deactivate",headers=headers)
        self.assertEqual(res.status_code,403)
    def test_csrf_required(self):
        self.authenticate(2)
        res=self.client.post(f"/v1/admin/staff/{self.users[1].id}/deactivate")
        self.assertEqual(res.status_code,403)
    def test_cannot_deactivate_self(self):
        headers=self.authenticate(2)
        res=self.client.post(f"/v1/admin/staff/{self.users[2].id}/deactivate",headers=headers)
        self.assertEqual(res.status_code,409)
    def test_deactivation_revokes_sessions_and_audits(self):
        # Issue a manager session and disable it while a superadmin is logged in.
        self.authenticate(1)
        headers=self.authenticate(2)
        res=self.client.post(f"/v1/admin/staff/{self.users[1].id}/deactivate",headers=headers)
        self.assertEqual(res.status_code,200)
        with self.sessionmaker() as db:
            manager=db.get(Staff,self.users[1].id)
            self.assertFalse(manager.active)
            sessions=db.scalars(select(StaffSession).where(StaffSession.staff_id==manager.id)).all()
            self.assertTrue(sessions)
            self.assertTrue(all(row.revoked_at is not None for row in sessions))
            events=db.scalars(select(StaffAudit.event)).all()
            self.assertIn("STAFF_DEACTIVATED",events)
    def test_superadmin_deactivation_leaves_one_active(self):
        headers=self.authenticate(2)
        res=self.client.post(f"/v1/admin/staff/{self.users[3].id}/deactivate",headers=headers)
        self.assertEqual(res.status_code,200)
        with self.sessionmaker() as db:
            self.assertTrue(db.get(Staff,self.users[2].id).active)
            self.assertFalse(db.get(Staff,self.users[3].id).active)

if __name__=="__main__":unittest.main()
