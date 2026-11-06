import json
import os
import uuid
from pathlib import Path

from memoryos_backend.workers.vault import _atomic_private_write, _memory_markdown


def test_markdown_projection_has_frontmatter_and_source_metadata(tmp_path: Path):
    project_id = str(uuid.uuid4())
    memory_id = str(uuid.uuid4())
    result = _memory_markdown({
        "id": memory_id,
        "project_id": project_id,
        "category": "procedural",
        "source": "verified-test",
        "source_event_id": None,
        "confidence": 1.0,
        "verified": True,
        "importance": 0.8,
        "created_at": "2026-11-06T10:00:00+00:00",
        "content": "Keep the API bound to localhost.",
    })
    assert result.startswith("---\nid: " + json.dumps(memory_id))
    assert "category: \"procedural\"" in result
    assert "verified: true" in result
    assert result.endswith("Keep the API bound to localhost.\n")


def test_projection_write_is_atomic_and_private(tmp_path: Path):
    path = tmp_path / "vault" / "projects" / "one" / "knowledge" / "memory.md"
    _atomic_private_write(path, "content\n")
    assert path.read_text() == "content\n"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert not path.with_name(path.name + ".tmp").exists()
