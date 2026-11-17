"""Sessions export as one conversation per dataset row, never one row per turn."""
import json
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework.response import Response

from accounts.models.organization import Organization
from model_hub.models.choices import DatasetSourceChoices, ModelTypes, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from tracer.models.project import Project


@pytest.mark.parametrize("existing", [False, True])
def test_span_import_keeps_informational_session_ids(existing):
    from tracer.serializers.dataset import (
        AddToExistingDatasetObserveSerializer,
        AddToNewDatasetObserveSerializer,
    )

    serializer_type = (
        AddToExistingDatasetObserveSerializer if existing else AddToNewDatasetObserveSerializer
    )
    body = {
        "span_ids": ["span-1"],
        "session_ids": ["external-session-label"],
        "source_kind": "session",
        "mapping_config": [{"col_name": "input", "span_field": "input", "data_type": "text"}],
        **({"dataset_id": str(uuid.uuid4())} if existing else {"new_dataset_name": "Spans"}),
    }
    serializer = serializer_type(data=body)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["session_ids"] == ["external-session-label"]
    assert serializer.validated_data["source_kind"] == "session"
    body["session_ids"] = ["label"] * 201
    assert not serializer_type(data=body).is_valid()


@pytest.mark.parametrize("mapping", [
    {"col_name": "", "span_field": "turns", "data_type": "json"},
    {"col_name": "conversation", "span_field": "turns", "data_type": "unsupported"},
    {"col_name": "conversation", "span_field": "input", "data_type": "json"},
])
def test_session_mapping_keeps_destination_validation(mapping):
    from tracer.services.dataset_sources import validate_import_mapping

    with pytest.raises(ValueError):
        validate_import_mapping([mapping], session_fields=True)


def test_session_mapping_accepts_conversations_without_changing_span_fields():
    from tracer.services.dataset_sources import validate_import_mapping

    mapping = [{"col_name": "conversation", "span_field": "turns", "data_type": "json"}]
    validate_import_mapping(mapping, session_fields=True)
    with pytest.raises(ValueError, match="Invalid source field"):
        validate_import_mapping(mapping)


@pytest.fixture
def session_reader(observe_project):
    ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    now = timezone.now()
    roots = [
        SimpleNamespace(
            id=f"root-{index}",
            trace_id=str(uuid.uuid4()),
            trace_session_id=ids[session],
            project_id=str(observe_project.id),
            end_user_id="conversation-user",
            start_time=now + timedelta(seconds=order),
        )
        for index, (session, order) in enumerate([(0, 2), (1, 1), (0, 1)])
    ]
    reader = MagicMock()
    reader.roots_by_session_ids.side_effect = lambda selected, **kwargs: [
        root for root in roots if root.trace_session_id in selected
    ]
    reader.export_fields_by_ids.side_effect = lambda selected, *args, **kwargs: {
        root.id: {
            "id": root.id,
            "input": json.dumps({"text": f"question-{root.id}"}),
            "output": json.dumps({"text": f"answer-{root.id}"}),
        }
        for root in roots if root.id in selected
    }
    reader.session_export_roots.side_effect = lambda selected, **kwargs: [
        SimpleNamespace(**vars(root), payload_bytes=100)
        for root in roots if root.trace_session_id in selected
    ]
    reader.aggregate_by_session_ids.side_effect = lambda selected, **kwargs: {
        sid: {"tokens": 30, "cost": 0.25, "traces_count": 2 if sid == ids[0] else 1}
        for sid in selected
    }
    with patch("tracer.views.dataset.get_reader") as get_reader, patch(
        "tracer.services.clickhouse.v2.end_user_dict_reader.resolve_user_ids",
        side_effect=lambda selected: {str(uid): "conversation-user" for uid in selected},
    ):
        get_reader.return_value.__enter__.return_value = reader
        yield ids, roots, reader


def payload(project, ids):
    return {
        "project": str(project.id),
        "session_ids": ids,
        "mapping_config": [
            {"col_name": "conversation", "span_field": "turns", "data_type": "json"},
            {"col_name": "session", "span_field": "session_id", "data_type": "text"},
            {"col_name": "user", "span_field": "user_id", "data_type": "text"},
            {"col_name": "usage", "span_field": "metrics", "data_type": "json"},
        ],
    }


