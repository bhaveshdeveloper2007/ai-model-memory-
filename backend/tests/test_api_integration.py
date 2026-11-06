from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from memoryos_backend.api import create_app
from memoryos_backend.db import SessionLocal
from memoryos_backend.models import Event, Memory, SyncOutbox
from memoryos_backend.settings import get_settings
from memoryos_backend.workers.vault import process_batch


def make_client() -> TestClient:
    get_settings.cache_clear()
    return TestClient(create_app())


def create_project(client: TestClient, headers: dict, identity: str) -> str:
    response = client.post("/v1/projects", headers=headers, json={
        "identity": identity,
        "name": identity.rsplit(":", 1)[-1],
        "root_path": "/tmp/" + identity.rsplit(":", 1)[-1],
    })
    assert response.status_code == 201
    return response.json()["id"]


def test_owner_auth_event_idempotency_and_secret_redaction(owner_headers, clean_test_database):
    with make_client() as client:
        assert client.get("/v1/projects").status_code == 401
        project_id = create_project(client, owner_headers, "test:events")
        event_id = str(uuid.uuid4())
        body = {
            "event_id": event_id,
            "project_id": project_id,
            "event_type": "test.tool.completed",
            "occurred_at": "2026-11-06T10:00:00Z",
            "agent_name": "codex-test",
            "payload": {"prompt": "do not store", "api_key": "sensitive-value", "tool_name": "pytest"},
        }
        first = client.post("/v1/events", headers=owner_headers, json=body)
        second = client.post("/v1/events", headers=owner_headers, json=body)
        assert first.status_code == 202 and first.json()["duplicate"] is False
        assert second.status_code == 202 and second.json()["duplicate"] is True
        changed = {**body, "payload": {"tool_name": "changed"}}
        assert client.post("/v1/events", headers=owner_headers, json=changed).status_code == 409
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Event)) == 1
            stored = db.get(Event, uuid.UUID(event_id))
            assert stored.payload == {"prompt": "[REDACTED]", "api_key": "[REDACTED]", "tool_name": "pytest"}


def test_agent_is_limited_to_granted_project_and_search_is_project_scoped(owner_headers, clean_test_database):
    with make_client() as client:
        project_a = create_project(client, owner_headers, "test:scope-a")
        project_b = create_project(client, owner_headers, "test:scope-b")
        created = client.post("/v1/agents", headers=owner_headers, json={"name": "review-agent", "kind": "claude-code"})
        assert created.status_code == 201
        agent = created.json()
        agent_headers = {"Authorization": "Bearer " + agent["token"]}
        grant = client.put(f"/v1/projects/{project_a}/agents/{agent['id']}", headers=owner_headers)
        assert grant.status_code == 204
        assert client.get(f"/v1/projects/{project_a}", headers=agent_headers).status_code == 200
        assert client.get(f"/v1/projects/{project_b}", headers=agent_headers).status_code == 403
        created_memory = client.post("/v1/memories", headers=agent_headers, json={
            "project_id": project_a,
            "category": "procedural",
            "source": "verified-test",
            "content": "Run the schema migration before starting the API.",
            "confidence": 1,
            "verified": True,
        })
        assert created_memory.status_code == 201
        results = client.get(f"/v1/projects/{project_a}/memories/search", headers=agent_headers, params={"q": "schema migration"})
        assert results.status_code == 200 and len(results.json()) == 1
        assert results.json()[0]["memory"]["source"] == "verified-test"
        assert client.get(f"/v1/projects/{project_b}/memories/search", headers=agent_headers, params={"q": "schema"}).status_code == 403
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Memory).where(Memory.project_id == uuid.UUID(project_b))) == 0


def test_task_session_checkpoint_error_and_decision_endpoints(owner_headers, clean_test_database):
    with make_client() as client:
        project_id = create_project(client, owner_headers, "test:lifecycle")
        session = client.post("/v1/sessions", headers=owner_headers, json={"project_id": project_id, "external_session_id": "session-1"})
        assert session.status_code == 201
        task = client.post("/v1/tasks", headers=owner_headers, json={"project_id": project_id, "title": "Implement route", "status": "in_progress"})
        assert task.status_code == 201
        updated = client.patch(f"/v1/tasks/{task.json()['id']}", headers=owner_headers, json={"status": "completed"})
        assert updated.status_code == 200 and updated.json()["status"] == "completed"
        decision = client.post("/v1/decisions", headers=owner_headers, json={"project_id": project_id, "summary": "Use PostgreSQL as the system of record."})
        error = client.post("/v1/errors", headers=owner_headers, json={"project_id": project_id, "summary": "Database was unavailable", "details": {"password": "private-value"}})
        checkpoint = client.post("/v1/checkpoints", headers=owner_headers, json={"project_id": project_id, "summary": "Resume API work", "state": {"next": "add tests"}})
        assert decision.status_code == 201 and error.status_code == 201 and checkpoint.status_code == 201
        assert client.get(f"/v1/sessions/{session.json()['id']}/resume", headers=owner_headers).status_code == 200


