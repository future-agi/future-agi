"""P3b step2 (THE FLIP) acceptance gates — PG-write correctness.

Exercises the REAL ingest stamp logic with the ClickHouse client FAKED, so these
assert pure-PG column state / failure-injection (no ch_rehearsal dependency):

  • Gate G (PG parts): a freshly-ingested net-new user+session carries the
    DETERMINISTIC end_user_id / trace_session_id stamped into the PG FK COLUMNS.
  • STAMP: those populated columns make `filter(session_id__in=[ts_id])` /
    `filter(end_user_id__in=[eu_id])` return the net-new rows (the §FK-COLUMN-reads
    protection the migration relies on).
  • §11.1a sentinel: user_id_type=None → the `or ''` sentinel id (the same id the
    committed read-side remap/end_users expects).
  • E (dual-write best-effort): a failing CH client on the curated mirror does NOT
    break ingestion (PG span still created, no raise) — forced through on_commit.
  • Bulk path: the riskiest setattr/bulk_update stamp (`session_id` attname →
    `session` field name) does not raise and persists the id.

The CH-span `trace_session_id == ts_id` sub-assertion of Gate G is NOT here — it
exists only via the `t.session_id` materialization join and is proven on
ch_rehearsal (Stage 2), not with CH mocked.

Plain pytest functions (NOT django.test.TestCase) so pytest-django fixtures
(`db`, `monkeypatch`, `observe_project`, `django_capture_on_commit_callbacks`)
compose cleanly; the on-commit capture fixture is what keeps the E gate
non-vacuous (the curated mirror runs in transaction.on_commit).
"""

from __future__ import annotations

import uuid

import pytest

from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_end_user_id,
    deterministic_trace_session_id,
)


def _otel_span(
    *,
    project_name: str,
    trace_id: str,
    span_id: str,
    user_id: str | None = None,
    user_id_type: str | None = None,
    session_name: str | None = None,
    parent_id: str | None = None,
) -> dict:
    """Minimal OTel-span dict accepted by convert_otel_span_to_observation_span."""
    attributes: dict = {
        "fi.span.kind": "LLM",
        "input.value": "hello",
        "output.value": "world",
    }
    if user_id is not None:
        attributes["user.id"] = user_id
    if user_id_type is not None:
        attributes["user.id.type"] = user_id_type
    if session_name is not None:
        attributes["session.id"] = session_name
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_id": parent_id,
        "name": "root-span",
        "start_time": 1_700_000_000_000_000_000,
        "end_time": 1_700_000_001_000_000_000,
        "attributes": attributes,
        "events": [],
        "status": "OK",
        "project_name": project_name,
        "project_type": "observe",
    }


