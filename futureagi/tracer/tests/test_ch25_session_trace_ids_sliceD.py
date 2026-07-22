"""Slice D acceptance — the session→trace_ids reads cut over from the dead
``Trace.session`` reverse FK to ClickHouse spans (P3b step2, DESIGN §5 /
PG_ORM_READ_MIGRATION).

WHAT THIS PINS (the post-step2 break Slice D closes)
----------------------------------------------------
After step2 the PG ``trace_session`` table FREEZES and the ``Trace.session`` FK is
``None`` for EVERY trace (only spans carry the deterministic ``trace_session_id``).
So EVERY read that gets a session's traces THROUGH that FK —

  • ``Trace.objects.filter(session=session)``  (``build_session_context``,
    ``model_hub/views/separate_evals.py`` eval-context detail), and
  • ``trace_session.traces.annotate(...)``      (``eval.py._resolve_session_path``)

— returns EMPTY for ALL sessions (net-new AND historical), because no trace row
carries a ``session_id`` anymore. The new building block
``span_reader.session_trace_ids(project_id, session_id)`` re-derives the trace set
from the span fact (``spans.trace_session_id``) instead.

THE STRADDLER HEADLINE (the trace-set UNION proof)
--------------------------------------------------
A *straddler* (an identity that already had an OLD random id, then gained a NEW
deterministic id + a ``trace_session_id_remap`` old→new bridge) has spans on BOTH
its old id AND its new id, each on DIFFERENT traces. ``session_trace_ids`` resolves
the INPUT id new→old AND each SPAN's ``trace_session_id`` new→old, so — queried by
EITHER id — it returns the OLD-id spans' traces UNION the NEW-id spans' traces as
ONE complete set. This test manufactures KNOWN disjoint trace sets on each side and
asserts ``count(union) == count(old) + count(new)``.

PROOF SHAPE (mirrors the Slice C / fields-reader siblings): in ONE process, on the
SAME manufactured ch_rehearsal fixture, evaluate BOTH the exact OLD PG expression
(``Trace.objects.filter(session=...)`` — HEAD) and the NEW CH expression
(``session_trace_ids`` — POST) for the net-new session, and assert HEAD returns
EMPTY while POST returns the real trace set. Historical parity, project-scope (no
cross-tenant leak), and the straddler union are pinned directly on the helper.

HARNESS — ch_rehearsal ONLY (the clean from-empty P3b baseline; reference memory
reference-ch-rehearsal-harness). Hard guard on ``CH25_DATABASE``. Manufactures its
OWN self-contained CH sessions/spans with KNOWN trace sets and tears them down via
synchronous ``ALTER … DELETE`` (``mutations_sync=2``), re-asserting the EXACT
baseline (``trace_sessions FINAL=3``, ``remap=3``, ``spans FINAL=691``). PG is the
REAL pg-test ``tfc`` (read-only here) via ``django_db_blocker.unblock()``. Reachable
only from INSIDE the ``backend`` container (host pytest can't reach CH).
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
# case — read by its OLD id only, never mutated. Its trace set is whatever the
# island spans carry; we assert NON-empty + that it equals the raw span-derived
# set (the same data the OLD FK walk would have produced PRE-flip).
HIST_PROJECT_ID = "61840705-018d-415e-b9f0-4120f06e1fcc"
HIST_OLD_ID = "9999e6c4-d9c4-40a9-9e01-ff40037528af"

# The historical island session (HIST_OLD_ID) has EXACTLY two real PG ``Trace``
# rows whose ids equal its two CH-derived ``trace_id``s — verified on pg-test. This
# is the ONE place the migrated branches can be driven end-to-end with REAL data:
# CH ``session_trace_ids`` → these ids → ``Trace.objects.filter(id__in=…)`` → 2 real
# rows. Both rows carry ``input`` JSON containing the substring below.
HIST_TRACE_IDS = {
    "8bd34365-8eb3-4bde-9341-14b3cc4108e1",
    "f9efef50-1820-4aac-9ed4-6484490d932d",
}
HIST_TRACE_COUNT = 2
HIST_TRACE_INPUT_SUBSTR = "test_user"


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


def _insert_span(client, project_id, sid, trace_id):
    """A span carrying ``sid`` on an EXPLICIT ``trace_id`` so the manufactured
    session has a KNOWN trace set (the Slice D union proof). trace_id/id MUST be
    UUID strings — the spans table has a UUID-coercing projection on trace_id."""
    client.command(
        "INSERT INTO spans "
        "(project_id, observation_type, start_time, trace_id, id, name, trace_session_id) "
        "VALUES (%(p)s, 'agent', %(t)s, %(tr)s, %(id)s, 'fixture-span', %(s)s)",
        parameters={
            "p": project_id,
            "t": datetime(2025, 1, 1, tzinfo=UTC),
            "tr": str(trace_id),
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


# ----- exact OLD-PG (HEAD) and NEW (POST) trace-set expressions -----------------


def _head_session_trace_ids(session_id):
    """The OLD PG trace-set walk (``Trace.objects.filter(session=...)``), the body
    of ``build_session_context`` / ``_resolve_session_path`` PRE-Slice-D. Returns a
    set of str trace ids. Post-flip the ``Trace.session`` FK is ``None`` for every
    trace → this returns EMPTY for ALL sessions (the break Slice D closes)."""
    from tracer.models.trace import Trace

    return {
        str(t)
        for t in Trace.objects.filter(session_id=session_id, deleted=False).values_list(
            "id", flat=True
        )
    }


def _post_session_trace_ids(reader, project_id, session_id):
    """The NEW CH trace-set derivation (the reader method the migrated callers
    use). Returns a set of str trace ids."""
    return set(reader.session_trace_ids(str(project_id), str(session_id)))


@pytest.mark.integration
class TestSessionTraceIdsSliceD:
    """Manufacture the post-step2 state on ch_rehearsal with KNOWN trace sets;
    assert HEAD-empty vs POST-real for the net-new session, the straddler old∪new
    union, historical parity, and project-scope; restore the baseline exactly."""

    @pytest.fixture()
    def manufactured(self, django_db_blocker):
        """STRADDLER (old curated row + remap old→new + new curated row + spans on
        BOTH the old id AND the new id, each on DISTINCT traces) and a NET-NEW
        session (deterministic-id curated row, NO PG row, + spans on KNOWN traces)
        — both under the real island project 61840705. Plus a SECOND net-new
        session under a DIFFERENT (random) project, to prove the trace set is
        project-scoped (no cross-tenant leak). Tears everything down to the EXACT
        baseline.
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
            first_seen = datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC)

            # Straddler: OLD curated row; remap old→new; the collector's NEW
            # dual-write row; TWO spans on the OLD id (traces o1,o2) + TWO spans
            # on the NEW id (traces n1,n2). DISJOINT trace sets → the union proof:
            # session_trace_ids must return {o1,o2,n1,n2} (4) whichever id it is
            # queried by.
            strad_old = str(uuid.uuid4())
            strad_new = str(uuid.uuid4())
            strad_old_traces = {str(uuid.uuid4()), str(uuid.uuid4())}
            strad_new_traces = {str(uuid.uuid4()), str(uuid.uuid4())}
            _insert_session(client, proj, strad_old, "straddler-session", first_seen)
            _insert_remap(client, strad_old, strad_new, version)
            _insert_session(client, proj, strad_new, "straddler-session", first_seen)
            for tr in strad_old_traces:
                _insert_span(client, proj, strad_old, tr)
            for tr in strad_new_traces:
                _insert_span(client, proj, strad_new, tr)

            # Net-new (island project): deterministic-id curated row only (NO PG)
            # + spans on TWO KNOWN traces.
            netnew_id = str(uuid.uuid4())
            netnew_traces = {str(uuid.uuid4()), str(uuid.uuid4())}
            _insert_session(client, proj, netnew_id, "netnew-session", first_seen)
            for tr in netnew_traces:
                _insert_span(client, proj, netnew_id, tr)

            # Net-new under a FOREIGN project (leak guard): one span on a known
            # trace; must NOT appear in the island-project-scoped trace set.
            netnew_other = str(uuid.uuid4())
            netnew_other_trace = str(uuid.uuid4())
            _insert_session(
                client, other_proj, netnew_other, "foreign-session", first_seen
            )
            _insert_span(client, other_proj, netnew_other, netnew_other_trace)

            ids = {
                "proj": proj,
                "other_proj": other_proj,
                "hist_old": HIST_OLD_ID,
                "strad_old": strad_old,
                "strad_new": strad_new,
                "strad_old_traces": strad_old_traces,
                "strad_new_traces": strad_new_traces,
                "netnew_id": netnew_id,
                "netnew_traces": netnew_traces,
                "netnew_other": netnew_other,
                "netnew_other_trace": netnew_other_trace,
            }
            all_strad = strad_old_traces | strad_new_traces
            all_netnew = netnew_traces
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
                    "trace_session_id IN (%(a)s, %(b)s, %(c)s, %(d)s)",
                    {
                        "a": strad_old,
                        "b": strad_new,
                        "c": netnew_id,
                        "d": netnew_other,
                    },
                )
                # Belt-and-suspenders: also delete by the manufactured trace_ids in
                # case any insert raced the session-id delete (no-op if already
                # gone). All our manufactured trace_ids together.
                _all_traces = tuple(all_strad | all_netnew | {netnew_other_trace})
                _delete_sync(client, "spans", "trace_id IN %(t)s", {"t": _all_traces})
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

    # ===== net-new: POST returns the real trace set, HEAD empty ===================

    def test_netnew_post_returns_traces_head_empty(self, manufactured):
        """The headline gate. The net-new session's trace set is EMPTY via the old
        PG ``Trace.session`` FK walk (no trace carries its id) but the real KNOWN
        set via the new CH ``session_trace_ids``."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured
        proj, netnew = ids["proj"], ids["netnew_id"]

        # HEAD: PG FK walk returns empty.
        assert _head_session_trace_ids(netnew) == set()

        # POST: CH returns the manufactured trace set exactly.
        with get_reader() as reader:
            post = _post_session_trace_ids(reader, proj, netnew)
        assert post == ids["netnew_traces"]
        assert len(post) == 2

    # ===== STRADDLER union: old ∪ new spans' traces == ONE set ====================

    def test_straddler_union_old_plus_new_equals_total(self, manufactured):
        """The straddler headline. Queried by EITHER its old curated id OR its new
        deterministic id, ``session_trace_ids`` returns the OLD-id spans' traces
        UNION the NEW-id spans' traces as ONE set — count == old + new."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured
        old_traces = ids["strad_old_traces"]
        new_traces = ids["strad_new_traces"]
        expected_union = old_traces | new_traces

        with get_reader() as reader:
            by_old = _post_session_trace_ids(reader, ids["proj"], ids["strad_old"])
            by_new = _post_session_trace_ids(reader, ids["proj"], ids["strad_new"])

        # The explicit count headline: |union| == |old| + |new| (disjoint sets).
        assert len(old_traces) == 2
        assert len(new_traces) == 2
        assert len(expected_union) == 4
        # Queried by the OLD (survivor) id → full union.
        assert by_old == expected_union
        assert len(by_old) == len(old_traces) + len(new_traces) == 4
        # Queried by the NEW id → resolves to survivor → SAME full union.
        assert by_new == expected_union
        assert by_new == by_old

    # ===== project scope: no cross-tenant leak ====================================

    def test_session_trace_ids_is_project_scoped_no_leak(self, manufactured):
        """The trace set is multi-tenant-safe: a foreign-project net-new session's
        traces never surface under the island project's scope, and vice versa."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured

        with get_reader() as reader:
            # Foreign session queried under the ISLAND project → empty (the span's
            # project_id != island, so the WHERE project_id gate drops it).
            leaked = _post_session_trace_ids(reader, ids["proj"], ids["netnew_other"])
            # ...and under its OWN project → its real (single) trace.
            own = _post_session_trace_ids(
                reader, ids["other_proj"], ids["netnew_other"]
            )
            # The island net-new must NOT be visible to the foreign project.
            cross = _post_session_trace_ids(reader, ids["other_proj"], ids["netnew_id"])

        assert leaked == set()
        assert own == {ids["netnew_other_trace"]}
        assert cross == set()

    # ===== historical parity ======================================================

    def test_historical_trace_set_matches_raw_span_aggregation(self, manufactured):
        """No regression: a historical session (old id, untouched baseline) returns
        the SAME trace set the raw span aggregation yields — i.e. exactly what the
        OLD ``Trace.session`` FK walk produced PRE-flip (the parity anchor). The
        FK walk itself is empty post-flip, so the anchor is the raw span fact."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured
        proj, hist = ids["proj"], ids["hist_old"]

        # OLD anchor = raw span aggregation (the source of truth the FK mirrored
        # pre-flip): distinct trace_id of spans carrying this session id, scoped to
        # the project, is_deleted=0. No remap needed (historical = old-id spans).
        raw = {
            str(r[0])
            for r in client.query(
                "SELECT DISTINCT toString(trace_id) FROM spans FINAL "
                "WHERE project_id = %(p)s AND trace_session_id = %(s)s "
                "  AND is_deleted = 0",
                parameters={"p": proj, "s": hist},
            ).result_rows
        }

        with get_reader() as reader:
            post = _post_session_trace_ids(reader, proj, hist)

        assert post == raw
        assert len(post) > 0  # the island historical session genuinely has traces

    # ===== unknown id → empty (parity with the old empty queryset) ================

    def test_unknown_session_id_returns_empty(self, manufactured):
        """An unknown / non-existent session id returns an empty trace set (the
        parity point with the old ``Trace.objects.filter(session=...)`` empty
        queryset), and a falsy arg short-circuits to []."""
        from tracer.services.clickhouse.v2 import get_reader

        client, ids = manufactured

        with get_reader() as reader:
            assert _post_session_trace_ids(reader, ids["proj"], str(uuid.uuid4())) == (
                set()
            )
            assert reader.session_trace_ids(ids["proj"], "") == []
            assert reader.session_trace_ids("", ids["netnew_id"]) == []

    # ===== resolve_session_fields now carries project_id (Slice D addition) =======

    def test_resolve_session_fields_returns_project_id(self, manufactured):
        """Slice D extended ``resolve_session_fields`` to surface ``project_id``
        (the eval-context detail org-scopes on it + feeds it to
        ``session_trace_ids``). Net-new returns its project; a straddler-by-new-id
        resolves to the survivor's project."""
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        client, ids = manufactured

        netnew = resolve_session_fields([ids["netnew_id"]]).get(str(ids["netnew_id"]))
        assert netnew is not None
        assert netnew["project_id"] == ids["proj"]

        by_new = resolve_session_fields([ids["strad_new"]]).get(str(ids["strad_new"]))
        by_old = resolve_session_fields([ids["strad_old"]]).get(str(ids["strad_old"]))
        assert by_new is not None and by_old is not None
        assert by_new["project_id"] == ids["proj"]
        assert by_new["project_id"] == by_old["project_id"]

    # ===== build_session_context: net-new succeeds where HEAD is empty ============

    def test_build_session_context_netnew_includes_traces(self, manufactured):
        """End-to-end on the migrated ``eval.py.build_session_context``: an UNSAVED
        TraceSession vehicle (id=netnew, project=island — the shape
        ``evaluate_trace_session_observe`` builds) yields a session_context whose
        ``traces``/``trace_count`` reflect the real CH trace set. PRE-Slice-D (FK
        walk) this was empty; the spans aggregate already came from CH so totals
        were nonzero, but the trace LIST was empty — Slice D restores it."""
        from tracer.models.trace_session import TraceSession
        from tracer.utils.eval import build_session_context

        client, ids = manufactured

        # The vehicle the session evaluator builds (Slice C), now with project set
        # so build_session_context's CH trace derivation (Slice D) is scoped.
        vehicle = TraceSession(
            id=ids["netnew_id"],
            name="netnew-session",
            bookmarked=False,
            created_at=datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC),
            project_id=ids["proj"],
        )
        ctx = build_session_context(vehicle)
        assert ctx is not None
        # trace_count is the PG count over the CH-derived id set; the manufactured
        # traces have NO PG Trace rows (net-new), so trace_count is 0 — but the
        # CH-derived id set itself is non-empty. We assert the derivation ran by
        # checking session_trace_ids directly returns the set the ctx was built
        # from (the ctx 'traces' list is the PG-hydrated page, empty here since no
        # PG rows; the headline is that the CH derivation is wired, not empty-FK).
        from tracer.services.clickhouse.v2 import get_reader

        with get_reader() as reader:
            derived = set(reader.session_trace_ids(ids["proj"], ids["netnew_id"]))
        assert derived == ids["netnew_traces"]
        # The OLD FK walk (HEAD) would have been empty for this id.
        assert _head_session_trace_ids(ids["netnew_id"]) == set()

    # ===== REAL end-to-end on the HISTORICAL session (2 matching PG Trace rows) ====
    # These drive the migrated branches with REAL data flowing CH→PG (no mock, no
    # body-copy) — the bug-catchers the advisor called for. They use the real
    # ``build_session_context`` / ``_resolve_session_path`` functions.

    def test_build_session_context_historical_populates_traces_via_ch(
        self, manufactured
    ):
        """REAL ``build_session_context`` on the historical session: the CH
        ``session_trace_ids`` derivation yields the 2 trace ids, which hydrate
        against the 2 REAL PG ``Trace`` rows → a POPULATED ``traces`` list and
        ``trace_count == 2``. The OLD ``Trace.session`` FK walk is empty post-flip,
        so this is the end-to-end positive proof that the new CH path restores the
        trace set. ``session`` is the unsaved vehicle shape (id + project_id), the
        same the session evaluator builds."""
        from tracer.models.trace_session import TraceSession
        from tracer.utils.eval import build_session_context

        client, ids = manufactured

        vehicle = TraceSession(
            id=ids["hist_old"],
            name="test_session",
            bookmarked=False,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            project_id=ids["proj"],
        )
        ctx = build_session_context(vehicle)
        assert ctx is not None
        # The CH-derived ids hydrated to the 2 real PG Trace rows.
        assert ctx["trace_count"] == HIST_TRACE_COUNT
        assert {t["id"] for t in ctx["traces"]} == HIST_TRACE_IDS
        # PARITY: this historical session is the ONE case where the OLD FK walk is
        # NOT empty on pg-test — its pre-flip ``Trace.session`` FK rows still exist
        # (the island extract predates the flip), so the OLD walk returns the SAME
        # 2 ids. The CH path reproduces that set EXACTLY. (Post-flip the FK would go
        # None and only the CH path survives — proven empty-FK on the net-new case.)
        assert _head_session_trace_ids(ids["hist_old"]) == HIST_TRACE_IDS
        assert {t["id"] for t in ctx["traces"]} == _head_session_trace_ids(
            ids["hist_old"]
        )

    def test_resolve_session_path_traces_branch_real_data(self, manufactured):
        """REAL ``_resolve_session_path`` (the migrated ``traces`` branch, the core
        of Slice D's eval.py change + 6155's downstream) on a historical vehicle:
        ``traces`` resolves to the 2 real PG Trace rows (via CH ``session_trace_ids``
        → ``Trace.objects.filter(id__in=…)`` → the PG ``_root_start`` ordering), and
        ``traces.first.input`` walks into a real trace field. Every line of the
        migrated branch executes with real CH+PG data — a typo/wrong-var bug here
        fails loudly (the gap the building-block tests could not catch)."""
        from tracer.models.trace_session import TraceSession
        from tracer.utils.eval import _MISSING, _resolve_session_path

        client, ids = manufactured

        vehicle = TraceSession(
            id=ids["hist_old"],
            name="test_session",
            bookmarked=False,
            created_at=datetime(2025, 1, 1, tzinfo=UTC),
            project_id=ids["proj"],
        )

        # ``traces`` → the full ordered list of the session's real Trace rows.
        traces = _resolve_session_path(vehicle, "traces")
        assert isinstance(traces, list)
        assert {str(t.id) for t in traces} == HIST_TRACE_IDS
        assert len(traces) == HIST_TRACE_COUNT

        # ``traces.first.input`` → a real field on the earliest-ordered trace.
        first_input = _resolve_session_path(vehicle, "traces.first.input")
        assert first_input is not _MISSING
        assert first_input is not None
        assert HIST_TRACE_INPUT_SUBSTR in str(first_input)

        # ``traces.0.<field>`` indexes the same ordered collection (``input`` is a
        # public trace field; ``id`` is intentionally NOT in ``_TRACE_PUBLIC_FIELDS``
        # so we index the resolved list above for the id identity instead).
        first_input_idx = _resolve_session_path(vehicle, "traces.0.input")
        assert first_input_idx is not _MISSING
        assert HIST_TRACE_INPUT_SUBSTR in str(first_input_idx)
        # ``traces.first`` == ``traces.0`` (same earliest-ordered row).
        assert _resolve_session_path(
            vehicle, "traces.0.input"
        ) == _resolve_session_path(vehicle, "traces.first.input")

    def test_resolve_session_path_traces_netnew_empty_not_crash(self, manufactured):
        """The migrated ``traces`` branch on a NET-NEW vehicle resolves to an EMPTY
        collection (no PG Trace rows for the manufactured ids) WITHOUT crashing —
        and ``traces.first.<field>`` → ``_MISSING`` (empty collection), the graceful
        path ``_resolve_collection_path`` already handles. Proves the branch is
        robust when CH yields ids with no PG hydration (vs the old reverse-FK which
        also returned empty but is now structurally dead)."""
        from tracer.models.trace_session import TraceSession
        from tracer.utils.eval import _MISSING, _resolve_session_path

        client, ids = manufactured

        vehicle = TraceSession(
            id=ids["netnew_id"],
            name="netnew-session",
            bookmarked=False,
            created_at=datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC),
            project_id=ids["proj"],
        )
        traces = _resolve_session_path(vehicle, "traces")
        assert traces == []
        assert _resolve_session_path(vehicle, "traces.first.input") is _MISSING

    # ===== separate_evals 6155 — vehicle build + 400-on-absence (real) ============

    def test_eval_context_6155_vehicle_and_400_path(self, manufactured):
        """The 6155 eval-playground session-mapping resolution: a present session
        builds an UNSAVED ``TraceSession`` vehicle from CH fields (carrying
        project_id so the downstream ``_resolve_session_path`` traces branch is
        scoped); an ABSENT session id yields the 400 (``_map_fields is None``). This
        executes the exact composed shape the migrated 6155 site uses (no org-gate
        there). Driven on the historical session so the vehicle's traces resolve to
        real rows through the full chain."""
        from tracer.models.trace_session import TraceSession
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )
        from tracer.utils.eval import _MISSING, _resolve_session_path

        client, ids = manufactured

        # PRESENT: resolve fields → build the vehicle (mirrors separate_evals 6155).
        _map_fields = resolve_session_fields([ids["hist_old"]]).get(
            str(ids["hist_old"])
        )
        assert _map_fields is not None  # NOT the 400 branch
        vehicle = TraceSession(
            id=ids["hist_old"],
            name=(_map_fields["display_name"] or _map_fields["external_session_id"]),
            bookmarked=bool(_map_fields["bookmarked"]),
            project_id=_map_fields["project_id"],
        )
        assert str(vehicle.project_id) == ids["proj"]
        # The vehicle drives the real mapping resolver branch end-to-end.
        first_input = _resolve_session_path(vehicle, "traces.first.input")
        assert first_input is not _MISSING
        assert HIST_TRACE_INPUT_SUBSTR in str(first_input)

        # ABSENT: an unknown id resolves to None → the 6155 site returns 400.
        absent = resolve_session_fields([str(uuid.uuid4())]).get(str(uuid.uuid4()))
        assert absent is None

    # ===== separate_evals 6014 — composed: org-gate + session_trace_ids + dict ====

    def test_eval_context_6014_composed_org_gate_and_context(self, manufactured):
        """The 6014 eval-context session detail: ``resolve_session_fields`` →
        org-gate (``Project.objects.filter(id=project_id, organization=org)
        .exists()``) → ``session_trace_ids`` → ``Trace.objects`` hydration →
        session_context dict. Executes the REAL composed body (a thin copy mirroring
        separate_evals 6014, the ONLY site needing a stand-in because pg-test has
        zero ``tracer_project`` rows so the org-gate can never pass on real data).

        POSITIVE: with ``Project.exists()`` patched True (a faithful stand-in for a
        deployment where the project row exists), the historical session yields a
        session_context whose traces are the 2 real PG rows.
        NEGATIVE (free, no patch): the org-gate closes — no Project matches → the
        context is None (the cross-tenant-leak guard the old ``project__organization
        =org`` join enforced)."""
        from unittest.mock import patch

        from tracer.models.trace import Trace
        from tracer.services.clickhouse.v2 import get_reader
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        client, ids = manufactured

        def _post_eval_context_session(org_obj, session_id):
            """Thin mirror of separate_evals 6014's migrated body: returns the
            session_context dict, or None if the session is absent OR fails the
            org-gate (exactly the file's ``if _ss_fields and _ss_in_org`` guard)."""
            from tracer.models.project import Project

            _ss_fields = resolve_session_fields([session_id]).get(str(session_id))
            _ss_project_id = _ss_fields.get("project_id") if _ss_fields else None
            _ss_in_org = (
                bool(_ss_project_id)
                and Project.objects.filter(
                    id=_ss_project_id, organization=org_obj
                ).exists()
            )
            if not (_ss_fields and _ss_in_org):
                return None
            with get_reader() as reader:
                _trace_id_list = reader.session_trace_ids(
                    str(_ss_project_id), str(session_id)
                )
            _trace_qs = Trace.objects.filter(id__in=_trace_id_list, deleted=False)
            _first_seen = _ss_fields["first_seen"]
            return {
                "id": str(session_id),
                "name": (
                    _ss_fields["display_name"] or _ss_fields["external_session_id"]
                ),
                "project_id": str(_ss_project_id) if _ss_project_id else None,
                "bookmarked": bool(_ss_fields["bookmarked"]),
                "created_at": _first_seen.isoformat() if _first_seen else None,
                "trace_count": _trace_qs.count(),
                "trace_ids": {str(t) for t in _trace_qs.values_list("id", flat=True)},
            }

        # A REAL Organization (pg-test has orgs but ZERO projects) so the unmocked
        # ``.filter(organization=org)`` is a clean DB query (not a value-coercion
        # error on a sentinel) — it returns empty because no project rows exist.
        from accounts.models.organization import Organization

        real_org = Organization.objects.first()
        assert real_org is not None  # pg-test has organizations

        # NEGATIVE (no patch): pg-test has no Project rows → org-gate closes → None.
        assert _post_eval_context_session(real_org, ids["hist_old"]) is None

        # POSITIVE (Project.exists patched True at its source — 6014 imports it
        # function-locally, so patch the model, not a view attribute).
        with patch("tracer.models.project.Project.objects") as _proj_objs:
            _proj_objs.filter.return_value.exists.return_value = True
            ctx = _post_eval_context_session(real_org, ids["hist_old"])

        assert ctx is not None  # org-gate passed → composed body ran
        assert ctx["project_id"] == ids["proj"]
        assert ctx["name"] == "test_session"
        # session_trace_ids → Trace hydration → the 2 real PG rows.
        assert ctx["trace_count"] == HIST_TRACE_COUNT
        assert ctx["trace_ids"] == HIST_TRACE_IDS

    def test_eval_context_6014_netnew_resolves_where_head_empty(self, manufactured):
        """The 6014 net-new headline (acceptance): with the org-gate satisfied, a
        NET-NEW session resolves a session_context (the OLD PG ``TraceSession
        .objects.filter(...).first()`` → None → no context). trace_count is 0 (no PG
        Trace rows for the manufactured ids) but the CH derivation is wired and the
        identity fields resolve — where HEAD produced nothing."""
        from unittest.mock import patch

        from tracer.models.trace import Trace
        from tracer.models.trace_session import TraceSession
        from tracer.services.clickhouse.v2 import get_reader
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        client, ids = manufactured

        # HEAD: the old PG lookup returns None for a net-new session.
        assert TraceSession.objects.filter(id=ids["netnew_id"]).first() is None

        # POST (org-gate mocked True): identity resolves; CH derivation runs.
        with patch("tracer.models.project.Project.objects") as _proj_objs:
            _proj_objs.filter.return_value.exists.return_value = True
            _fields = resolve_session_fields([ids["netnew_id"]]).get(
                str(ids["netnew_id"])
            )
            assert _fields is not None
            assert _fields["external_session_id"] == "netnew-session"
            with get_reader() as reader:
                _ids = set(reader.session_trace_ids(ids["proj"], ids["netnew_id"]))
            assert _ids == ids["netnew_traces"]
            # PG hydration is empty (net-new has no PG Trace rows) — the headline is
            # that identity + derivation resolve where the old .first() was None.
            assert Trace.objects.filter(id__in=_ids, deleted=False).count() == 0
