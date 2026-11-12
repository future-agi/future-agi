"""S60: database failures are not eval-config validation errors."""

import uuid
from unittest.mock import patch

import pytest
from django.db import OperationalError

from tracer.serializers.eval_task import EditEvalTaskSerializer

MARKER = "S60_PRIVATE_SQL_HOST_MARKER"
URL = "/tracer/eval-task/update_eval_task/"


def _payload(config_id):
    return {
        "eval_task_id": str(uuid.uuid4()),
        "edit_type": "fresh_run",
        "evals": [str(config_id)],
    }


def test_unexpected_eval_lookup_exception_propagates():
    serializer = EditEvalTaskSerializer(data=_payload(uuid.uuid4()))
    with (
        patch(
            "tracer.serializers.eval_task.scope_to_caller",
            side_effect=OperationalError(MARKER),
        ),
        pytest.raises(OperationalError, match=MARKER),
    ):
        serializer.is_valid()


@pytest.mark.django_db
@pytest.mark.parametrize("error_type", [OperationalError, RuntimeError])
def test_eval_update_database_failure_has_fixed_body(auth_client, error_type):
    # Exercise the request decorator too: it must not do the scoped DB read
    # outside the action's fixed generic error handler.
    with patch(
        "tracer.serializers.eval_task.scope_to_caller",
        side_effect=error_type(MARKER),
    ) as lookup:
        response = auth_client.patch(URL, _payload(uuid.uuid4()), format="json")
    assert lookup.call_count == 1
    assert response.status_code == 500
    assert response.data["type"] == "server_error"
    assert response.data["code"] == "server_error"
    assert response.data["message"] == "Evaluation task could not be updated"
    assert MARKER not in str(response.data)
    assert error_type.__name__ not in str(response.data)


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["unknown", "foreign_workspace"])
def test_invalid_eval_id_keeps_validation_text(
    auth_client, organization, workspace, custom_eval_config, kind
):
    from accounts.models.workspace import Workspace

    config_id = uuid.uuid4()
    if kind == "foreign_workspace":
        foreign = Workspace.no_workspace_objects.create(
            name="S60 foreign", organization=organization, is_default=False
        )
        project = custom_eval_config.project
        project.workspace = foreign
        project.save(update_fields=["workspace"])
        config_id = custom_eval_config.id
    response = auth_client.patch(URL, _payload(config_id), format="json")
    assert response.status_code == 400
    expected = (
        "Invalid eval config IDs: ['Could not find eval configs with IDs: "
        f"{config_id}']"
    )
    assert response.data["details"]["evals"] == [expected]


@pytest.mark.django_db
def test_owned_eval_config_still_validates(auth_client, custom_eval_config):
    response = auth_client.patch(URL, _payload(custom_eval_config.id), format="json")
    # The config passes; the deliberately nonexistent task is the next check.
    assert response.status_code == 400
    assert response.data["message"] == "Eval task not found"