@pytest.mark.django_db
@pytest.mark.parametrize("existing", [False, True])
def test_two_sessions_create_two_rows_with_ordered_turns(
    auth_client, observe_project, organization, workspace, user, session_reader, existing
):
    ids, roots, reader = session_reader
    body = payload(observe_project, ids)
    if existing:
        dataset = Dataset.objects.create(
            name="Existing conversations", organization=organization, workspace=workspace,
            user=user, model_type=ModelTypes.GENERATIVE_LLM.value,
            source=DatasetSourceChoices.OBSERVE.value,
        )
        for mapping in body["mapping_config"]:
            Column.objects.create(
                dataset=dataset, name=mapping["col_name"], data_type=mapping["data_type"],
                source=SourceChoices.OTHERS.value,
            )
        body["dataset_id"] = str(dataset.id)
        action = "add_to_existing_dataset"
    else:
        body["new_dataset_name"] = "Conversation examples"
        action = "add_to_new_dataset"
    with patch("tracer.views.dataset.check_if_dataset_creation_is_allowed", return_value=(True, {})):
        response = auth_client.post(f"/tracer/dataset/{action}/", body, format="json")
    assert response.status_code == 200, response.data
    dataset_id = response.data["result"]["dataset_id"]
    rows = list(Row.objects.filter(dataset_id=dataset_id).order_by("order"))
    assert len(rows) == 2
    values = {
        cell.column.name: cell.value
        for cell in Cell.objects.filter(row=rows[0]).select_related("column")
    }
    assert values["session"] == ids[0]
    assert values["user"] == "conversation-user"
    turns = json.loads(values["conversation"])
    assert [turn["input"]["text"] for turn in turns] == ["question-root-2", "question-root-0"]
    assert [turn["output"]["text"] for turn in turns] == ["answer-root-2", "answer-root-0"]
    assert json.loads(values["usage"])["tokens"] == 30
    reader.session_export_roots.assert_called_once_with(
        ids, project_id=str(observe_project.id), max_turns=1000,
        max_payload_bytes=10 * 1024 * 1024, include_turns=True,
    )
    assert reader.export_fields_by_ids.call_args.kwargs["org_id"] == str(organization.id)


@pytest.mark.django_db
def test_other_org_session_refuses_entire_selection(auth_client, observe_project, session_reader):
    ids, roots, reader = session_reader
    body = payload(observe_project, [ids[0], str(uuid.uuid4())])
    body["new_dataset_name"] = "Must not create a partial conversation dataset"
    with patch("tracer.views.dataset.create_new_dataset") as create:
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 400
    # An unknown/foreign id must be checked against project-pinned roots, not ignored.
    reader.session_export_roots.assert_called_once()
    create.assert_not_called()


@pytest.mark.django_db
def test_other_org_project_is_refused_before_session_read(auth_client, session_reader):
    ids, _, reader = session_reader
    foreign_org = Organization.objects.create(name="Foreign organization")
    foreign_project = Project.no_workspace_objects.create(
        name="Foreign project", organization=foreign_org, trace_type="observe",
        model_type=ModelTypes.GENERATIVE_LLM.value,
    )
    body = payload(foreign_project, ids)
    body["new_dataset_name"] = "Forbidden"
    response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 400
    reader.roots_by_session_ids.assert_not_called()
    reader.session_export_roots.assert_not_called()


@pytest.mark.django_db
def test_select_all_uses_session_filters_cursor_and_exclusions(
    auth_client, observe_project, session_reader
):
    ids, _, _ = session_reader
    body = payload(observe_project, [ids[1]])
    body.update(new_dataset_name="Filtered conversations", select_all=True,
                session_filters=[{"column": "bookmarked", "operator": "equals", "value": True}])
    requests = []

    def page(view, request, **kwargs):
        requests.append(dict(request.data))
        index = len(requests) - 1
        return Response({"result": {
            "table": [{"session_id": ids[index]}],
            "metadata": {"query_complete": True, "has_more": index == 0,
                         "next_cursor": "next-page" if index == 0 else None},
        }})

    with patch("tracer.views.trace_session.TraceSessionView.list_sessions", autospec=True, side_effect=page), patch(
        "tracer.views.dataset.check_if_dataset_creation_is_allowed", return_value=(True, {})
    ):
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 200, response.data
    assert len(requests) == 2
    assert requests[0]["filters"] == body["session_filters"]
    assert requests[0]["project_id"] == str(observe_project.id)
    assert requests[1]["cursor"] == "next-page"
    dataset_id = response.data["result"]["dataset_id"]
    assert Row.objects.filter(dataset_id=dataset_id).count() == 1
    assert Cell.objects.get(dataset_id=dataset_id, column__name="session").value == ids[0]


