import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import pytest
from django.conf import settings as django_settings
from django.db import connection
from django.http import Http404
from rest_framework import status

from simulate.models import (
    AgentDefinition,
    CallExecution,
    RunTest,
    Scenarios,
)
from simulate.models import TestExecution as ExecutionModel
from simulate.serializers.preview_pagination import (
    SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE,
    SIMULATION_PREVIEW_MAX_PAGE_SIZE,
    SimulationPreviewCursorQuerySerializer,
    SimulationPreviewErrorSerializer,
    SimulationPreviewPageSerializer,
)
from simulate.services.preview_pagination import (
    PreviewCursorInvalid,
    PreviewCursorState,
    PreviewRevision,
    PreviewSnapshotChanged,
    _read_revision_state,
    assert_preview_revision,
    decode_preview_cursor,
    encode_preview_cursor,
)
from simulate.views.preview_pagination import (
    PREVIEW_SERVER_WALL_SECONDS,
    PreviewReadDeadlineExceeded,
    RunTestPreviewExecutionsView,
    _remaining_ms,
)


def _cursor_state(parent_id="00000000-0000-0000-0000-000000000001"):
    from django.utils import timezone

    return PreviewCursorState(
        kind="run_test_executions",
        parent_id=parent_id,
        scope_id=None,
        snapshot_at=timezone.now(),
        revision=PreviewRevision(
            snapshot="100:200:150",
            physical_total=3,
            active_total=3,
        ),
        page_size=2,
        emitted=2,
        after_created_at=timezone.now(),
        after_id=UUID("00000000-0000-0000-0000-000000000002"),
    )


def test_preview_cursor_is_signed_and_bound_to_parent_page_size_and_kind(settings):
    settings.SECRET_KEY = "simulation-preview-cursor-secret"
    state = _cursor_state()
    token = encode_preview_cursor(state)

    decoded = decode_preview_cursor(
        token,
        expected_kind=state.kind,
        expected_parent_id=state.parent_id,
        expected_scope_id=None,
        expected_page_size=state.page_size,
    )
    assert decoded.revision == state.revision
    assert decoded.emitted == 2

    with pytest.raises(PreviewCursorInvalid):
        decode_preview_cursor(
            f"{token}x",
            expected_kind=state.kind,
            expected_parent_id=state.parent_id,
            expected_scope_id=None,
            expected_page_size=state.page_size,
        )

    with pytest.raises(PreviewCursorInvalid):
        decode_preview_cursor(
            token,
            expected_kind=state.kind,
            expected_parent_id="00000000-0000-0000-0000-000000000099",
            expected_scope_id=None,
            expected_page_size=state.page_size,
        )
    with pytest.raises(PreviewCursorInvalid):
        decode_preview_cursor(
            token,
            expected_kind=state.kind,
            expected_parent_id=state.parent_id,
            expected_scope_id=None,
            expected_page_size=1,
        )


