"""PostgreSQL-only proposed SQLAlchemy model, NOT a migration nor a live schema."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): ...

class Handoff(Base):
    __tablename__ = "patroai_handoff"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    transcript_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    contact_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    contact_preference: Mapped[str] = mapped_column(String(12), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    transcript_provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="new")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("status IN ('new','in_review','closed')", name="ck_patroai_handoff_status"),
        CheckConstraint("transcript_provenance = 'user_submitted'", name="ck_patroai_handoff_provenance"),
        CheckConstraint("contact_preference IN ('none','email','whatsapp')", name="ck_patroai_handoff_preference"),
        Index("ix_patroai_handoff_status_received", "status", "received_at"),
        Index("ix_patroai_handoff_retention", "retention_due_at"),
    )

class HandoffKey(Base):
    __tablename__ = "patroai_handoff_key"
    key_hmac: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    request_hmac: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    # Nullable inside transaction for reservation, MUST be non-null by committed transaction.
    handoff_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("patroai_handoff.id", ondelete="RESTRICT"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replay_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tombstone_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class Audit(Base):
    __tablename__ = "patroai_handoff_audit"
    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    handoff_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("patroai_handoff.id", ondelete="RESTRICT"), nullable=False)
    event: Mapped[str] = mapped_column(String(40), nullable=False)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Never log user text, contact, token, raw idempotency key, or unredacted payload.
