import json
from io import BytesIO

import pytest

from memoryos_backend.retrieval import EmbeddingUnavailable, OllamaEmbeddingProvider
from memoryos_backend.settings import Settings


def make_settings(**overrides):
    values = {
        "database_url": "postgresql+psycopg:///memoryos_test",
        "embedding_model": "all-minilm",
        "embedding_model_version": "test-model-version",
        "embedding_dimensions": 384,
        "ollama_url": "http://127.0.0.1:11434",
    }
    values.update(overrides)
    return Settings(**values)


def test_embedding_provider_accepts_loopback_and_checks_dimensions(monkeypatch):
    provider = OllamaEmbeddingProvider(make_settings())
    class Response(BytesIO):
        def __enter__(self): return self
        def __exit__(self, *_): self.close()

    def fake_urlopen(request, timeout):
        sent = json.loads(request.data)
        assert sent["model"] == "all-minilm"
        assert timeout == 8
        return Response(json.dumps({"embeddings": [[0.25] * 384]}).encode())

    monkeypatch.setattr("memoryos_backend.retrieval.urlopen", fake_urlopen)
    assert len(provider.embed("project context")) == 384


def test_embedding_provider_fails_gracefully_without_local_service(monkeypatch):
    provider = OllamaEmbeddingProvider(make_settings())

    def unavailable(*_, **__):
        raise OSError("connection refused")

    monkeypatch.setattr("memoryos_backend.retrieval.urlopen", unavailable)
    with pytest.raises(EmbeddingUnavailable, match="local Ollama"):
        provider.embed("context")


def test_embedding_provider_rejects_remote_hosts():
    with pytest.raises(ValueError, match="loopback"):
        OllamaEmbeddingProvider(make_settings(ollama_url="https://example.com"))
