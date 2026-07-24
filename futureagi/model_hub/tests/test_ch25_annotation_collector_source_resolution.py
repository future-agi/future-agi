"""CH25 wave-3 — collector (ClickHouse-only) traces/spans/sessions made usable in
annotation queues.

WHAT THIS PINS
--------------
The ``fi-collector`` writes spans/traces/sessions ONLY to ClickHouse (v2 schema),
bypassing Django/PG. So a collector source has NO PG ``tracer_observation_span`` /
``tracer_trace_session`` row. The annotation-queue source-RESOLUTION + render path
resolved sources through PG, so "Add to queue" silently dropped every collector
span/session and the annotate view rendered them as ``{"deleted": true}``.

The fix teaches the three central selectors (``resolve_source_object``,
``resolve_source_preview``, ``resolve_source_content``), the serializer
``create()`` store, and the ``root_spans`` tenant gate to fall back to CH when the
PG row is absent — re-checking tenant scope against the PG ``Project`` row (which
DOES live in PG), fail-closed.

PROOF SHAPE
-----------
These are UNIT tests over the REAL selector functions. The ClickHouse boundary
(``get_reader`` → ``CHSpanReader.get`` and ``resolve_session_fields``) is mocked so
the test needs no live CH, but the PG ``Project`` tenant gate, the FK-absent
``RelatedObjectDoesNotExist`` collapse, and the dict-shape mapping are all
exercised for real against a real test DB.

Each test FAILS if the fix is reverted:
  • resolve of a collector span: revert → PG ``.get`` raises DoesNotExist → the
    function returns ``None`` (today's bug) → the "is a CHSpan" assert fails.
  • preview/content of a collector span: revert → ``item.observation_span`` raises
    → outer except → ``{"error": ...}`` / ``{"deleted": true}`` → the mapped-dict
    asserts fail.
  • root_spans gate: revert → PG ``Trace.objects`` gate returns ``[]`` for the
    collector trace → ``{}`` → the non-empty-result assert fails.
  • cross-org deny: the gate must NOT leak — these lock fail-closed behavior.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    AnnotationQueueStatusChoices,
    QueueItem,
)
from model_hub.models.choices import (
    AnnotationTypeChoices,
    AnnotatorRole,
    QueueItemSourceType,
    QueueItemStatus,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from model_hub.utils import annotation_queue_helpers as helpers
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project, ProjectSourceChoices
from tracer.services.clickhouse.v2.span_reader import CHSpan

CH_READER_PATH = "tracer.services.clickhouse.v2.get_reader"
SESSION_FIELDS_PATH = (
    "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_fields"
)


def _make_project(*, organization, workspace, name="ch25-collector-proj"):
    return Project.objects.create(
        name=f"{name} {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _make_chspan(
    *, project_id, span_id=None, trace_id=None, parent_span_id="", org_id=None
):
    """A fully-populated CHSpan dataclass standing in for a collector span row
    (the shape ``CHSpanReader.get`` returns). No PG row exists for it. ``org_id``
    is populated for the org-scoped ``resolve_ch_span_source`` gate; the
    project-scoped fallback path ignores it, so it defaults to None."""
    return CHSpan(
        id=span_id or f"ch-span-{uuid.uuid4().hex[:12]}",
        project_id=str(project_id),
        trace_id=trace_id or str(uuid.uuid4()),
        parent_span_id=parent_span_id,
        name="collector root span",
        observation_type="agent",
        operation_name="invoke_agent",
        start_time=datetime(2025, 5, 1, 10, 0, 0, tzinfo=UTC),
        end_time=datetime(2025, 5, 1, 10, 0, 2, tzinfo=UTC),
        latency_ms=2000,
        model="gpt-4o",
        provider="openai",
        prompt_tokens=11,
        completion_tokens=7,
        total_tokens=18,
        cost=0.0021,
        status="OK",
        status_message="",
        org_id=str(org_id) if org_id is not None else None,
        project_version_id=None,
        end_user_id=None,
        trace_session_id=None,
        prompt_version_id=None,
        prompt_label_id=None,
        custom_eval_config_id=None,
        input='{"messages": [{"role": "user", "content": "hi"}]}',
        output='{"role": "assistant", "content": "hello"}',
        tags='["collector"]',
        span_events='[{"name": "event-a"}]',
        metadata='{"k": "v"}',
        resource_attrs='{"service.name": "collector-svc"}',
        attributes_extra='{"extra.key": "extra-val"}',
        attrs_string={"gen_ai.request.model": "gpt-4o"},
        attrs_number={"gen_ai.usage.total_tokens": 18.0},
        attrs_bool={"gen_ai.stream": 1},
    )


class _ReaderCM:
    """Context-manager stub mimicking ``get_reader()`` → ``CHSpanReader``."""

    def __init__(self, span):
        self._span = span
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, span_id):
        self.get_calls.append(str(span_id))
        if self._span is None:
            return None
        return self._span if str(span_id) == str(self._span.id) else None

    def root_ids_by_trace_ids(self, trace_ids, project_ids=None):
        """Lean stub: ``{trace_id: (root_span_id, project_id)}``, roots only."""
        ids = {str(t) for t in trace_ids}
        if self._span is None or str(self._span.trace_id) not in ids:
            return {}
        if self._span.parent_span_id:  # roots only
            return {}
        pid = str(self._span.project_id) if self._span.project_id else None
        return {str(self._span.trace_id): (str(self._span.id), pid)}

    def list_by_trace(self, trace_id, *, project_id=None):
        if self._span is None:
            return []
        return [self._span] if str(self._span.trace_id) == str(trace_id) else []

    def roots_by_trace_ids(
        self, trace_ids, *, include_heavy=False, project_id=None, org_id=None
    ):
        # Mirror the real reader: parentless spans for the given traces.
        if self._span is None or getattr(self._span, "parent_span_id", None):
            return []
        return (
            [self._span]
            if str(self._span.trace_id) in {str(t) for t in trace_ids}
            else []
        )


# ─────────────────────────── observation_span: resolve ───────────────────────


@pytest.mark.django_db
def test_collector_span_resolves_via_ch(organization, workspace):
    """A collector span (no PG row) resolves through the CH fallback when org +
    workspace match, returning the CHSpan itself (FAILS if reverted → None)."""
    project = _make_project(organization=organization, workspace=workspace)
    span = _make_chspan(project_id=project.id)

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.OBSERVATION_SPAN.value,
            span.id,
            organization=organization,
            workspace=workspace,
        )
    assert resolved is span
    assert resolved.id == span.id


@pytest.mark.django_db
def test_collector_span_cross_org_denied(organization, workspace, django_user_model):
    """A collector span whose project belongs to a DIFFERENT org is denied (the CH
    fallback re-checks tenant scope against PG). FAILS if the gate is removed."""
    from accounts.models.organization import Organization

    other_org = Organization.objects.create(name="Other Org")
    other_project = _make_project(organization=other_org, workspace=None)
    span = _make_chspan(project_id=other_project.id)

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.OBSERVATION_SPAN.value,
            span.id,
            organization=organization,  # requesting org != other_org
        )
    assert resolved is None


@pytest.mark.django_db
def test_tenant_scoped_project_denies_without_organization(organization, workspace):
    """Regression (cross-org leak): the CH tenant gate FAILS CLOSED when no
    organization is supplied. A ``None`` org cannot prove ownership, so even an
    existing project must NOT resolve — this is the chokepoint that stops a caller
    (e.g. the single-item serializer) from silently skipping the org filter.
    FAILS if ``_tenant_scoped_project`` resolves without an org."""
    project = _make_project(organization=organization, workspace=workspace)
    # With the org it resolves...
    assert (
        helpers._tenant_scoped_project(project.id, organization=organization)
        is not None
    )
    # ...without it, never (the hole the serializer create() previously left open).
    assert (
        helpers._tenant_scoped_project(
            project.id, organization=None, workspace=workspace
        )
        is None
    )


@pytest.mark.django_db
def test_collector_span_org_omitted_denied():
    """Regression (cross-org leak): resolving a collector span with BOTH org and
    workspace omitted (the fully-ungated signature the serializer ``create()`` used
    when the request carried no org) must deny — a foreign-org collector span must
    NOT resolve when the org gate is absent. Org-gated only (no workspace arg) so
    it FAILS on revert via the org gate, not a workspace mismatch — the case the
    org-explicit cross-org test could not reach."""
    from accounts.models.organization import Organization

    other_org = Organization.objects.create(name="Other Org (org-omitted)")
    other_project = _make_project(organization=other_org, workspace=None)
    span = _make_chspan(project_id=other_project.id)

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.OBSERVATION_SPAN.value,
            span.id,
            # org AND workspace deliberately omitted — mirrors the serializer hole
        )
    assert resolved is None


@pytest.mark.django_db
def test_collector_span_empty_project_id_denied(organization):
    """Deny-on-ambiguity: a CHSpan with no project_id can't be tenant-verified →
    None (never trust an untenanted span)."""
    span = _make_chspan(project_id="")  # falsy project_id

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.OBSERVATION_SPAN.value,
            span.id,
            organization=organization,
        )
    assert resolved is None


@pytest.mark.django_db
def test_span_absent_in_both_pg_and_ch_returns_none(organization):
    """A source id that exists in NEITHER PG NOR CH → None (add denied, no crash)."""
    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(None)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.OBSERVATION_SPAN.value,
            f"missing-{uuid.uuid4().hex}",
            organization=organization,
        )
    assert resolved is None


# ─────────────────────── observation_span: serializer store ──────────────────


@pytest.mark.django_db
def test_serializer_create_persists_collector_span_soft_id(
    organization, workspace, user
):
    """The serializer create() stores the soft id (not the FK object) so a CHSpan
    persists; the QueueItem carries observation_span_id with NO PG row."""
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    project = _make_project(organization=organization, workspace=workspace)
    span = _make_chspan(project_id=project.id)
    queue = AnnotationQueue.objects.create(
        name=f"q-{uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization,
        workspace=workspace,
        project=project,
        created_by=user,
    )

    request = mock.Mock()
    request.organization = organization
    request.workspace = workspace
    serializer = QueueItemSerializer(context={"request": request})

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        item = serializer.create(
            {
                "queue": queue,
                "source_type": QueueItemSourceType.OBSERVATION_SPAN.value,
                "source_id": span.id,
                "organization": organization,
                "workspace": workspace,
                "status": QueueItemStatus.PENDING.value,
            }
        )
    item.refresh_from_db()
    assert str(item.observation_span_id) == str(span.id)


# ─────────────────────── observation_span: preview/content ────────────────────


def _collector_item(*, organization, workspace, queue, span):
    """A QueueItem whose observation_span_id points at a collector span (NO PG
    ObservationSpan row exists for it)."""
    return QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=span.id,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
    )


def _queue(*, organization, workspace, user, project):
    return AnnotationQueue.objects.create(
        name=f"q-{uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization,
        workspace=workspace,
        project=project,
        created_by=user,
    )


@pytest.mark.django_db
def test_collector_span_preview_renders_from_ch(organization, workspace, user):
    """Preview of a collector span (no PG row) returns the mapped dict, NOT the
    deleted sentinel. FAILS if reverted (FK access raises → error dict)."""
    project = _make_project(organization=organization, workspace=workspace)
    span = _make_chspan(project_id=project.id)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    item = _collector_item(
        organization=organization, workspace=workspace, queue=queue, span=span
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        preview = helpers.resolve_source_preview(item)

    assert preview["type"] == "observation_span"
    assert "deleted" not in preview
    assert preview["name"] == "collector root span"
    assert preview["observation_type"] == "agent"
    assert preview["latency_ms"] == 2000


@pytest.mark.django_db
def test_collector_span_content_renders_from_ch(organization, workspace, user):
    """Content of a collector span returns the full mapped dict with the CHSpan
    field renames applied (attrs_* merged, JSON strings parsed to dicts/lists)."""
    project = _make_project(organization=organization, workspace=workspace)
    span = _make_chspan(project_id=project.id)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    item = _collector_item(
        organization=organization, workspace=workspace, queue=queue, span=span
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        content = helpers.resolve_source_content(item)

    assert content["type"] == "observation_span"
    assert "deleted" not in content
    assert content["span_id"] == str(span.id)
    assert content["status"] == "OK"
    assert content["cost"] == 0.0021
    # span_attributes is the merge of attrs_string/number/bool + attributes_extra.
    assert content["span_attributes"]["gen_ai.request.model"] == "gpt-4o"
    assert content["span_attributes"]["extra.key"] == "extra-val"
    # JSON-string columns become Python containers.
    assert content["metadata"] == {"k": "v"}
    assert content["resource_attributes"] == {"service.name": "collector-svc"}
    assert content["events"] == [{"name": "event-a"}]
    # eval_attributes present (empty dict), not omitted → shape parity.
    assert content["eval_attributes"] == {}


@pytest.mark.django_db
def test_collector_span_content_deleted_when_ch_missing(organization, workspace, user):
    """If the span is gone from CH too, content returns the deleted sentinel
    (semantically correct, same shape as today)."""
    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    item = QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.OBSERVATION_SPAN.value,
        observation_span_id=f"gone-{uuid.uuid4().hex[:12]}",
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(None)):
        content = helpers.resolve_source_content(item)
    assert content == {"type": "observation_span", "deleted": True}


@pytest.mark.django_db
def test_malformed_ch_json_does_not_raise(organization, workspace, user):
    """A CHSpan with malformed metadata/resource_attrs/span_events JSON renders
    safe defaults ({}/[]) instead of 500-ing the annotate-detail page."""
    project = _make_project(organization=organization, workspace=workspace)
    span = _make_chspan(project_id=project.id)
    bad = {
        "metadata": "{not json",
        "resource_attrs": "}}}",
        "span_events": "[oops",
    }
    span = CHSpan(**{**span.__dict__, **bad})
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    item = _collector_item(
        organization=organization, workspace=workspace, queue=queue, span=span
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        content = helpers.resolve_source_content(item)
    assert content["metadata"] == {}
    assert content["resource_attributes"] == {}
    assert content["events"] == []


# ──────────────────────────── root_spans tenant gate ─────────────────────────


@pytest.mark.django_db
def test_allowed_root_spans_returns_collector_trace(organization, workspace):
    """The root_spans gate returns {trace_id: root_span_id} for a collector trace
    whose project IS org-accessible (FAILS if reverted → {} via the PG gate)."""
    from django.db.models import Q

    from tracer.views.observation_span import allowed_root_spans_for_request

    project = _make_project(organization=organization, workspace=workspace)
    trace_id = str(uuid.uuid4())
    span = _make_chspan(project_id=project.id, trace_id=trace_id, parent_span_id="")

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        result = allowed_root_spans_for_request(
            [trace_id], organization=organization, project_scope_q=Q()
        )
    assert result == {trace_id: str(span.id)}


@pytest.mark.django_db
def test_allowed_root_spans_cross_org_omitted(organization, workspace):
    """A collector trace whose root-span project belongs to a DIFFERENT org is
    omitted (cross-org leak prevented). FAILS if the gate is removed/fail-open."""
    from django.db.models import Q

    from accounts.models.organization import Organization
    from tracer.views.observation_span import allowed_root_spans_for_request

    other_org = Organization.objects.create(name="Other Org 2")
    other_project = _make_project(organization=other_org, workspace=None)
    trace_id = str(uuid.uuid4())
    span = _make_chspan(
        project_id=other_project.id, trace_id=trace_id, parent_span_id=""
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        result = allowed_root_spans_for_request(
            [trace_id], organization=organization, project_scope_q=Q()
        )
    assert result == {}


@pytest.mark.django_db
def test_allowed_root_spans_empty_input(organization):
    """No trace_ids → {} (unchanged shape)."""
    from django.db.models import Q

    from tracer.views.observation_span import allowed_root_spans_for_request

    assert (
        allowed_root_spans_for_request(
            [], organization=organization, project_scope_q=Q()
        )
        == {}
    )


@pytest.mark.django_db
def test_allowed_root_spans_uses_lean_projection(organization, workspace):
    """Root-spans gate must use the lean ``root_ids_by_trace_ids`` read, never
    the wide ``list_by_trace_ids`` (which OOMs CH, code 241, on fat voice roots)."""
    from unittest.mock import MagicMock

    from django.db.models import Q

    from tracer.views.observation_span import allowed_root_spans_for_request

    project = _make_project(organization=organization, workspace=workspace)
    trace_id = str(uuid.uuid4())
    root_span_id = f"root-{uuid.uuid4().hex[:12]}"

    fake_reader = MagicMock()
    fake_reader.__enter__.return_value = fake_reader
    fake_reader.__exit__.return_value = False
    fake_reader.root_ids_by_trace_ids.return_value = {
        trace_id: (root_span_id, str(project.id))
    }
    # The wide read must never be called on the root-spans path.
    fake_reader.list_by_trace_ids.side_effect = AssertionError(
        "root-spans must not read full span rows (OOMs shared ClickHouse)"
    )

    with mock.patch(CH_READER_PATH, return_value=fake_reader):
        result = allowed_root_spans_for_request(
            [trace_id], organization=organization, project_scope_q=Q()
        )
    fake_reader.root_ids_by_trace_ids.assert_called_once()
    assert result == {trace_id: root_span_id}


@pytest.mark.django_db
def test_allowed_root_spans_forwards_project_ids(organization, workspace):
    """``project_ids`` is passed to the reader to prune the CH scan (sort-key
    prefix) — it must not change the fail-closed tenant result."""
    from unittest.mock import MagicMock

    from django.db.models import Q

    from tracer.views.observation_span import allowed_root_spans_for_request

    project = _make_project(organization=organization, workspace=workspace)
    trace_id = str(uuid.uuid4())

    fake_reader = MagicMock()
    fake_reader.__enter__.return_value = fake_reader
    fake_reader.__exit__.return_value = False
    fake_reader.root_ids_by_trace_ids.return_value = {
        trace_id: (f"root-{trace_id}", str(project.id))
    }

    with mock.patch(CH_READER_PATH, return_value=fake_reader):
        allowed_root_spans_for_request(
            [trace_id],
            organization=organization,
            project_scope_q=Q(),
            project_ids=[str(project.id)],
        )
    _, kwargs = fake_reader.root_ids_by_trace_ids.call_args
    assert kwargs.get("project_ids") == [str(project.id)]


# ──────────────────────────── trace_session (Slice 2) ────────────────────────


@pytest.mark.django_db
def test_collector_session_resolves_via_ch(organization, workspace):
    """A collector trace_session (no PG row) resolves through the CH session
    reader + PG Project tenant gate, returning a duck-typed source object."""
    project = _make_project(organization=organization, workspace=workspace)
    session_id = str(uuid.uuid4())
    fields = {
        session_id: {
            "external_session_id": "ext-session-1",
            "first_seen": datetime(2025, 5, 1, tzinfo=UTC),
            "project_id": str(project.id),
            "bookmarked": False,
            "display_name": None,
        }
    }

    with mock.patch(SESSION_FIELDS_PATH, return_value=fields):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.TRACE_SESSION.value,
            session_id,
            organization=organization,
            workspace=workspace,
        )
    assert resolved is not None
    assert str(resolved.id) == session_id
    assert str(resolved.project_id) == str(project.id)


@pytest.mark.django_db
def test_collector_session_cross_project_denied(organization, workspace):
    """A session whose project belongs to a different org is denied (no
    cross-tenant leak)."""
    from accounts.models.organization import Organization

    other_org = Organization.objects.create(name="Other Org 3")
    other_project = _make_project(organization=other_org, workspace=None)
    session_id = str(uuid.uuid4())
    fields = {
        session_id: {
            "external_session_id": "ext",
            "first_seen": None,
            "project_id": str(other_project.id),
            "bookmarked": False,
            "display_name": None,
        }
    }

    with mock.patch(SESSION_FIELDS_PATH, return_value=fields):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.TRACE_SESSION.value,
            session_id,
            organization=organization,
        )
    assert resolved is None


@pytest.mark.django_db
def test_collector_session_content_renders_from_ch(organization, workspace, user):
    """Content of a collector session returns name/project_id/created_at from CH,
    NOT the deleted sentinel."""
    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    session_id = str(uuid.uuid4())
    item = QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.TRACE_SESSION.value,
        trace_session_id=session_id,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
    )
    first_seen = datetime(2025, 5, 2, 9, 0, 0, tzinfo=UTC)
    fields = {
        session_id: {
            "external_session_id": "ext-session-9",
            "first_seen": first_seen,
            "project_id": str(project.id),
            "bookmarked": False,
            "display_name": None,
        }
    }

    with mock.patch(SESSION_FIELDS_PATH, return_value=fields):
        content = helpers.resolve_source_content(item)
    assert content["type"] == "trace_session"
    assert "deleted" not in content
    assert content["session_id"] == session_id
    assert content["name"] == "ext-session-9"
    assert content["project_id"] == str(project.id)
    assert content["created_at"] == first_seen


# ─────────────────────────── OSS-with-ee-stripped guard ──────────────────────


def test_resolution_path_imports_no_ee():
    """The annotation source-resolution modules must not couple to ``ee`` — the
    OSS build (ee/ stripped) must still resolve collector sources."""
    import inspect

    import tracer.views.observation_span as obs_view

    for mod in (helpers, obs_view):
        src = inspect.getsource(mod)
        assert "import ee" not in src
        assert "from ee" not in src


@pytest.mark.django_db
def test_collector_span_round_trips_create_to_annotate(
    auth_client, organization, workspace, user
):
    """Full add→annotate round-trip for a CH-only collector span (NO PG row).

    The resolution tests above prove the source resolves and the create persists
    the soft id; this proves the item can actually be ANNOTATED end-to-end. The
    submit path reads ``item.observation_span_id`` for the Score FK and
    CH-resolves the span-notes target — without the fix it dereferences
    ``item.observation_span`` and 500s with ``DoesNotExist``. Asserts the Score
    persists on the soft id with no PG ``ObservationSpan`` backing it.
    """
    project = _make_project(organization=organization, workspace=workspace)
    span = _make_chspan(project_id=project.id)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    AnnotationQueueAnnotator.objects.get_or_create(
        queue=queue, user=user, defaults={"role": AnnotatorRole.MANAGER.value}
    )
    label = AnnotationsLabels.objects.create(
        name=f"rt-label-{uuid.uuid4().hex[:8]}",
        type=AnnotationTypeChoices.TEXT.value,
        organization=organization,
        workspace=workspace,
        settings={"placeholder": "", "min_length": 0, "max_length": 1000},
    )
    AnnotationQueueLabel.objects.create(queue=queue, label=label, required=True)
    item = _collector_item(
        organization=organization, workspace=workspace, queue=queue, span=span
    )

    url = f"/model-hub/annotation-queues/{queue.id}/items/{item.id}/annotations/submit/"
    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        resp = auth_client.post(
            url,
            {
                "annotations": [
                    {"label_id": str(label.id), "value": {"text": "ship it"}}
                ],
                "item_notes": "looks good",
            },
            format="json",
        )

    assert resp.status_code == 200, resp.data
    score = Score.objects.get(queue_item=item, label=label, deleted=False)
    assert score.source_type == QueueItemSourceType.OBSERVATION_SPAN.value
    # Score persists the collector soft id — and NO PG ObservationSpan backs it.
    assert str(score.observation_span_id) == str(span.id)
    assert not ObservationSpan.objects.filter(id=span.id).exists()


# ─────────── for_source: collector trace span_notes (TH-6622) ────────────────


@pytest.mark.django_db
def test_for_source_collector_trace_span_notes_resolves_via_ch(
    auth_client, organization, workspace, user
):
    """The trace-detail annotate panel calls ``for-source`` with a trace source
    carrying ``span_notes_source_id`` = the (collector, CH-only) root span. Pre-fix
    the endpoint resolved that span via PG only and 404'd with "Span notes source
    not found"; the CH fallback makes the panel load. Revert → PG miss → 404
    (TH-6622)."""
    project = _make_project(organization=organization, workspace=workspace)
    # org_id must match the requester — resolve_ch_span_source is org-gated.
    span = _make_chspan(project_id=project.id, org_id=organization.pk)
    reader = _ReaderCM(span)

    with mock.patch(CH_READER_PATH, return_value=reader):
        resp = auth_client.get(
            "/model-hub/annotation-queues/for-source/",
            {
                "sources": json.dumps(
                    [
                        {
                            "source_type": "trace",
                            "source_id": str(uuid.uuid4()),
                            "span_notes_source_id": span.id,
                        }
                    ]
                ),
            },
        )

    assert resp.status_code == 200, resp.data
    # The CH fallback actually ran (proves it's not a lucky PG hit)...
    assert str(span.id) in reader.get_calls
    # ...and no PG ObservationSpan backs the collector root span.
    assert not ObservationSpan.objects.filter(id=span.id).exists()


@pytest.mark.django_db
def test_for_source_collector_span_notes_cross_org_denied(
    auth_client, organization, workspace
):
    """The CH span-notes fallback re-checks org scope: a collector root span owned
    by a DIFFERENT org must still 404 — no cross-org leak into ``for_source``."""
    from accounts.models.organization import Organization

    other_org = Organization.objects.create(name="Other Org (for_source span_notes)")
    other_project = _make_project(organization=other_org, workspace=None)
    span = _make_chspan(project_id=other_project.id, org_id=other_org.pk)

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        resp = auth_client.get(
            "/model-hub/annotation-queues/for-source/",
            {
                "sources": json.dumps(
                    [
                        {
                            "source_type": "trace",
                            "source_id": str(uuid.uuid4()),
                            "span_notes_source_id": span.id,
                        }
                    ]
                ),
            },
        )

    assert resp.status_code == 404, resp.data


# ───────────────────── N+1 batch (CollectorSourceCache) ───────────────────────


class _CountingReaderCM:
    """Reader stub recording ``list_by_ids`` vs per-item ``get`` so a test can prove
    a page does ONE batch CH read, not a point-read per item (the N+1 the cache removes)."""

    def __init__(self, spans):
        self._by_id = {str(s.id): s for s in spans}
        self.list_by_ids_calls = []
        self.get_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_by_ids(self, span_ids, *, project_id=None, include_heavy=True):
        ids = [str(s) for s in span_ids]
        self.list_by_ids_calls.append(ids)
        return [self._by_id[i] for i in ids if i in self._by_id]

    def get(self, span_id):  # the per-item path — must NOT be hit when batched
        self.get_calls.append(str(span_id))
        return self._by_id.get(str(span_id))


@pytest.mark.django_db
def test_list_serializer_batches_collector_ch_reads(organization, workspace, user):
    """Serializing a page of N collector spans + M collector sessions does ONE batch
    CH read per kind (``list_by_ids`` / ``resolve_session_fields`` once each), not a
    per-item point-read. FAILS — per-item ``get`` / N ``resolve_session_fields`` calls
    — if the ``CollectorSourceCache`` wiring (or its DRF context plumbing) regresses.
    A correctness-only test would still pass on regression; this asserts call counts."""
    from model_hub.serializers.annotation_queues import QueueItemSerializer

    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )

    spans = [_make_chspan(project_id=project.id) for _ in range(3)]
    span_items = [
        _collector_item(
            organization=organization, workspace=workspace, queue=queue, span=s
        )
        for s in spans
    ]

    session_ids = [str(uuid.uuid4()) for _ in range(2)]
    session_fields = {
        sid: {
            "external_session_id": f"ext-{i}",
            "first_seen": datetime(2025, 5, 2, 9, 0, 0, tzinfo=UTC),
            "project_id": str(project.id),
            "bookmarked": False,
            "display_name": None,
        }
        for i, sid in enumerate(session_ids)
    }
    session_items = [
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE_SESSION.value,
            trace_session_id=sid,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
        )
        for sid in session_ids
    ]

    items = span_items + session_items
    reader_cm = _CountingReaderCM(spans)

    with (
        mock.patch(CH_READER_PATH, return_value=reader_cm) as get_reader,
        mock.patch(
            SESSION_FIELDS_PATH, return_value=session_fields
        ) as resolve_sessions,
    ):
        data = QueueItemSerializer(items, many=True).data

    # one batch span read, carrying every collector span id — never a per-item point read
    assert len(reader_cm.list_by_ids_calls) == 1, reader_cm.list_by_ids_calls
    assert reader_cm.get_calls == [], reader_cm.get_calls
    assert get_reader.call_count == 1
    assert set(reader_cm.list_by_ids_calls[0]) == {str(s.id) for s in spans}
    # one batch session read, carrying every collector session id
    assert resolve_sessions.call_count == 1
    assert set(resolve_sessions.call_args.args[0]) == set(session_ids)

    # previews actually resolved from CH (not the deleted sentinel)
    previews = [row["source_preview"] for row in data]
    assert all("deleted" not in p for p in previews), previews
    span_previews = [p for p in previews if p["type"] == "observation_span"]
    assert len(span_previews) == 3
    assert all(p["name"] == "collector root span" for p in span_previews)


# ─────────────────────────────── trace (TH-6647) ─────────────────────────────
# A non-voice grid "Add to annotation queue" stores a `source_type=trace` item.
# For a collector trace (no PG row) the add/render paths must resolve it from CH
# via its root span, instead of the old PG miss → "Not found: trace=…".


def _collector_trace_item(*, organization, workspace, queue, trace_id):
    """A QueueItem whose trace_id points at a collector trace (NO PG Trace row)."""
    return QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.TRACE.value,
        trace_id=trace_id,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
    )


@pytest.mark.django_db
def test_collector_trace_resolves_via_ch(organization, workspace):
    """A collector trace (no PG row) resolves through the CH fallback via its root
    span, returning a soft-id source (``.id`` = trace_id). FAILS on revert (the
    trace branch bailed → None → "Not found: trace=…", the TH-6647 bug)."""
    project = _make_project(organization=organization, workspace=workspace)
    trace_id = str(uuid.uuid4())
    root_span = _make_chspan(project_id=project.id, trace_id=trace_id)

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(root_span)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.TRACE.value,
            trace_id,
            organization=organization,
            workspace=workspace,
        )
    assert resolved is not None
    assert str(resolved.id) == trace_id
    assert str(resolved.project_id) == str(project.id)


@pytest.mark.django_db
def test_collector_trace_cross_org_denied(organization, workspace):
    """A collector trace whose root span belongs to a DIFFERENT org is denied (the
    CH fallback re-checks tenant scope against PG). FAILS if the gate is removed."""
    from accounts.models.organization import Organization

    other_org = Organization.objects.create(name="Other Org (trace)")
    other_project = _make_project(organization=other_org, workspace=None)
    trace_id = str(uuid.uuid4())
    root_span = _make_chspan(project_id=other_project.id, trace_id=trace_id)

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(root_span)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.TRACE.value,
            trace_id,
            organization=organization,  # requesting org != other_org
        )
    assert resolved is None


@pytest.mark.django_db
def test_collector_trace_absent_returns_none(organization):
    """A trace with no spans in CH (nothing to annotate) resolves to None."""
    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(None)):
        resolved = helpers.resolve_source_object(
            QueueItemSourceType.TRACE.value,
            str(uuid.uuid4()),
            organization=organization,
        )
    assert resolved is None


@pytest.mark.django_db
def test_ch_trace_source_always_available(organization, workspace):
    """A CH-resolved collector trace has no PG reverse relation, so the in-progress
    availability guard treats it as available (it's surfaced from CH once done)."""
    project = _make_project(organization=organization, workspace=workspace)
    trace_id = str(uuid.uuid4())
    root_span = _make_chspan(project_id=project.id, trace_id=trace_id)
    ch_source = helpers._CHTraceSource(trace_id, root_span)

    available, reason = helpers.is_source_available_for_annotation(
        QueueItemSourceType.TRACE.value, ch_source
    )
    assert available is True
    assert reason is None


@pytest.mark.django_db
def test_collector_trace_preview_renders_from_ch(organization, workspace, user):
    """Preview of a collector trace (no PG row) renders from the CH root span, NOT
    the deleted sentinel. FAILS on revert (FK access → deleted/error dict)."""
    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    trace_id = str(uuid.uuid4())
    root_span = _make_chspan(project_id=project.id, trace_id=trace_id)
    item = _collector_trace_item(
        organization=organization, workspace=workspace, queue=queue, trace_id=trace_id
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(root_span)):
        preview = helpers.resolve_source_preview(item)

    assert preview["type"] == "trace"
    assert "deleted" not in preview
    assert preview["name"] == "collector root span"
    assert preview["latency_ms"] == 2000


@pytest.mark.django_db
def test_collector_trace_content_renders_from_ch(organization, workspace, user):
    """Content of a collector trace returns type=trace with the CH-resolved
    root_span_id (the non-voice render focus) and the root span's fields."""
    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    trace_id = str(uuid.uuid4())
    root_span = _make_chspan(project_id=project.id, trace_id=trace_id)
    item = _collector_trace_item(
        organization=organization, workspace=workspace, queue=queue, trace_id=trace_id
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(root_span)):
        content = helpers.resolve_source_content(item)

    assert content["type"] == "trace"
    assert "deleted" not in content
    assert content["trace_id"] == trace_id
    # trace items render via InlineTraceView(trace_id); span_id is dropped so a
    # score never mis-attaches to the root span as if it were a span item.
    assert "span_id" not in content
    assert content["status"] == "OK"


@pytest.mark.django_db
def test_collector_trace_content_deleted_when_ch_missing(organization, workspace, user):
    """If the trace has no spans in CH either, content returns the deleted
    sentinel (same shape as today) rather than raising."""
    project = _make_project(organization=organization, workspace=workspace)
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    item = _collector_trace_item(
        organization=organization,
        workspace=workspace,
        queue=queue,
        trace_id=str(uuid.uuid4()),
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(None)):
        content = helpers.resolve_source_content(item)
    assert content == {"type": "trace", "deleted": True}


# ───────── CollectorSourceCache.for_items: per-project scoping (TH-6864) ─────────
# for_items groups a page's items by their denormalized project_id and scopes each
# CH read to it, pruning the spans PK prefix instead of scanning the whole
# multi-tenant table — while a page whose items span projects still resolves EVERY
# root. A single queue-wide scope would drop off-project items and render them
# deleted; these tests fail if that regression is reintroduced.


class _MultiSpanReaderCM:
    """``get_reader()`` stub over many CHSpans. ``roots_by_trace_ids`` mirrors the
    real reader: parentless spans matching trace_id AND — when a project_id is
    passed — that project. So a MIS-scoped read (wrong project_id) resolves nothing,
    and the correctness tests fail if for_items stops scoping per item."""

    def __init__(self, spans):
        self._spans = list(spans)
        self.roots_calls = []  # (sorted trace_ids tuple, project_id) per call

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def roots_by_trace_ids(
        self, trace_ids, *, include_heavy=False, project_id=None, org_id=None
    ):
        self.roots_calls.append((tuple(sorted(str(t) for t in trace_ids)), project_id))
        ids = {str(t) for t in trace_ids}
        return [
            s
            for s in self._spans
            if not s.parent_span_id
            and str(s.trace_id) in ids
            and (project_id is None or str(s.project_id) == str(project_id))
        ]


def _trace_item(trace_id, project_id):
    """Unsaved QueueItem carrying only what for_items reads."""
    return QueueItem(
        source_type=QueueItemSourceType.TRACE.value,
        trace_id=trace_id,
        project_id=project_id,
    )


def test_for_items_mixed_project_resolves_every_root():
    """A page whose items span two projects resolves ALL roots — each read is scoped
    to its own project. Fails if for_items scopes the whole page by one project (the
    off-project items would resolve to nothing → rendered deleted)."""
    proj_a, proj_b = str(uuid.uuid4()), str(uuid.uuid4())
    t_a1, t_a2, t_b1 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    spans = [
        _make_chspan(project_id=proj_a, trace_id=t_a1, parent_span_id=""),
        _make_chspan(project_id=proj_a, trace_id=t_a2, parent_span_id=""),
        _make_chspan(project_id=proj_b, trace_id=t_b1, parent_span_id=""),
    ]
    items = [
        _trace_item(t_a1, proj_a),
        _trace_item(t_a2, proj_a),
        _trace_item(t_b1, proj_b),
    ]
    reader = _MultiSpanReaderCM(spans)
    with mock.patch(CH_READER_PATH, return_value=reader):
        cache = helpers.CollectorSourceCache.for_items(items)
    assert cache.trace_root(t_a1) is not None
    assert cache.trace_root(t_a2) is not None
    assert cache.trace_root(t_b1) is not None
    # one scoped read per distinct project, each carrying that project's own id
    assert {pid for _tids, pid in reader.roots_calls} == {proj_a, proj_b}


def test_for_items_scopes_read_to_item_project():
    """for_items forwards each item's project_id to the reader (PK-prefix prune),
    not None. Fails if the scoping is dropped (regressing to the wide scan)."""
    proj, tid = str(uuid.uuid4()), str(uuid.uuid4())
    reader = _MultiSpanReaderCM(
        [_make_chspan(project_id=proj, trace_id=tid, parent_span_id="")]
    )
    with mock.patch(CH_READER_PATH, return_value=reader):
        cache = helpers.CollectorSourceCache.for_items([_trace_item(tid, proj)])
    assert cache.trace_root(tid) is not None
    assert reader.roots_calls == [((tid,), proj)]


def test_for_items_null_project_falls_back_unscoped():
    """A pre-denormalization item (project_id NULL) is read UNSCOPED (project_id
    None) and still resolves — the migration degrades gracefully, never wrong."""
    tid = str(uuid.uuid4())
    reader = _MultiSpanReaderCM(
        [_make_chspan(project_id=str(uuid.uuid4()), trace_id=tid, parent_span_id="")]
    )
    with mock.patch(CH_READER_PATH, return_value=reader):
        cache = helpers.CollectorSourceCache.for_items([_trace_item(tid, None)])
    assert cache.trace_root(tid) is not None
    assert reader.roots_calls == [((tid,), None)]


def test_for_items_read_count_is_bounded_by_projects_not_items():
    """PERF GUARD (TH-6864). A page of many items across a FEW projects issues one
    scoped CH read PER PROJECT — O(projects), not O(items), and never a single
    unscoped wide scan. Fails if for_items regresses to a per-item read or drops
    scoping — both are the full-table shapes that made the endpoint take 10s+. This
    mirrors the repo's query-shape perf tests (tracer test_session_list_performance),
    deterministic where a wall-clock assertion would flake."""
    proj_a, proj_b = str(uuid.uuid4()), str(uuid.uuid4())
    spans, items = [], []
    for _ in range(20):
        t_a, t_b = str(uuid.uuid4()), str(uuid.uuid4())
        spans.append(_make_chspan(project_id=proj_a, trace_id=t_a, parent_span_id=""))
        spans.append(_make_chspan(project_id=proj_b, trace_id=t_b, parent_span_id=""))
        items.append(_trace_item(t_a, proj_a))
        items.append(_trace_item(t_b, proj_b))
    reader = _MultiSpanReaderCM(spans)
    with mock.patch(CH_READER_PATH, return_value=reader):
        cache = helpers.CollectorSourceCache.for_items(items)
    # 40 items across 2 projects → exactly 2 reads, one per project, each scoped
    assert len(reader.roots_calls) == 2
    assert {pid for _tids, pid in reader.roots_calls} == {proj_a, proj_b}
    assert all(pid is not None for _tids, pid in reader.roots_calls)
    assert all(cache.trace_root(it.trace_id) is not None for it in items)


# ─────────────────── trace content: project_source (TH-7077) ──────────────────


@pytest.mark.django_db
def test_trace_content_includes_project_source(organization, workspace, user):
    """A trace item in a voice (simulator-source) project carries ``project_source``
    so the FE renders the voice call UI instead of the raw trace tree. FAILS on
    revert — the CH-native cutover dropped this key and regressed voice calls."""
    project = _make_project(organization=organization, workspace=workspace)
    project.source = ProjectSourceChoices.SIMULATOR.value
    project.save(update_fields=["source"])
    trace_id = str(uuid.uuid4())
    span = _make_chspan(project_id=project.id, trace_id=trace_id, parent_span_id="")
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    item = QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.TRACE.value,
        trace_id=trace_id,
        project=project,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        content = helpers.resolve_source_content(item)

    assert content["type"] == "trace"
    assert content["project_source"] == ProjectSourceChoices.SIMULATOR.value


@pytest.mark.django_db
def test_trace_content_project_source_none_when_project_unset(
    organization, workspace, user
):
    """A trace item with no denormalized project (NULL soft FK — e.g. a pre-backfill
    row or a CH-outage add) degrades to ``project_source=None``. The lookup must
    never raise: ``resolve_source_content`` wraps every branch in one try/except, so
    a throw would collapse the whole item to the error sentinel."""
    project = _make_project(organization=organization, workspace=workspace)
    trace_id = str(uuid.uuid4())
    span = _make_chspan(project_id=project.id, trace_id=trace_id, parent_span_id="")
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    item = QueueItem.objects.create(
        queue=queue,
        source_type=QueueItemSourceType.TRACE.value,
        trace_id=trace_id,
        project=None,
        organization=organization,
        workspace=workspace,
        status=QueueItemStatus.PENDING.value,
    )

    with mock.patch(CH_READER_PATH, return_value=_ReaderCM(span)):
        content = helpers.resolve_source_content(item)

    assert content["type"] == "trace"
    assert content["project_source"] is None


@pytest.mark.django_db
def test_project_source_uses_preloaded_relation_no_n_plus_1(
    organization, workspace, user
):
    """``project_source`` reads the ``select_related('project')`` the batched
    export/list loops already fetch — resolving many items must issue ZERO extra
    ``tracer_project`` queries. FAILS on revert if the helper re-queries Project per
    item (the N+1 the export path must never regress)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    project = _make_project(organization=organization, workspace=workspace)
    project.source = ProjectSourceChoices.SIMULATOR.value
    project.save(update_fields=["source"])
    queue = _queue(
        organization=organization, workspace=workspace, user=user, project=project
    )
    for _ in range(5):
        QueueItem.objects.create(
            queue=queue,
            source_type=QueueItemSourceType.TRACE.value,
            trace_id=str(uuid.uuid4()),
            project=project,
            organization=organization,
            workspace=workspace,
            status=QueueItemStatus.PENDING.value,
        )

    # Mirror the export/list queryset: project is select_related, so the source
    # read is a join hit already materialized, not a per-item query.
    items = list(
        QueueItem.objects.filter(queue=queue, deleted=False).select_related("project")
    )
    with CaptureQueriesContext(connection) as ctx:
        sources = [helpers._queue_item_project_source(it) for it in items]

    assert sources == [ProjectSourceChoices.SIMULATOR.value] * 5
    project_queries = [q for q in ctx.captured_queries if "tracer_project" in q["sql"]]
    assert not project_queries, f"N+1: {len(project_queries)} per-item project queries"
