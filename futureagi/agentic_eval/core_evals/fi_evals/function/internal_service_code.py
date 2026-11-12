"""Code evals cannot reach the platform's internal services.

The code executor's egress guard denies private and cluster addresses, so a
code eval that calls model serving never gets an answer. The built-in
Embedding Similarity and Semantic List Contains evals were such code until
they moved into the backend (SYSTEM_EVALS_VERSION 29), and a user's copy of
either still is: it caught the connection error and scored 0.0, which reads
as a real result. Such code is refused before it runs, with an error that
names the built-in eval to use instead. The user's eval is left as it is.
"""

from __future__ import annotations

import re

from agentic_eval.core_evals.fi_utils.exceptions import CodeEvalInternalServiceError

INTERNAL_SERVICE_MESSAGE = (
    "this eval calls an internal service that custom code can't reach; "
    "use the built-in {name} eval"
)

# The keyword the engine handed only to those two built-ins, and the serving
# host their code fell back to.
_SERVING_REFERENCES = (
    re.compile(r"\b_model_serving_url\b"),
    re.compile(r"https?://serving(?=[:/'\"\s]|$)"),
)
# Words only the Semantic List Contains code uses.
_SEMANTIC_LIST_CONTAINS_WORDS = ("similarity_threshold", "phrases")


def _calls_model_serving(code: str) -> bool:
    if any(reference.search(code) for reference in _SERVING_REFERENCES):
        return True
    from agentic_eval.core.embeddings.serving_client import serving_base_url

    configured = serving_base_url()
    return bool(configured) and configured in code


def builtin_for_internal_service_code(code: object) -> str | None:
    """The built-in eval that replaces ``code``, or None when the code calls no
    internal service."""
    if not isinstance(code, str) or not _calls_model_serving(code):
        return None
    if any(word in code for word in _SEMANTIC_LIST_CONTAINS_WORDS):
        return "semantic_list_contains"
    return "embedding_similarity"


def refuse_internal_service_code(code: object) -> None:
    """Raise ``CodeEvalInternalServiceError`` for code that calls an internal
    service; return for any other code."""
    name = builtin_for_internal_service_code(code)
    if name is not None:
        raise CodeEvalInternalServiceError(INTERNAL_SERVICE_MESSAGE.format(name=name))
