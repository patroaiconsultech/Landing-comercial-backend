"""R1.2 strict payload boundary. Browser-supplied transcript is never server-attested."""
from __future__ import annotations

from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, field_validator, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Message(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2500)

    @field_validator("content")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("blank message")
        return value  # NO normalization of user text or silent truncation

class Consent(StrictModel):
    share_transcript: StrictBool

    @field_validator("share_transcript")
    @classmethod
    def require_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("explicit transcript consent required")
        return value
    contact_permission: StrictBool
    notice_version: Literal["patroai-handoff-v1"]

class EmailContact(StrictModel):
    email: EmailStr
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, name: str | None) -> str | None:
        if name is not None and not name.strip():
            raise ValueError("blank name")
        return name

class WhatsappContact(StrictModel):
    phone: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$", max_length=16)
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, name: str | None) -> str | None:
        if name is not None and not name.strip():
            raise ValueError("blank name")
        return name

class HandoffSubmission(StrictModel):
    conversation_id: UUID
    messages: list[Message] = Field(min_length=2, max_length=16)
    consent: Consent
    contact_preference: Literal["none", "email", "whatsapp"]
    contact: EmailContact | WhatsappContact | None = None

    @model_validator(mode="after")
    def valid_contact_combination(self):
        roles = {msg.role for msg in self.messages}
        if roles != {"user", "assistant"}:
            raise ValueError("both user and assistant are required")
        pref = self.contact_preference
        permission = self.consent.contact_permission
        supplied = "contact" in self.model_fields_set
        if pref == "none":
            if permission or supplied:
                raise ValueError("contact not permitted for none")
        elif pref == "email":
            if not permission or not supplied or not isinstance(self.contact, EmailContact):
                raise ValueError("email permission and matching contact required")
        elif pref == "whatsapp":
            if not permission or not supplied or not isinstance(self.contact, WhatsappContact):
                raise ValueError("whatsapp permission and matching contact required")
        return self

class Receipt(StrictModel):
    handoff_id: UUID
    status: Literal["new", "in_review", "closed"]
    received_at: str

class Summary(Receipt):
    version: int = Field(ge=1)

class Detail(Summary):
    selected_messages: list[Message]
    contact_preference: Literal["none", "email", "whatsapp"]
    contact: EmailContact | WhatsappContact | None = None
    consent_notice_version: str
    transcript_provenance: Literal["user_submitted"] = "user_submitted"

class StatusChange(StrictModel):
    status: Literal["in_review", "closed"]
