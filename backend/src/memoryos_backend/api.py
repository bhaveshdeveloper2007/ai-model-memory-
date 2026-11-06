from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .db import get_session
from .models import (
    Agent,
    AgentLease,
    AgentSession,
    AuditLog,
    Checkpoint,
    Decision,
    ErrorRecord,
    Event,
    Memory,
    MemoryEmbedding,
    Project,
    SyncOutbox,
    Task,
    TaskDependency,
)
from .schemas import (
    AgentCreate,
    CheckpointCreate,
    DecisionCreate,
    ErrorCreate,
    EventAccepted,
    EventCreate,
    EventRead,
    MemoryCreate,
    MemoryRead,
    MemorySearchResult,
    MemoryUpdate,
    LeaseCreate,
    ProjectCreate,
    ProjectRead,
    SessionCreate,
    SessionRead,
    TaskCreate,
    TaskDependencyCreate,
    TaskRead,
    TaskUpdate,
)
from .security import Actor, redact_payload, require_actor, require_admin, require_project_access, token_digest
from .logging_config import configure_logging, monotonic_ms
from .retrieval import EmbeddingUnavailable, configured_provider
from .settings import get_settings

logger = logging.getLogger("memoryos.api")


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(
        title="Memory OS API",
        version="0.2.0",
        summary="Local-first, project-scoped agent memory and activity service.",
    )

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        start = time.perf_counter()
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > 1_048_576:
            return Response(status_code=413, content="Request body exceeds 1 MiB.")
        response = await call_next(request)
        logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": monotonic_ms(start),
            },
        )
        return response

    @app.get("/health", tags=["health"])
    def health(db: Session = Depends(get_session)):
        try:
            db.execute(text("SELECT 1"))
            vector = db.execute(text("SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar_one_or_none()
        except Exception:
            raise HTTPException(status_code=503, detail="Database unavailable.") from None
        return {"status": "ok", "database": "connected", "vector_extension": vector is not None}

    @app.get("/v1/projects", response_model=list[ProjectRead], tags=["projects"])
    def list_projects(
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        query = select(Project).order_by(Project.name)
        if not actor.is_admin:
            query = query.where(Project.permissions.contains({"agent_ids": [str(actor.agent_id)]}))
        return list(db.scalars(query))

    @app.post("/v1/projects", response_model=ProjectRead, status_code=201, tags=["projects"])
    def create_project(
        body: ProjectCreate,
        actor: Actor = Depends(require_admin),
        db: Session = Depends(get_session),
    ):
        existing = db.scalar(select(Project).where(Project.identity == body.identity))
        if existing:
            return existing
        project = Project(**body.model_dump())
        db.add(project)
        db.flush()
        db.add(AuditLog(project_id=project.id, action="project.created", outcome="allowed", details={"name": body.name}))
        db.commit()
        db.refresh(project)
        return project

    @app.get("/v1/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
    def get_project(
        project_id: uuid.UUID,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        return require_project_access(db, actor, project_id)

    @app.post("/v1/agents", status_code=201, tags=["agents"])
    def create_agent(
        body: AgentCreate,
        actor: Actor = Depends(require_admin),
        db: Session = Depends(get_session),
    ):
        secret = secrets.token_urlsafe(32)
        agent = Agent(name=body.name, kind=body.kind, capabilities=body.capabilities, credential_digest=token_digest(secret))
        db.add(agent)
        db.add(AuditLog(action="agent.credential.created", outcome="allowed", details={"agent_name": body.name}))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Agent name already exists.") from None
        db.refresh(agent)
        return {"id": agent.id, "name": agent.name, "kind": agent.kind, "token": secret}

    @app.put("/v1/projects/{project_id}/agents/{agent_id}", status_code=204, tags=["agents"])
    def grant_agent(
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor: Actor = Depends(require_admin),
        db: Session = Depends(get_session),
    ):
        project = db.get(Project, project_id)
        agent = db.get(Agent, agent_id)
        if project is None or agent is None:
            raise HTTPException(status_code=404, detail="Project or agent not found.")
        permissions = dict(project.permissions or {})
        allowed = set(permissions.get("agent_ids", []))
        allowed.add(str(agent_id))
        permissions["agent_ids"] = sorted(allowed)
        project.permissions = permissions
        db.add(AuditLog(project_id=project_id, agent_id=agent_id, action="project.agent.granted", outcome="allowed"))
        db.commit()
        return Response(status_code=204)

    @app.delete("/v1/projects/{project_id}/agents/{agent_id}", status_code=204, tags=["agents"])
    def revoke_agent(
        project_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor: Actor = Depends(require_admin),
        db: Session = Depends(get_session),
    ):
        project = db.get(Project, project_id)
        if project is None or db.get(Agent, agent_id) is None:
            raise HTTPException(status_code=404, detail="Project not found.")
        permissions = dict(project.permissions or {})
        permissions["agent_ids"] = [value for value in permissions.get("agent_ids", []) if value != str(agent_id)]
        project.permissions = permissions
        db.add(AuditLog(project_id=project_id, agent_id=agent_id, action="project.agent.revoked", outcome="allowed"))
        db.commit()
        return Response(status_code=204)

    @app.post("/v1/sessions", response_model=SessionRead, status_code=201, tags=["sessions"])
    def start_session(
        body: SessionCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        if actor.agent_id is None:
            agent = db.scalar(select(Agent).where(Agent.name == "codex"))
            if agent is None:
                agent = Agent(name="codex", kind="codex")
                db.add(agent)
                db.flush()
            agent_id = agent.id
        else:
            agent_id = actor.agent_id
        if body.external_session_id:
            current = db.scalar(select(AgentSession).where(
                AgentSession.project_id == body.project_id,
                AgentSession.agent_id == agent_id,
                AgentSession.external_session_id == body.external_session_id,
                AgentSession.status == "active",
            ))
            if current:
                return current
        session = AgentSession(
            project_id=body.project_id,
            agent_id=agent_id,
            external_session_id=body.external_session_id,
            last_state=redact_payload(body.state),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @app.post("/v1/events", response_model=EventAccepted, status_code=202, tags=["events"])
    def record_event(
        body: EventCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        payload = redact_payload(body.payload)
        agent_name = actor.agent_name if actor.agent_id else body.agent_name
        if actor.agent_id:
            agent = db.get(Agent, actor.agent_id)
        else:
            agent = db.scalar(select(Agent).where(Agent.name == agent_name))
            if agent is None:
                agent = Agent(name=agent_name, kind="generic")
                db.add(agent)
                db.flush()
        if body.session_id:
            agent_session = db.get(AgentSession, body.session_id)
            if agent_session is None or agent_session.project_id != body.project_id or agent_session.agent_id != agent.id:
                raise HTTPException(status_code=422, detail="Session does not match this project and agent.")
        digest_source = json.dumps({
            "project_id": str(body.project_id),
            "agent_id": str(agent.id),
            "session_id": str(body.session_id) if body.session_id else None,
            "event_type": body.event_type,
            "occurred_at": body.occurred_at.isoformat(),
            "payload": payload,
            "source": body.source,
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
        existing = db.get(Event, body.event_id)
        if existing:
            if existing.digest != digest:
                raise HTTPException(status_code=409, detail="Event ID already exists with different content.")
            return EventAccepted(event_id=body.event_id, duplicate=True)
        event = Event(
            id=body.event_id,
            project_id=body.project_id,
            agent_id=agent.id,
            session_id=body.session_id,
            event_type=body.event_type,
            occurred_at=body.occurred_at,
            payload=payload,
            digest=digest,
            source=body.source,
        )
        db.add(event)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.get(Event, body.event_id)
            if existing and existing.digest == digest:
                return EventAccepted(event_id=body.event_id, duplicate=True)
            raise HTTPException(status_code=409, detail="Event could not be stored idempotently.") from None
        return EventAccepted(event_id=body.event_id, duplicate=False)

    @app.get("/v1/projects/{project_id}/events", response_model=list[EventRead], tags=["events"])
    def list_events(
        project_id: uuid.UUID,
        limit: int = Query(default=100, ge=1, le=500),
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, project_id)
        return list(db.scalars(select(Event).where(Event.project_id == project_id).order_by(Event.occurred_at.desc()).limit(limit)))

    @app.post("/v1/memories", response_model=MemoryRead, status_code=201, tags=["memories"])
    def add_memory(
        body: MemoryCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        if body.source_event_id:
            event = db.get(Event, body.source_event_id)
            if event is None or event.project_id != body.project_id:
                raise HTTPException(status_code=422, detail="Source event is not in this project.")
        memory_values = body.model_dump()
        memory_values["content"] = redact_payload(memory_values["content"])
        memory_values["attributes"] = redact_payload(memory_values["attributes"])
        memory = Memory(**memory_values)
        db.add(memory)
        db.flush()
        enqueue_memory_projection(db, memory)
        db.add(AuditLog(project_id=body.project_id, agent_id=actor.agent_id, action="memory.created", outcome="allowed", details={"memory_id": str(memory.id)}))
        db.commit()
        db.refresh(memory)
        return memory

    @app.get("/v1/projects/{project_id}/memories/search", response_model=list[MemorySearchResult], tags=["memories"])
    def search_memories(
        project_id: uuid.UUID,
        q: str = Query(min_length=1, max_length=1000),
        limit: int = Query(default=10, ge=1, le=100),
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, project_id)
        query = text("""
            SELECT m.id, m.project_id, m.source_event_id, m.category, m.source,
                   m.content, m.confidence, m.verified, m.importance, m.created_at,
                   (ts_rank_cd(to_tsvector('english', m.content), plainto_tsquery('english', :q)) * 0.65
                    + m.importance * 0.20 + m.confidence * 0.10
                    + CASE WHEN m.verified THEN 0.05 ELSE 0 END
                    + 0.03 * exp(-extract(epoch FROM (now() - m.created_at)) / 2592000.0)) AS score
            FROM memories AS m
            WHERE m.project_id = :project_id
              AND m.archived_at IS NULL AND m.deleted_at IS NULL
              AND to_tsvector('english', m.content) @@ plainto_tsquery('english', :q)
            ORDER BY score DESC, m.created_at DESC
            LIMIT :limit
        """)
        ranked: dict[uuid.UUID, MemorySearchResult] = {}
        rows = db.execute(query, {"project_id": project_id, "q": q, "limit": limit}).mappings()
        for row in rows:
            memory = MemoryRead(**{key: row[key] for key in MemoryRead.model_fields})
            ranked[memory.id] = MemorySearchResult(
                memory=memory,
                score=float(row["score"]),
                selected_because="Project-scoped full-text match ranked with importance, verification, and recency.",
            )
        provider = configured_provider()
        if provider:
            try:
                vector = provider.embed(q)
                distance = MemoryEmbedding.embedding.cosine_distance(vector)
                semantic_rows = db.execute(
                    select(Memory, distance.label("distance"))
                    .join(MemoryEmbedding, MemoryEmbedding.memory_id == Memory.id)
                    .where(
                        Memory.project_id == project_id,
                        MemoryEmbedding.model_name == get_settings().embedding_model,
                        Memory.deleted_at.is_(None),
                        Memory.archived_at.is_(None),
                    )
                    .order_by(distance)
                    .limit(limit)
                )
                for memory, distance_value in semantic_rows:
                    semantic_score = max(0.0, min(1.0, 1.0 - float(distance_value)))
                    previous = ranked.get(memory.id)
                    lexical_score = previous.score if previous else 0.0
                    ranked[memory.id] = MemorySearchResult(
                        memory=MemoryRead.model_validate(memory),
                        score=0.6 * semantic_score + 0.4 * lexical_score,
                        selected_because="Project-scoped local vector similarity combined with full-text rank, importance, verification, and recency.",
                    )
            except (EmbeddingUnavailable, ValueError):
                logger.warning("local embedding provider unavailable; using full-text search only", extra={"component": "retrieval"})
        return sorted(ranked.values(), key=lambda item: item.score, reverse=True)[:limit]

    @app.get("/v1/projects/{project_id}/memories", response_model=list[MemoryRead], tags=["memories"])
    def list_memories(
        project_id: uuid.UUID,
        limit: int = Query(default=100, ge=1, le=500),
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, project_id)
        return list(db.scalars(select(Memory).where(
            Memory.project_id == project_id,
            Memory.deleted_at.is_(None),
        ).order_by(Memory.created_at.desc()).limit(limit)))

    def enqueue_memory_projection(db: Session, memory: Memory, *, delete_file: bool = False) -> None:
        payload = {"id": str(memory.id), "project_id": str(memory.project_id)}
        if not delete_file:
            payload.update({
                "category": memory.category,
                "source": memory.source,
                "source_event_id": str(memory.source_event_id) if memory.source_event_id else None,
                "content": memory.content,
                "confidence": memory.confidence,
                "verified": memory.verified,
                "importance": memory.importance,
                "attributes": memory.attributes,
                "created_at": memory.created_at.isoformat() if memory.created_at else datetime.now(timezone.utc).isoformat(),
            })
        db.add(SyncOutbox(
            kind="memory_markdown_delete" if delete_file else "memory_markdown_upsert",
            aggregate_id=memory.id,
            payload=payload,
        ))

    @app.get("/v1/memories/{memory_id}", response_model=MemoryRead, tags=["memories"])
    def get_memory(
        memory_id: uuid.UUID,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        memory = db.get(Memory, memory_id)
        if memory is None or memory.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Memory not found.")
        require_project_access(db, actor, memory.project_id)
        return memory

    @app.patch("/v1/memories/{memory_id}", response_model=MemoryRead, tags=["memories"])
    def update_memory(
        memory_id: uuid.UUID,
        body: MemoryUpdate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        memory = db.get(Memory, memory_id)
        if memory is None or memory.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Memory not found.")
        require_project_access(db, actor, memory.project_id, write=True)
        values = body.model_dump(exclude_unset=True)
        for key in ("content", "confidence", "verified", "importance"):
            if key in values and values[key] is not None:
                setattr(memory, key, redact_payload(values[key]) if key == "content" else values[key])
        if values.get("archived") is not None:
            memory.archived_at = datetime.now(timezone.utc) if values["archived"] else None
        memory.updated_at = datetime.now(timezone.utc)
        enqueue_memory_projection(db, memory)
        db.add(AuditLog(project_id=memory.project_id, agent_id=actor.agent_id, action="memory.updated", outcome="allowed", details={"memory_id": str(memory.id)}))
        db.commit()
        db.refresh(memory)
        return memory

    @app.delete("/v1/memories/{memory_id}", status_code=204, tags=["memories"])
    def delete_memory(
        memory_id: uuid.UUID,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        memory = db.get(Memory, memory_id)
        if memory is None or memory.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Memory not found.")
        require_project_access(db, actor, memory.project_id, write=True)
        memory.deleted_at = datetime.now(timezone.utc)
        enqueue_memory_projection(db, memory, delete_file=True)
        db.add(AuditLog(project_id=memory.project_id, agent_id=actor.agent_id, action="memory.deleted", outcome="allowed", details={"memory_id": str(memory.id)}))
        db.commit()
        return Response(status_code=204)

    @app.post("/v1/tasks", response_model=TaskRead, status_code=201, tags=["tasks"])
    def create_task(
        body: TaskCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        values = body.model_dump()
        if actor.agent_id:
            values["owner_agent_id"] = actor.agent_id
        if values.get("title"):
            values["title"] = redact_payload(values["title"])
        if values.get("details"):
            values["details"] = redact_payload(values["details"])
        values["acceptance_criteria"] = redact_payload(values["acceptance_criteria"])
        task = Task(**values)
        db.add(task)
        db.flush()
        db.add(AuditLog(project_id=body.project_id, agent_id=actor.agent_id, action="task.created", outcome="allowed", details={"task_id": str(task.id)}))
        db.commit()
        db.refresh(task)
        return task

    @app.get("/v1/projects/{project_id}/tasks", response_model=list[TaskRead], tags=["tasks"])
    def list_tasks(
        project_id: uuid.UUID,
        status_filter: str | None = Query(default=None, alias="status", max_length=32),
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, project_id)
        query = select(Task).where(Task.project_id == project_id)
        if status_filter:
            query = query.where(Task.status == status_filter)
        return list(db.scalars(query.order_by(Task.updated_at.desc())))

    @app.patch("/v1/tasks/{task_id}", response_model=TaskRead, tags=["tasks"])
    def update_task(
        task_id: uuid.UUID,
        body: TaskUpdate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        task = db.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        require_project_access(db, actor, task.project_id, write=True)
        if actor.agent_id and task.owner_agent_id and task.owner_agent_id != actor.agent_id:
            raise HTTPException(status_code=403, detail="Task is assigned to another agent.")
        task.status = body.status
        task.updated_at = datetime.now(timezone.utc)
        db.add(AuditLog(project_id=task.project_id, agent_id=actor.agent_id, action="task.status.updated", outcome="allowed", details={"task_id": str(task.id), "status": task.status}))
        db.commit()
        db.refresh(task)
        return task

    @app.post("/v1/tasks/{task_id}/dependencies", status_code=201, tags=["tasks"])
    def add_task_dependency(
        task_id: uuid.UUID,
        body: TaskDependencyCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        task = db.get(Task, task_id)
        dependency = db.get(Task, body.depends_on_id)
        if task is None or dependency is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        require_project_access(db, actor, task.project_id, write=True)
        if dependency.project_id != task.project_id:
            raise HTTPException(status_code=422, detail="Task dependencies must stay within one project.")
        if task.id == dependency.id:
            raise HTTPException(status_code=422, detail="A task cannot depend on itself.")
        creates_cycle = db.execute(text("""
            WITH RECURSIVE reachable(id) AS (
                SELECT CAST(:depends_on_id AS uuid)
                UNION
                SELECT td.depends_on_id
                FROM task_dependencies td JOIN reachable r ON td.task_id = r.id
                WHERE td.project_id = CAST(:project_id AS uuid)
            )
            SELECT EXISTS(SELECT 1 FROM reachable WHERE id = CAST(:task_id AS uuid))
        """), {"depends_on_id": str(dependency.id), "project_id": str(task.project_id), "task_id": str(task.id)}).scalar_one()
        if creates_cycle:
            raise HTTPException(status_code=409, detail="This dependency would create a cycle.")
        relation = TaskDependency(project_id=task.project_id, task_id=task.id, depends_on_id=dependency.id)
        db.add(relation)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="This dependency already exists or violates a project boundary.") from None
        return {"task_id": task.id, "depends_on_id": dependency.id, "project_id": task.project_id}

    @app.get("/v1/tasks/{task_id}/dependencies", tags=["tasks"])
    def list_task_dependencies(
        task_id: uuid.UUID,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        task = db.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        require_project_access(db, actor, task.project_id)
        relations = db.scalars(select(TaskDependency).where(TaskDependency.task_id == task.id))
        return [{"task_id": row.task_id, "depends_on_id": row.depends_on_id} for row in relations]

    @app.post("/v1/leases", tags=["coordination"])
    def acquire_lease(
        body: LeaseCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        if actor.agent_id is None:
            agent = db.scalar(select(Agent).where(Agent.name == "codex"))
            if agent is None:
                agent = Agent(name="codex", kind="codex")
                db.add(agent)
                db.flush()
            agent_id = agent.id
        else:
            agent_id = actor.agent_id
        resource_key = f"{body.project_id}:{body.resource_key}"
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=body.ttl_seconds)
        statement = pg_insert(AgentLease).values(
            resource_key=resource_key,
            project_id=body.project_id,
            agent_id=agent_id,
            expires_at=expires,
            heartbeat_at=now,
        ).on_conflict_do_update(
            index_elements=[AgentLease.resource_key],
            set_={"agent_id": agent_id, "project_id": body.project_id, "expires_at": expires, "heartbeat_at": now, "session_id": None},
            where=(AgentLease.expires_at <= now) | (AgentLease.agent_id == agent_id),
        )
        result = db.execute(statement)
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=409, detail="Resource is leased by another agent.")
        db.add(AuditLog(project_id=body.project_id, agent_id=agent_id, action="lease.acquired", outcome="allowed", details={"resource_key": body.resource_key, "expires_at": expires.isoformat()}))
        db.commit()
        return {"resource_key": body.resource_key, "project_id": body.project_id, "agent_id": agent_id, "expires_at": expires, "advisory": True}

    @app.post("/v1/leases/heartbeat", tags=["coordination"])
    def heartbeat_lease(
        body: LeaseCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        resource_key = f"{body.project_id}:{body.resource_key}"
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=body.ttl_seconds)
        filters = [AgentLease.resource_key == resource_key, AgentLease.project_id == body.project_id, AgentLease.expires_at > now]
        if actor.agent_id:
            filters.append(AgentLease.agent_id == actor.agent_id)
        result = db.execute(update(AgentLease).where(*filters).values(heartbeat_at=now, expires_at=expires))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=404, detail="An active lease owned by this agent was not found.")
        db.commit()
        return {"resource_key": body.resource_key, "expires_at": expires}

    @app.delete("/v1/leases", status_code=204, tags=["coordination"])
    def release_lease(
        body: LeaseCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        filters = [AgentLease.resource_key == f"{body.project_id}:{body.resource_key}", AgentLease.project_id == body.project_id]
        if actor.agent_id:
            filters.append(AgentLease.agent_id == actor.agent_id)
        result = db.execute(delete(AgentLease).where(*filters))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(status_code=404, detail="Lease not found or not owned by this agent.")
        db.add(AuditLog(project_id=body.project_id, agent_id=actor.agent_id, action="lease.released", outcome="allowed", details={"resource_key": body.resource_key}))
        db.commit()
        return Response(status_code=204)

    @app.post("/v1/decisions", status_code=201, tags=["decisions"])
    def record_decision(
        body: DecisionCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        values = body.model_dump()
        values["summary"] = redact_payload(values["summary"])
        if values.get("rationale"):
            values["rationale"] = redact_payload(values["rationale"])
        decision = Decision(**values)
        db.add(decision)
        db.add(AuditLog(project_id=body.project_id, agent_id=actor.agent_id, action="decision.recorded", outcome="allowed"))
        db.commit()
        db.refresh(decision)
        return {"id": decision.id, "project_id": decision.project_id, "summary": decision.summary, "created_at": decision.created_at}

    @app.post("/v1/errors", status_code=201, tags=["errors"])
    def record_error(
        body: ErrorCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        error = ErrorRecord(**{**body.model_dump(), "summary": redact_payload(body.summary), "details": redact_payload(body.details)})
        db.add(error)
        db.add(AuditLog(project_id=body.project_id, agent_id=actor.agent_id, action="error.recorded", outcome="allowed"))
        db.commit()
        db.refresh(error)
        return {"id": error.id, "project_id": error.project_id, "category": error.category, "summary": error.summary, "status": error.status, "created_at": error.created_at}

    @app.post("/v1/checkpoints", status_code=201, tags=["checkpoints"])
    def save_checkpoint(
        body: CheckpointCreate,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        require_project_access(db, actor, body.project_id, write=True)
        checkpoint = Checkpoint(**{**body.model_dump(), "summary": redact_payload(body.summary), "state": redact_payload(body.state)})
        db.add(checkpoint)
        db.add(AuditLog(project_id=body.project_id, agent_id=actor.agent_id, action="checkpoint.saved", outcome="allowed"))
        db.commit()
        db.refresh(checkpoint)
        return {"id": checkpoint.id, "project_id": checkpoint.project_id, "summary": checkpoint.summary, "created_at": checkpoint.created_at}

    @app.get("/v1/sessions/{session_id}/resume", tags=["sessions"])
    def resume_session(
        session_id: uuid.UUID,
        actor: Actor = Depends(require_actor),
        db: Session = Depends(get_session),
    ):
        session = db.get(AgentSession, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found.")
        require_project_access(db, actor, session.project_id)
        project = db.get(Project, session.project_id)
        tasks = list(db.scalars(select(Task).where(
            Task.project_id == session.project_id,
            Task.status.not_in(["completed", "failed", "cancelled"]),
        ).order_by(Task.updated_at.desc()).limit(50)))
        checkpoint = db.scalar(select(Checkpoint).where(
            Checkpoint.project_id == session.project_id,
        ).order_by(Checkpoint.created_at.desc()).limit(1))
        events = list(db.scalars(select(Event).where(
            Event.project_id == session.project_id,
        ).order_by(Event.occurred_at.desc()).limit(20)))
        return {
            "session": SessionRead.model_validate(session),
            "project": ProjectRead.model_validate(project),
            "unfinished_tasks": [TaskRead.model_validate(task) for task in tasks],
            "latest_checkpoint": checkpoint,
            "recent_events": [EventRead.model_validate(event) for event in events],
            "internal_model_state_restored": False,
        }

    return app


app = create_app()
