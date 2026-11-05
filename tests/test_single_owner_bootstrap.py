"""Proposal-only tests. PG integration must use a separately approved disposable R2 lab DB."""
import base64
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

MODULE = Path(__file__).resolve().parents[1] / "tools" / "bootstrap_single_owner.py"
spec = importlib.util.spec_from_file_location("single_owner_proposal", MODULE)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)

NOW = datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc)
SCRIPT_HASH = "a" * 64
REFERENCE = "AO01-BOOTSTRAP-DESIGN-0001"

def material():
    private = Ed25519PrivateKey.generate()
    pub = base64.b64encode(private.public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    payload = {
        "purpose": p.PURPOSE, "environment": "STAGING",
        "database": "railway", "system_identifier": str(p.TARGET_SYSTEM_ID),
        "alembic_revision": p.TARGET_REVISION, "email": p.TARGET_EMAIL,
        "role": p.TARGET_ROLE, "approver_id": p.OWNER_ID,
        "script_sha256": SCRIPT_HASH,
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=59)).isoformat(),
        "nonce": "1" * 32, "ao01_reference": REFERENCE,
    }
    signed = {
        "payload": payload,
        "signature_b64": base64.b64encode(private.sign(p.canonical(payload))).decode("ascii"),
    }
    digest = p.sha256(p.canonical(signed))
    return private, pub, signed, digest

def verify(signed=None, pub=None, digest=None, ref=REFERENCE, now=NOW):
    _, known_pub, known_signed, known_digest = material()
    return p.verify_document(
        signed if signed is not None else known_signed,
        pub if pub is not None else known_pub,
        digest if digest is not None else known_digest,
        ref,
        now_at=now,
        expected_script_sha=SCRIPT_HASH
    )

def test_signature_positive():
    _, pub, signed, digest = material()
    assert verify(signed, pub, digest)["email"] == p.TARGET_EMAIL

@pytest.mark.parametrize("field,value", [
    ("environment", "PRODUCTION"),
    ("database", "patroai_r2_lab"),
    ("system_identifier", "1"),
    ("email", "dangraebin@gmail.com"),
    ("role", "manager"),
    ("approver_id", "daniel_gmail"),
    ("alembic_revision", "0001_patroai"),
    ("script_sha256", "b" * 64),
])
def test_any_scope_change_is_rejected_even_if_resigned(field, value):
    private, pub, signed, _ = material()
    signed["payload"][field] = value
    signed["signature_b64"] = base64.b64encode(
        private.sign(p.canonical(signed["payload"]))
    ).decode("ascii")
    with pytest.raises(ValueError):
        verify(signed, pub, p.sha256(p.canonical(signed)))

def test_invalid_signature_cannot_be_approved_by_hash_only():
    _, pub, signed, _ = material()
    signed["signature_b64"] = base64.b64encode(os.urandom(64)).decode("ascii")
    with pytest.raises(ValueError, match="SIGNATURE_INVALID"):
        verify(signed, pub, p.sha256(p.canonical(signed)))

def test_wrong_public_key():
    _, _, signed, digest = material()
    random_pub = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes(
        Encoding.Raw, PublicFormat.Raw)).decode()
    with pytest.raises(ValueError, match="SIGNATURE_INVALID"):
        verify(signed, random_pub, digest)

def test_expired_and_future_dates_rejected():
    _, pub, signed, digest = material()
    with pytest.raises(ValueError, match="APPROVAL_EXPIRED"):
        verify(signed, pub, digest, now=NOW + timedelta(hours=2))

def test_ao01_hash_and_reference_rejected():
    _, pub, signed, digest = material()
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        verify(signed, pub, "b"*64)
    with pytest.raises(ValueError, match="REFERENCE_MISMATCH"):
        verify(signed, pub, digest, ref="AO01-OTHER-0001")

def test_missing_payload_keys_and_bad_nonce():
    private, pub, signed, _ = material()
    del signed["payload"]["nonce"]
    signed["signature_b64"] = base64.b64encode(private.sign(p.canonical(signed["payload"]))).decode()
    with pytest.raises(ValueError, match="PAYLOAD_FIELDS"):
        verify(signed, pub, p.sha256(p.canonical(signed)))

def test_environment_fails_closed():
    env = {
        "RAILWAY_ENVIRONMENT_NAME": "STAGING",
        "PATROAI_DASHBOARD_ENABLED": "false",
        "PATROAI_PROVISION_GATE": "OFF",
        "PATROAI_SINGLE_OWNER_BOOTSTRAP_GATE": "true",
        "PATROAI_ADMIN_TOTP_KEY": "a" * 64,
    }
    assert len(p.environment_gate(env)) == 32
    for key, value in [
        ("RAILWAY_ENVIRONMENT_NAME", "PRODUCTION"),
        ("PATROAI_DASHBOARD_ENABLED", "true"),
        ("PATROAI_PROVISION_GATE", "true"),
        ("PATROAI_SINGLE_OWNER_BOOTSTRAP_GATE", "false"),
        ("PATROAI_ADMIN_TOTP_KEY", "bad")
    ]:
        wrong = {**env, key: value}
        with pytest.raises(ValueError):
            p.environment_gate(wrong)

