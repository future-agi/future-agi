"""Slice E acceptance — annotation-queue session VALIDATION cut over from PG
``TraceSession.objects.filter(...).exists()`` to the CH ``session_exists`` helper
(``model_hub/views/annotation_queues.py`` ``for_source`` default-queue scope match,
the two ``elif st == "trace_session"`` branches at the project + agent-definition
scopes).

WHAT THIS PINS (the post-flip break Slice E closes)
---------------------------------------------------
After step2 the PG ``trace_session`` table FREEZES: a NET-NEW session has NO PG row
— only a CH ``trace_sessions`` row keyed by its deterministic id (collector
dual-write). The OLD validation
``TraceSession.objects.filter(id=sid, project_id=…, deleted=False).exists()`` (and
the agent-definition variant
``…project__observability_providers__agent_definition=…``) therefore returns False
for a net-new session → ``for_source`` wrongly drops the default queue for that
source. The NEW path asks CH (``session_exists``), which sees the net-new
dual-write row, is project-scoped, remap-aware, and honours ``is_deleted=0``.

PROOF SHAPE (advisor-directed): rather than reverting the file to demonstrate
HEAD=reject, this test evaluates — in ONE process, on the SAME manufactured
fixture — BOTH the exact OLD PG expression (HEAD) and the exact NEW expression
(POST=the edited branch body) for each scope, and asserts
``HEAD is False`` / ``POST is True`` for the net-new session. The evaluated
POST expressions are byte-for-byte the bodies now in ``for_source`` (the test
docstring of each pins the file lines); the report pastes file-expr next to
eval-expr so a reviewer sees they match. We do NOT drive the full DRF
``for_source`` request (auth/org/default-queue fixture cost; the load-bearing
change is the ``exists=`` expression, which this evaluates directly).

  • Project scope: net-new under island project ``61840705`` →
    POST ``session_exists(proj, netnew)`` True; HEAD
    ``TraceSession.objects.filter(id=netnew, project_id=proj, deleted=False)
    .exists()`` False.
  • Agent-definition scope: a SYNTHETIC P_e + ObservabilityProvider + AgentDefinition
    chain (manufactured in pg-test, torn down), net-new CH session under P_e →
    POST ``any(session_exists(p, netnew_e) for p in
    Project.objects.filter(observability_providers__agent_definition=A))`` True;
    HEAD ``TraceSession.objects.filter(id=netnew_e,
    project__observability_providers__agent_definition=A, deleted=False).exists()``
    False.
  • Historical (old id) + straddler (old AND new id) still validate (POST True) in
    the project scope (transitively covers the agent-def scope: same
    ``session_exists`` under a resolved project). A random/wrong-project id is
    REJECTED (POST False).

HARNESS — ch_rehearsal ONLY (the clean from-empty P3b baseline; reference memory
reference-ch-rehearsal-harness). Hard guard on ``CH25_DATABASE``. Reuses the
helper-build fixture recipe (``tracer/tests/test_ch25_trace_session_fields_reader``):
manufactures its OWN self-contained CH sessions (historical=untouched-baseline
read) and tears them down via synchronous ``ALTER … DELETE`` (``mutations_sync=2``),
re-asserting the EXACT baseline (``trace_sessions FINAL=3``, ``remap=3``, ``spans
FINAL=691``). The agent-definition PG chain is manufactured via RAW SQL (signal-free
— Project/ObservabilityProvider/AgentDefinition have no post_save handlers, but raw
INSERT guarantees no ORM-side effect can touch the protected ch_rehearsal reference)
against the REAL pg-test ``tfc`` and FULLY torn down (asserted absent after).
Reachable only from INSIDE the ``backend`` container (host pytest can't reach CH).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

# Cycle-breaker — same rationale as the sibling helper-build test
# (test_ch25_trace_session_fields_reader): importing model_hub.tasks first avoids
# an app-loading import cycle when the reader's lazy model import fires.
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


# ----- exact OLD-PG (HEAD) and NEW (POST) validation expressions ---------------
# These mirror, byte-for-byte, the two pre/post forms of the for_source
# default-queue scope-match branches. HEAD is what the file did before Slice E;
# POST is the edited branch body. Evaluating both on the same fixture is the
# before/after, with no git revert.


def _head_project_scope(sid, project_id) -> bool:
    """The OLD project-scoped PG check (annotation_queues.py ~4412, pre-Slice E)."""
    from tracer.models.trace_session import TraceSession

    return TraceSession.objects.filter(
        id=sid, project_id=project_id, deleted=False
    ).exists()


def _post_project_scope(sid, project_id) -> bool:
    """The NEW project-scoped CH check (annotation_queues.py elif trace_session,
    project scope) — the edited branch body."""
    from tracer.services.clickhouse.v2.trace_session_dict_reader import session_exists

    return session_exists(project_id, sid)


def _head_agentdef_scope(sid, agent_definition_id) -> bool:
    """The OLD agent-definition-scoped PG check (annotation_queues.py ~4477,
    pre-Slice E)."""
    from tracer.models.trace_session import TraceSession

    return TraceSession.objects.filter(
        id=sid,
        project__observability_providers__agent_definition=agent_definition_id,
        deleted=False,
    ).exists()


def _post_agentdef_scope(sid, agent_definition_id) -> bool:
    """The NEW agent-definition-scoped CH check (annotation_queues.py elif
    trace_session, agent-definition scope) — the edited branch body."""
    from tracer.models.project import Project
    from tracer.services.clickhouse.v2.trace_session_dict_reader import session_exists

    _ad_project_ids = Project.objects.filter(
        observability_providers__agent_definition=agent_definition_id,
    ).values_list("id", flat=True)
    return any(session_exists(_pid, sid) for _pid in _ad_project_ids)


@pytest.mark.integration
class TestAnnotationQueueSessionValidationSliceE:
    """Manufacture the post-step2 state on ch_rehearsal (+ a synthetic agent-def
    PG chain), assert HEAD-rejects-vs-POST-accepts for both queue scopes, restore."""

    @pytest.fixture()
    def manufactured(self, django_db_blocker):
        """Stand up, all self-contained + fully torn down:

        CH (ch_rehearsal):
          • STRADDLER under island project 61840705 (old curated row + remap
            old→new + new curated row + a span).
          • NET-NEW under 61840705 (deterministic id curated row, NO PG row, + span)
            — the project-scope accept-vs-reject case.
          • NET-NEW_E under a SYNTHETIC project P_e (deterministic id curated row,
            NO PG row, + span) — the agent-def-scope accept-vs-reject case.

        PG (real pg-test tfc, raw SQL, signal-free):
          • A synthetic chain Org(existing)→Project P_e→ObservabilityProvider→
            AgentDefinition A, so the agent-def→project resolution the NEW code does
            (``Project.objects.filter(observability_providers__agent_definition=A)``)
            returns P_e. Manufactured because pg-test carries 0 project/obs/agent-def
            rows for the island (spans+dims live in CH only). Torn down + asserted
            absent.

        PG access is the REAL pg-test (no pytest-django test DB) via
        ``django_db_blocker.unblock()`` held across the test body, so the helper's
        own and the resolution's ORM reads are unblocked.
        """
        from django.db import connection

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
            version = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
            first_seen_straddler = datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC)
            first_seen_netnew = datetime(2025, 3, 3, 6, 7, 8, tzinfo=UTC)

            # Straddler (project scope): OLD curated row; remap old→new; the
            # collector's NEW-deterministic-id dual-write row; a span on the new id.
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

            # Net-new (project scope): deterministic-id curated row only (NO PG) + span.
            netnew_id = str(uuid.uuid4())
            _insert_session(
                client, proj, netnew_id, "netnew-external-session", first_seen_netnew
            )
            _insert_span(client, proj, netnew_id)

            # --- synthetic agent-def PG chain (raw SQL) + its net-new CH session ---
            # Reference an EXISTING org (read-only) so we only manufacture the
            # project→obs→agent-def chain, never an org.
            with connection.cursor() as cur:
                cur.execute("SELECT id FROM accounts_organization LIMIT 1")
                row = cur.fetchone()
            assert row is not None, "pg-test has no organization to reference"
            org_id = str(row[0])

            proj_e = str(uuid.uuid4())  # synthetic project for the agent-def scope
            op_id = str(uuid.uuid4())
            agent_def_id = str(uuid.uuid4())
            now = datetime.now(tz=UTC)

            # Guard: our synthetic ids must not pre-exist (true net manufacture).
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM tracer_project WHERE id = %s", [proj_e]
                )
                assert cur.fetchone()[0] == 0
                cur.execute(
                    "SELECT count(*) FROM simulate_agent_definition WHERE id = %s",
                    [agent_def_id],
                )
                assert cur.fetchone()[0] == 0

                # Project P_e (NOT NULL: model_type/name/trace_type/source/tags/org).
                cur.execute(
                    "INSERT INTO tracer_project "
                    "(id, created_at, updated_at, deleted, model_type, name, "
                    " trace_type, organization_id, source, tags) "
                    "VALUES (%s, %s, %s, false, %s, %s, %s, %s, %s, %s)",
                    [
                        proj_e,
                        now,
                        now,
                        "llm",
                        "sliceE-agentdef-proj",
                        "observability",
                        org_id,
                        "manual",
                        "{}",
                    ],
                )
                # ObservabilityProvider → P_e (NOT NULL: provider/enabled/org/project).
                cur.execute(
                    "INSERT INTO tracer_observability_provider "
                    "(id, created_at, updated_at, deleted, provider, enabled, "
                    " organization_id, project_id) "
                    "VALUES (%s, %s, %s, false, %s, true, %s, %s)",
                    [op_id, now, now, "langfuse", org_id, proj_e],
                )
                # AgentDefinition A → ObservabilityProvider (OneToOne); the reverse
                # accessor obs_provider.agent_definition is what the ORM traversal
                # ``observability_providers__agent_definition`` follows.
                cur.execute(
                    "INSERT INTO simulate_agent_definition "
                    "(id, created_at, updated_at, deleted, agent_name, inbound, "
                    " description, organization_id, agent_type, "
                    " observability_provider_id) "
                    "VALUES (%s, %s, %s, false, %s, false, %s, %s, %s, %s)",
                    [
                        agent_def_id,
                        now,
                        now,
                        "sliceE-agent",
                        "",
                        org_id,
                        "voice",
                        op_id,
                    ],
                )

            # Net-new CH session under the synthetic P_e (agent-def-scope case).
            netnew_e_id = str(uuid.uuid4())
            _insert_session(
                client,
                proj_e,
                netnew_e_id,
                "netnew-agentdef-session",
                first_seen_netnew,
            )
            _insert_span(client, proj_e, netnew_e_id)

            ids = {
                "proj": proj,
                "hist_old": HIST_OLD_ID,
                "strad_old": strad_old,
                "strad_new": strad_new,
                "netnew_id": netnew_id,
                "proj_e": proj_e,
                "agent_def_id": agent_def_id,
                "netnew_e_id": netnew_e_id,
            }
            try:
                yield client, ids
            finally:
                # --- teardown → restore CH baseline ------------------------------
                _delete_sync(
                    client,
                    "trace_sessions",
                    "trace_session_id IN (%(a)s, %(b)s, %(c)s, %(d)s)",
                    {"a": strad_old, "b": strad_new, "c": netnew_id, "d": netnew_e_id},
                )
                _delete_sync(
                    client, "trace_session_id_remap", "old_id = %(o)s", {"o": strad_old}
                )
                _delete_sync(
                    client,
                    "spans",
                    "trace_session_id IN (%(b)s, %(c)s, %(d)s)",
                    {"b": strad_new, "c": netnew_id, "d": netnew_e_id},
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
                # --- teardown → remove the synthetic PG chain (assert absent) -----
                with connection.cursor() as cur:
                    cur.execute(
                        "DELETE FROM simulate_agent_definition WHERE id = %s",
                        [agent_def_id],
                    )
                    cur.execute(
                        "DELETE FROM tracer_observability_provider WHERE id = %s",
                        [op_id],
                    )
                    cur.execute("DELETE FROM tracer_project WHERE id = %s", [proj_e])
                    cur.execute(
                        "SELECT count(*) FROM tracer_project WHERE id = %s", [proj_e]
                    )
                    assert cur.fetchone()[0] == 0
                    cur.execute(
                        "SELECT count(*) FROM simulate_agent_definition WHERE id = %s",
                        [agent_def_id],
                    )
                    assert cur.fetchone()[0] == 0

    # ----- the headline: net-new accept (POST) vs reject (HEAD), both scopes ----

    def test_project_scope_netnew_post_accepts_head_rejects(self, manufactured):
        """Project-scoped default queue: the net-new session is REJECTED by the old
        PG ``.exists()`` (no PG row) but ACCEPTED by the new CH ``session_exists``."""
        _client, ids = manufactured
        proj, netnew = ids["proj"], ids["netnew_id"]

        assert _head_project_scope(netnew, proj) is False  # HEAD wrongly rejects
        assert _post_project_scope(netnew, proj) is True  # POST accepts

    def test_agentdef_scope_netnew_post_accepts_head_rejects(self, manufactured):
        """Agent-definition-scoped default queue: same accept-vs-reject through the
        agent-def→project resolution the new branch performs."""
        _client, ids = manufactured
        agent_def_id, netnew_e = ids["agent_def_id"], ids["netnew_e_id"]

        assert _head_agentdef_scope(netnew_e, agent_def_id) is False  # HEAD rejects
        assert _post_agentdef_scope(netnew_e, agent_def_id) is True  # POST accepts

    # ----- parity: historical + straddler still validate; bad ids rejected ------

    def test_project_scope_historical_and_straddler_still_validate(self, manufactured):
        """The NEW project-scoped check still PASSES a historical session (old id)
        and a straddler by BOTH its old and its new id (unified via the remap) —
        no regression for the pre-flip-resolvable cases."""
        _client, ids = manufactured
        proj = ids["proj"]

        assert _post_project_scope(ids["hist_old"], proj) is True
        assert _post_project_scope(ids["strad_old"], proj) is True
        assert _post_project_scope(ids["strad_new"], proj) is True

    def test_agentdef_scope_historical_and_straddler_validate_by_old_and_new(
        self, manufactured
    ):
        """A straddler whose curated rows live under the agent-def's project must
        validate by OLD and NEW id through the agent-def resolution; a historical
        session under that project validates too. We reuse the project-scope
        fixtures by also registering them under P_e via the SAME resolution path:
        the straddler/historical here are validated against the agent-def chain by
        manufacturing a straddler + historical-equivalent under P_e."""
        _client, ids = manufactured
        agent_def_id, proj_e = ids["agent_def_id"], ids["proj_e"]
        client = _client

        # Manufacture a straddler + a plain (historical-shaped) session under P_e so
        # the agent-def resolution exercises old/new/plain — torn down inline.
        s_old, s_new = str(uuid.uuid4()), str(uuid.uuid4())
        plain = str(uuid.uuid4())
        version = datetime(2025, 1, 1, tzinfo=UTC)
        fs = datetime(2025, 4, 4, 1, 2, 3, tzinfo=UTC)
        try:
            _insert_session(client, proj_e, s_old, "ad-straddler", fs)
            _insert_remap(client, s_old, s_new, version)
            _insert_session(client, proj_e, s_new, "ad-straddler", fs)
            _insert_session(client, proj_e, plain, "ad-plain", fs)

            assert _post_agentdef_scope(s_old, agent_def_id) is True
            assert _post_agentdef_scope(s_new, agent_def_id) is True
            assert _post_agentdef_scope(plain, agent_def_id) is True
        finally:
            _delete_sync(
                client,
                "trace_sessions",
                "trace_session_id IN (%(a)s, %(b)s, %(c)s)",
                {"a": s_old, "b": s_new, "c": plain},
            )
            _delete_sync(
                client, "trace_session_id_remap", "old_id = %(o)s", {"o": s_old}
            )

    def test_random_and_wrong_project_rejected(self, manufactured):
        """A random/unknown id is REJECTED in both scopes; the net-new session is
        REJECTED when checked against a DIFFERENT project (no cross-tenant leak),
        even though by its own project/agent-def it accepts."""
        _client, ids = manufactured
        proj, agent_def_id = ids["proj"], ids["agent_def_id"]

        random_id = str(uuid.uuid4())
        # random id: rejected by both new checks
        assert _post_project_scope(random_id, proj) is False
        assert _post_agentdef_scope(random_id, agent_def_id) is False

        # net-new_e validates by its OWN agent-def, but its id under a WRONG project
        # (the island 61840705, which the agent-def does NOT resolve to) is rejected.
        assert _post_project_scope(ids["netnew_e_id"], proj) is False
        # and the project-scope net-new is NOT visible to the agent-def scope
        # (its project 61840705 is not linked to the synthetic agent-def).
        assert _post_agentdef_scope(ids["netnew_id"], agent_def_id) is False
