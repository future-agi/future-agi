"""Shared parity helpers for the legacy-to-replicated ClickHouse migration commands."""
from __future__ import annotations

import re
import time
from enum import Enum

import structlog
from django.core.management.base import CommandError

logger = structlog.get_logger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OneShotDecision(Enum):
    """Outcome of the one-shot append-only guard for a single table."""

    COPY = "copy"
    NOOP = "noop"
    REFUSE = "refuse"


def one_shot_decision(
    before_counts: dict[str, int],
    expected_replicas: int,
    source_count: int,
) -> OneShotDecision:
    """Decide copy/noop/refuse for an append-only one-shot table.

    A missing replica counts as not-yet-converged. Dry-run callers pad
    via ``simulated_post_ensure_counts`` first so the sim matches the
    real run's post-``ensure_target`` view.
    """
    complete = len(before_counts) == expected_replicas
    if complete and all(c == 0 for c in before_counts.values()):
        return OneShotDecision.COPY
    if (
        complete
        and source_count > 0
        and all(c >= source_count for c in before_counts.values())
    ):
        return OneShotDecision.NOOP
    return OneShotDecision.REFUSE


def simulated_post_ensure_counts(
    before_counts: dict[str, int],
    expected_replicas: int,
) -> dict[str, int]:
    """Pad missing replicas as 0 to model post-``ensure_target ON CLUSTER`` state."""
    padded = dict(before_counts)
    while len(padded) < expected_replicas:
        padded[f"__pending_{len(padded)}__"] = 0
    return padded


def require_identifier(value: str, flag: str) -> str:
    """Reject anything that could smuggle SQL through a CLI flag."""
    if not _IDENTIFIER_RE.fullmatch(value):
        raise CommandError(
            f"{flag} {value!r} is not a valid ClickHouse identifier. "
            "Allowed: letters, digits, underscores; must not start with a digit."
        )
    return value


def expected_replica_count(client, cluster: str) -> int:
    """Number of hosts the cluster spans; used to gate the parity result."""
    rows = client.execute(
        "SELECT count() FROM system.clusters WHERE cluster = %(c)s",
        {"c": cluster},
    )
    return rows[0][0] if rows else 0


def per_replica_counts(
    client,
    database: str,
    table: str,
    cluster: str,
) -> dict[str, int]:
    """Per-replica row count via ``system.tables.total_rows``.

    Reads ``system.tables`` (one row per replica that holds the table,
    including empty ones) rather than ``count() GROUP BY hostName()`` on
    the target itself; the latter drops empty replicas from the result.
    """
    rows = client.execute(
        f"SELECT hostName(), total_rows FROM clusterAllReplicas("
        f"'{cluster}', system.tables) "
        f"WHERE database = %(d)s AND name = %(t)s",
        {"d": database, "t": table},
    )
    return {host: int(cnt or 0) for host, cnt in rows}


def poll_replica_parity(
    client,
    *,
    database: str,
    table: str,
    cluster: str,
    expected: int,
    expected_replicas: int,
    max_wait_sec: float = 30.0,
    poll_interval: float = 2.0,
) -> tuple[dict[str, int], bool]:
    """Poll until every expected replica reports ``>= expected`` rows.

    Convergence requires ``len(counts) >= expected_replicas`` AND each
    entry ``>= expected``: an absent replica counts as not-yet-converged.
    """
    deadline = time.monotonic() + max_wait_sec
    counts: dict[str, int] = {}
    while True:
        counts = per_replica_counts(client, database, table, cluster)
        converged = (
            len(counts) >= expected_replicas
            and bool(counts)
            and all(c >= expected for c in counts.values())
        )
        if converged:
            return counts, True
        if time.monotonic() >= deadline:
            logger.warning(
                "migrate_parity_wait_timed_out",
                target=f"{database}.{table}",
                expected=expected,
                expected_replicas=expected_replicas,
                per_replica_counts=counts,
                max_wait_sec=max_wait_sec,
            )
            return counts, False
        time.sleep(poll_interval)


def shared_columns(
    client,
    source_db: str,
    target_db: str,
    table: str,
) -> tuple[list[str], list[str]]:
    """Return ``(shared_in_target_order, source_only)`` for a name-aligned copy."""
    def cols(db: str) -> list[str]:
        return [
            r[0]
            for r in client.execute(
                "SELECT name FROM system.columns "
                "WHERE database = %(d)s AND table = %(t)s ORDER BY position",
                {"d": db, "t": table},
            )
        ]
    src = cols(source_db)
    tgt = cols(target_db)
    src_set, tgt_set = set(src), set(tgt)
    shared = [c for c in tgt if c in src_set]
    source_only = [c for c in src if c not in tgt_set]
    return shared, source_only
