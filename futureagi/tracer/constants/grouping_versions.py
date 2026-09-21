"""Versions owned by the grouping preparation pipeline.

FEATURE_POLICY_VERSION identifies the feature-preparation recipe, not the
investigation report, snapshot transport contract, or full clustering algorithm.
The database work identity is (report, policy_version): retries reuse the same
job; a new policy permits a separate job for the same report. Changing this
constant does NOT enqueue historical reports or perform a backfill.

Version history
---------------
f6-minilm-features/v1:
    Initial F6 preparation recipe using statement ("semantics") and task views
    with all-MiniLM-L6-v2, 384-dimensional embeddings. Strict serving rejects
    silent truncation; the default path rejects oversized input rather than
    guessing missing content. This is a preparation-only implementation.

When to bump
------------
Bump when changing inputs/views, text construction, chunking/pooling rules, or
embedding model/revision/configuration so previous features cannot be assumed
compatible. Do not bump for comments, formatting, or equivalent refactoring.
Document each new recipe here; do not rewrite the meaning of an existing
version or update old job rows to make their features appear compatible.

Current limitation
------------------
This is a manually maintained recipe label, not a hash of model configuration.
Before executable job claims/completion are implemented, durable work must also
pin the exact immutable model configuration (or include its digest in identity).
The worker's FEATURE_VERSION in grouping/features.mjs currently declares the
same label independently; changes require coordinated compatibility review.
"""

from typing import Final

FEATURE_POLICY_VERSION: Final[str] = "f6-minilm-features/v1"
