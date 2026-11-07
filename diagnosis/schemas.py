"""Strict public request contract. Consents are separate, explicit and versioned."""
from __future__ import annotations
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

NOTICE_VERSION = "patroai-diagnosis-v1"
class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Consent(Strict):
    ai_processing: StrictBool
    commercial_contact: StrictBool
    notice_version: Literal["patroai-diagnosis-v1"]
    @field_validator("ai_processing", "commercial_contact")
    @classmethod
    def require_opt_in(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("Explicit, separate consent required")
        return value

class Registration(Strict):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$", max_length=16)
    company: str | None = Field(default=None, max_length=120)
    consent: Consent
    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        value=value.strip()
        if len(value)<2: raise ValueError("Invalid name")
        return value
    @field_validator("company")
    @classmethod
    def company_value(cls, value: str | None) -> str | None:
        if value is None: return None
        value=value.strip()
        return value or None

class TurnRequest(Strict):
    turn_id: UUID
    expected_version: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=1200)
    language: Literal["pt", "en", "es"] = "pt"
    @field_validator("turn_id")
    @classmethod
    def uuidv4(cls, value: UUID) -> UUID:
        if value.version != 4: raise ValueError("UUIDv4 required")
        return value
    @field_validator("message")
    @classmethod
    def text_not_empty(cls, value: str) -> str:
        if not value.strip(): raise ValueError("Empty message")
        return value

class CompleteRequest(Strict):
    expected_version: int = Field(ge=1)

class ReviewRequest(Strict):
    status: Literal["in_review","qualified","disqualified","contacted"]
