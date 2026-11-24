"""Primary-indexed F6 LSH candidate narrowing over ``f6_grouping_buckets``.

Small and medium projects stay on the single-statement exact cosine scan in
``GroupingFeatureStore.candidate_occurrences``. When a project's eligible
candidate count exceeds ``LSH_EXACT_FALLBACK_THRESHOLD`` (for example,
multi-million-trace tenants), this module probes exact + 1-bit Hamming
neighbor LSH buckets in ClickHouse first so the downstream ``IN %(candidates)s``
exact cosine scan stays well under ``MAX_QUERY_BYTES`` (16 MB).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.grouping.feature_store import BUCKET_TABLE, MAX_QUERY_BYTES

LSH_EXACT_FALLBACK_THRESHOLD = 2000
MAX_LSH_CANDIDATES = 20000
BUCKET_KEY_RE = re.compile(r"^(semantics|task):([0-7]):(\d{1,3})$")


def expand_lsh_probe_keys(view: str, bucket_keys: Sequence[object]) -> list[str]:
    """Return exact + 1-bit Hamming neighbor LSH bucket keys for multi-probe recall."""
    probes: set[str] = set()
    for raw in bucket_keys:
        if not isinstance(raw, str):
            continue
        match = BUCKET_KEY_RE.fullmatch(raw)
        if not match or match.group(1) != view:
            continue
        table = int(match.group(2))
        sig = int(match.group(3))
        if not 0 <= sig <= 255:
            continue
        probes.add(f"{view}:{table}:{sig}")
        for bit in range(8):
            probes.add(f"{view}:{table}:{sig ^ (1 << bit)}")
    return sorted(probes)


def lsh_bucket_sql() -> str:
    """Primary-indexed LSH candidate narrowing ranked by multi-table collisions."""
    return (
        "SELECT feature_digest\n"
        f"FROM {BUCKET_TABLE}\n"
        "WHERE organization_id = %(org)s AND project_id = %(project)s\n"
        "  AND view = %(view)s AND bucket_key IN %(buckets)s\n"
        "GROUP BY feature_digest\n"
        "ORDER BY count() DESC, feature_digest ASC\n"
        "LIMIT %(bucket_limit)s"
    )


def narrow_candidates_with_lsh(
    *,
    client: ClickHouseClient,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    pending: Sequence[Mapping[str, Any]],
    candidates: Sequence[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Narrow oversized per-view candidate lists using ``f6_grouping_buckets``.

    For any view with ``<= LSH_EXACT_FALLBACK_THRESHOLD`` eligible candidates,
    returns all eligible candidates unchanged (100% exact). For larger views,
    probes ``f6_grouping_buckets`` using exact + 1-bit Hamming neighbors and
    intersects with the caller's eligible candidates, keeping at most
    ``MAX_LSH_CANDIDATES`` highest-collision candidates per view.
    """
    candidates_by_view: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        candidates_by_view[str(item["view"])].append(item)

    probes_by_view: dict[str, set[str]] = defaultdict(set)
    for row in pending:
        view = str(row["view"])
        raw_buckets = row.get("bucket_keys")
        if isinstance(raw_buckets, (list, tuple)):
            probes_by_view[view].update(expand_lsh_probe_keys(view, raw_buckets))

    narrowed_all: list[dict[str, Any]] = []
    for view, view_candidates in sorted(candidates_by_view.items()):
        probe_keys = sorted(probes_by_view.get(view, ()))
        if len(view_candidates) <= LSH_EXACT_FALLBACK_THRESHOLD or not probe_keys:
            narrowed_all.extend(view_candidates)
            continue

        bucket_rows = client.execute_read(
            lsh_bucket_sql(),
            {
                "org": organization_id,
                "project": project_id,
                "view": view,
                "buckets": tuple(probe_keys),
                "bucket_limit": MAX_LSH_CANDIDATES * 2,
            },
            settings={"max_query_size": MAX_QUERY_BYTES},
        )[0]
        by_digest: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in view_candidates:
            by_digest[str(item["feature_digest"])].append(item)

        matched: list[dict[str, Any]] = []
        seen_digests: set[str] = set()
        for row in bucket_rows:
            if not row:
                continue
            digest = str(row[0])
            if digest in seen_digests or digest not in by_digest:
                continue
            seen_digests.add(digest)
            matched.extend(by_digest[digest])
            if len(seen_digests) >= MAX_LSH_CANDIDATES:
                break

        if len(matched) >= limit or len(view_candidates) > MAX_LSH_CANDIDATES:
            narrowed_all.extend(matched)
        else:
            narrowed_all.extend(view_candidates)

    return narrowed_all
