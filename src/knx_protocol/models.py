"""Pydantic models for the KNX <-> MQTT protocol."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "1.0"


class EventType(str, Enum):
    KNX_EVENT = "knx.event"


class CommandType(str, Enum):
    READ = "knx.command.read"
    WRITE = "knx.command.write"


class ReplyType(str, Enum):
    READ = "knx.reply.read"
    WRITE = "knx.reply.write"


class ReplyStatus(str, Enum):
    OK = "ok"
    ERROR = "error"


class SourceInfo(BaseModel):
    """Origin of the message (service + optional instance id)."""

    service: str
    instance_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class EnvelopeBase(BaseModel):
    schema_version: Literal["1.0"] = Field(default=SCHEMA_VERSION)
    message_type: str
    message_id: UUID
    timestamp: datetime
    source: SourceInfo

    model_config = ConfigDict(extra="forbid")


class KNXEventPayload(BaseModel):
    """Event payload sent from knx2mqtt to consumers."""

    device_id: str
    destination: str
    value: Any | None = None
    unit: str | None = None
    direction: str | None = None
    device_name: str | None = None
    destination_name: str | None = None
    knx_message_type: str | None = None
    dpt_main: int | None = None
    dpt_sub: int | None = None

    model_config = ConfigDict(extra="allow")


class KNXCommandPayload(BaseModel):
    """Commands sent to knx2mqtt over MQTT."""

    action: CommandType
    destination: str | None = None
    destinations: list[str] | None = None
    value: Any | None = None
    dpt_main: int | None = None
    dpt_sub: int | None = None

    model_config = ConfigDict(extra="forbid")


class KNXReplyPayload(BaseModel):
    """Reply to a command (read/write)."""

    status: ReplyStatus
    destination: str | None = None
    value: Any | None = None
    error: str | None = None

    model_config = ConfigDict(extra="forbid")


class EventEnvelope(EnvelopeBase):
    message_type: EventType = Field(default=EventType.KNX_EVENT)
    payload: KNXEventPayload


class CommandEnvelope(EnvelopeBase):
    message_type: CommandType
    payload: KNXCommandPayload


class ReplyEnvelope(EnvelopeBase):
    message_type: ReplyType
    payload: KNXReplyPayload