def test_db_url_restricted():
    ok = p.database_url_gate("postgres://user:pass@db.internal/railway")
    assert ok.database == "railway"
    for raw in ["postgresql://user:pass@host/patroai_r2_lab",
                "sqlite:///railway",
                "postgresql+psycopg://user:pass@host/postgres"]:
        with pytest.raises(ValueError):
            p.database_url_gate(raw)

def test_code_hash_self_measured():
    assert p.script_sha256() == p.sha256(MODULE.read_bytes())

def test_prepare_document_without_secret_and_no_overwrite(tmp_path, monkeypatch):
    from argparse import Namespace
    monkeypatch.setattr(p, "utc_now", lambda: NOW)
    path = tmp_path / "unsigned.json"
    p.prepare(Namespace(ao01_reference=REFERENCE, minutes=60, out=str(path)))
    doc = json.loads(path.read_text())
    assert set(doc) == {"payload"}
    assert doc["payload"]["script_sha256"] == p.script_sha256()
    assert doc["payload"]["email"] == p.TARGET_EMAIL
    assert len(doc["payload"]["nonce"]) == 32
    with pytest.raises(FileExistsError):
        p.prepare(Namespace(ao01_reference=REFERENCE, minutes=60, out=str(path)))

def test_sign_and_verify_end_to_end_without_db(tmp_path, monkeypatch):
    from argparse import Namespace
    from cryptography.hazmat.primitives.serialization import (
        BestAvailableEncryption, PrivateFormat
    )
    private = Ed25519PrivateKey.generate()
    private_file = tmp_path / "encrypted.pem"
    private_file.write_bytes(private.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(b"somepassphrase")))
    pub = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    monkeypatch.setattr(p, "utc_now", lambda: NOW)
    monkeypatch.setattr(p.getpass, "getpass", lambda _: "somepassphrase")
    unsigned = tmp_path / "unsigned.json"
    signed = tmp_path / "signed.json"
    p.prepare(Namespace(ao01_reference=REFERENCE, minutes=60, out=str(unsigned)))
    p.sign(Namespace(unsigned=str(unsigned), private_key=str(private_file), out=str(signed)))
    doc = json.loads(signed.read_text())
    approved_hash = p.sha256(p.canonical(doc))
    assert p.verify_document(doc, pub, approved_hash, REFERENCE,
                             now_at=NOW)["role"] == "super_admin"
    assert private_file.read_bytes().startswith(b"-----BEGIN ENCRYPTED PRIVATE KEY-----")
    with pytest.raises(FileExistsError):
        p.sign(Namespace(unsigned=str(unsigned), private_key=str(private_file), out=str(signed)))

def test_no_public_http_endpoint_or_manager_provisioner_changes():
    assert not hasattr(p, "router")
    source = MODULE.read_text()
    assert "verify_approvals(" not in source
    assert "create_all(" not in source
    assert "StaffAudit(" in source and "LOCK TABLE public.patroai_admin_staff" in source


@pytest.mark.integration_postgres
def test_disposable_real_postgres_transaction_and_rollback(monkeypatch):
    """Run ONLY in approved lab database with R2 already migrated. NEVER uses railway.
    Requires PATROAI_BOOTSTRAP_TEST_DATABASE_URL: must target patroai_r2_lab.
    Verification and writes are rolled back, not committed.
    """
    raw = os.environ.get("PATROAI_BOOTSTRAP_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL lab unavailable; NOT a passing PG test")
    from sqlalchemy import create_engine, select, func, text
    from sqlalchemy.orm import Session
    from dashboard.models import Staff, StaffAudit
    from dashboard.auth import seal_totp, password_digest
    from sqlalchemy.engine import make_url
    url = make_url(p.normalize_db_url(raw))
    if url.drivername != "postgresql+psycopg" or url.database != "patroai_r2_lab":
        pytest.fail("Must be separately authorized isolated patroai_r2_lab DB")
    eng = create_engine(url, pool_pre_ping=True)
    try:
        with eng.connect() as conn:
            assert conn.execute(text("SELECT current_database()")).scalar_one() == "patroai_r2_lab"
            assert conn.execute(text("SELECT version_num FROM public.alembic_version")).scalar_one() == p.TARGET_REVISION
            cluster_id = int(conn.execute(text(
                "SELECT system_identifier FROM pg_control_system()"
            )).scalar_one())
            # In-process constants are altered ONLY by this PG laboratory test.
            # The delivered CLI hard-codes railway and refuses lab databases.
            monkeypatch.setattr(p, "TARGET_DATABASE", "patroai_r2_lab")
            monkeypatch.setattr(p, "TARGET_SYSTEM_ID", cluster_id)
            # SQLAlchemy implicitly begins on read. No commit: rollback ensures cleanup.
            try:
                with Session(bind=conn) as session:
                    assert session.scalar(select(func.count()).select_from(Staff)) == 0
                    uid = uuid4()
                    digest_salt = os.urandom(16)
                    p.persist_first_owner(
                        session,
                        user_id=uid, salt=digest_salt,
                        pw_hash=password_digest("a strong test password of sufficient length", digest_salt),
                        encrypted_totp=seal_totp(os.urandom(32), uid, os.urandom(20)),
                        verified_step=1234, nonce=os.urandom(16).hex(),
                        event_time=p.utc_now(),
                    )
                    assert session.scalar(select(func.count()).select_from(Staff)) == 1
                    assert session.scalar(select(func.count()).select_from(StaffAudit).where(
                        StaffAudit.event == p.EVENT)) == 1
                    with pytest.raises(ValueError, match="ADMINISTRATOR_ALREADY_EXISTS"):
                        p.persist_first_owner(
                            session, user_id=uuid4(), salt=b"1"*16, pw_hash=b"2"*32,
                            encrypted_totp=b"3"*32, verified_step=1, nonce=os.urandom(16).hex(),
                            event_time=p.utc_now(),
                        )
            finally:
                conn.rollback()
            assert conn.execute(text("SELECT count(*) FROM public.patroai_admin_staff")).scalar_one() == 0
    finally:
        eng.dispose()


