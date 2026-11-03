"""One vertical slice: strict consent -> encrypted payload -> atomic storage & receipt."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4
import json

from .schemas import HandoffSubmission, Receipt
from .security import Secrets, canonical_payload, digest, encrypt

REPLAY_HOURS = 72        # institutional ratification still pending
TOMBSTONE_DAYS = 30      # after expiry, an old key cannot be reliably detected

class StoreUnavailable(Exception): ...
class DuplicateConflict(Exception): ...
class DuplicateExpired(Exception): ...

@dataclass(frozen=True)
class CandidateRecord:
    handoff_id: UUID
    key_hmac: bytes
    payload_hmac: bytes
    conversation_id: UUID
    transcript_ciphertext: bytes
    contact_ciphertext: bytes | None
    preference: str
    consent_version: str
    transcript_provenance: str
    received_at: datetime
    retention_due_at: datetime
    replay_until: datetime
    tombstone_until: datetime

class AtomicStore(Protocol):
    """submit MUST atomically insert idempotency + handoff + audit, or return replay.
    A partial commit MUST NOT return success. PostgreSQL uniqueness must arbitrate concurrency.
    """
    def submit(self, record: CandidateRecord) -> tuple[Receipt, bool]: ...

@dataclass
class HandoffService:
    store: AtomicStore
    keys: Secrets
    retention_days: int  # governed externally; never set by visitor

    def submit(self, data: HandoffSubmission, idempotency_key: UUID) -> tuple[Receipt, bool]:
        if idempotency_key.version != 4:
            raise ValueError("UUIDv4 required")
        if not 1 <= self.retention_days <= 365:
            raise StoreUnavailable("Missing approved retention policy")
        now = datetime.now(timezone.utc)
        record_id = uuid4()
        message_bytes = json.dumps([m.model_dump() for m in data.messages], ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8")
        contact_bytes = None
        if data.contact_preference != "none":
            contact_bytes = json.dumps(data.contact.model_dump(exclude_none=True, mode="json"),
                                       ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        record = CandidateRecord(
            handoff_id=record_id,
            key_hmac=digest(self.keys.fingerprint_key, f"patroai:handoff:{idempotency_key}".encode()),
            payload_hmac=digest(self.keys.fingerprint_key, canonical_payload(data)),
            conversation_id=data.conversation_id,
            transcript_ciphertext=encrypt(self.keys, record_id, "transcript", message_bytes),
            contact_ciphertext=encrypt(self.keys, record_id, "contact", contact_bytes) if contact_bytes is not None else None,
            preference=data.contact_preference, consent_version=data.consent.notice_version,
            transcript_provenance="user_submitted",  # derived server-side, never trusted from browser
            received_at=now, retention_due_at=now + timedelta(days=self.retention_days),
            replay_until=now + timedelta(hours=REPLAY_HOURS),
            tombstone_until=now + timedelta(days=TOMBSTONE_DAYS),
        )
        return self.store.submit(record)
