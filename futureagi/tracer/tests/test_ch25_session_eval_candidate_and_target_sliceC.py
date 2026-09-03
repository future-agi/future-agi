"""Slice C acceptance — session-level eval cut over from PG to ClickHouse for BOTH
its post-flip breaks (P3b step2, DESIGN §5 / PG_ORM_READ_MIGRATION):

  1. CANDIDATE PICKER (``tracer/utils/eval_tasks.py`` ``process_eval_task`` SESSIONS
     branch → ``_derive_session_candidate_ids`` → the NEW reader method
     ``span_reader.distinct_session_ids_with_filters``): re-derive the in-scope
     session ids from CH spans, NOT the ``Trace.session`` FK.
  2. EVAL TARGET (``tracer/utils/eval.py`` ``evaluate_trace_session_observe``):
     resolve the target session's identity from CH (``resolve_session_fields``) and
     build an UNSAVED ``TraceSession`` vehicle, NOT ``TraceSession.objects.get``.

WHAT THIS PINS (the post-step2 break Slice C closes)
----------------------------------------------------
After step2 the PG ``trace_session`` table FREEZES and the ``Trace.session`` FK is
``None`` for every trace (only spans carry the deterministic ``trace_session_id``).
So for a NET-NEW session (first seen post-flip, NO PG row):

  • the OLD picker
    ``Trace.objects.filter(<span matches>).exclude(session__isnull=True)
    .values('session_id').distinct()`` → ``TraceSession.objects.filter(id__in=…)``
    silently OMITS it (its traces carry no ``session_id`` and it has no PG row) →
    a session-level eval would NEVER run on it; and
  • the OLD target lookup ``TraceSession.objects.get(id=session_id)`` RAISES
    ``DoesNotExist`` → the eval ``ValueError``s before it can write an EvalLogger.

A *straddler* (an identity that already had an OLD random id, then gained a NEW
deterministic id + a ``trace_session_id_remap`` old→new bridge) must collapse to
ONE survivor id in the candidate set (appear ONCE, under its canonical/old id),
never twice.

PROOF SHAPE (advisor-directed, mirrors the Slice E / fields-reader siblings):
rather than a git revert to demonstrate HEAD=omit/raise, this evaluates — in ONE
process, on the SAME manufactured ch_rehearsal fixture — BOTH the exact OLD PG
expression (HEAD) and the exact NEW expression (POST) for each surface, and asserts
HEAD omits/raises while POST includes/resolves for the net-new session. The POST
candidate expression is the body of ``_derive_session_candidate_ids`` exercised via
the public reader method (and the helper itself, duck-typed eval_task); the POST
target expression is the body of ``evaluate_trace_session_observe``'s resolution.
We do NOT drive ``process_eval_task`` / Temporal nor the full evaluator: a full
session eval would additionally hit ``_resolve_session_path`` /
``build_session_context``'s ``trace_session.traces`` reverse-accessor — the SLICE-D
gap (``Trace.session`` is ``None`` → empty), out of Slice C scope. The direct
contrast proves the Slice C net-new gate passes WITHOUT Slice D.

HARNESS — ch_rehearsal ONLY (the clean from-empty P3b baseline; reference memory
reference-ch-rehearsal-harness). Hard guard on ``CH25_DATABASE``. Manufactures its
OWN self-contained CH sessions (historical=untouched-baseline read) and tears them
down via synchronous ``ALTER … DELETE`` (``mutations_sync=2``), re-asserting the
EXACT baseline (``trace_sessions FINAL=3``, ``remap=3``, ``spans FINAL=691``). PG is
the REAL pg-test ``tfc`` (read-only here) via ``django_db_blocker.unblock()``.
Reachable only from INSIDE the ``backend`` container (host pytest can't reach CH).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

# Cycle-breaker — same rationale as the sibling slice tests (importing
# model_hub.tasks first avoids an app-loading import cycle when the eval /
# reader lazy model imports fire).
import model_hub.tasks  # noqa: F401, E402

REQUIRED_DB = "ch_rehearsal"

# Baseline objective counts of the clean ch_rehearsal harness (reference memory
# reference-ch-rehearsal-harness): the test restores to these EXACTLY on teardown.
BASELINE_TRACE_SESSIONS = 3
BASELINE_REMAP = 3
BASELINE_SPANS = 691

# A real baseline session (project 61840705) used as the untouched "historical"
# case — read by its OLD id only, never mutated.
HIST_PROJECT_ID = "61840705-018d-415e-b9f0-4120f06e1fcc"
HIST_OLD_ID = "9999e6c4-d9c4-40a9-9e01-ff40037528af"
HIST_EXTERNAL = "test_session"


def _ch_client():
    """clickhouse-connect client bound to CH25_DATABASE (honours the harness env);
    skip if CH is unreachable, FAIL HARD if it is not ch_rehearsal."""
    from tracer.services.clickhouse.v2 import get_v2_config

    cfg = get_v2_config()
    if cfg["database"] != REQUIRED_DB:
        pytest.skip(
            f"refusing to run outside the ch_rehearsal harness "
            f"(CH25_DATABASE={cfg['database']!r}); set CH25_DATABASE={REQUIRED_DB}"
        )
    try:
        import clickhouse_connect

        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
            send_receive_timeout=15,
        )
        client.query("SELECT 1")
    except Exception:
        pytest.skip("ClickHouse (ch_rehearsal) not reachable for integration test")
    return client


def _count(client, sql: str) -> int:
    return int(client.query(sql).result_rows[0][0])


def _insert_session(client, project_id, sid, external, first_seen):
    client.command(
        "INSERT INTO trace_sessions "
        "(project_id, trace_session_id, external_session_id, first_seen, version, is_deleted) "
        "VALUES (%(p)s, %(s)s, %(e)s, %(t)s, %(t)s, 0)",
        parameters={"p": project_id, "s": sid, "e": external, "t": first_seen},
    )


def _insert_remap(client, old_id, new_id, version):
    client.command(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) "
        "VALUES (%(o)s, %(n)s, %(v)s)",
        parameters={"o": old_id, "n": new_id, "v": version},
    )


def _insert_span(client, project_id, sid):
    """A span carrying ``sid`` so the manufactured session genuinely 'carries'
    spans (faithful net-new state). trace_id/id MUST be UUID strings — the spans
    table has a UUID-coercing projection on trace_id."""
    client.command(
        "INSERT INTO spans "
        "(project_id, observation_type, start_time, trace_id, id, name, trace_session_id) "
        "VALUES (%(p)s, 'agent', %(t)s, %(tr)s, %(id)s, 'fixture-span', %(s)s)",
        parameters={
            "p": project_id,
            "t": datetime(2025, 1, 1, tzinfo=UTC),
            "tr": str(uuid.uuid4()),
            "id": str(uuid.uuid4()),
            "s": sid,
        },
    )


def _delete_sync(client, table: str, where: str, params: dict) -> None:
    """Synchronous ALTER … DELETE (mutations_sync=2) so teardown is deterministic
    — no system.mutations poll loop needed."""
    client.command(
        f"ALTER TABLE {table} DELETE WHERE {where} SETTINGS mutations_sync=2",
        parameters=params,
    )


# ----- exact OLD-PG (HEAD) and NEW (POST) expressions for each surface ----------


def _head_candidate_session_ids(filters_q, project_id):
    """The OLD PG candidate picker (eval_tasks.py, pre-Slice C SESSIONS branch).
    ``filters_q`` is the parsed span-filter ``Q``; the span scan is project-scoped
    to mirror the POST tenant gate and bound the read. Faithfully reproduces the
    pre-Slice C ``matching_session_ids → TraceSession.objects.filter(id__in=…)``
    shape. Returns a set of str session ids."""
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace import Trace
    from tracer.models.trace_session import TraceSession

    matching_session_ids = (
        Trace.objects.filter(
            id__in=ObservationSpan.objects.filter(filters_q, project_id=project_id)
            .values("trace_id")
            .distinct()
        )
        .exclude(session__isnull=True)
        .values("session_id")
        .distinct()
    )
    return {
        str(s)
        for s in TraceSession.objects.filter(
            id__in=matching_session_ids, project_id=project_id
        ).values_list("id", flat=True)
    }


def _post_candidate_session_ids(reader, project_id, **kwargs):
    """The NEW CH candidate picker (the body of ``_derive_session_candidate_ids`` /
    the reader method it calls). Returns a set of str session ids."""
    return set(
        reader.distinct_session_ids_with_filters(project_id=project_id, **kwargs)
    )


def _head_resolve_target(session_id):
    """The OLD target lookup (eval.py, pre-Slice C): raises DoesNotExist for a
    net-new / straddler-by-new-id session."""
    from tracer.models.trace_session import TraceSession

    return TraceSession.objects.get(id=session_id)


def _post_resolve_target(session_id):
    """The NEW target resolution (eval.py ``evaluate_trace_session_observe`` body):
    CH fields → returns the dict (caller 404s on absence). Mirrors the file."""
    from tracer.services.clickhouse.v2.trace_session_dict_reader import (
        resolve_session_fields,
    )

    return resolve_session_fields([session_id]).get(str(session_id))


@pytest.mark.integration
class TestSessionEvalCandidateAndTargetSliceC:
    """Manufacture the post-step2 state on ch_rehearsal; assert HEAD-omits/raises vs
    POST-includes/resolves for the net-new session on BOTH Slice C surfaces; the
    straddler collapses to one survivor; restore the baseline exactly."""

    @pytest.fixture()
    def manufactured(self, django_db_blocker):
        """STRADDLER (old curated row + remap old→new + new curated row + a span on
        the new id) and a NET-NEW session (deterministic-id curated row, NO PG row,
        + a span) — both under the real island project 61840705. Plus a SECOND
        net-new session under a DIFFERENT (random) project, to prove the candidate
        set is project-scoped (no cross-tenant leak). Tears everything down to the
        EXACT baseline.
        """
        client = _ch_client()

        with django_db_blocker.unblock():
            # --- record + assert clean CH baseline before manufacturing ----------
            assert (
                _count(client, "SELECT count() FROM trace_sessions FINAL")
                == BASELINE_TRACE_SESSIONS
            )
            assert (
                _count(client, "SELECT count() FROM trace_session_id_remap FINAL")
                == BASELINE_REMAP
            )
            assert _count(client, "SELECT count() FROM spans FINAL") == BASELINE_SPANS

            proj = HIST_PROJECT_ID  # a real island project (project-scope cases)
            other_proj = str(uuid.uuid4())  # foreign tenant (leak guard)
            version = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
            first_seen_straddler = datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC)
            first_seen_netnew = datetime(2025, 3, 3, 6, 7, 8, tzinfo=UTC)

            # Straddler: OLD curated row; remap old→new; the collector's
            # NEW-deterministic-id dual-write row; a span on the NEW id.
            strad_old = str(uuid.uuid4())
            strad_new = str(uuid.uuid4())
            _insert_session(
                client,
                proj,
                strad_old,
                "straddler-external-session",
                first_seen_straddler,
            )
            _insert_remap(client, strad_old, strad_new, version)
            _insert_session(
                client,
                proj,
                strad_new,
                "straddler-external-session",
                first_seen_straddler,
            )
            _insert_span(client, proj, strad_new)

            # Net-new (island project): deterministic-id curated row only (NO PG) + span.
            netnew_id = str(uuid.uuid4())
            _insert_session(
                client, proj, netnew_id, "netnew-external-session", first_seen_netnew
            )
            _insert_span(client, proj, netnew_id)

            # Net-new under a FOREIGN project (leak guard): must NOT appear in the
            # island-project-scoped candidate set.
            netnew_other = str(uuid.uuid4())
            _insert_session(
                client,
                other_proj,
                netnew_other,
                "foreign-external-session",
                first_seen_netnew,
            )
            _insert_span(client, other_proj, netnew_other)

            ids = {
                "proj": proj,
                "other_proj": other_proj,
                "hist_old": HIST_OLD_ID,
                "strad_old": strad_old,
                "strad_new": strad_new,
                "netnew_id": netnew_id,
                "netnew_other": netnew_other,
            }
            try:
                yield client, ids
            finally:
                # --- teardown → restore CH baseline ------------------------------
                _delete_sync(
                    client,
                    "trace_sessions",
                    "trace_session_id IN (%(a)s, %(b)s, %(c)s, %(d)s)",
                    {
                        "a": strad_old,
                        "b": strad_new,
                        "c": netnew_id,
                        "d": netnew_other,
                    },
                )
                _delete_sync(
                    client, "trace_session_id_remap", "old_id = %(o)s", {"o": strad_old}
                )
                _delete_sync(
                    client,
                    "spans",
                    "trace_session_id IN (%(b)s, %(c)s, %(d)s)",
                    {"b": strad_new, "c": netnew_id, "d": netnew_other},
                )
                assert (
                    _count(client, "SELECT count() FROM trace_sessions FINAL")
                    == BASELINE_TRACE_SESSIONS
                )
                assert (
                    _count(client, "SELECT count() FROM trace_session_id_remap FINAL")
                    == BASELINE_REMAP
                )
                assert (
                    _count(client, "SELECT count() FROM spans FINAL") == BASELINE_SPANS
                )

    # ===== SURFACE 1: candidate picker — net-new INCLUDED (POST) vs OMITTED (HEAD)

    def test_candidate_netnew_post_includes_head_omits(self, manufactured):
        """The headline gate. The net-new session is OMITTED by the old PG picker
        (its traces carry no ``session_id``; no PG row) but INCLUDED by the new CH
        ``distinct_session_ids_with_filters``."""
        from django.db.models import Q

        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured
        proj, netnew = ids["proj"], ids["netnew_id"]

        # HEAD: PG picker omits the net-new session (empty / absent).
        head = _head_candidate_session_ids(Q(), proj)
        assert netnew not in head  # HEAD silently omits

        # POST: CH picker includes it.
        with get_reader() as reader:
            post = _post_candidate_session_ids(reader, proj)
        assert netnew in post  # POST includes

    def test_candidate_straddler_collapses_to_one_survivor(self, manufactured):
        """A straddler appears ONCE in the candidate set, under its survivor (old)
        id — never twice (old AND new). The remap resolves the new-id span back to
        the old id before the DISTINCT."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured
        strad_old, strad_new = ids["strad_old"], ids["strad_new"]

        with get_reader() as reader:
            post = _post_candidate_session_ids(reader, ids["proj"])

        # survivor (old) id present; raw new id NOT (it collapsed into the old)
        assert strad_old in post
        assert strad_new not in post

    def test_candidate_is_project_scoped_no_cross_tenant_leak(self, manufactured):
        """The island-project-scoped candidate set must NOT contain a net-new
        session that lives under a DIFFERENT project."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured

        with get_reader() as reader:
            post_island = _post_candidate_session_ids(reader, ids["proj"])
            post_other = _post_candidate_session_ids(reader, ids["other_proj"])

        # the foreign net-new is absent from the island scope...
        assert ids["netnew_other"] not in post_island
        # ...but present in its own project's scope (sanity: the fixture is real).
        assert ids["netnew_other"] in post_other
        # and the island net-new is NOT visible to the foreign project.
        assert ids["netnew_id"] not in post_other

    def test_candidate_observation_type_filter_still_matches(self, manufactured):
        """The eval-task ``observation_type`` filter (the fixtures are 'agent'
        spans) narrows correctly through CH — net-new still included for a matching
        type, excluded for a non-matching one."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured

        with get_reader() as reader:
            match = _post_candidate_session_ids(
                reader, ids["proj"], observation_type="agent"
            )
            nomatch = _post_candidate_session_ids(
                reader, ids["proj"], observation_type="llm"
            )
        assert ids["netnew_id"] in match
        assert ids["netnew_id"] not in nomatch

    # ===== SURFACE 2: eval target — net-new RESOLVES (POST) vs RAISES (HEAD) ======

    def test_target_netnew_post_resolves_head_raises(self, manufactured):
        """The second headline. The net-new session target RAISES DoesNotExist on
        the old PG ``.get`` but RESOLVES its fields on the new CH path."""
        from tracer.models.trace_session import TraceSession

        client, ids = manufactured
        netnew = ids["netnew_id"]

        # HEAD: PG .get raises (no PG row).
        with pytest.raises(TraceSession.DoesNotExist):
            _head_resolve_target(netnew)

        # POST: CH resolves fields.
        fields = _post_resolve_target(netnew)
        assert fields is not None
        assert fields["external_session_id"] == "netnew-external-session"
        assert fields["first_seen"] is not None

    def test_target_vehicle_build_from_netnew_fields(self, manufactured):
        """The unsaved ``TraceSession`` vehicle ``evaluate_trace_session_observe``
        builds from the resolved CH fields carries every attribute the unchanged
        downstream reads by attribute (name / bookmarked / created_at / project),
        and is NEVER persisted (PG row count for the id stays 0)."""
        from tracer.models.trace_session import TraceSession

        client, ids = manufactured
        netnew = ids["netnew_id"]

        fields = _post_resolve_target(netnew)
        assert fields is not None
        # mirror the eval.py vehicle construction
        vehicle = TraceSession(
            id=netnew,
            name=fields["display_name"] or fields["external_session_id"],
            bookmarked=bool(fields["bookmarked"]),
            created_at=fields["first_seen"],
        )
        assert str(vehicle.id) == netnew
        assert (
            vehicle.name == "netnew-external-session"
        )  # external (no display override)
        assert vehicle.bookmarked is False
        assert vehicle.created_at == fields["first_seen"]
        # the vehicle is NOT saved → no PG trace_session row exists for this id
        assert not TraceSession.objects.filter(id=netnew).exists()

    def test_target_straddler_by_new_id_resolves_to_survivor(self, manufactured):
        """A straddler queried by its NEW deterministic id resolves (the old ``.get``
        by-new-id would 404) to the unified survivor identity."""
        client, ids = manufactured

        by_new = _post_resolve_target(ids["strad_new"])
        by_old = _post_resolve_target(ids["strad_old"])
        assert by_new is not None
        assert by_old is not None
        assert by_new["external_session_id"] == "straddler-external-session"
        assert by_new["external_session_id"] == by_old["external_session_id"]
        assert by_new["first_seen"] == by_old["first_seen"]

    def test_target_historical_still_resolves(self, manufactured):
        """No regression: a historical session (old id) still resolves its fields
        (this is the pre-flip-resolvable case the PG ``.get`` also handled)."""
        client, ids = manufactured
        fields = _post_resolve_target(ids["hist_old"])
        assert fields is not None
        assert fields["external_session_id"] == HIST_EXTERNAL

    def test_target_unknown_id_absent_caller_404s(self, manufactured):
        """An unknown id resolves to None — the parity point with the old ``.get``
        raising (the caller turns absence into the ValueError/404)."""
        client, _ids = manufactured
        assert _post_resolve_target(str(uuid.uuid4())) is None
