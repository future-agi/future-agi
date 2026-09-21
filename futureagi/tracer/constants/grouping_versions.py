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

f6-minilm-features/v2:
    Uses the unchanged serving API. Contiguous UTF-8 chunks of at most 128
    bytes preserve every input character; nonblank chunk vectors are pooled
    by character length. This bounds each serving input but does not certify
    tokenizer coverage or a model-weights revision: both remain unknown.
    The operator serving-release label separates deployment cache entries;
    it is not evidence of model identity. Source fingerprints exclude only
    mutable report grouping_status, while full snapshot integrity still binds
    that status. Statement/task views and seeded F6 LSH are unchanged.

When to bump
------------
Bump when changing inputs/views, text construction, chunking/pooling rules, or
embedding model/revision/configuration so previous features cannot be assumed
compatible. Do not bump for comments, formatting, or equivalent refactoring.
Document each new recipe here; do not rewrite the meaning of an existing
version or update old job rows to make their features appear compatible.

Current limitation
------------------
This is a manually maintained recipe label, not a hash of model weights.
Durable feature rows bind the serving-release cache namespace, source/view
digests and dimension; they must not claim a verified immutable model revision.
The worker's FEATURE_VERSION in grouping/features.mjs currently declares the
same label independently; changes require coordinated compatibility review.
"""

from typing import Final

FEATURE_POLICY_VERSION: Final[str] = "f6-minilm-features/v2"

# Registry algorithm identity, separate from the input feature recipe. The
# matching worker policy pins sealed F6 retrieval/admission/repair/revisit and
# reconciliation settings plus the approved MiniLM representation. A change to
# grouping decisions requires a new algorithm identity and explicit migration;
# never relabel an existing registry as if it had used the new policy.
GROUPING_POLICY_VERSION: Final[str] = "f6-minilm/v1"

# Post-grouping impact assessment. Independent of F6 membership decisions.
# Bump for rubric, evidence selection, or model changes; old receipts retain
# their original meaning. No count-based severity floor in this policy.
SEVERITY_POLICY_VERSION: Final[str] = "feed-severity/v1"
