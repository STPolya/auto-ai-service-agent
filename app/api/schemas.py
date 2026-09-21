"""Explicit CRM contracts, independent of ORM serialization."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.services.support_status import SupportStatus


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class UserResponse(BaseModel):
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None


class SupportRequestResponse(BaseModel):
    id: int
    status: SupportStatus
    summary: str | None
    created_at: datetime
    updated_at: datetime
    conversation_id: int
    user: UserResponse


class ConversationResponse(BaseModel):
    id: int
    created_at: datetime


class MessageResponse(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class VehicleResponse(BaseModel):
    id: int
    brand: str
    model: str
    year: int | None
    license_plate: str | None


class SupportRequestDetailResponse(SupportRequestResponse):
    conversation: ConversationResponse
    messages: list[MessageResponse]
    vehicles: list[VehicleResponse]


class StatusUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: SupportStatus