def test_task_dependencies_reject_cycles_and_cross_project_links(owner_headers, clean_test_database):
    with make_client() as client:
        project_a = create_project(client, owner_headers, "test:dependencies-a")
        project_b = create_project(client, owner_headers, "test:dependencies-b")
        first = client.post("/v1/tasks", headers=owner_headers, json={"project_id": project_a, "title": "First"}).json()
        second = client.post("/v1/tasks", headers=owner_headers, json={"project_id": project_a, "title": "Second"}).json()
        other = client.post("/v1/tasks", headers=owner_headers, json={"project_id": project_b, "title": "Other project"}).json()
        assert client.post(f"/v1/tasks/{first['id']}/dependencies", headers=owner_headers, json={"depends_on_id": second["id"]}).status_code == 201
        assert client.post(f"/v1/tasks/{second['id']}/dependencies", headers=owner_headers, json={"depends_on_id": first["id"]}).status_code == 409
        assert client.post(f"/v1/tasks/{first['id']}/dependencies", headers=owner_headers, json={"depends_on_id": other["id"]}).status_code == 422


def test_agent_leases_are_advisory_expiring_and_agent_owned(owner_headers, clean_test_database):
    with make_client() as client:
        project_id = create_project(client, owner_headers, "test:leases")
        created = client.post("/v1/agents", headers=owner_headers, json={"name": "lease-agent", "kind": "generic"}).json()
        agent_headers = {"Authorization": "Bearer " + created["token"]}
        assert client.put(f"/v1/projects/{project_id}/agents/{created['id']}", headers=owner_headers).status_code == 204
        body = {"project_id": project_id, "resource_key": "src/main.py", "ttl_seconds": 120}
        assert client.post("/v1/leases", headers=owner_headers, json=body).status_code == 200
        assert client.post("/v1/leases", headers=agent_headers, json=body).status_code == 409
        assert client.delete("/v1/leases", headers=agent_headers, json=body).status_code == 404
        assert client.delete("/v1/leases", headers=owner_headers, json=body).status_code == 204
        assert client.post("/v1/leases", headers=agent_headers, json=body).status_code == 200
        assert client.post("/v1/leases/heartbeat", headers=agent_headers, json=body).status_code == 200
        assert client.delete("/v1/leases", headers=agent_headers, json=body).status_code == 204


def test_memory_outbox_projects_private_markdown(owner_headers, tmp_path: Path, clean_test_database):
    with make_client() as client:
        project_id = create_project(client, owner_headers, "test:vault")
        response = client.post("/v1/memories", headers=owner_headers, json={
            "project_id": project_id,
            "category": "project",
            "source": "codex-test",
            "content": "Durable memory projected to a local Markdown vault.",
            "importance": 0.7,
        })
        assert response.status_code == 201
        assert process_batch(vault_root=tmp_path / "vault") == 1
        memory_id = response.json()["id"]
        markdown = tmp_path / "vault" / "projects" / project_id / "knowledge" / f"{memory_id}.md"
        assert markdown.exists()
        assert "category: \"project\"" in markdown.read_text()
        assert markdown.stat().st_mode & 0o777 == 0o600
        with SessionLocal() as db:
            outbox = db.scalar(select(SyncOutbox))
            assert outbox.status == "completed"


def test_memory_update_search_and_safe_delete(owner_headers, tmp_path: Path, clean_test_database):
    with make_client() as client:
        project_id = create_project(client, owner_headers, "test:memory-lifecycle")
        response = client.post("/v1/memories", headers=owner_headers, json={
            "project_id": project_id,
            "category": "semantic",
            "source": "test",
            "content": "The service runs on PostgreSQL.",
        })
        memory_id = response.json()["id"]
        assert client.get(f"/v1/memories/{memory_id}", headers=owner_headers).status_code == 200
        updated = client.patch(f"/v1/memories/{memory_id}", headers=owner_headers, json={"verified": True, "content": "The service uses PostgreSQL locally."})
        assert updated.status_code == 200 and updated.json()["verified"] is True
        assert len(client.get(f"/v1/projects/{project_id}/memories/search", headers=owner_headers, params={"q": "uses PostgreSQL"}).json()) == 1
        assert client.delete(f"/v1/memories/{memory_id}", headers=owner_headers).status_code == 204
        assert client.get(f"/v1/memories/{memory_id}", headers=owner_headers).status_code == 404
        assert client.get(f"/v1/projects/{project_id}/memories/search", headers=owner_headers, params={"q": "PostgreSQL"}).json() == []
        assert process_batch(vault_root=tmp_path / "vault") == 3
        markdown = tmp_path / "vault" / "projects" / project_id / "knowledge" / f"{memory_id}.md"
        assert not markdown.exists()


def test_health_check_reports_postgres_and_pgvector(clean_test_database):
    with make_client() as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["database"] == "connected"
        assert response.json()["vector_extension"] is True
