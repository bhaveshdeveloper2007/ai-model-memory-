from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(StrictModel):
    identity: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=240)
    root_path: str = Field(min_length=1, max_length=4096)
    remote_url: str | None = Field(default=None, max_length=2048)


class ProjectRead(StrictModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    identity: str
    name: str
    root_path: str
    remote_url: str | None
    created_at: datetime
    updated_at: datetime


class AgentCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    kind: str = Field(min_length=1, max_length=80)
    capabilities: list[str] = Field(default_factory=list, max_length=64)


class AgentRead(StrictModel):
    id: UUID
    name: str
    kind: str


class EventCreate(StrictModel):
    event_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    event_type: str = Field(min_length=1, max_length=160)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_name: str = Field(default="generic", min_length=1, max_length=160)
    session_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="api", max_length=64)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return value


class EventRead(StrictModel):
    id: UUID
    project_id: UUID
    agent_id: UUID
    session_id: UUID | None
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]
    source: str

    model_config = ConfigDict(from_attributes=True)


class EventAccepted(StrictModel):
    event_id: UUID
    persisted: Literal[True] = True
    duplicate: bool = False


class SessionCreate(StrictModel):
    project_id: UUID
    external_session_id: str | None = Field(default=None, max_length=256)
    state: dict[str, Any] = Field(default_factory=dict)


class SessionRead(StrictModel):
    id: UUID
    project_id: UUID
    agent_id: UUID
    status: str
    started_at: datetime
    ended_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class TaskCreate(StrictModel):
    project_id: UUID
    title: str = Field(min_length=1, max_length=500)
    details: str | None = Field(default=None, max_length=20000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=100)
    status: Literal["pending", "ready", "in_progress", "blocked", "awaiting_approval", "completed", "failed", "cancelled"] = "pending"
    weight: int = Field(default=1, ge=1, le=100)
    owner_agent_id: UUID | None = None


class TaskUpdate(StrictModel):
    status: Literal["pending", "ready", "in_progress", "blocked", "awaiting_approval", "completed", "failed", "cancelled"]


class TaskDependencyCreate(StrictModel):
    depends_on_id: UUID


class LeaseCreate(StrictModel):
    project_id: UUID
    resource_key: str = Field(min_length=1, max_length=400)
    ttl_seconds: int = Field(default=300, ge=10, le=3600)


class TaskRead(StrictModel):
    id: UUID
    project_id: UUID
    owner_agent_id: UUID | None
    title: str
    details: str | None
    acceptance_criteria: list
    status: str
    weight: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryCreate(StrictModel):
    project_id: UUID
    source_event_id: UUID | None = None
    category: Literal["working", "episodic", "semantic", "procedural", "project"]
    source: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=100000)
    confidence: float = Field(default=0.5, ge=0, le=1)
    verified: bool = False
    importance: float = Field(default=0.5, ge=0, le=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class MemoryRead(StrictModel):
    id: UUID
    project_id: UUID
    source_event_id: UUID | None
    category: str
    source: str
    content: str
    confidence: float
    verified: bool
    importance: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MemoryUpdate(StrictModel):
    content: str | None = Field(default=None, min_length=1, max_length=100000)
    confidence: float | None = Field(default=None, ge=0, le=1)
    verified: bool | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    archived: bool | None = None


class MemorySearchResult(StrictModel):
    memory: MemoryRead
    score: float
    selected_because: str


class DecisionCreate(StrictModel):
    project_id: UUID
    session_id: UUID | None = None
    source_event_id: UUID | None = None
    summary: str = Field(min_length=1, max_length=20000)
    rationale: str | None = Field(default=None, max_length=50000)


class ErrorCreate(StrictModel):
    project_id: UUID
    session_id: UUID | None = None
    category: str = Field(default="general", max_length=100)
    summary: str = Field(min_length=1, max_length=20000)
    details: dict[str, Any] = Field(default_factory=dict)


class CheckpointCreate(StrictModel):
    project_id: UUID
    session_id: UUID | None = None
    summary: str = Field(min_length=1, max_length=20000)
    state: dict[str, Any] = Field(default_factory=dict)
