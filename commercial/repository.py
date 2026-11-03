"""Atomic PostgreSQL insert/replay. Intentionally not connected to real infrastructure."""
from __future__ import annotations
from datetime import datetime, timezone
import hmac
from typing import Callable
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from .models import Handoff, HandoffKey, Audit
from .schemas import Receipt
from .service import CandidateRecord, DuplicateConflict, DuplicateExpired, StoreUnavailable

class PostgresAtomicStore:
    """Requires PostgreSQL, real Alembic migration, and approved encryption/retention.

    The row reservation uses a UNIQUE key_hmac and a single transaction. A competing
    insert waits for the first transaction outcome under PostgreSQL READ COMMITTED.
    No connection is opened until submit is called by an explicitly wired service.
    """
    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    def submit(self, record: CandidateRecord) -> tuple[Receipt, bool]:
        with self.session_factory() as session:
            with session.begin():
                claimed = session.execute(
                    insert(HandoffKey).values(
                        key_hmac=record.key_hmac, request_hmac=record.payload_hmac,
                        handoff_id=None, created_at=record.received_at,
                        replay_until=record.replay_until,
                        tombstone_until=record.tombstone_until,
                    ).on_conflict_do_nothing(index_elements=[HandoffKey.key_hmac])
                     .returning(HandoffKey.key_hmac)
                ).scalar_one_or_none()

                if claimed is None:
                    old = session.execute(
                        select(HandoffKey).where(HandoffKey.key_hmac == record.key_hmac)
                    ).scalar_one_or_none()
                    if old is None or old.handoff_id is None:
                        raise StoreUnavailable("Uncommitted or inconsistent idempotency record")
                    # Compare content first; never reveal whether an old handoff exists to others.
                    if not hmac.compare_digest(old.request_hmac, record.payload_hmac):
                        raise DuplicateConflict()
                    now = datetime.now(timezone.utc)
                    if now > old.replay_until:
                        raise DuplicateExpired()
                    original = session.get(Handoff, old.handoff_id)
                    if original is None:
                        raise StoreUnavailable("Idempotency references missing handoff")
                    return Receipt(handoff_id=original.id, status="new",
                                   received_at=original.received_at.isoformat()), True

                new_handoff = Handoff(
                    id=record.handoff_id, conversation_id=record.conversation_id,
                    transcript_ciphertext=record.transcript_ciphertext,
                    contact_ciphertext=record.contact_ciphertext,
                    contact_preference=record.preference, consent_version=record.consent_version,
                    transcript_provenance=record.transcript_provenance,
                    status="new", version=1, received_at=record.received_at,
                    retention_due_at=record.retention_due_at,
                )
                session.add(new_handoff)
                session.flush()
                session.execute(
                    HandoffKey.__table__.update().where(HandoffKey.key_hmac == record.key_hmac)
                    .values(handoff_id=record.handoff_id)
                )
                session.add(Audit(handoff_id=record.handoff_id, event="handoff_created",
                                  happened_at=record.received_at))
                session.flush()
            # response only after context manager COMMITS both lead and audit
            return Receipt(handoff_id=record.handoff_id, status="new",
                           received_at=record.received_at.isoformat()), False
