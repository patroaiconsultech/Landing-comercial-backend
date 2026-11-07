"""R3-A schema candidate. Alembic alone owns table creation."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, LargeBinary, String, UniqueConstraint)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from commercial.models import Base

STATUSES = ("registered", "in_progress", "completed", "in_review",
            "qualified", "disqualified", "contacted", "revoked")

class Diagnosis(Base):
    __tablename__ = "patroai_diagnosis"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    init_key_digest: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    init_payload_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    session_digest: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True, nullable=False)
    session_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    contact_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    consent_ai: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_contact: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consent_notice_version: Mapped[str] = mapped_column(String(64), nullable=False)
    consent_accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="registered")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('registered','in_progress','completed','in_review','qualified','disqualified','contacted','revoked')",
                        name="ck_patroai_diagnosis_status"),
        CheckConstraint("version >= 1 AND turn_count >= 0", name="ck_patroai_diagnosis_counts"),
        Index("ix_patroai_diagnosis_status_created", "status", "created_at"),
        Index("ix_patroai_diagnosis_retention", "retention_due_at"),
    )

class DiagnosisTurn(Base):
    __tablename__ = "patroai_diagnosis_turn"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("patroai_diagnosis.id", ondelete="RESTRICT"), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    user_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    assistant_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    expected_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('pending','complete','failed')", name="ck_patroai_diagnosis_turn_status"),
        Index("ix_patroai_diag_turn_history", "diagnosis_id", "finished_at"),
    )

class DiagnosisEvent(Base):
    __tablename__ = "patroai_diagnosis_event"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("patroai_diagnosis.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("diagnosis_id", "kind", name="uq_patroai_diag_event_once"),)

class DiagnosisAudit(Base):
    __tablename__ = "patroai_diagnosis_audit"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True),
        ForeignKey("patroai_diagnosis.id", ondelete="RESTRICT"), nullable=False)
    staff_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Never include phone, name, content, session token, OTP or provider text.

class DiagnosisRate(Base):
    __tablename__ = "patroai_diagnosis_rate"
    key_digest: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
