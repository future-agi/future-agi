"""Slice F acceptance — session bulk-select + score-grid scope cut from PG to CH
for their post-flip breaks (P3b step2, PG_ORM_READ_MIGRATION Slice F).

TWO SITES
---------
1. SESSION BULK-SELECT (``model_hub/services/bulk_selection.py``
   ``resolve_filtered_session_ids`` → wired as the ``trace_session`` resolver in
   ``annotation_queues.py`` "add filtered sessions to a queue"). The matched
   session-id set is re-derived from CH (the same remap-aware
   ``SessionListQueryBuilder`` the live grid uses), NOT
   ``TraceSession.objects.filter(project_id=…)``.

2. SCORE COLUMN CONFIG (``tracer/views/trace_session.py``
   ``_build_score_column_config``): the redundant
   ``trace_session_id__in=TraceSession.objects.filter(project_id=…).values('id')``
   subquery is DROPPED; the read is scoped by ``label_id__in`` alone (a label
   belongs to exactly one project).

WHAT THIS PINS (the post-step2 breaks Slice F closes)
-----------------------------------------------------
After step2 the PG ``trace_session`` table FREEZES (no new rows). For a NET-NEW
session (first seen post-flip, NO PG row):

  • SITE 1 — the OLD bulk-select base
    ``TraceSession.objects.filter(project_id=…)`` (then span-aggregate by
    ``trace__session_id``) SILENTLY OMITS it → it can never be bulk-added to a
    queue by "select all matching this filter". The CH ``spans``-derived list
    INCLUDES it. A *straddler* (old random id + new deterministic id + a
    ``trace_session_id_remap`` bridge) unifies to ONE survivor id (counted once).

  • SITE 2 — the OLD subquery
    ``trace_session_id__in = TraceSession.objects.filter(project_id=…)`` drops a
    net-new session's Score from the per-label annotator set (the session is not
    in the subquery → its annotators vanish from the column config). Dropping the
    subquery surfaces them. Crucially the literal ``Score.project_id=project_id``
    alternative would be WRONG: ``Score.project`` is a nullable DevelopAI FK left
    NULL by the tracer-side annotation write path, so it would drop EVERY
    NULL-project session score (historical-parity failure). ``label_id__in`` is
    the parity-correct scope (and what the sibling ``_fetch_session_scores`` uses).

PROOF SHAPE (mirrors Slice C/E): ONE process, on a SINGLE manufactured fixture,
evaluates BOTH the exact OLD (HEAD) and NEW (POST) expression per site and
asserts HEAD-omits/drops vs POST-includes for the net-new entity, with
historical parity. SITE 1 manufactures self-contained CH sessions on the clean
ch_rehearsal harness; SITE 2 inserts ONE synthetic Score on a net-new session in
the REAL pg-test ``tfc`` (otherwise read-only), with ``project=NULL`` to match
production's write shape — and tears it down to the EXACT prior counts.

HARNESS — ch_rehearsal ONLY (hard ``CH25_DATABASE`` guard). CH fixtures torn
down via synchronous ``ALTER … DELETE`` (``mutations_sync=2``); the synthetic PG
Score deleted by pk; both re-assert the EXACT baseline. Reachable only from
INSIDE the ``backend`` container.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

# Cycle-breaker — same rationale as the Slice C/E siblings.
import model_hub.tasks  # noqa: F401, E402

REQUIRED_DB = "ch_rehearsal"

# Clean ch_rehearsal baseline (reference memory reference-ch-rehearsal-harness).
BASELINE_TRACE_SESSIONS = 3
BASELINE_REMAP = 3
BASELINE_SPANS = 691


def _ch_client():
    """clickhouse-connect client bound to CH25_DATABASE; skip if unreachable,
    FAIL HARD if it is not ch_rehearsal (never touch ch_test / default)."""
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


def _insert_span(client, project_id, sid, *, start_time=None):
    """A span carrying ``sid`` so the manufactured session genuinely carries
    spans. trace_id/id MUST be UUID strings (spans has a UUID-coercing
    projection on trace_id). ``start_time`` is event-time (2025-dated on the
    island) — the session-list builder filters on it."""
    client.command(
        "INSERT INTO spans "
        "(project_id, observation_type, start_time, trace_id, id, name, trace_session_id) "
        "VALUES (%(p)s, 'agent', %(t)s, %(tr)s, %(id)s, 'fixture-span', %(s)s)",
        parameters={
            "p": project_id,
            "t": start_time or datetime(2025, 1, 1, tzinfo=UTC),
            "tr": str(uuid.uuid4()),
            "id": str(uuid.uuid4()),
            "s": sid,
        },
    )


def _delete_sync(client, table: str, where: str, params: dict) -> None:
    """Synchronous ALTER … DELETE (mutations_sync=2) — deterministic teardown."""
    client.command(
        f"ALTER TABLE {table} DELETE WHERE {where} SETTINGS mutations_sync=2",
        parameters=params,
    )


def _purge_pg_project(project_id) -> None:
    """FK-safe, partial-safe teardown of EVERYTHING this test created under a
    synthetic project: scores (by the project's sessions or labels), spans,
    traces, sessions, labels, then the project itself. Raw SQL so it dodges the
    baked model's newer columns AND so a half-built fixture still cleans up
    (every statement is a no-op if its rows were never created). Restores the
    pg-test baseline exactly."""
    from django.db import connection

    pid = str(project_id)
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM model_hub_score WHERE trace_session_id IN "
            "(SELECT id FROM trace_session WHERE project_id = %s) "
            "OR label_id IN "
            "(SELECT id FROM model_hub_annotationslabels WHERE project_id = %s)",
            [pid, pid],
        )
        cur.execute("DELETE FROM tracer_observation_span WHERE project_id = %s", [pid])
        cur.execute("DELETE FROM tracer_trace WHERE project_id = %s", [pid])
        cur.execute("DELETE FROM trace_session WHERE project_id = %s", [pid])
        cur.execute(
            "DELETE FROM model_hub_annotationslabels WHERE project_id = %s", [pid]
        )
        cur.execute("DELETE FROM tracer_project WHERE id = %s", [pid])


def _raw_insert_session_score(
    *, score_id, trace_session_id, label_id, annotator_id, organization_id, value
):
    """Insert a trace_session Score via RAW SQL, writing ONLY the columns that
    exist on pg-test's (older) ``model_hub_score`` snapshot — the baked ORM
    model declares newer columns (e.g. ``value_history``) absent there, so an
    ORM ``.create()`` 500s. ``project_id`` is deliberately left NULL (the
    production tracer-side write shape). ``value`` is JSONB."""
    import json

    from django.db import connection
    from django.utils import timezone

    now = timezone.now()
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO model_hub_score "
            "(id, created_at, updated_at, deleted, source_type, value, "
            " score_source, label_id, annotator_id, organization_id, "
            " trace_session_id, project_id) "
            "VALUES (%s, %s, %s, false, 'trace_session', %s, 'human', "
            " %s, %s, %s, %s, NULL)",
            [
                str(score_id),
                now,
                now,
                json.dumps(value),
                str(label_id),
                str(annotator_id),
                str(organization_id),
                str(trace_session_id),
            ],
        )


# --------------------------------------------------------------------------
# SITE 1 — exact OLD-PG (HEAD) and NEW (POST) session-bulk-select expressions
# --------------------------------------------------------------------------


def _head_bulkselect_session_ids(project_id, organization):
    """The OLD PG bulk-select base derivation (pre-Slice F
    ``_build_session_base_queryset`` → ``_apply_session_filters`` with NO
    filters): span-aggregate over ``trace__session_id`` of the sessions that
    have a PG ``TraceSession`` row. Returns a set of str session ids. A net-new
    session (no PG row) is structurally absent."""
    from tracer.models.observation_span import ObservationSpan
    from tracer.models.trace_session import TraceSession

    session_ids = TraceSession.objects.filter(project_id=project_id).values("id")
    rows = (
        ObservationSpan.objects.filter(trace__session_id__in=session_ids)
        .values("trace__session_id")
        .distinct()
    )
    return {str(r["trace__session_id"]) for r in rows}


def _post_bulkselect_session_ids(project_id, organization, **kwargs):
    """The NEW CH bulk-select (the public resolver). Returns a set of str ids.

    The ids are str-cast: the CH path emits str session ids while the PG
    fallback emits UUIDs — the consumer (``_add_items_filter_mode``) normalises
    via ``str(source_id)`` before building QueueItems, so both shapes are
    equivalent downstream. We mirror that normalisation here."""
    from model_hub.services.bulk_selection import resolve_filtered_session_ids

    result = resolve_filtered_session_ids(
        project_id=project_id,
        filters=kwargs.get("filters", []),
        exclude_ids=kwargs.get("exclude_ids"),
        organization=organization,
        cap=kwargs.get("cap", 10_000),
        user=kwargs.get("user"),
    )
    return {str(i) for i in result.ids}


# --------------------------------------------------------------------------
# SITE 2 — exact OLD (HEAD) and NEW (POST) score-column-config scopes
# --------------------------------------------------------------------------


def _head_scorecol_rows(label_ids, project_id):
    """The OLD ``_build_score_column_config`` Score scope (pre-Slice F): the
    redundant ``trace_session_id__in=TraceSession.filter(project_id=…)``
    subquery. Returns the set of (label_id, annotator_id) the column config
    would surface. A net-new session's score is dropped (its session is not in
    the subquery)."""
    from model_hub.models.score import Score
    from tracer.models.trace_session import TraceSession

    rows = (
        Score.objects.filter(
            label_id__in=label_ids,
            trace_session_id__isnull=False,
            deleted=False,
            trace_session_id__in=TraceSession.objects.filter(
                project_id=project_id
            ).values("id"),
        )
        .values_list("label_id", "annotator_id")
        .distinct()
    )
    return {(str(lid), str(aid)) for lid, aid in rows}


def _post_scorecol_rows(label_ids, project_id):
    """The NEW scope (label-only, the body of the migrated
    ``_build_score_column_config``). Returns the set of (label_id,
    annotator_id)."""
    from model_hub.models.score import Score

    rows = (
        Score.objects.filter(
            label_id__in=label_ids,
            trace_session_id__isnull=False,
            deleted=False,
        )
        .values_list("label_id", "annotator_id")
        .distinct()
    )
    return {(str(lid), str(aid)) for lid, aid in rows}


@pytest.mark.integration
class TestSessionBulkSelectAndScoreColSliceF:
    """Manufacture the post-step2 state; assert HEAD-omits/drops vs
    POST-includes for the net-new entity on BOTH Slice F sites; straddler
    counted once; historical parity; wrong-project excluded; restore baselines."""

    # ===== SITE 1: session bulk-select =======================================

    @pytest.fixture()
    def ch_sessions(self, django_db_blocker):
        """A SYNTHETIC ``tracer_project`` (under a REAL pg-test org — the
        resolver scope-checks project→org membership, and the pg-test island
        carries NO project rows) plus manufactured CH sessions under it:

          • a HISTORICAL session (old id, real PG ``trace_session`` row → also
            surfaced by the OLD PG path, the parity anchor),
          • a STRADDLER (old curated row + remap old→new + new curated row + a
            span on the new id) — must unify to ONE survivor id,
          • a NET-NEW session (deterministic-id curated row, NO PG row, + a span)
            — the headline omit→include case,
          • a NET-NEW session under a DIFFERENT project (wrong-project guard).

        Restores BOTH the CH baseline (sessions/remap/spans) AND the PG baseline
        (the synthetic project + its one historical trace_session deleted)."""
        from accounts.models.organization import Organization
        from tracer.models.observation_span import ObservationSpan
        from tracer.models.project import Project, ProjectSourceChoices
        from tracer.models.trace import Trace
        from tracer.models.trace_session import TraceSession

        client = _ch_client()

        with django_db_blocker.unblock():
            assert (
                _count(client, "SELECT count() FROM trace_sessions FINAL")
                == BASELINE_TRACE_SESSIONS
            )
            assert (
                _count(client, "SELECT count() FROM trace_session_id_remap FINAL")
                == BASELINE_REMAP
            )
            assert _count(client, "SELECT count() FROM spans FINAL") == BASELINE_SPANS

            org = Organization.objects.first()
            if org is None:
                pytest.skip("no Organization in pg-test for the Site-1 project")

            project = Project.objects.create(
                organization=org,
                name=f"sliceF-bulkselect-{uuid.uuid4().hex[:8]}",
                model_type="GenerativeLLM",
                trace_type="observe",
                source=ProjectSourceChoices.PROTOTYPE.value,
            )
            proj = str(project.id)
            other_proj = str(uuid.uuid4())  # foreign tenant (CH-only, no PG row)
            version = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
            fs_hist = datetime(2025, 1, 1, 1, 1, 1, tzinfo=UTC)
            fs_strad = datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC)
            fs_new = datetime(2025, 3, 3, 6, 7, 8, tzinfo=UTC)
            hist_id = uuid.uuid4()
            strad_old = str(uuid.uuid4())
            strad_new = str(uuid.uuid4())
            netnew_id = str(uuid.uuid4())
            netnew_other = str(uuid.uuid4())
            # ALL CH ids this fixture inserts — drained in finally even if setup
            # raises partway, so a partial fixture can never leak.
            ch_sids = [str(hist_id), strad_old, strad_new, netnew_id, netnew_other]
            ch_span_sids = [str(hist_id), strad_new, netnew_id, netnew_other]
            try:
                # HISTORICAL: a real PG trace_session row + a PG trace + PG span
                # under it (so the OLD PG aggregate, which scans ObservationSpan
                # by trace__session_id, surfaces it) + matching CH curated row +
                # CH span (so the NEW path surfaces it too). The parity anchor.
                TraceSession.objects.create(
                    id=hist_id, project=project, name="hist-ext"
                )
                hist_trace = Trace.objects.create(project=project, session_id=hist_id)
                ObservationSpan.objects.create(
                    id=f"hist-span-{uuid.uuid4().hex}",
                    project=project,
                    trace=hist_trace,
                    name="hist-span",
                    observation_type="agent",
                    start_time=fs_hist,
                )
                _insert_session(client, proj, str(hist_id), "hist-ext", fs_hist)
                _insert_span(client, proj, str(hist_id))

                # STRADDLER.
                _insert_session(client, proj, strad_old, "strad-ext", fs_strad)
                _insert_remap(client, strad_old, strad_new, version)
                _insert_session(client, proj, strad_new, "strad-ext", fs_strad)
                _insert_span(client, proj, strad_new)

                # NET-NEW (island project): curated row only (NO PG) + span.
                _insert_session(client, proj, netnew_id, "netnew-ext", fs_new)
                _insert_span(client, proj, netnew_id)

                # NET-NEW under a FOREIGN project (leak guard).
                _insert_session(client, other_proj, netnew_other, "foreign-ext", fs_new)
                _insert_span(client, other_proj, netnew_other)

                ids = {
                    "proj": proj,
                    "org": org,
                    "other_proj": other_proj,
                    "hist_id": str(hist_id),
                    "strad_old": strad_old,
                    "strad_new": strad_new,
                    "netnew_id": netnew_id,
                    "netnew_other": netnew_other,
                }
                yield client, ids
            finally:
                # --- PG teardown (project-scoped, FK-safe, partial-safe) -------
                _purge_pg_project(project.id)
                # --- CH teardown ----------------------------------------------
                _delete_sync(
                    client,
                    "trace_sessions",
                    "trace_session_id IN %(ids)s",
                    {"ids": tuple(ch_sids)},
                )
                _delete_sync(
                    client, "trace_session_id_remap", "old_id = %(o)s", {"o": strad_old}
                )
                _delete_sync(
                    client,
                    "spans",
                    "trace_session_id IN %(ids)s",
                    {"ids": tuple(ch_span_sids)},
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

    def test_bulkselect_netnew_post_includes_head_omits(
        self, ch_sessions, django_db_blocker
    ):
        """Headline gate 1. The net-new session is OMITTED by the old PG base
        (no PG TraceSession row) but INCLUDED by the new CH resolver."""
        client, ids = ch_sessions
        with django_db_blocker.unblock():
            head = _head_bulkselect_session_ids(ids["proj"], ids["org"])
            assert ids["netnew_id"] not in head  # HEAD silently omits

            post = _post_bulkselect_session_ids(ids["proj"], ids["org"])
            assert ids["netnew_id"] in post  # POST includes

    def test_bulkselect_straddler_counted_once(self, ch_sessions, django_db_blocker):
        """A straddler appears ONCE (under its survivor/old id), never twice —
        the remap unifies its old + new id spans before the DISTINCT."""
        client, ids = ch_sessions
        with django_db_blocker.unblock():
            post = _post_bulkselect_session_ids(ids["proj"], ids["org"])
        assert ids["strad_old"] in post
        assert ids["strad_new"] not in post

    def test_bulkselect_wrong_project_excluded(self, ch_sessions, django_db_blocker):
        """A net-new session under a DIFFERENT project must NOT appear in the
        island-project-scoped bulk-select (no cross-tenant leak)."""
        client, ids = ch_sessions
        with django_db_blocker.unblock():
            post = _post_bulkselect_session_ids(ids["proj"], ids["org"])
        assert ids["netnew_other"] not in post

    def test_bulkselect_historical_parity(self, ch_sessions, django_db_blocker):
        """No regression: the historical session (old id, real PG row) that the
        OLD PG path surfaces is STILL surfaced by the CH path, and the CH result
        is a SUPERSET of the OLD result (historical ∪ net-new)."""
        client, ids = ch_sessions
        with django_db_blocker.unblock():
            head = _head_bulkselect_session_ids(ids["proj"], ids["org"])
            post = _post_bulkselect_session_ids(ids["proj"], ids["org"])
        assert ids["hist_id"] in head  # the OLD path DOES surface the historical
        assert ids["hist_id"] in post  # ...and so does the NEW path (parity)
        assert head.issubset(post)  # CH is a superset; nothing historical lost

    def test_bulkselect_exclude_ids_drops_netnew(self, ch_sessions, django_db_blocker):
        """``exclude_ids`` (the deselected-rows set) removes a session from the
        CH-derived result — proves the exclusion path composes with net-new."""
        client, ids = ch_sessions
        with django_db_blocker.unblock():
            post = _post_bulkselect_session_ids(
                ids["proj"], ids["org"], exclude_ids={ids["netnew_id"]}
            )
        assert ids["netnew_id"] not in post
        assert ids["strad_old"] in post  # a non-excluded session still present

    def test_bulkselect_cap_truncates(self, ch_sessions, django_db_blocker):
        """The CH cap+1 truncation path: ``cap`` smaller than the match count
        reports ``truncated`` and ``total_matching == cap + 1``. (The existing
        endpoint truncation test is forced onto PG by the Slice-F fallback
        fixture, so this backfills CH-path cap coverage.) The project carries 3
        sessions (hist + straddler-survivor + net-new); cap=1 truncates."""
        from model_hub.services.bulk_selection import resolve_filtered_session_ids

        client, ids = ch_sessions
        with django_db_blocker.unblock():
            result = resolve_filtered_session_ids(
                project_id=ids["proj"],
                filters=[],
                organization=ids["org"],
                cap=1,
            )
        assert result.truncated is True
        assert len(result.ids) == 1
        assert result.total_matching == 2  # cap + 1 sentinel

    def test_bulkselect_wrong_org_raises_does_not_exist(
        self, ch_sessions, django_db_blocker
    ):
        """The new upfront project scope-check raises ``Project.DoesNotExist``
        when the project is not in the org (the caller maps it to 404) — same
        outcome the old ``_build_session_base_queryset`` ``Project.get`` gave."""
        from accounts.models.organization import Organization
        from model_hub.services.bulk_selection import resolve_filtered_session_ids
        from tracer.models.project import Project

        client, ids = ch_sessions
        with django_db_blocker.unblock():
            other_org = Organization.objects.exclude(id=ids["org"].id).first()
            if other_org is None:
                pytest.skip("need a second Organization for the wrong-org guard")
            with pytest.raises(Project.DoesNotExist):
                resolve_filtered_session_ids(
                    project_id=ids["proj"],
                    filters=[],
                    organization=other_org,
                )

    def test_bulkselect_workspace_mismatch_returns_empty(
        self, ch_sessions, django_db_blocker
    ):
        """A workspace that doesn't match the project's workspace returns an
        empty result (the PG base queryset would have filtered to empty) — the
        new upfront workspace guard. The synthetic project has
        ``workspace_id=None``, so any sentinel workspace mismatches."""
        from types import SimpleNamespace

        from model_hub.services.bulk_selection import resolve_filtered_session_ids

        client, ids = ch_sessions
        sentinel_ws = SimpleNamespace(id=uuid.uuid4())
        with django_db_blocker.unblock():
            result = resolve_filtered_session_ids(
                project_id=ids["proj"],
                filters=[],
                organization=ids["org"],
                workspace=sentinel_ws,
            )
        assert result.ids == []
        assert result.total_matching == 0
        assert result.truncated is False

    # ===== SITE 2: score column config (synthetic PG Score on a net-new) =====

    @pytest.fixture()
    def pg_netnew_score(self, django_db_blocker):
        """Manufacture a self-contained Site-2 fixture in the REAL pg-test
        ``tfc`` (otherwise read-only): a synthetic label L (project P, real org),
        a HISTORICAL session HS (a real PG ``trace_session`` row under P) scored
        with L by annotator A1, and a NET-NEW session (NO PG ``trace_session``
        row) scored with L by annotator A2 — the net-new Score carries
        ``project=NULL`` (production's tracer-side write shape, so the test
        exercises the EXACT bug the literal ``project_id`` scope would cause).

        pg-test's island carries NO labels and NO project rows, so everything is
        synthetic; all rows are torn down to the EXACT prior counts. The two
        annotators are distinct so HEAD (historical-only) and POST
        (historical + net-new) row-sets differ by exactly the net-new row.
        """
        from accounts.models.organization import Organization
        from accounts.models.user import User
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.project import Project, ProjectSourceChoices
        from tracer.models.trace_session import TraceSession

        with django_db_blocker.unblock():
            org = Organization.objects.first()
            if org is None:
                pytest.skip("no Organization in pg-test for the Site-2 fixture")
            # Fetch only the id column — pg-test's snapshot predates some
            # accounts_user columns the baked model declares (e.g.
            # last_timezone), so a full-row fetch would 500 on UndefinedColumn.
            user_ids = list(User.objects.values_list("id", flat=True)[:2])
            if len(user_ids) < 2:
                pytest.skip("need 2 Users in pg-test for the Site-2 fixture")
            annot_hist_id, annot_new_id = user_ids[0], user_ids[1]

            project = Project.objects.create(
                organization=org,
                name=f"sliceF-scorecol-{uuid.uuid4().hex[:8]}",
                model_type="GenerativeLLM",
                trace_type="observe",
                source=ProjectSourceChoices.PROTOTYPE.value,
            )
            netnew_session_id = uuid.uuid4()  # NO trace_session row exists for it
            hist_score_id = uuid.uuid4()
            netnew_score_id = uuid.uuid4()
            try:
                label = AnnotationsLabels.objects.create(
                    name=f"sliceF-label-{uuid.uuid4().hex[:8]}",
                    type="thumbs_up_down",
                    organization=org,
                    project=project,
                )
                # HISTORICAL session: a real PG trace_session row under the
                # project, so the OLD subquery scope
                # (TraceSession.filter(project_id=…)) INCLUDES its score — the
                # parity anchor.
                hist_session = TraceSession.objects.create(
                    project=project, name="scorecol-hist"
                )

                before = Score.objects.filter(
                    label_id=label.id, trace_session_id__isnull=False, deleted=False
                ).count()

                # Insert the Score rows via RAW SQL (not the ORM): pg-test's
                # model_hub_score snapshot predates columns the baked Score
                # model declares (e.g. value_history), so an ORM .create() 500s
                # on UndefinedColumn. We write ONLY the columns pg-test has; the
                # HEAD/POST reads select only (label_id, annotator_id), which
                # exist. project_id is left NULL — production's tracer-side shape.
                _raw_insert_session_score(
                    score_id=hist_score_id,
                    trace_session_id=hist_session.id,
                    label_id=label.id,
                    annotator_id=annot_hist_id,
                    organization_id=org.id,
                    value={"value": "up"},
                )
                _raw_insert_session_score(
                    score_id=netnew_score_id,
                    trace_session_id=netnew_session_id,
                    label_id=label.id,
                    annotator_id=annot_new_id,
                    organization_id=org.id,
                    value={"value": "down"},
                )
                yield {
                    "label_id": label.id,
                    "project_id": project.id,
                    "hist_session_id": str(hist_session.id),
                    "hist_annotator_id": annot_hist_id,
                    "netnew_session_id": str(netnew_session_id),
                    "netnew_annotator_id": annot_new_id,
                    "netnew_score_id": netnew_score_id,
                    "before_count": before,
                }
            finally:
                # Project-scoped, FK-safe, partial-safe purge (covers BOTH the
                # historical-session score AND the net-new-session score: the
                # latter's session has no PG row but its label is under the
                # project, so the label-based score delete catches it).
                _purge_pg_project(project.id)
                after = Score.objects.filter(
                    trace_session_id=netnew_session_id, deleted=False
                ).count()
                assert after == 0, (
                    "pg-test Score baseline not restored "
                    f"(synthetic net-new score still present: {after})"
                )

    def test_scorecol_netnew_post_includes_head_drops(
        self, pg_netnew_score, django_db_blocker
    ):
        """Headline gate 2. The net-new-session Score is DROPPED by the old
        subquery scope (its session has no PG row) but INCLUDED by the new
        label-only scope."""
        f = pg_netnew_score
        label_ids = [f["label_id"]]
        netnew_row = (str(f["label_id"]), str(f["netnew_annotator_id"]))

        with django_db_blocker.unblock():
            head = _head_scorecol_rows(label_ids, f["project_id"])
            post = _post_scorecol_rows(label_ids, f["project_id"])

        assert netnew_row not in head  # HEAD subquery drops the net-new score
        assert netnew_row in post  # POST label-only surfaces it

    def test_scorecol_historical_parity_and_no_overbroadening(
        self, pg_netnew_score, django_db_blocker
    ):
        """Scope-equivalence proof: the HISTORICAL score (a session WITH a PG
        trace_session row) is surfaced IDENTICALLY by the OLD subquery scope and
        the NEW label-only scope — so dropping the subquery neither loses
        historical rows NOR over-broadens. The ONLY delta POST adds over HEAD is
        the net-new row."""
        f = pg_netnew_score
        label_ids = [f["label_id"]]
        hist_row = (str(f["label_id"]), str(f["hist_annotator_id"]))
        netnew_row = (str(f["label_id"]), str(f["netnew_annotator_id"]))

        with django_db_blocker.unblock():
            head = _head_scorecol_rows(label_ids, f["project_id"])
            post = _post_scorecol_rows(label_ids, f["project_id"])

        # The historical score appears in BOTH scopes (parity anchor).
        assert hist_row in head
        assert hist_row in post
        # Restricted to historical (drop the net-new from POST), the two scopes
        # are byte-identical → no historical loss, no over-broadening.
        assert (post - {netnew_row}) == head, (
            "label-only scope diverges from the subquery scope on historical "
            f"rows (head={head}, post_historical={post - {netnew_row}})"
        )
        # And the net-new row is the single addition POST makes.
        assert post - head == {netnew_row}

    def test_scorecol_project_id_scope_would_drop_nullproject_score(
        self, pg_netnew_score, django_db_blocker
    ):
        """Proof that the LITERAL ``Score.project_id=project_id`` alternative is
        wrong: the synthetic net-new Score has ``project=NULL`` (production
        shape), so a ``project_id`` filter excludes it — whereas the chosen
        label-only scope keeps it. This is why Slice F drops the subquery rather
        than swapping it for a project_id filter."""
        from model_hub.models.score import Score

        f = pg_netnew_score
        with django_db_blocker.unblock():
            kept_by_label_only = (
                Score.objects.filter(
                    label_id=f["label_id"],
                    trace_session_id__isnull=False,
                    deleted=False,
                )
                .filter(id=f["netnew_score_id"])
                .exists()
            )
            kept_by_project_scope = (
                Score.objects.filter(
                    label_id=f["label_id"],
                    trace_session_id__isnull=False,
                    deleted=False,
                    project_id=f["project_id"],
                )
                .filter(id=f["netnew_score_id"])
                .exists()
            )
        assert kept_by_label_only is True
        assert kept_by_project_scope is False  # the dropped-row bug, demonstrated
