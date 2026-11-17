"""The lane's acceptance proof (learning-service PLAN P1-09): every tile and point of an agent's
curves equals an independent recompute from the raw spans. Failures are counted, approval wait
is excluded, and every figure carries its n.

A replay set of spans (``_replay_set.py``), in the shape P1-01 stamps them, is seeded into the
test ClickHouse through the production ``adapt()`` path (``_ch_seed``). The set covers:
- two versions over two days, with failed and unfinished runs, approval waits and LLM costs;
- both wire encodings of the integer stamps: strings (the Django receiver) and numbers (the
  collector);
- runs that must count: unpriced, no model call, charged nothing, a wait longer than the
  run, an LLM call after the window's end, a span sent twice, a span deleted after it was
  sent, a price-table cost on a non-LLM span, a ledger cost stamped as a string (unpriced),
  and an unversioned run. The second send and the deletion arrive in a later export, seeded
  as an INSERT of its own with merges held, so both versions are on disk when the read runs;
- spans that must not count: a run that began before the window, a trace whose first root
  began before it, another agent in the same project, the same agent id in another
  project, and another project's LLM span inside one of this agent's traces.

The recompute (``_recompute.py``) reads the same rows in plain Python. It imports nothing from
the code under test and uses the unbounded rules, with no look-back or grace window.
"""

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from django.utils import timezone

from tracer.tests.omega_agent_curves._recompute import (
    MIN_N,
    recompute_curves,
    recompute_runs,
)
from tracer.tests.omega_agent_curves._replay_set import build_replay
from tracer.tests.omega_agent_curves.conftest import new_agent_id, read_curves

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


def _client():
    import clickhouse_connect

    from tracer.services.clickhouse.v2 import get_v2_config

    cfg = get_v2_config()
    try:
        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
        )
        client.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"test ClickHouse not reachable ({exc!r})")
    return client


@pytest.fixture
def replay(organization, project_a):
    client = _client()
    today = timezone.now().astimezone(UTC).date()
    day1 = today - timedelta(days=3)
    start = datetime.combine(day1, time.min, tzinfo=UTC)
    end = start + timedelta(days=2)
    agent_id = new_agent_id()
    other_project = str(uuid.uuid4())
    rows_of, notes = build_replay(
        str(project_a.id), other_project, str(organization.id), agent_id, start, end
    )
    from tracer.tests._ch_seed import seed_ch_spans

    # The later export (a re-sent span, a deletion) goes in an INSERT of its own, so it lands
    # in a part of its own, as in production. One INSERT would prove nothing about the read's
    # FINAL: ClickHouse collapses an inserted block by the table's engine (optimize_on_insert),
    # so both versions of a span would never reach the table. Merges wait until teardown, so
    # both versions stay on disk while the tests read.
    later = rows_of.resends
    first = [row for row in rows_of.rows if all(row is not r for r in later)]
    client.command("SYSTEM STOP MERGES spans")
    try:
        seed_ch_spans(first, client=client, version_from_created_at=True)
        seed_ch_spans(later, client=client, version_from_created_at=True)
        yield {
            "rows": rows_of.rows,
            "notes": notes,
            "agent_id": agent_id,
            "start": start,
            "end": end,
            "dates": (day1, day1 + timedelta(days=1)),
            "resent_ids": sorted({row["id"] for row in later}),
        }
    finally:
        # Merges first: the DELETE below is a mutation, which stopped merges would hold.
        client.command("SYSTEM START MERGES spans")
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id IN (%(a)s, %(b)s) "
            "SETTINGS mutations_sync=2",
            parameters={"a": str(project_a.id), "b": other_project},
        )
        client.close()


def _stored_rows(project_id, span_ids):
    """Rows on disk for these spans, every version, read without FINAL."""
    client = _client()
    try:
        return client.query(
            "SELECT count() FROM spans "
            "WHERE project_id = %(project)s AND has(%(ids)s, id)",
            parameters={"project": project_id, "ids": list(span_ids)},
        ).result_rows[0][0]
    finally:
        client.close()


def _expected_runs(replay, project_a):
    return recompute_runs(
        replay["rows"],
        str(project_a.id),
        replay["agent_id"],
        replay["start"],
        replay["end"],
    )