def test_call_preview_cursor_is_bound_to_the_selected_run_test(settings):
    from django.utils import timezone

    settings.SECRET_KEY = "simulation-preview-cursor-secret"
    run_test_id = "00000000-0000-0000-0000-000000000010"
    state = PreviewCursorState(
        kind="test_execution_calls",
        parent_id="00000000-0000-0000-0000-000000000001",
        scope_id=run_test_id,
        snapshot_at=timezone.now(),
        revision=PreviewRevision(
            snapshot="100:200:150", physical_total=2, active_total=2
        ),
        page_size=1,
        emitted=1,
        after_created_at=timezone.now(),
        after_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    token = encode_preview_cursor(state)

    with pytest.raises(PreviewCursorInvalid, match="another run test"):
        decode_preview_cursor(
            token,
            expected_kind=state.kind,
            expected_parent_id=state.parent_id,
            expected_scope_id="00000000-0000-0000-0000-000000000099",
            expected_page_size=state.page_size,
        )


def test_revision_rejects_equal_count_membership_or_bulk_version_change():
    revision = PreviewRevision(
        snapshot="100:200:150",
        physical_total=3,
        active_total=3,
    )
    # Counts are deliberately identical. all_visible=False represents either
    # an equal-count delete/insert swap or QuerySet.update(), both of which
    # create a current xmin that was not visible in the signed snapshot.
    with patch(
        "simulate.services.preview_pagination._read_revision_state",
        return_value=(True, 3, 3, False),
    ):
        with pytest.raises(PreviewSnapshotChanged):
            assert_preview_revision(
                kind="run_test_executions",
                parent_id="00000000-0000-0000-0000-000000000001",
                revision=revision,
            )


def test_revision_reconstructs_epoch_aware_xmin_before_visibility_check():
    with patch("simulate.services.preview_pagination.connection.cursor") as cursor:
        cursor_context = cursor.return_value.__enter__.return_value
        cursor_context.fetchone.return_value = (True, 3, 3, True)
        _read_revision_state(
            kind="run_test_executions",
            parent_id="00000000-0000-0000-0000-000000000001",
            original_snapshot="4294967300:4294967310:",
        )
        sql = cursor_context.execute.call_args.args[0]
    assert "txid_current()::bigint AS current_txid" in sql
    assert "current_txid - age(parent_item.xmin)" in sql
    assert "current_txid - age(child_item.xmin)" in sql
    assert "xmin::text" not in sql


def test_preview_query_uses_configured_default_and_caps_each_user_action():
    default_serializer = SimulationPreviewCursorQuerySerializer(data={})
    assert default_serializer.is_valid(), default_serializer.errors
    assert (
        default_serializer.validated_data["page_size"]
        == SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE
    )

    serializer = SimulationPreviewCursorQuerySerializer(
        data={"page_size": SIMULATION_PREVIEW_MAX_PAGE_SIZE + 1}
    )
    assert not serializer.is_valid()
    assert "page_size" in serializer.errors


def test_call_preview_query_requires_run_test_scope():
    from simulate.serializers.preview_pagination import (
        SimulationCallPreviewCursorQuerySerializer,
    )

    serializer = SimulationCallPreviewCursorQuerySerializer(data={})
    assert not serializer.is_valid()
    assert set(serializer.errors) == {"run_test_id"}


def test_preview_response_serializers_reject_missing_or_unknown_only_payloads():
    missing_page_fields = SimulationPreviewPageSerializer(data={"results": []})
    assert not missing_page_fields.is_valid()
    assert set(missing_page_fields.errors) >= {
        "next_cursor",
        "has_more",
        "snapshot_total",
        "loaded_through",
        "complete",
        "exact",
        "snapshot_at",
    }

    unknown_only_error = SimulationPreviewErrorSerializer(
        data={"garbage": "accepted-before-read-only-fix"}
    )
    assert not unknown_only_error.is_valid()
    assert set(unknown_only_error.errors) == {"code", "detail"}


def test_preview_server_uses_one_bounded_wall_for_all_statements():
    assert PREVIEW_SERVER_WALL_SECONDS == (
        django_settings.INTERACTIVE_READ_DEFAULT_WALL_MS / 1_000
    )
    with patch("simulate.views.preview_pagination.time.monotonic", return_value=100.0):
        assert (
            _remaining_ms(100.0 + PREVIEW_SERVER_WALL_SECONDS)
            == django_settings.INTERACTIVE_READ_DEFAULT_WALL_MS
        )
        with pytest.raises(PreviewReadDeadlineExceeded):
            _remaining_ms(100.0)


def test_missing_source_during_continuation_is_restartable_drift():
    view = RunTestPreviewExecutionsView()
    request = SimpleNamespace(
        method="GET",
        data={},
        query_params={"cursor": "signed-continuation"},
    )
    with patch.object(view, "_read_page", side_effect=Http404):
        response = view.get(
            request,
            run_test_id=UUID("00000000-0000-0000-0000-000000000001"),
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data == {
        "code": "simulation_preview_snapshot_changed",
        "detail": "Simulation preview source changed while more rows were loading.",
        "restart_required": True,
    }


def test_preview_routes_have_generated_runtime_openapi_contracts():
    swagger_path = (
        Path(__file__).resolve().parents[3]
        / "api_contracts"
        / "openapi"
        / "swagger.json"
    )
    swagger = json.loads(swagger_path.read_text())
    for path in (
        "/simulate/run-tests/{run_test_id}/preview-executions/",
        "/simulate/test-executions/{test_execution_id}/preview-calls/",
    ):
        operation = swagger["paths"][path]["get"]
        assert operation["x-runtime-request-validation"] is True
        assert operation["x-runtime-response-validation"] is True
        assert operation["responses"]["200"]["schema"] == {
            "$ref": "#/definitions/SimulationPreviewPage"
        }
        assert set(operation["responses"]) >= {"200", "400", "404", "409", "503"}

    assert set(swagger["definitions"]["SimulationPreviewItem"]["required"]) == {
        "id",
        "status",
        "created_at",
    }
    assert set(swagger["definitions"]["SimulationPreviewPage"]["required"]) == {
        "results",
        "next_cursor",
        "has_more",
        "snapshot_total",
        "loaded_through",
        "complete",
        "exact",
        "snapshot_at",
    }
    assert set(swagger["definitions"]["SimulationPreviewError"]["required"]) == {
        "code",
        "detail",
    }
    call_parameters = swagger["paths"][
        "/simulate/test-executions/{test_execution_id}/preview-calls/"
    ]["get"]["parameters"]
    run_test_parameter = next(
        parameter for parameter in call_parameters if parameter["name"] == "run_test_id"
    )
    assert run_test_parameter == {
        "name": "run_test_id",
        "in": "query",
        "description": (
            "Run-test scope selected by the preview. The execution and every "
            "signed continuation must belong to this run test."
        ),
        "required": True,
        "type": "string",
        "format": "uuid",
    }


@pytest.fixture
def preview_run_test(db, organization, workspace):
    agent = AgentDefinition.objects.create(
        agent_name="Preview paging agent",
        agent_type=AgentDefinition.AgentTypeChoices.TEXT,
        inbound=True,
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )
    return RunTest.objects.create(
        name="Preview paging run",
        organization=organization,
        workspace=workspace,
        agent_definition=agent,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )


@pytest.fixture
def preview_scenario(db, organization, workspace, preview_run_test):
    scenario = Scenarios.objects.create(
        name="Preview paging scenario",
        source="seed",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        source_type=Scenarios.SourceTypes.AGENT_DEFINITION,
        organization=organization,
        workspace=workspace,
        agent_definition=preview_run_test.agent_definition,
    )
    preview_run_test.scenarios.add(scenario)
    return scenario


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="requires PostgreSQL MVCC"
)
def test_execution_continuation_rejects_bulk_update_without_updated_at(
    auth_client, preview_run_test
):
    executions = [
        ExecutionModel.objects.create(
            run_test=preview_run_test,
            status=ExecutionModel.ExecutionStatus.COMPLETED,
        )
        for _ in range(3)
    ]
    first = auth_client.get(
        f"/simulate/run-tests/{preview_run_test.id}/preview-executions/",
        {"page_size": 1},
    )
    assert first.status_code == status.HTTP_200_OK, first.content
    cursor = first.json()["next_cursor"]
    hidden = next(
        execution
        for execution in executions
        if str(execution.id) != first.json()["results"][0]["id"]
    )
    original_updated_at = hidden.updated_at

    ExecutionModel.objects.filter(id=hidden.id).update(
        status=ExecutionModel.ExecutionStatus.FAILED
    )
    hidden.refresh_from_db()
    assert hidden.updated_at == original_updated_at

    continuation = auth_client.get(
        f"/simulate/run-tests/{preview_run_test.id}/preview-executions/",
        {"page_size": 1, "cursor": cursor},
    )
    assert continuation.status_code == status.HTTP_409_CONFLICT
    assert continuation.json()["code"] == "simulation_preview_snapshot_changed"
    assert continuation.json()["restart_required"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="requires PostgreSQL MVCC"
)
def test_execution_continuation_rejects_equal_count_membership_swap(
    auth_client, preview_run_test
):
    executions = [
        ExecutionModel.objects.create(
            run_test=preview_run_test,
            status=ExecutionModel.ExecutionStatus.COMPLETED,
        )
        for _ in range(2)
    ]
    first = auth_client.get(
        f"/simulate/run-tests/{preview_run_test.id}/preview-executions/",
        {"page_size": 1},
    )
    assert first.status_code == status.HTTP_200_OK, first.content
    cursor = first.json()["next_cursor"]
    hidden = next(
        execution
        for execution in executions
        if str(execution.id) != first.json()["results"][0]["id"]
    )

    # QuerySet.delete is a physical delete (the BaseModel soft-delete override
    # applies only to instance.delete). Replacing it preserves both physical
    # and active counts, so xmin visibility is what must catch the swap.
    ExecutionModel.all_objects.filter(id=hidden.id).delete()
    ExecutionModel.objects.create(
        run_test=preview_run_test,
        status=ExecutionModel.ExecutionStatus.COMPLETED,
    )

    continuation = auth_client.get(
        f"/simulate/run-tests/{preview_run_test.id}/preview-executions/",
        {"page_size": 1, "cursor": cursor},
    )
    assert continuation.status_code == status.HTTP_409_CONFLICT
    assert continuation.json()["code"] == "simulation_preview_snapshot_changed"


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="requires PostgreSQL MVCC"
)
def test_initial_preview_does_not_drop_future_skewed_application_timestamps(
    auth_client, preview_run_test
):
    executions = [
        ExecutionModel.objects.create(
            run_test=preview_run_test,
            status=ExecutionModel.ExecutionStatus.COMPLETED,
        )
        for _ in range(2)
    ]
    from django.utils import timezone

    ExecutionModel.all_objects.filter(
        id__in=[execution.id for execution in executions]
    ).update(created_at=timezone.now() + timedelta(minutes=5))

    response = auth_client.get(
        f"/simulate/run-tests/{preview_run_test.id}/preview-executions/",
        {"page_size": 1},
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    assert response.json()["snapshot_total"] == len(executions)
    assert len(response.json()["results"]) == 1
    assert response.json()["next_cursor"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="requires PostgreSQL MVCC"
)
def test_call_preview_has_truthful_read_more_and_terminal_state(
    auth_client, preview_run_test, preview_scenario
):
    execution = ExecutionModel.objects.create(
        run_test=preview_run_test,
        status=ExecutionModel.ExecutionStatus.COMPLETED,
    )
    calls = [
        CallExecution.objects.create(
            test_execution=execution,
            scenario=preview_scenario,
            status=CallExecution.CallStatus.COMPLETED,
            simulation_call_type=AgentDefinition.AgentTypeChoices.TEXT,
        )
        for _ in range(2)
    ]

    first = auth_client.get(
        f"/simulate/test-executions/{execution.id}/preview-calls/",
        {"page_size": 1, "run_test_id": str(preview_run_test.id)},
    )
    assert first.status_code == status.HTTP_200_OK, first.content
    first_body = first.json()
    assert first_body["exact"] is True
    assert first_body["complete"] is False
    assert first_body["snapshot_total"] == len(calls)
    assert first_body["next_cursor"]

    second = auth_client.get(
        f"/simulate/test-executions/{execution.id}/preview-calls/",
        {
            "page_size": 1,
            "run_test_id": str(preview_run_test.id),
            "cursor": first_body["next_cursor"],
        },
    )
    assert second.status_code == status.HTTP_200_OK, second.content
    second_body = second.json()
    assert second_body["complete"] is True
    assert second_body["next_cursor"] is None
    assert second_body["snapshot_at"] == first_body["snapshot_at"]
    assert {
        first_body["results"][0]["id"],
        second_body["results"][0]["id"],
    } == {str(call.id) for call in calls}


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(
    connection.vendor != "postgresql", reason="requires PostgreSQL MVCC"
)
def test_call_preview_rejects_execution_from_another_selected_run_test(
    auth_client, preview_run_test, preview_scenario
):
    other_run_test = RunTest.objects.create(
        name="Other preview run",
        organization=preview_run_test.organization,
        workspace=preview_run_test.workspace,
        agent_definition=preview_run_test.agent_definition,
        source_type=RunTest.SourceTypes.AGENT_DEFINITION,
    )
    execution = ExecutionModel.objects.create(
        run_test=preview_run_test,
        status=ExecutionModel.ExecutionStatus.COMPLETED,
    )
    CallExecution.objects.create(
        test_execution=execution,
        scenario=preview_scenario,
        status=CallExecution.CallStatus.COMPLETED,
        simulation_call_type=AgentDefinition.AgentTypeChoices.TEXT,
    )

    response = auth_client.get(
        f"/simulate/test-executions/{execution.id}/preview-calls/",
        {"page_size": 1, "run_test_id": str(other_run_test.id)},
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["code"] == "simulation_preview_not_found"
