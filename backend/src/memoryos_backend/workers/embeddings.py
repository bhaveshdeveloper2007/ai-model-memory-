from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..db import SessionLocal
from ..models import Memory, MemoryEmbedding
from ..retrieval import EmbeddingUnavailable, configured_provider
from ..settings import get_settings


def process_batch(*, limit: int = 8, session_factory=SessionLocal) -> int:
    settings = get_settings()
    provider = configured_provider()
    if provider is None:
        return 0
    processed = 0
    with session_factory() as db:
        memories = list(db.scalars(
            select(Memory)
            .where(
                Memory.deleted_at.is_(None),
                Memory.archived_at.is_(None),
                ~select(MemoryEmbedding.memory_id).where(
                    MemoryEmbedding.memory_id == Memory.id,
                    MemoryEmbedding.model_name == settings.embedding_model,
                ).exists(),
            )
            .order_by(Memory.created_at)
            .limit(limit)
        ))
        for memory in memories:
            try:
                vector = provider.embed(memory.content)
            except EmbeddingUnavailable:
                break
            statement = insert(MemoryEmbedding).values(
                memory_id=memory.id,
                model_name=settings.embedding_model,
                model_version=settings.embedding_model_version,
                embedding=vector,
            ).on_conflict_do_update(
                index_elements=[MemoryEmbedding.memory_id, MemoryEmbedding.model_name],
                set_={"model_version": settings.embedding_model_version, "embedding": vector, "created_at": datetime.now(timezone.utc)},
            )
            db.execute(statement)
            db.commit()
            processed += 1
    return processed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.once:
        raise SystemExit("Run with --once under the supervisor until continuous worker lifecycle is installed.")
    print(process_batch())


if __name__ == "__main__":
    main()