def test_each_run_read_from_the_spans_equals_the_recompute(replay, project_a):
    from tracer.services.omega_agents.curves.runs import read_agent_runs

    runs = read_agent_runs(
        project_id=str(project_a.id),
        agent_id=replay["agent_id"],
        start=replay["start"],
        end=replay["end"],
    )

    expected = _expected_runs(replay, project_a)
    assert [vars(r) for r in runs] == expected
    # Both versions of each re-sent span were on disk for that read (merges wait until
    # teardown), so only the read's FINAL replaced them: the re-send counted once, and the
    # deleted span's first version not at all.
    assert len(replay["resent_ids"]) == 2
    assert _stored_rows(str(project_a.id), replay["resent_ids"]) == 4
    by_trace = {r["trace_id"]: r for r in expected}
    notes = replay["notes"]
    for excluded in (
        "began_before",
        "first_root_before",
        "other_agent",
        "other_project",
    ):
        assert notes[excluded] not in by_trace, excluded
    assert by_trace[notes["sent_twice"]]["cost_micros"] == 4_321
    assert by_trace[notes["deleted_span"]]["cost_micros"] == 900
    assert by_trace[notes["chain_cost"]]["cost_micros"] == 300
    assert by_trace[notes["call_after_window"]]["cost_micros"] == 2_500
    assert by_trace[notes["unpriced"]]["unpriced_llm_spans"] == 1
    shared = by_trace[notes["other_project_llm_in_trace"]]
    assert (shared["llm_spans"], shared["cost_micros"]) == (1, 600)
    as_string = by_trace[notes["cost_stamped_as_string"]]
    assert (as_string["llm_spans"], as_string["unpriced_llm_spans"]) == (1, 1)
    assert by_trace[notes["collector_encoding"]]["version"] == 2
    assert by_trace[notes["collector_encoding"]]["approval_wait_ms"] == 1_111
    assert by_trace[notes["unversioned"]]["version"] is None


def test_every_tile_and_point_equals_the_independent_recompute(
    auth_client, replay, project_a
):
    day1, day2 = replay["dates"]

    response = read_curves(
        auth_client,
        replay["agent_id"],
        project_a.id,
        start_date=day1.isoformat(),
        end_date=day2.isoformat(),
    )

    assert response.status_code == 200, response.content
    answer = response.json()["result"]
    expected = recompute_curves(_expected_runs(replay, project_a))
    assert answer["minimumN"] == MIN_N
    for part in ("totals", "versions", "points", "overview"):
        assert answer[part] == expected[part], part


def test_failures_count_wait_is_excluded_and_every_point_has_its_n(
    auth_client, replay, project_a
):
    day1, day2 = replay["dates"]
    answer = read_curves(
        auth_client,
        replay["agent_id"],
        project_a.id,
        start_date=day1.isoformat(),
        end_date=day2.isoformat(),
    ).json()["result"]

    expected_runs = _expected_runs(replay, project_a)
    totals = answer["totals"]
    # Failures: in n, in the success rate's denominator, and in cost.
    failed = [r for r in expected_runs if r["status"] == "ERROR"]
    assert totals["failed"] == len(failed) >= 4
    assert totals["n"] == len(expected_runs)
    assert totals["outcomeSuccessRate"]["n"] == totals["n"]
    priced_failed = [r for r in failed if r["unpriced_llm_spans"] == 0]
    assert sum(r["cost_micros"] for r in priced_failed) > 0
    assert totals["totalCostUsd"]["n"] == totals["pricedRuns"]
    # Approval wait: taken out, never counted as agent latency.
    waited = sum(min(r["latency_ms"], r["approval_wait_ms"]) for r in expected_runs)
    assert totals["approvalWaitExcludedMs"] == waited > 0
    # n on every point, version and tile.
    for row in answer["points"] + answer["versions"] + [totals]:
        assert row["n"] > 0
        for name in (
            "outcomeSuccessRate",
            "costPerRunUsd",
            "p50LatencyMs",
            "p95LatencyMs",
        ):
            assert row[name]["n"] >= 0 and "reason" in row[name]
    for tile in answer["overview"]["tiles"].values():
        assert tile["n"] >= MIN_N and tile["previousN"] >= MIN_N
        assert tile["delta"] is not None
    assert (answer["overview"]["version"], answer["overview"]["previousVersion"]) == (
        2,
        1,
    )


def test_another_agent_in_the_same_project_reads_only_its_own_runs(
    auth_client, replay, project_a
):
    day1, day2 = replay["dates"]
    other = replay["rows"]
    other_agent = next(
        row["span_attributes"]["omega.agent.id"]
        for row in other
        if row["trace_id"] == replay["notes"]["other_agent"]
    )

    answer = read_curves(
        auth_client,
        other_agent,
        project_a.id,
        start_date=day1.isoformat(),
        end_date=day2.isoformat(),
    ).json()["result"]

    assert answer["totals"]["n"] == 1
    assert [v["version"] for v in answer["versions"]] == [7]