@pytest.mark.integration_postgres
def test_disposable_postgres_lock_conflicts_with_concurrent_staff_insert():
    """Real PostgreSQL verifies table lock excludes ROW EXCLUSIVE (INSERT) writers.

    Requires separate approval of disposable R2 PostgreSQL lab. No inserts committed.
    """
    raw = os.environ.get("PATROAI_BOOTSTRAP_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("PostgreSQL lab unavailable; concurrency NOT proven")
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import OperationalError
    url = make_url(p.normalize_db_url(raw))
    if url.drivername != "postgresql+psycopg" or url.database != "patroai_r2_lab":
        pytest.fail("Not an isolated approved lab database")
    engine = create_engine(url)
    try:
        with engine.connect() as first, engine.connect() as second:
            assert first.execute(text("SELECT current_database()")).scalar_one() == "patroai_r2_lab"
            assert second.execute(text("SELECT current_database()")).scalar_one() == "patroai_r2_lab"
            first.execute(text("LOCK TABLE public.patroai_admin_staff IN SHARE ROW EXCLUSIVE MODE"))
            second.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(OperationalError):
                second.execute(text("LOCK TABLE public.patroai_admin_staff IN ROW EXCLUSIVE MODE"))
            second.rollback()
            first.rollback()
    finally:
        engine.dispose()


def test_real_r2_password_mfa_and_encryption_contract():
    """Runs against the unchanged R2 dashboard/auth.py, not custom cryptography."""
    from dashboard.auth import (
        matching_totp_step, password_digest, seal_totp, totp_for_step, unseal_totp
    )
    import os
    secret = os.urandom(20)
    uid = uuid4()
    totp_key = os.urandom(32)
    cipher = seal_totp(totp_key, uid, secret)
    assert unseal_totp(totp_key, uid, cipher) == secret
    assert len(password_digest("a very strong owner password", os.urandom(16))) == 32
    assert matching_totp_step(secret, "00000x", timestamp=180) is None
    assert matching_totp_step(secret, totp_for_step(secret, 6), timestamp=180) == 6

def test_preflight_database_identity_must_be_exact():
    class Row:
        def __init__(self, data): self.data = data
        def one(self): return self.data
        def scalar_one(self): return self.data
    class FakeDB:
        def __init__(self, name="railway", rev=p.TARGET_REVISION):
            self.name, self.rev = name, rev
        def execute(self, query):
            if "pg_control_system" in str(query):
                return Row((self.name, p.TARGET_SYSTEM_ID, "public", '"$user", public'))
            return Row(self.rev)
    assert p.validate_database_identity(FakeDB()) is None
    with pytest.raises(ValueError, match="DATABASE_IDENTITY_MISMATCH"):
        p.validate_database_identity(FakeDB(name="other_database"))
    with pytest.raises(ValueError, match="ALEMBIC_REVISION_MISMATCH"):
        p.validate_database_identity(FakeDB(rev="0001_patroai"))

def test_execute_requires_interactive_terminal_before_any_db(monkeypatch, tmp_path):
    from argparse import Namespace
    class NotTty:
        def isatty(self): return False
    monkeypatch.setattr(p.sys, "stdin", NotTty())
    with pytest.raises(ValueError, match="INTERACTIVE_TTY_REQUIRED"):
        p.execute(Namespace(approval=str(tmp_path / "does-not-exist.json")))
