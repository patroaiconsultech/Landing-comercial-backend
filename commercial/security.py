"""Ciphers and fingerprints; all key material is server-side, never logged."""
from __future__ import annotations
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from uuid import UUID
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .schemas import HandoffSubmission

@dataclass(frozen=True)
class Secrets:
    encryption_key: bytes  # 32 random bytes, independent from Postgres
    fingerprint_key: bytes # >=32 random bytes, independent from encryption key

    def __post_init__(self):
        if len(self.encryption_key) != 32 or len(self.fingerprint_key) < 32:
            raise ValueError("Missing or invalid server-only keys")
        if self.encryption_key == self.fingerprint_key:
            raise ValueError("Keys must be independent")

def digest(secret: bytes, value: bytes) -> bytes:
    return hmac.digest(secret, value, "sha256")

def canonical_payload(payload: HandoffSubmission) -> bytes:
    # Includes full, untrimmed message text and explicit consent fields.
    return json.dumps(payload.model_dump(mode="json", exclude_none=True),
                      sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def encrypt(secrets: Secrets, record_id: UUID, purpose: str, value: bytes) -> bytes:
    nonce = os.urandom(12)
    associated = f"patroai:{record_id}:{purpose}:v1".encode("ascii")
    return nonce + AESGCM(secrets.encryption_key).encrypt(nonce, value, associated)

def decrypt(secrets: Secrets, record_id: UUID, purpose: str, ciphertext: bytes) -> bytes:
    nonce, data = ciphertext[:12], ciphertext[12:]
    associated = f"patroai:{record_id}:{purpose}:v1".encode("ascii")
    return AESGCM(secrets.encryption_key).decrypt(nonce, data, associated)
