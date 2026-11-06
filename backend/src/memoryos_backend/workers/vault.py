from __future__ import annotations

import argparse
import json
import os
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from ..db import SessionLocal
from ..models import AuditLog, SyncOutbox
from ..settings import get_settings


def _memory_markdown(payload: dict) -> str:
    frontmatter = {
        "id": payload["id"],
        "project_id": payload["project_id"],
        "category": payload["category"],
        "source": payload["source"],
        "source_event_id": payload.get("source_event_id"),
        "confidence": payload["confidence"],
        "verified": payload["verified"],
        "importance": payload["importance"],
        "created_at": payload["created_at"],
    }
    return "---\n" + "".join(f"{key}: {json.dumps(value, ensure_ascii=False)}\n" for key, value in frontmatter.items()) + "---\n\n" + payload["content"].rstrip() + "\n"


def _atomic_private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_projection(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def process_batch(*, vault_root: Path | None = None, limit: int = 20, session_factory=SessionLocal) -> int:
    root = (vault_root or get_settings().data_dir / "vault").resolve()
    processed = 0
    with session_factory() as db:
        with db.begin():
            jobs = list(db.scalars(
                select(SyncOutbox)
                .where(SyncOutbox.status == "pending", SyncOutbox.available_at <= datetime.now(timezone.utc))
                .order_by(SyncOutbox.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            ))
            for job in jobs:
                try:
                    if job.kind not in {"memory_markdown_upsert", "memory_markdown_delete"}:
                        raise ValueError("Unsupported outbox operation.")
                    project_id = uuid.UUID(job.payload["project_id"])
                    memory_id = uuid.UUID(job.payload["id"])
                    target = root / "projects" / str(project_id) / "knowledge" / f"{memory_id}.md"
                    if root not in target.resolve().parents:
                        raise ValueError("Vault path escapes its configured root.")
                    if job.kind == "memory_markdown_delete":
                        _remove_projection(target)
                    else:
                        _atomic_private_write(target, _memory_markdown(job.payload))
                    job.status = "completed"
                    job.completed_at = datetime.now(timezone.utc)
                    job.last_error = None
                    processed += 1
                except Exception as exc:
                    job.attempts += 1
                    job.last_error = str(exc)[:1000]
                    if job.attempts >= 8:
                        job.status = "failed"
                        db.add(AuditLog(action="vault.projection.failed", outcome="failed", details={"outbox_id": str(job.id), "kind": job.kind}))
                    else:
                        delay = min(3600, 2 ** job.attempts)
                        job.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    return processed


def run_worker() -> None:
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    delay = get_settings().outbox_poll_seconds
    while not stopping:
        process_batch()
        time.sleep(max(0.5, min(delay, 60)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        print(json.dumps({"processed": process_batch()}))
    else:
        run_worker()


if __name__ == "__main__":
    main()
