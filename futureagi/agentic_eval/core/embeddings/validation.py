"""Shape checks for serving embeddings, without changing valid vector values.

ValueError describes a malformed response, not an unavailable service. The
embedding evaluators wrap it in their existing EmbeddingError so surfaces
store an error rather than a score. Messages never include response contents.
"""

from __future__ import annotations

import math
from typing import Any


def validate_embedding_batch(embeddings: Any, expected_count: int) -> None:
    """Require one non-empty, finite numeric vector per input and one dimension.

    All-zero vectors are valid: their documented cosine score remains 0.0.
    Tuples are accepted for in-process embedders as well as JSON's lists.
    """
    if not isinstance(embeddings, list | tuple) or len(embeddings) != expected_count:
        raise ValueError(
            "Invalid embedding response: vector count does not match input count."
        )
    dimension = None
    for vector in embeddings:
        if not isinstance(vector, list | tuple) or not vector:
            raise ValueError(
                "Invalid embedding response: vectors must be non-empty lists."
            )
        if dimension is None:
            dimension = len(vector)
        elif len(vector) != dimension:
            raise ValueError(
                "Invalid embedding response: vectors must have the same dimension."
            )
        for value in vector:
            numeric = isinstance(value, int | float) and not isinstance(value, bool)
            try:
                finite = numeric and math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(
                    "Invalid embedding response: vector entries must be finite numbers."
                )


def parse_embedding_response(
    body: Any, expected_count: int, *, allow_flat: bool = False
) -> list[list[float]]:
    """Validate before a single-input client unwraps (and could discard) vectors."""
    if not isinstance(body, dict):
        raise ValueError(
            "Invalid embedding response: expected an object with embeddings."
        )
    embeddings = body.get("embeddings")
    if (
        allow_flat
        and isinstance(embeddings, list)
        and embeddings
        and not isinstance(embeddings[0], list)
    ):
        embeddings = [embeddings]
    validate_embedding_batch(embeddings, expected_count)
    return embeddings
