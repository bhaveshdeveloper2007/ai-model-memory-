from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .settings import Settings, get_settings


class EmbeddingUnavailable(RuntimeError):
    pass


class OllamaEmbeddingProvider:
    """Local-only Ollama embedding adapter; it never accepts a non-loopback URL."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        parsed = urlsplit(self.settings.ollama_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ollama embeddings must use a loopback URL.")
        self.endpoint = self.settings.ollama_url.rstrip("/") + "/api/embed"

    def embed(self, content: str) -> list[float]:
        if not self.settings.embedding_model:
            raise EmbeddingUnavailable("No local embedding model is configured.")
        request = Request(
            self.endpoint,
            data=json.dumps({"model": self.settings.embedding_model, "input": content[:4000]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=8) as response:
                body = json.loads(response.read())
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise EmbeddingUnavailable("The configured local Ollama embedding model is unavailable.") from None
        vectors = body.get("embeddings")
        if not isinstance(vectors, list) or not vectors or not isinstance(vectors[0], list):
            raise EmbeddingUnavailable("Local embedding provider returned an invalid response.")
        vector = [float(value) for value in vectors[0]]
        if len(vector) != self.settings.embedding_dimensions:
            raise EmbeddingUnavailable(
                f"Local embedding dimensions do not match the configured {self.settings.embedding_dimensions}-dimension index."
            )
        return vector


def configured_provider() -> OllamaEmbeddingProvider | None:
    settings = get_settings()
    if not settings.embedding_model:
        return None
    return OllamaEmbeddingProvider(settings)
