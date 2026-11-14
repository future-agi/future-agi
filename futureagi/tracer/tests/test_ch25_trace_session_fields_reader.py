"""Integration acceptance for the P3b-step2 CH session existence/fields helper
(``trace_session_dict_reader.session_exists`` / ``resolve_session_fields``) — the
shared building block Slices C/D/E/F need.

WHAT THIS PINS (the post-step2 break the helper closes)
-------------------------------------------------------
After step2 the PG ``trace_session`` table FREEZES: a net-new session has NO PG
row — only a CH ``trace_sessions`` row keyed by its DETERMINISTIC id (the
collector dual-write). A *straddler* (an identity that already had an OLD random
id) keeps its old-keyed curated row, gains a NEW-deterministic-id ``trace_sessions``
row, and a ``trace_session_id_remap`` ``old→new`` bridge row. The shipped
``resolve_external_session_ids`` is forward-label-only and reads the (stale) dict;
it cannot answer EXISTENCE or return ``first_seen``, and a PG ``.get`` 404s the
net-new / straddler-by-new-id session.

So this test MANUFACTURES that post-step2 state on the clean ch_rehearsal
baseline and asserts the helper sees it:

  • ``session_exists`` → True for historical (old id), straddler (BOTH old & new
    id), net-new (deterministic id); False for a random UUID + wrong project.
  • ``resolve_session_fields`` → historical ``external_session_id`` matches the
    curated row; straddler returns ONE unified entity whether queried by old or
    new id; net-new returns its ``external_session_id`` (a PG ``.get`` would 404);
    an overlay row surfaces its ``bookmarked``/``display_name`` override — INCLUDING
    a straddler bookmarked on its OLD id but queried by its NEW id (the
    resolved-id overlay-keying correctness point) — and a session WITHOUT an
    overlay returns ``bookmarked=False`` / ``display_name=None``.

HARNESS — ch_rehearsal ONLY (the clean from-empty P3b baseline). The test refuses
to run anywhere else (a hard guard on ``CH25_DATABASE``), reads pg-test/real CH
data read-only, manufactures its OWN self-contained fixtures (so "historical" is a
pure untouched-baseline read), and tears them ALL down — CH via synchronous
``ALTER … DELETE`` (``mutations_sync=2``), PG overlay rows via ``.delete()`` — then
re-asserts the EXACT baseline counts (``trace_sessions FINAL=3``, ``remap=3``,
``spans FINAL=691``). It is reachable only from INSIDE the ``backend`` container
(host pytest can't reach CH), mirroring ``test_trace_session_ch_query.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

# Cycle-breaker — same rationale as the sibling test_trace_session_ch_query /
# test_eval_task_runtime (importing model_hub.tasks first avoids an app-loading
# import cycle when the reader's lazy model import fires).
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


@pytest.mark.integration
class TestSessionFieldsReaderOnRehearsal:
    """Manufacture the post-step2 state on ch_rehearsal, assert the helper, restore."""

    @pytest.fixture()
    def manufactured(self, django_db_blocker):
        """Stand up a STRADDLER (self-contained: old-id curated row + remap old→new
        + new-id curated row + a span) and a NET-NEW session (deterministic-id
        curated row, NO PG row, + a span), plus the PG overlay row the field
        assertions need. Tears everything down to the EXACT baseline.

        Real-vs-manufactured split (advisor): only ``9999e6c4`` is read as
        historical — it legitimately exists pre-flip. The straddler and net-new
        do NOT exist pre-flip by definition, so they are MANUFACTURED here and
        torn down. We do NOT lean on any pre-existing overlay row (ambient,
        undocumented, mutable) for assertions.

        PG: the ORM hits the REAL pg-test ``tfc`` (no pytest-django test DB) via
        ``django_db_blocker.unblock()`` held open across the test body, so the
        helper's own ``TraceSessionOverlay.objects`` read is unblocked too. The
        overlay table is NOT empty on pg-test (it carries pre-existing slice-2b
        rows); we SNAPSHOT it, assert our random ids are absent, and restore to
        that snapshot count (delete ONLY our row) — never touching the others.
        """
        from tracer.models.trace_session import TraceSessionOverlay

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

            # --- snapshot the pre-existing overlay set (preserve, don't depend) ---
            overlay_baseline_count = TraceSessionOverlay.objects.count()

            proj = HIST_PROJECT_ID  # a real project so project-scoping is exercised
            version = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
            first_seen_straddler = datetime(2025, 2, 2, 3, 4, 5, tzinfo=UTC)
            first_seen_netnew = datetime(2025, 3, 3, 6, 7, 8, tzinfo=UTC)

            # Straddler: OLD curated row keyed by old id; remap old→new; the
            # collector's NEW-deterministic-id dual-write row; a span on the new id.
            strad_old = str(uuid.uuid4())
            strad_new = str(uuid.uuid4())
            strad_external = "straddler-external-session"
            # guard: our random ids must not collide with a pre-existing overlay
            assert not TraceSessionOverlay.objects.filter(
                trace_session_id__in=[strad_old, strad_new]
            ).exists()
            _insert_session(
                client, proj, strad_old, strad_external, first_seen_straddler
            )
            _insert_remap(client, strad_old, strad_new, version)
            _insert_session(
                client, proj, strad_new, strad_external, first_seen_straddler
            )
            _insert_span(client, proj, strad_new)

            # Net-new: deterministic-id curated row only (NO PG row) + span.
            netnew_id = str(uuid.uuid4())
            netnew_external = "netnew-external-session"
            _insert_session(client, proj, netnew_id, netnew_external, first_seen_netnew)
            _insert_span(client, proj, netnew_id)

            # PG overlay: bookmark/rename the straddler on its OLD (survivor) id —
            # the field read must surface it even when the straddler is queried by
            # its NEW id (resolved-id overlay-keying). Net-new + historical get NO
            # overlay row → default bookmarked=False/display_name=None.
            TraceSessionOverlay.objects.create(
                trace_session_id=strad_old,
                project_id=proj,
                bookmarked=True,
                display_name="straddler-renamed",
            )

            ids = {
                "proj": proj,
                "hist_old": HIST_OLD_ID,
                "strad_old": strad_old,
                "strad_new": strad_new,
                "strad_external": strad_external,
                "netnew_id": netnew_id,
                "netnew_external": netnew_external,
            }
            try:
                yield client, ids
            finally:
                # --- teardown → restore CH baseline + overlay snapshot -----------
                # Delete ONLY our overlay row (preserve the pre-existing rows).
                TraceSessionOverlay.objects.filter(trace_session_id=strad_old).delete()
                _delete_sync(
                    client,
                    "trace_sessions",
                    "trace_session_id IN (%(a)s, %(b)s, %(c)s)",
                    {"a": strad_old, "b": strad_new, "c": netnew_id},
                )
                _delete_sync(
                    client,
                    "trace_session_id_remap",
                    "old_id = %(o)s",
                    {"o": strad_old},
                )
                _delete_sync(
                    client,
                    "spans",
                    "trace_session_id IN (%(b)s, %(c)s)",
                    {"b": strad_new, "c": netnew_id},
                )
                # CH baseline restored, exactly.
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
                # Overlay restored to its pre-existing snapshot (our row gone).
                assert TraceSessionOverlay.objects.count() == overlay_baseline_count
                assert not TraceSessionOverlay.objects.filter(
                    trace_session_id=strad_old
                ).exists()

    # ----- session_exists -----------------------------------------------------

    def test_session_exists_all_states(self, manufactured):
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            session_exists,
        )

        _client, ids = manufactured
        proj = ids["proj"]

        # historical by its old id
        assert session_exists(proj, ids["hist_old"]) is True
        # straddler by BOTH old and new id (unified via the remap)
        assert session_exists(proj, ids["strad_old"]) is True
        assert session_exists(proj, ids["strad_new"]) is True
        # net-new by its deterministic id (a PG .get would 404)
        assert session_exists(proj, ids["netnew_id"]) is True

        # negatives
        assert session_exists(proj, str(uuid.uuid4())) is False  # random uuid
        assert session_exists(proj, None) is False
        assert session_exists(None, ids["hist_old"]) is False

    def test_session_exists_is_project_scoped(self, manufactured):
        """A session id is globally unique but the existence check must be
        project-scoped — a different project answers False (no cross-tenant leak)."""
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            session_exists,
        )

        _client, ids = manufactured
        other_project = str(uuid.uuid4())
        assert session_exists(other_project, ids["netnew_id"]) is False
        assert session_exists(other_project, ids["strad_new"]) is False
        # ...but the real project still answers True.
        assert session_exists(ids["proj"], ids["netnew_id"]) is True

    # ----- resolve_session_fields --------------------------------------------

    def test_resolve_fields_historical_matches_curated(self, manufactured):
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        _client, ids = manufactured
        out = resolve_session_fields([ids["hist_old"]])
        assert ids["hist_old"] in out
        rec = out[ids["hist_old"]]
        assert rec["external_session_id"] == HIST_EXTERNAL
        assert rec["first_seen"] is not None
        # no overlay row on the baseline session → default
        assert rec["bookmarked"] is False
        assert rec["display_name"] is None

    def test_resolve_fields_straddler_unified_by_old_or_new(self, manufactured):
        """ONE unified entity whether queried by old or new id, AND the overlay
        (written on the OLD id) surfaces when queried by the NEW id — the
        resolved-id overlay-keying correctness point."""
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        _client, ids = manufactured
        out = resolve_session_fields([ids["strad_old"], ids["strad_new"]])

        # both input ids present, each pointing at the SAME logical entity
        assert ids["strad_old"] in out
        assert ids["strad_new"] in out
        by_old = out[ids["strad_old"]]
        by_new = out[ids["strad_new"]]

        for rec in (by_old, by_new):
            assert rec["external_session_id"] == ids["strad_external"]
            assert rec["first_seen"] is not None
            # overlay written on the OLD id resolves for BOTH query ids
            assert rec["bookmarked"] is True
            assert rec["display_name"] == "straddler-renamed"

        # identical curated identity regardless of which id was used
        assert by_old["external_session_id"] == by_new["external_session_id"]
        assert by_old["first_seen"] == by_new["first_seen"]

    def test_resolve_fields_netnew_returns_external_no_overlay(self, manufactured):
        """A net-new session (no PG row — a PG .get would 404) returns its
        external_session_id; with no overlay row → bookmarked=False/display_name=None."""
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        _client, ids = manufactured
        out = resolve_session_fields([ids["netnew_id"]])
        assert ids["netnew_id"] in out
        rec = out[ids["netnew_id"]]
        assert rec["external_session_id"] == ids["netnew_external"]
        assert rec["first_seen"] is not None
        assert rec["bookmarked"] is False
        assert rec["display_name"] is None

    def test_resolve_fields_missing_id_absent(self, manufactured):
        """A random/unknown id is ABSENT from the result (caller decides 404);
        empty input → empty dict with no CH round-trip."""
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        _client, _ids = manufactured
        random_id = str(uuid.uuid4())
        out = resolve_session_fields([random_id])
        assert random_id not in out
        assert out == {}
        assert resolve_session_fields([]) == {}
        assert resolve_session_fields([None, ""]) == {}

    def test_resolve_fields_batch_mixed(self, manufactured):
        """One batched call resolves historical + straddler(old&new) + net-new and
        drops an unknown id — the Slice C/D batch shape."""
        from tracer.services.clickhouse.v2.trace_session_dict_reader import (
            resolve_session_fields,
        )

        _client, ids = manufactured
        unknown = str(uuid.uuid4())
        out = resolve_session_fields(
            [
                ids["hist_old"],
                ids["strad_old"],
                ids["strad_new"],
                ids["netnew_id"],
                unknown,
            ]
        )
        assert set(out) == {
            ids["hist_old"],
            ids["strad_old"],
            ids["strad_new"],
            ids["netnew_id"],
        }
        assert unknown not in out
        assert out[ids["hist_old"]]["external_session_id"] == HIST_EXTERNAL
        assert out[ids["netnew_id"]]["external_session_id"] == ids["netnew_external"]
        assert out[ids["strad_new"]]["bookmarked"] is True