class _FakeCHClient:
    """Records inserts; optionally raises on insert (E gate)."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.inserts: list[tuple] = []

    def insert(self, table, rows, column_names=None):
        if self.fail:
            raise RuntimeError("forced CH outage")
        self.inserts.append((table, rows, column_names))

    def close(self):
        pass


@pytest.fixture
def fake_ch(monkeypatch):
    """Dual-write ON, but every CH client is a FAKE — nothing hits real CH and we
    can inject E's failure. Returns the fake so tests can flip ``.fail`` / inspect
    ``.inserts``."""
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
    fake = _FakeCHClient()
    import tracer.services.clickhouse.v2.curated_writer as cw
    import tracer.services.clickhouse.v2.trace_writer as tw

    monkeypatch.setattr(cw, "_get_client", lambda: fake)
    monkeypatch.setattr(tw, "_get_client", lambda: fake)
    return fake


def _ingest(project, org_id, workspace_id, capture, **kw):
    from tracer.utils.create_otel_span import create_single_otel_span

    data = _otel_span(project_name=project.name, **kw)
    with capture(execute=True):
        return create_single_otel_span(data, org_id, workspace_id)


# ── Gate G (PG) + STAMP ─────────────────────────────────────────────────────
@pytest.mark.django_db
def test_gate_g_and_stamp_net_new_user_and_session(
    observe_project,
    organization,
    workspace,
    fake_ch,
    django_capture_on_commit_callbacks,
):
    project = observe_project
    org_id = str(organization.id)
    trace_id = uuid.uuid4().hex
    span_id = uuid.uuid4().hex
    user_id = "netnew@futureagi.com"
    session_name = f"sess-{uuid.uuid4().hex[:8]}"

    span = _ingest(
        project,
        org_id,
        str(workspace.id),
        django_capture_on_commit_callbacks,
        trace_id=trace_id,
        span_id=span_id,
        user_id=user_id,
        user_id_type="email",
        session_name=session_name,
    )

    eu_id = deterministic_end_user_id(project.id, org_id, user_id, "email")
    ts_id = deterministic_trace_session_id(project.id, session_name)

    span.refresh_from_db()
    trace = Trace.objects.get(id=span.trace_id)

    # Gate G: stamped columns == deterministic ids EXACTLY.
    assert str(span.end_user_id) == str(eu_id), (span.end_user_id, eu_id)
    assert str(trace.session_id) == str(ts_id), (trace.session_id, ts_id)

    # STAMP: the populated columns are visible to FK-column reads.
    assert Trace.objects.filter(session_id__in=[ts_id]).filter(id=trace.id).exists()
    assert (
        ObservationSpan.objects.filter(end_user_id__in=[eu_id])
        .filter(id=span.id)
        .exists()
    )

    # The curated mirror fired keyed by the deterministic ids (fake succeeded).
    tables = {t for (t, _r, _c) in fake_ch.inserts}
    assert "end_users" in tables and "trace_sessions" in tables, tables


# ── §11.1a sentinel ─────────────────────────────────────────────────────────
@pytest.mark.django_db
def test_sentinel_null_user_id_type(
    observe_project,
    organization,
    workspace,
    fake_ch,
    django_capture_on_commit_callbacks,
):
    project = observe_project
    org_id = str(organization.id)
    user_id = "no-type-user"

    span = _ingest(
        project,
        org_id,
        str(workspace.id),
        django_capture_on_commit_callbacks,
        trace_id=uuid.uuid4().hex,
        span_id=uuid.uuid4().hex,
        user_id=user_id,
        user_id_type=None,  # NULL → '' sentinel
    )

    eu_id = deterministic_end_user_id(project.id, org_id, user_id, None)
    span.refresh_from_db()
    assert str(span.end_user_id) == str(eu_id), (span.end_user_id, eu_id)


# ── E (dual-write best-effort) ──────────────────────────────────────────────
@pytest.mark.django_db
def test_e_curated_mirror_failure_does_not_break_ingestion(
    observe_project,
    organization,
    workspace,
    fake_ch,
    django_capture_on_commit_callbacks,
):
    project = observe_project
    org_id = str(organization.id)
    fake_ch.fail = True  # every CH insert raises
    user_id = "fail-user@futureagi.com"
    session_name = f"failsess-{uuid.uuid4().hex[:8]}"

    # capture(execute=True) FORCES the on_commit mirror to run with the failing
    # client. If best-effort weren't honored this would raise.
    span = _ingest(
        project,
        org_id,
        str(workspace.id),
        django_capture_on_commit_callbacks,
        trace_id=uuid.uuid4().hex,
        span_id=uuid.uuid4().hex,
        user_id=user_id,
        user_id_type="email",
        session_name=session_name,
    )

    # Ingestion completed, PG span persisted, ids still stamped.
    assert ObservationSpan.objects.filter(id=span.id).exists()
    eu_id = deterministic_end_user_id(project.id, org_id, user_id, "email")
    ts_id = deterministic_trace_session_id(project.id, session_name)
    span.refresh_from_db()
    trace = Trace.objects.get(id=span.trace_id)
    assert str(span.end_user_id) == str(eu_id)
    assert str(trace.session_id) == str(ts_id)
    # The failing client recorded NOTHING (raised before append).
    assert fake_ch.inserts == []


# ── Bulk path (the riskiest setattr/bulk_update edit) ───────────────────────
@pytest.mark.django_db
def test_bulk_resolve_ids_pure_and_deterministic(observe_project, organization):
    from tracer.utils.trace_ingestion import (
        _resolve_end_user_ids,
        _resolve_session_ids,
    )

    project = observe_project
    org_id = str(organization.id)
    session_name = f"bulk-sess-{uuid.uuid4().hex[:8]}"
    user_id = "bulk-user@futureagi.com"
    parsed = [
        {
            "session_name": session_name,
            "project": project,
            "end_user": {
                "user_id": user_id,
                "user_id_type": "email",
                "user_id_hash": "h",
                "metadata": {},
                "project": project,
            },
        }
    ]
    eu_map, eu_curated = _resolve_end_user_ids(parsed, org_id)
    ts_map, ts_curated = _resolve_session_ids(parsed)

    eu_id = deterministic_end_user_id(project.id, org_id, user_id, "email")
    ts_id = deterministic_trace_session_id(project.id, session_name)
    key = (user_id, org_id, str(project.id), "email")
    assert str(eu_map[key]) == str(eu_id)
    assert str(ts_map[(session_name, project.id)]) == str(ts_id)
    assert str(eu_curated[0].end_user_id) == str(eu_id)
    assert str(ts_curated[0].trace_session_id) == str(ts_id)
    assert eu_curated[0].user_id_type == "email"


@pytest.mark.django_db
def test_bulk_update_traces_stamps_session_id_without_error(observe_project):
    from tracer.utils.trace_ingestion import _bulk_update_traces

    project = observe_project
    trace = Trace.objects.create(project=project, name="bulk-trace")
    ts_id = deterministic_trace_session_id(project.id, "bulk-x")

    # The exact shape _prepare_trace_update_data builds: attname key, bare UUID.
    traces_to_update = {str(trace.id): {"session_id": ts_id}}
    all_traces = {trace.id: trace}

    # Must NOT raise (bare-UUID-to-.session ValueError / bulk_update field err).
    _bulk_update_traces(traces_to_update, all_traces)

    trace.refresh_from_db()
    assert str(trace.session_id) == str(ts_id), (trace.session_id, ts_id)


# ── Gate G (LANGFUSE path) — the third ingest path flips too ────────────────
@pytest.mark.django_db
def test_gate_g_langfuse_stamps_deterministic_ids(
    observe_project,
    organization,
    workspace,
    fake_ch,
    django_capture_on_commit_callbacks,
):
    """The Langfuse ingest path (`upsert_langfuse_trace`) stamps the SAME
    deterministic ids as the OTel/bulk paths and mirrors the curated rows to CH.

    Langfuse hardcodes ``user_id_type="custom"`` (DESIGN §11.1) — that exact
    value MUST feed ``deterministic_end_user_id`` so a Langfuse user collides with
    the historical remap. Asserts: the span carries the deterministic
    ``end_user_id``; the trace carries the deterministic ``session_id``; and the
    curated ``end_users``/``trace_sessions`` mirror fired (faked CH succeeded).
    """
    from integrations.transformers.langfuse_transformer import LangfuseTransformer
    from tracer.utils.langfuse_upsert import upsert_langfuse_trace

    project = observe_project
    org = organization
    user_id = "lf-user@futureagi.com"
    session_id = f"lf-sess-{uuid.uuid4().hex[:8]}"
    obs_id = uuid.uuid4().hex
    assembled = {
        "id": uuid.uuid4().hex,
        "name": "lf-trace",
        "userId": user_id,
        "sessionId": session_id,
        "observations": [
            {
                "id": obs_id,
                "type": "GENERATION",
                "name": "gen",
                "startTime": "2026-05-01T00:00:00.000Z",
                "endTime": "2026-05-01T00:00:01.000Z",
                "input": "hi",
                "output": "yo",
            }
        ],
        "scores": [],
    }

    with django_capture_on_commit_callbacks(execute=True):
        upsert_langfuse_trace(
            assembled_trace=assembled,
            transformer=LangfuseTransformer(),
            project_id=str(project.id),
            org=org,
            workspace=workspace,
            org_id=str(org.id),
        )

    eu_id = deterministic_end_user_id(str(project.id), str(org.id), user_id, "custom")
    ts_id = deterministic_trace_session_id(str(project.id), session_id)

    span = ObservationSpan.no_workspace_objects.get(id=obs_id)
    assert str(span.end_user_id) == str(eu_id), (span.end_user_id, eu_id)
    trace = Trace.no_workspace_objects.get(id=span.trace_id)
    assert str(trace.session_id) == str(ts_id), (trace.session_id, ts_id)

    tables = {t for (t, _r, _c) in fake_ch.inserts}
    assert "end_users" in tables and "trace_sessions" in tables, tables
