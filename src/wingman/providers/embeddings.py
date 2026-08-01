"""Embedding providers: text to semantic vectors (RFC-010).

Embedding is a model call with data egress: document text is sent to the
configured provider. It happens only on explicit 'wingman embed' — never as a
side effect of ingestion or search. Providers return unit-length vectors, so
cosine similarity is a plain dot product.

- voyage: Voyage AI's embeddings API (VOYAGE_API_KEY). Anthropic's
  recommended embeddings partner; called over plain HTTPS with no SDK.
- hashed: deterministic local feature-hashing — no network, no key. A
  bag-of-words floor for tests and offline use; real semantic quality
  comes from the voyage provider.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from typing import Literal, Protocol

_VOYAGE_ENDPOINT = "https://api.voyageai.com/v1/embeddings"
_VOYAGE_ENV_KEY = "VOYAGE_API_KEY"
_TIMEOUT_SECONDS = 60
# Voyage caps a request two ways: number of texts, and total tokens across
# the whole batch (320K for voyage-4). 64 essay-length documents can exceed
# the token cap, so batches are packed against BOTH limits: at most
# BATCH_SIZE texts and at most MAX_BATCH_CHARS characters per call
# (~250K tokens at a conservative 3.2 chars/token — real headroom under 320K).
BATCH_SIZE = 64
MAX_BATCH_CHARS = 800_000
# Per-text cap: stay well under the 32K-token context; ~4 chars/token makes
# 100K chars safe. Also the per-text term in the batch packing arithmetic.
TEXT_CHAR_LIMIT = 100_000

InputType = Literal["document", "query"]


class EmbeddingError(Exception):
    """An embedding call failed; the message says how to recover."""


class EmbeddingProvider(Protocol):
    provider_name: str
    model: str

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]: ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]


class VoyageEmbeddingProvider:
    """Voyage AI embeddings over plain HTTPS (no SDK dependency).

    'strict', when True, suppresses the 'os.environ' fallback below even
    when no 'api_key' was given — required for tenant isolation under a
    shared multi-tenant process (RFC-048): a tenant with no key configured
    must fail loud, never silently pick up whatever VOYAGE_API_KEY happens
    to be set in the shared process's environment (which the constructor's
    'api_key' parameter alone does not prevent, since 'embed' otherwise
    checks 'os.environ' itself on every call).
    """

    provider_name = "voyage"

    def __init__(self, model: str, api_key: str | None = None, strict: bool = False) -> None:
        self.model = model
        self._api_key = api_key
        self._strict = strict

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        if self._strict:
            api_key = self._api_key or ""
            if not api_key:
                raise EmbeddingError(
                    f"no {_VOYAGE_ENV_KEY} configured for this workspace. "
                    "Set it via Manage → Keys."
                )
        else:
            api_key = self._api_key or os.environ.get(_VOYAGE_ENV_KEY, "").strip()
            if not api_key:
                raise EmbeddingError(
                    f"{_VOYAGE_ENV_KEY} is not set. Export it (see https://www.voyageai.com) "
                    "or switch [models.embed_semantic] to provider = 'hashed' in models.toml."
                )
        payload = json.dumps(
            {
                "input": [text[:TEXT_CHAR_LIMIT] for text in texts],
                "model": self.model,
                "input_type": input_type,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            _VOYAGE_ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise EmbeddingError(
                f"Voyage API returned {exc.code} for model {self.model!r}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise EmbeddingError(f"could not reach the Voyage API ({exc})") from exc
        try:
            rows = sorted(body["data"], key=lambda row: int(row["index"]))
            vectors = [[float(value) for value in row["embedding"]] for row in rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"Voyage API response had an unexpected shape ({exc})") from exc
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"Voyage API returned {len(vectors)} embeddings for {len(texts)} texts"
            )
        return vectors


class HashedEmbeddingProvider:
    """Deterministic local embeddings via feature hashing (offline floor).

    Each word is hashed into one of `dim` buckets; the counts are normalized
    to a unit vector. Overlapping vocabularies land near each other, which is
    enough for tests and a keyword-level similarity signal without any
    network egress.
    """

    provider_name = "hashed"

    def __init__(self, model: str = "hashed-256", dim: int = 256) -> None:
        self.model = model
        self._dim = dim

    def embed(self, texts: list[str], input_type: InputType) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * self._dim
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.sha256(word.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % self._dim
                vector[bucket] += 1.0
            vectors.append(_normalize(vector))
        return vectors
