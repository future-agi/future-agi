"""Authenticated mutation journeys against a frozen PostgreSQL replica (TH-3119).

Unrelated FK fixtures are committed before freeze; only the producer's target
changes afterwards. No ORM or routing mocks are used. ClickHouse mirroring is
disabled because this suite exercises the PostgreSQL mutation contract.
"""

import uuid
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework import serializers

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401
from tracer.models.observation_span import EvalLogger, EvalTargetType, ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.serializers.observation_span import ObservationSpanSerializer
from tracer.views.observation_span import ObservationSpanView
from tracer.views.trace import TraceView

pytestmark = pytest.mark.django_db(transaction=True)

SPAN_URL = "/tracer/observation-span/"
TRACE_URL = "/tracer/trace/"


@pytest.fixture(autouse=True)
def disable_external_mirrors(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")


def _bulk_trace(client, project, **fields):
    trace_id = str(uuid.uuid4())
    response = client.post(
        TRACE_URL + "bulk_create/",
        {
            "traces": [
                {"id": trace_id, "project": str(project.id), "name": "fresh", **fields}
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["Trace IDs"] == [trace_id]
    return trace_id


def _span_payload(project, trace_id):
    return {
        "id": "span_" + uuid.uuid4().hex,
        "project": str(project.id),
        "trace": str(trace_id),
        "name": "fresh span",
        "observation_type": "llm",
        "start_time": timezone.now().isoformat(),
        "end_time": timezone.now().isoformat(),
    }


def _create_span(client, project, trace_id):
    payload = _span_payload(project, trace_id)
    response = client.post(SPAN_URL, payload, format="json")
    assert response.status_code == 201, response.data
    assert response.data["result"]["id"] == payload["id"]
    return payload["id"]


def _tag(client, entity, target_id, tags):
    if entity == "span":
        return client.post(
            SPAN_URL + "update-tags/",
            {"span_id": target_id, "tags": tags},
            format="json",
        )
    return client.patch(TRACE_URL + f"{target_id}/tags/", {"tags": tags}, format="json")


def test_c37_authenticated_create_accepts_just_bulk_created_trace(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
):
    lagging_replica.freeze()
    trace_id = _bulk_trace(auth_client, project)
    span_id = _create_span(auth_client, project, trace_id)
    assert (
        str(ObservationSpan.objects.using("default").get(pk=span_id).trace_id)
        == trace_id
    )
    assert not Trace.objects.filter(pk=trace_id).exists()


def test_c37_declared_trace_field_accepts_new_trace_without_request(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
):
    lagging_replica.freeze()
    trace_id = _bulk_trace(auth_client, project)
    serializer = ObservationSpanSerializer(data=_span_payload(project, trace_id))
    assert serializer.is_valid(), serializer.errors
    supplied_trace = serializer.validated_data["trace"]
    assert str(supplied_trace.pk) == trace_id
    assert supplied_trace._state.db == "default"
    assert (
        serializer.validate({"project": project, "trace": supplied_trace})["trace"]
        is supplied_trace
    )


def test_c66_bulk_create_spans_accepts_just_bulk_created_trace(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
):
    lagging_replica.freeze()
    trace_id = _bulk_trace(auth_client, project)
    payload = _span_payload(project, trace_id)
    response = auth_client.post(
        SPAN_URL + "bulk_create/", {"observation_spans": [payload]}, format="json"
    )
    assert response.status_code == 200, response.data
    span_id = response.data["result"]["Observation Span IDs"][0]
    assert (
        str(ObservationSpan.objects.using("default").get(pk=span_id).trace_id)
        == trace_id
    )


def test_c39_patch_without_trace_validates_current_parent_project(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    observe_project,
):
    trace_id = _bulk_trace(auth_client, project)
    span_id = _create_span(auth_client, project, trace_id)
    lagging_replica.freeze()
    moved = auth_client.patch(
        TRACE_URL + f"{trace_id}/", {"project": str(observe_project.id)}, format="json"
    )
    assert moved.status_code == 200, moved.data
    assert Trace.objects.get(pk=trace_id).project_id == project.id

    response = auth_client.patch(
        SPAN_URL + f"{span_id}/", {"project": str(observe_project.id)}, format="json"
    )
    assert response.status_code == 200, response.data
    assert (
        ObservationSpan.objects.using("default").get(pk=span_id).project_id
        == observe_project.id
    )

    # Neither the implicit nor the supplied-parent path may bypass project equality.
    for fields in (
        {"project": str(project.id)},
        {"project": str(project.id), "trace": trace_id},
    ):
        denied = auth_client.patch(SPAN_URL + f"{span_id}/", fields, format="json")
        assert denied.status_code == 400, denied.data
        assert "trace" in denied.data["details"]


@pytest.mark.parametrize("entity", ["span", "trace"])
@pytest.mark.parametrize("method", ["patch", "put"])
def test_c65_c71_name_edit_preserves_acknowledged_tags(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    entity,
    method,
):
    trace_id = _bulk_trace(auth_client, project)
    span_id = _create_span(auth_client, project, trace_id)
    target_id = span_id if entity == "span" else trace_id
    model = ObservationSpan if entity == "span" else Trace
    url = SPAN_URL if entity == "span" else TRACE_URL
    lagging_replica.freeze()
    response = _tag(auth_client, entity, target_id, ["acknowledged"])
    assert response.status_code == 200, response.data
    assert model.objects.get(pk=target_id).tags != ["acknowledged"]
    data = {"name": "renamed"}
    if method == "put":
        data["project"] = str(project.id)
        if entity == "span":
            data["trace"] = trace_id
            data["observation_type"] = "llm"
    response = getattr(auth_client, method)(url + f"{target_id}/", data, format="json")
    assert response.status_code == 200, response.data
    current = model.objects.using("default").get(pk=target_id)
    assert current.name == "renamed"
    assert current.tags == ["acknowledged"]


@pytest.mark.parametrize("entity", ["span", "trace"])
def test_c65_c71_destroy_accepts_just_created_target(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    entity,
):
    # Replicate the span's parent first to isolate the mutation target lookup.
    parent_id = _bulk_trace(auth_client, project)
    lagging_replica.freeze()
    if entity == "span":
        target_id = _create_span(auth_client, project, parent_id)
        model, url = ObservationSpan, SPAN_URL
    else:
        target_id = _bulk_trace(auth_client, project)
        model, url = Trace, TRACE_URL
    response = auth_client.delete(url + f"{target_id}/")
    assert response.status_code == 204, response.data
    assert model.all_objects.using("default").get(pk=target_id).deleted


@pytest.mark.parametrize("entity", ["span", "trace"])
def test_c67_c72_tag_just_returned_target_id(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    entity,
):
    parent_id = _bulk_trace(auth_client, project)
    lagging_replica.freeze()
    if entity == "span":
        target_id = _create_span(auth_client, project, parent_id)
        model = ObservationSpan
    else:
        target_id = _bulk_trace(auth_client, project)
        model = Trace
    assert not model.objects.filter(pk=target_id).exists()
    response = _tag(auth_client, entity, target_id, ["immediate"])
    assert response.status_code == 200, response.data
    assert model.objects.using("default").get(pk=target_id).tags == ["immediate"]


@pytest.fixture
def annotation_label(project, organization, workspace):
    return AnnotationsLabels.objects.create(
        name="Replica mutation note",
        organization=organization,
        workspace=workspace,
        type=AnnotationTypeChoices.TEXT.value,
        settings={"placeholder": "", "min_length": 0, "max_length": 1000},
    )


@pytest.mark.parametrize("target", ["observation_span_id", "trace_id"])
def test_c68_c69_annotate_just_created_root(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    annotation_label,
    user,
    target,
):
    trace_id = _bulk_trace(auth_client, project)
    lagging_replica.freeze()
    span_id = _create_span(auth_client, project, trace_id)
    assert not ObservationSpan.objects.filter(pk=span_id).exists()
    response = auth_client.post(
        SPAN_URL + "add_annotations/",
        {
            target: span_id if target == "observation_span_id" else trace_id,
            "annotation_values": {str(annotation_label.id): "accepted"},
            "notes": "persisted note",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["result"]["failed_labels"] == []
    assert response.data["result"]["success_labels"] == [str(annotation_label.id)]
    score = Score.no_workspace_objects.using("default").get(
        observation_span_id=span_id,
        label=annotation_label,
        annotator=user,
        deleted=False,
    )
    assert score.value == {"text": "accepted"}
    assert score.notes == "persisted note"
    assert (
        SpanNotes.objects.using("default")
        .get(span_id=span_id, created_by_user=user)
        .notes
        == "persisted note"
    )


def test_bulk_annotation_annotates_span_created_after_freeze(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    annotation_label,
    user,
):
    # The SDK annotation endpoint: a client creates a span and annotates it
    # straight away. Not among the audited IDs; found while settling the opt-in.
    trace_id = _bulk_trace(auth_client, project)
    lagging_replica.freeze()
    span_id = _create_span(auth_client, project, trace_id)
    assert not ObservationSpan.objects.filter(pk=span_id).exists()
    response = auth_client.post(
        "/tracer/bulk-annotation/",
        {
            "records": [
                {
                    "observation_span_id": span_id,
                    "annotations": [
                        {
                            "annotation_label_id": str(annotation_label.id),
                            "value": "accepted",
                        }
                    ],
                    "notes": [{"text": "bulk note"}],
                }
            ]
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    assert result["errors"] is None
    assert (result["annotations_created"], result["notes_created"]) == (1, 1)
    score = Score.no_workspace_objects.using("default").get(
        observation_span_id=span_id,
        label=annotation_label,
        annotator=user,
        deleted=False,
    )
    assert score.value == {"text": "accepted"}
    assert (
        SpanNotes.objects.using("default")
        .filter(span_id=span_id, notes="bulk note")
        .exists()
    )


def test_bulk_annotation_updates_annotation_of_span_created_after_freeze(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    annotation_label,
    user,
):
    """A second annotation of the same (span, label) updates the first. The
    update path must not lazy-load the span from the replica either."""
    trace_id = _bulk_trace(auth_client, project)
    lagging_replica.freeze()
    span_id = _create_span(auth_client, project, trace_id)
    assert not ObservationSpan.objects.filter(pk=span_id).exists()

    def annotate(value):
        return auth_client.post(
            "/tracer/bulk-annotation/",
            {
                "records": [
                    {
                        "observation_span_id": span_id,
                        "annotations": [
                            {
                                "annotation_label_id": str(annotation_label.id),
                                "value": value,
                            }
                        ],
                    }
                ]
            },
            format="json",
        )

    first = annotate("accepted")
    second = annotate("revised")

    assert first.status_code == 200, first.data
    assert second.status_code == 200, second.data
    result = second.data["result"]
    assert result["errors"] is None
    assert (result["annotations_created"], result["annotations_updated"]) == (0, 1)
    score = Score.no_workspace_objects.using("default").get(
        observation_span_id=span_id,
        label=annotation_label,
        annotator=user,
        deleted=False,
    )
    assert score.value == {"text": "revised"}


def test_c73_session_delete_includes_unreplicated_trace_membership(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    trace_session,
):
    unrelated_id = _bulk_trace(auth_client, observe_project)
    unrelated_span = _create_span(auth_client, observe_project, unrelated_id)
    lagging_replica.freeze()
    trace_id = _bulk_trace(auth_client, observe_project, session=str(trace_session.id))
    span_id = _create_span(auth_client, observe_project, trace_id)
    # Current EvalLogger CHECK requires both span and trace for non-session
    # targets. Keep trace_session NULL: only the trace-membership cascade can
    # find this row, not the direct session-target cascade.
    entry = EvalLogger.objects.create(
        trace_id=trace_id, observation_span_id=span_id, target_type=EvalTargetType.TRACE
    )
    assert not Trace.objects.filter(pk=trace_id).exists()
    response = auth_client.delete(f"/tracer/trace-session/{trace_session.id}/")
    assert response.status_code == 204, response.data
    for model, pk in (
        (TraceSession, trace_session.id),
        (Trace, trace_id),
        (ObservationSpan, span_id),
        (EvalLogger, entry.pk),
    ):
        row = model.all_objects.using("default").get(pk=pk)
        assert row.deleted, model.__name__
        assert row.deleted_at is not None, model.__name__
    assert not Trace.objects.using("default").get(pk=unrelated_id).deleted
    assert not ObservationSpan.objects.using("default").get(pk=unrelated_span).deleted


@pytest.mark.parametrize("view_class", [ObservationSpanView, TraceView])
@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_browse_querysets_still_route_to_replica(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    user,
    workspace,
    view_class,
    action,
):
    trace_id = _bulk_trace(auth_client, project)
    span_id = _create_span(auth_client, project, trace_id)
    lagging_replica.freeze()
    late_trace = _bulk_trace(auth_client, project)
    late_span = _create_span(auth_client, project, trace_id)
    applied, late = (
        (span_id, late_span)
        if view_class is ObservationSpanView
        else (trace_id, late_trace)
    )
    view = view_class()
    view.action = action
    view.request = SimpleNamespace(
        user=user,
        organization=project.organization,
        workspace=workspace,
        query_params={},
    )
    view.kwargs = {"pk": applied} if action == "retrieve" else {}
    with lagging_replica.capture() as queries:
        queryset = view.get_queryset()
        assert queryset.db == "replica"
        ids = {str(row.pk) for row in queryset}
    assert applied in ids
    assert late not in ids
    assert queries.captured_queries


@pytest.mark.parametrize("boundary", ["organization", "workspace"])
def test_mutations_preserve_tenant_denial(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    user,
    annotation_label,
    boundary,
):
    organization = (
        Organization.objects.create(name="Foreign organization")
        if boundary == "organization"
        else project.organization
    )
    workspace = Workspace.objects.create(
        name="Foreign workspace",
        organization=organization,
        created_by=user,
        is_active=True,
        is_default=False,
    )
    foreign_project = Project.objects.create(
        name="Foreign project",
        organization=organization,
        workspace=workspace,
        model_type=project.model_type,
        trace_type="observe",
    )
    session = TraceSession.objects.create(project=foreign_project)
    trace = Trace.objects.create(
        project=foreign_project, session=session, tags=["private"]
    )
    span = ObservationSpan.objects.create(
        id="foreign_" + uuid.uuid4().hex,
        project=foreign_project,
        trace=trace,
        tags=["private"],
    )
    lagging_replica.freeze()
    responses = [
        auth_client.post(SPAN_URL, _span_payload(project, trace.pk), format="json"),
        auth_client.post(
            SPAN_URL + "bulk_create/",
            {"spans": [_span_payload(foreign_project, trace.pk)]},
            format="json",
        ),
        _tag(auth_client, "span", span.pk, ["leaked"]),
        _tag(auth_client, "trace", str(trace.pk), ["leaked"]),
    ]
    for url, pk in ((SPAN_URL, span.pk), (TRACE_URL, trace.pk)):
        responses.append(
            auth_client.patch(url + f"{pk}/", {"name": "leaked"}, format="json")
        )
        responses.append(auth_client.delete(url + f"{pk}/"))
    for field, pk in (("observation_span_id", span.pk), ("trace_id", str(trace.pk))):
        responses.append(
            auth_client.post(
                SPAN_URL + "add_annotations/",
                {
                    field: pk,
                    "annotation_values": {str(annotation_label.pk): "leaked"},
                    "notes": "leaked",
                },
                format="json",
            )
        )
    assert all(r.status_code in (400, 404) for r in responses), [
        (r.status_code, r.data) for r in responses
    ]
    for model, pk in ((ObservationSpan, span.pk), (Trace, trace.pk)):
        current = model.all_objects.using("default").get(pk=pk)
        assert current.tags == ["private"]
        assert current.name != "leaked"
        assert not current.deleted
    assert (
        not Score.no_workspace_objects.using("default")
        .filter(observation_span_id=span.pk)
        .exists()
    )
    assert not SpanNotes.objects.using("default").filter(span_id=span.pk).exists()


def test_c39_span_under_soft_deleted_trace_validates_as_before(
    project, observe_project
):
    # Like ``instance.trace``, the primary read of the parent finds a
    # soft-deleted trace: the update validates instead of raising DoesNotExist.
    parent = Trace.objects.create(project=project, name="soft-deleted parent")
    span = ObservationSpan.objects.create(
        id="span_" + uuid.uuid4().hex,
        project=project,
        trace=parent,
        name="live span",
        observation_type="llm",
        start_time=timezone.now(),
        end_time=timezone.now(),
    )
    Trace.all_objects.filter(pk=parent.pk).update(deleted=True)
    span = ObservationSpan.objects.get(pk=span.pk)

    serializer = ObservationSpanSerializer(instance=span)
    assert serializer.validate({"project": project}) == {"project": project}
    with pytest.raises(serializers.ValidationError) as denied:
        serializer.validate({"project": observe_project})
    assert "trace" in denied.value.detail


def test_serializer_creation_without_parent_is_validation_error_not_exception(project):
    serializer = ObservationSpanSerializer(data={"project": str(project.pk)})
    assert not serializer.is_valid()
    assert "trace" in serializer.errors
    assert serializer.validate({"project": project}) == {"project": project}
