from __future__ import annotations

import os
from urllib.parse import urlparse

import pytest

# Tests are deliberately pinned to a dedicated database regardless of the shell's
# normal DATABASE_URL so a mistaken invocation cannot truncate development data.
test_url = os.environ.get("MEMORYOS_TEST_DATABASE_URL", "postgresql+psycopg:///memoryos_test")
database_name = urlparse(test_url.replace("+psycopg", "")).path.rstrip("/").split("/")[-1]
if database_name != "memoryos_test":
    raise RuntimeError("Tests require a database named memoryos_test; refusing to run against another database.")
os.environ["MEMORYOS_DATABASE_URL"] = test_url
os.environ.setdefault("MEMORYOS_API_TOKEN", "test-owner-token-not-for-production")

from sqlalchemy import text

from memoryos_backend.db import engine
from memoryos_backend.models import Base


@pytest.fixture
def clean_test_database():
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE " + ", ".join(f'"{name}"' for name in Base.metadata.tables) + " CASCADE"))
    yield


@pytest.fixture
def owner_headers():
    return {"Authorization": "Bearer test-owner-token-not-for-production"}
