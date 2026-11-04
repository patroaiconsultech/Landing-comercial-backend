"""Private admin tables. Never use create_all in application startup."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from commercial.models import Base

class Staff(Base):
    __tablename__ = "patroai_admin_staff"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    password_salt: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False)
    password_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    totp_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    last_totp_step: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-1)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("role IN ('analyst','manager')", name="ck_patroai_staff_role"),)

class AuthWindow(Base):
    __tablename__ = "patroai_admin_auth_window"
    key_digest: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

class LoginChallenge(Base):
    __tablename__ = "patroai_admin_login_challenge"
    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    staff_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("patroai_admin_staff.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

class StaffSession(Base):
    __tablename__ = "patroai_admin_session"
    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    staff_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("patroai_admin_staff.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class StaffAudit(Base):
    __tablename__ = "patroai_admin_audit"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    staff_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    handoff_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    request_id: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # No transcript, contact, OTP, password or session token in audit records.

Index("ix_patroai_admin_audit_occurred", StaffAudit.occurred_at)