@pytest.mark.django_db
def test_select_all_exclusions_over_session_limit_exports_small_selection(
    auth_client, observe_project, session_reader
):
    ids, _, reader = session_reader
    excluded_ids = [str(uuid.uuid4()) for _ in range(101)]
    body = payload(observe_project, excluded_ids)
    body.update(new_dataset_name="Many exclusions, small selection", select_all=True)
    page = Response({"result": {
        "table": [{"session_id": sid} for sid in excluded_ids + ids],
        "metadata": {"query_complete": True, "has_more": False},
    }})
    with patch("tracer.views.dataset.SESSION_EXPORT_MAX_SESSIONS", 100), patch(
        "tracer.views.trace_session.TraceSessionView.list_sessions", return_value=page
    ) as listing, patch(
        "tracer.views.dataset.check_if_dataset_creation_is_allowed", return_value=(True, {})
    ):
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 200, response.data
    listing.assert_called_once()
    assert response.data["result"]["rows_created"] == 2
    dataset_id = response.data["result"]["dataset_id"]
    assert Row.objects.filter(dataset_id=dataset_id).count() == 2
    assert set(Cell.objects.filter(
        dataset_id=dataset_id, column__name="session"
    ).values_list("value", flat=True)) == set(ids)
    reader.session_export_roots.assert_called_once_with(
        ids, project_id=str(observe_project.id), max_turns=1000,
        max_payload_bytes=10 * 1024 * 1024, include_turns=True,
    )


@pytest.mark.django_db
def test_explicit_selection_over_session_limit_is_refused(
    auth_client, observe_project, session_reader
):
    _, _, reader = session_reader
    body = payload(observe_project, [str(uuid.uuid4()) for _ in range(101)])
    body.update(new_dataset_name="Too many explicitly selected sessions", select_all=False)
    with patch("tracer.views.dataset.SESSION_EXPORT_MAX_SESSIONS", 100), patch(
        "tracer.views.trace_session.TraceSessionView.list_sessions"
    ) as listing, patch("tracer.views.dataset.create_new_dataset") as create:
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 400
    assert "100 sessions" in str(response.data)
    assert "filters" in str(response.data)
    listing.assert_not_called()
    reader.session_export_roots.assert_not_called()
    reader.export_fields_by_ids.assert_not_called()
    create.assert_not_called()


@pytest.mark.django_db
def test_incomplete_select_all_never_creates_partial_dataset(auth_client, observe_project, session_reader):
    ids, _, _ = session_reader
    body = payload(observe_project, [])
    body.update(new_dataset_name="Incomplete", select_all=True)
    with patch("tracer.views.trace_session.TraceSessionView.list_sessions", return_value=Response({
        "result": {"table": [{"session_id": ids[0]}], "metadata": {"query_complete": False}}
    })) as listing, patch("tracer.views.dataset.create_new_dataset") as create:
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 400
    listing.assert_called_once()
    create.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("select_all", [False, True])
def test_session_limit_refuses_before_read_or_create(
    auth_client, observe_project, session_reader, select_all
):
    ids, _, reader = session_reader
    body = payload(observe_project, [] if select_all else ids)
    body.update(new_dataset_name="Over session limit", select_all=select_all)
    page = Response({"result": {
        "table": [{"session_id": sid} for sid in ids],
        "metadata": {"query_complete": True, "has_more": True, "next_cursor": "next"},
    }})
    with patch("tracer.views.dataset.SESSION_EXPORT_MAX_SESSIONS", 1, create=True), patch(
        "tracer.views.trace_session.TraceSessionView.list_sessions", return_value=page
    ) as listing, patch("tracer.views.dataset.create_new_dataset") as create:
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 400
    assert "1 sessions" in str(response.data)
    assert "filters" in str(response.data)
    reader.roots_by_session_ids.assert_not_called()
    reader.session_export_roots.assert_not_called()
    reader.export_fields_by_ids.assert_not_called()
    create.assert_not_called()
    if select_all:
        listing.assert_called_once()


