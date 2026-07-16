"""S7 / A5: `resolve_session_fields` injects a project_id clause and the clause
engages the PrimaryKey index.

Moved out of `test_baselines.py` (strict-xfail dropped) once A5 (project scoping) landed
the `project_id` kwarg. The `trace_sessions` table at CI scale holds only ~60
rows (5 target + 50 noise + a handful of other projects) — all in ONE 8192-row
ClickHouse granule. Because granule-skip requires crossing a granule boundary,
read_rows cannot discriminate scoped vs. unscoped at this table size (both read
the same single granule). The S4 read_rows approach works for spans (69k rows,
many granules) but NOT here.

Instead, S7 verifies A5 via two complementary checks:

  (a) Clause injection — the test captures the REAL SQL that `resolve_session_fields`
      emits (tagged with a unique `log_comment` so `system.query_log` can identify
      it) and asserts that SQL contains `project_id = `.  This check goes RED if
      someone removes the `project_clause` append from the production function.

  (b) Structural pruning — `EXPLAIN indexes=1` on the captured emitted SQL must
      show `project_id` in the PrimaryKey Condition (not `Condition: true` which
      means full-table scan).  Scale-independent: the planner recognises the
      sort-key prefix regardless of how many granules are actually skipped at
      runtime.
"""

from __future__ import annotations

import re

import pytest

from tests.stress.ch_asserts import _client

pytestmark = pytest.mark.stress


def _pk_condition_line(client, query: str, params: dict) -> str:
    """Return the PrimaryKey Condition line from EXPLAIN indexes=1 output.

    Locates the `PrimaryKey` section in the EXPLAIN tree and returns the first
    `Condition:` line within it. Returns ``""`` if the section is absent (query
    hits no MergeTree table or the CH version omits it).
    """
    rows = client.query(f"EXPLAIN indexes=1 {query}", parameters=params).result_rows
    in_pk = False
    for row in rows:
        line = row[0].strip()
        if line == "PrimaryKey":
            in_pk = True
            continue
        if in_pk and line.startswith("Condition:"):
            return line
        if in_pk and line.startswith("Keys:"):
            continue  # Condition follows Keys — keep scanning.
        if in_pk and not any(
            line.startswith(p)
            for p in ("Keys:", "Condition:", "project_id", "trace_session_id")
        ):
            break  # Left PrimaryKey block without finding Condition.
    return ""


@pytest.mark.django_db
def test_s7_session_resolve_prunes_to_project(stress_dataset):
    """A5: resolve_session_fields(project_id=X) injects project_id and prunes.

    Guards the PRODUCTION function — not a hand-crafted query — by:
      (a) Capturing the real SQL via system.query_log (log_comment tag) and
          asserting it contains 'project_id = '.  Goes RED if project_clause
          is removed from resolve_session_fields.
      (b) Running EXPLAIN indexes=1 on the captured emitted SQL to confirm
          ClickHouse engages the sort-key prefix (not Condition: true).
    """
    from tracer.services.clickhouse.v2.query_settings import ch_query_settings
    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    manifest = stress_dataset.target
    setup_client = _client()
    try:
        session_uuids = [
            r[0]
            for r in setup_client.query(
                "SELECT toString(trace_session_id) FROM trace_sessions FINAL "
                "WHERE project_id = %(p)s AND is_deleted = 0",
                parameters={"p": manifest.project_id},
            ).result_rows
        ]
        total_sessions = setup_client.query(
            "SELECT count() FROM trace_sessions FINAL WHERE is_deleted = 0"
        ).result_rows[0][0]
    finally:
        setup_client.close()

    assert session_uuids, "No seeded sessions for target project — seed may have failed"
    # Two-project contrast: the whole table has sessions for other projects
    # too, so there is something to prune.
    assert total_sessions > len(session_uuids), (
        "All sessions belong to one project — cross-project noise needed to validate pruning"
    )

    # Drive the PRODUCTION function under a unique log_comment so we can
    # recover the actual SQL it emitted from system.query_log.
    tag = "stress:A5:session-scoped"
    with ch_query_settings(log_comment=tag):
        resolve_session_fields(session_uuids, project_id=manifest.project_id)

    # Flush logs and extract the emitted query text.
    log_client = _client()
    try:
        log_client.command("SYSTEM FLUSH LOGS")
        log_rows = log_client.query(
            "SELECT query FROM system.query_log "
            "WHERE log_comment = %(t)s AND type = 'QueryFinish' "
            "ORDER BY event_time_microseconds DESC LIMIT 5",
            parameters={"t": tag},
        ).result_rows
    finally:
        log_client.close()

    assert log_rows, (
        f"No QueryFinish entries in system.query_log for log_comment={tag!r}. "
        "Ensure CH query_log is enabled (log_queries=1)."
    )

    # Find the JOIN query that reads trace_sessions (the main fetch in
    # resolve_session_fields; ignore the PG TraceSessionOverlay queries).
    emitted_sql: str | None = None
    for (q,) in log_rows:
        if "trace_sessions" in q:
            emitted_sql = q
            break

    assert emitted_sql is not None, (
        f"No query touching trace_sessions found under log_comment={tag!r}. "
        f"Logged queries: {[r[0][:150] for r in log_rows]}"
    )

    # (a) The production function MUST have injected the project_id clause.
    assert "project_id = " in emitted_sql or "project_id=" in emitted_sql, (
        f"resolve_session_fields did NOT emit a project_id clause.\n"
        f"Emitted SQL (first 600 chars):\n{emitted_sql[:600]}\n"
        "Did someone remove the project_clause append from resolve_session_fields?"
    )

    # (b) EXPLAIN on the real emitted SQL must engage the PrimaryKey index.
    # The captured query has literal values inlined (clickhouse-connect
    # substitutes %(name)s client-side before sending), so no parameters needed.
    # Strip the trailing FORMAT clause clickhouse-connect appends — EXPLAIN
    # rejects a query that ends in FORMAT.
    explain_sql = re.sub(r"\s+FORMAT\s+\w+\s*;?\s*$", "", emitted_sql, flags=re.I)
    explain_client = _client()
    try:
        cond = _pk_condition_line(explain_client, explain_sql, {})
    finally:
        explain_client.close()

    assert cond, (
        "EXPLAIN produced no PrimaryKey Condition for the emitted SQL — "
        "check the query shape or the CH version"
    )
    assert "true" not in cond.lower(), (
        f"PrimaryKey Condition is 'Condition: true' (no key engagement); "
        f"got {cond!r}. project_id clause present but not a recognised sort-key prefix?"
    )
    assert "project_id" in cond, (
        f"Expected 'project_id' in PrimaryKey Condition; got {cond!r}"
    )

    # Functional smoke: the actual function resolves all target sessions.
    resolved = resolve_session_fields(session_uuids, project_id=manifest.project_id)
    assert len(resolved) == len(session_uuids), (
        f"resolve_session_fields returned {len(resolved)} records, "
        f"expected {len(session_uuids)}"
    )
    for sid in session_uuids:
        assert sid in resolved, f"Session {sid} missing from resolved result"
        rec = resolved[sid]
        assert "external_session_id" in rec
        assert "first_seen" in rec
        assert "project_id" in rec
        assert rec["project_id"] == manifest.project_id, (
            f"Wrong project_id in resolved record: {rec['project_id']!r}"
        )