@pytest.mark.django_db
@pytest.mark.parametrize("limit, value, message", [
    ("SESSION_EXPORT_MAX_TURNS", 2, "2 turns"),
    ("SESSION_EXPORT_MAX_BYTES", 250, "250 payload bytes"),
])
def test_session_budget_refuses_before_hydrating_or_creating(
    auth_client, observe_project, session_reader, limit, value, message
):
    ids, _, reader = session_reader
    body = payload(observe_project, ids)
    body["new_dataset_name"] = "Over export budget"
    # One identity per batch proves the budget is request-wide, not per read.
    with patch(f"tracer.views.dataset.{limit}", value, create=True), patch(
        "tracer.views.dataset.CH_EXPORT_READ_BATCH_SIZE", 1
    ), patch("tracer.views.dataset.create_new_dataset") as create:
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 400
    assert message in str(response.data)
    assert "filters" in str(response.data)
    reader.export_fields_by_ids.assert_not_called()
    create.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["session_id", "user_id", "metrics"])
def test_unmapped_turns_are_not_hydrated(
    auth_client, observe_project, session_reader, field
):
    ids, _, reader = session_reader
    body = payload(observe_project, ids)
    body["mapping_config"] = [m for m in body["mapping_config"] if m["span_field"] == field]
    body["new_dataset_name"] = "Identity only"
    with patch("tracer.views.dataset.check_if_dataset_creation_is_allowed", return_value=(True, {})):
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 200, response.data
    reader.export_fields_by_ids.assert_not_called()
    reader.roots_by_session_ids.assert_not_called()
    assert reader.session_export_roots.call_args.kwargs["include_turns"] is False
    assert Row.objects.filter(dataset_id=response.data["result"]["dataset_id"]).count() == 2


@pytest.mark.django_db
def test_export_resolves_external_user_label_in_one_batch(
    auth_client, observe_project, session_reader
):
    ids, roots, _ = session_reader
    internal_id = str(uuid.uuid4())
    for root in roots:
        root.end_user_id = internal_id
    body = payload(observe_project, ids)
    body["new_dataset_name"] = "External user labels"
    with patch(
        "tracer.services.clickhouse.v2.end_user_dict_reader.resolve_user_ids",
        return_value={internal_id: "customer-123"},
    ) as resolve, patch(
        "tracer.views.dataset.check_if_dataset_creation_is_allowed", return_value=(True, {})
    ):
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 200, response.data
    values = list(Cell.objects.filter(
        dataset_id=response.data["result"]["dataset_id"], column__name="user"
    ).values_list("value", flat=True))
    assert values == ["customer-123", "customer-123"]
    resolve.assert_called_once()
    assert set(resolve.call_args.args[0]) == {internal_id}


@pytest.mark.django_db
def test_session_rows_are_inserted_in_bounded_batches(
    auth_client, observe_project, session_reader
):
    ids, _, _ = session_reader
    body = payload(observe_project, ids)
    body["new_dataset_name"] = "Batched rows"
    with patch("tracer.views.dataset.CHUNK_SIZE", 1), patch(
        "tracer.views.dataset.Row.objects.bulk_create", wraps=Row.objects.bulk_create
    ) as insert, patch(
        "tracer.views.dataset.check_if_dataset_creation_is_allowed", return_value=(True, {})
    ):
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 200, response.data
    assert [len(call.args[0]) for call in insert.call_args_list] == [1, 1]


@pytest.mark.django_db
def test_serialized_payload_limit_includes_duplicate_mappings(
    auth_client, observe_project, session_reader
):
    ids, _, reader = session_reader
    body = payload(observe_project, ids)
    body["mapping_config"] = [
        {"col_name": f"session-{index}", "span_field": "session_id", "data_type": "text"}
        for index in range(4)
    ]
    body["new_dataset_name"] = "Over serialized budget"
    with patch("tracer.views.dataset.SESSION_EXPORT_MAX_BYTES", 200, create=True), patch(
        "tracer.views.dataset.create_new_dataset"
    ) as create:
        response = auth_client.post("/tracer/dataset/add_to_new_dataset/", body, format="json")
    assert response.status_code == 400
    assert "200 payload bytes" in str(response.data)
    assert "filters" in str(response.data)
    reader.export_fields_by_ids.assert_not_called()
    create.assert_not_called()
