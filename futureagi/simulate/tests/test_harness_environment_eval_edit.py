"""Editing one eval of an environment, over HTTP: every refusal of
``PATCH evaluations/{id}/`` in the order the route checks them, that a refusal
saves nothing, and that an edit never grades.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.develop_dataset import KnowledgeBaseFile
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import RunTest, SimulateEvalConfig
from simulate.services.eval_config_edit import EvalConfigEditRefused, update_eval_config
from simulate.services.hosted_harness import create_hosted_job
from simulate.tests import test_harness_environment_evals as env_evals
from tfc.middleware.workspace_context import clear_workspace_context

from .test_hosted_harness_channels import _payload

# A built environment and a client in its workspace, exactly as the
# environment's other eval endpoint tests build them.
environment = env_evals.environment
env_client = env_evals.env_client

ENVIRONMENTS = env_evals.ENVIRONMENTS


@pytest.fixture
def no_grading():
    """Spies on both grading jobs; an edit must start neither."""
    with (
        patch(
            "simulate.services.test_executor.run_new_evals_on_call_executions_task.apply_async"
        ) as bulk,
        patch(
            "simulate.services.test_executor._run_simulate_evaluations_task.apply_async"
        ) as single,
    ):
        yield bulk, single


def _edit(client, job_id, eval_config_id, workspace, body):
    return client.patch(
        f"{ENVIRONMENTS}/{job_id}/evaluations/{eval_config_id}/",
        body,
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


@pytest.fixture
def word_count(environment, workspace):
    return EvalTemplate.objects.create(
        name="word_count_edit",
        description="Word count contract template",
        organization=environment.organization,
        workspace=workspace,
        config={
            "required_keys": ["text"],
            "optional_keys": [],
            "output": "Pass/Fail",
            "eval_type_id": "word_count_in_range",
            "function_params_schema": {
                "min_words": {
                    "type": "integer",
                    "required": True,
                    "default": 1,
                    "minimum": 0,
                },
                "max_words": {
                    "type": "integer",
                    "required": True,
                    "default": 20,
                    "minimum": 1,
                },
            },
            "config": {},
        },
        eval_tags=["api-contract"],
    )


@pytest.fixture
def politeness(environment, word_count):
    """An eval a person added: it maps an input of its own."""
    return SimulateEvalConfig.objects.create(
        name="Politeness",
        eval_template=word_count,
        run_test=environment.run_test,
        config={"params": {"min_words": 2, "max_words": 8}},
        mapping={"text": "transcript"},
    )


def _row(config):
    """Every stored field an edit could change."""
    config.refresh_from_db()
    return {
        field: getattr(config, field)
        for field in (
            "name",
            "config",
            "mapping",
            "filters",
            "model",
            "error_localizer",
            "kb_id",
            "eval_template_id",
            "updated_at",
        )
    }


def _clock(environment):
    environment.refresh_from_db()
    return environment.content_updated_at


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("body", "field", "message"),
    [
        pytest.param(
            {"threshold": 0.7}, "threshold", "Unknown field.", id="unknown-key"
        ),
        pytest.param({"run": True}, "run", "Unknown field.", id="run-flag"),
        pytest.param(
            {"test_execution_id": str(uuid.uuid4())},
            "test_execution_id",
            "Unknown field.",
            id="grade-this-run",
        ),
        pytest.param(
            {"template_id": str(uuid.uuid4())},
            "template_id",
            "Unknown field.",
            id="template-switch",
        ),
        pytest.param(
            {"error_localizer": "maybe"},
            "error_localizer",
            "Must be a valid boolean.",
            id="wrong-type",
        ),
    ],
)
def test_edit_refuses_a_body_that_fails_validation(
    env_client, workspace, no_grading, body, field, message
):
    """The body is checked before the environment is looked up, so no such
    environment is needed to see the refusal."""
    response = _edit(env_client, uuid.uuid4(), uuid.uuid4(), workspace, body)

    assert response.status_code == 400, response.content
    payload = response.json()
    assert payload["status"] is False
    assert payload["details"] == {field: [message]}
    assert payload["detail"] == f"{field}: {message}"


@pytest.mark.django_db
def test_edit_answers_not_found_for_an_environment_outside_this_workspace(
    env_client, user, environment, workspace, no_grading, politeness
):
    other_workspace = Workspace.objects.create(
        name="Other workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    body = {"name": "Politeness v2"}
    before = _row(politeness)
    clock = _clock(environment)

    elsewhere = _edit(env_client, environment.id, politeness.id, other_workspace, body)
    missing = _edit(env_client, uuid.uuid4(), politeness.id, workspace, body)

    for response in (elsewhere, missing):
        assert response.status_code == 404, response.content
        assert response.json() == {"detail": "Environment not found"}
    assert _row(politeness) == before
    assert _clock(environment) == clock


@pytest.mark.django_db
def test_edit_refuses_an_environment_that_is_still_building(
    env_client, user, workspace, no_grading
):
    job, _ = create_hosted_job(
        user.organization,
        _payload(),
        idempotency_key="env-eval-edit-unbuilt",
        workspace=workspace,
    )

    response = _edit(env_client, job.id, uuid.uuid4(), workspace, {"name": "x"})

    assert response.status_code == 409, response.content
    assert response.json() == {
        "detail": "Environment has no evaluations until it finishes building"
    }


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["unknown", "removed", "another-run-tests"])
def test_edit_refuses_an_eval_that_is_not_this_environments(
    env_client, environment, workspace, no_grading, politeness, word_count, kind
):
    """An empty body, which proves the lookup comes before "Nothing to
    change"."""
    if kind == "unknown":
        eval_config_id = uuid.uuid4()
    elif kind == "removed":
        SimulateEvalConfig.objects.filter(pk=politeness.pk).update(deleted=True)
        eval_config_id = politeness.id
    else:
        other = RunTest.objects.create(
            name="Other run test",
            organization=environment.organization,
            workspace=workspace,
        )
        eval_config_id = SimulateEvalConfig.objects.create(
            name="Politeness",
            eval_template=word_count,
            run_test=other,
            mapping={"text": "transcript"},
        ).id

    response = _edit(env_client, environment.id, eval_config_id, workspace, {})

    assert response.status_code == 404, response.content
    assert response.json() == {"detail": "Evaluation not found"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"name": "Task completion v2"}, id="rename"),
        pytest.param({"mapping": {"conversation": "transcript"}}, id="map-an-input"),
        pytest.param({}, id="empty-body"),
    ],
)
def test_edit_refuses_a_harness_filled_eval_whatever_the_body(
    env_client, environment, workspace, no_grading, body
):
    template = EvalTemplate.objects.create(
        name="suite_edit",
        config={"required_keys": ["conversation"], "output": "Pass/Fail"},
        owner="system",
    )
    suite = SimulateEvalConfig.objects.create(
        name="Task completion",
        eval_template=template,
        run_test=environment.run_test,
        mapping={},
    )
    before = _row(suite)
    clock = _clock(environment)

    response = _edit(env_client, environment.id, suite.id, workspace, body)

    assert response.status_code == 400, response.content
    assert response.json() == {
        "detail": "Task completion is set by the harness and can't be edited here."
    }
    assert _row(suite) == before
    assert _clock(environment) == clock


@pytest.mark.django_db
def test_edit_refuses_an_empty_body(
    env_client, environment, workspace, no_grading, politeness
):
    before = _row(politeness)
    clock = _clock(environment)

    response = _edit(env_client, environment.id, politeness.id, workspace, {})

    assert response.status_code == 400, response.content
    assert response.json() == {"detail": "Nothing to change"}
    assert _row(politeness) == before
    assert _clock(environment) == clock


@pytest.mark.django_db
def test_edit_refuses_a_config_that_does_not_fit_the_template(
    env_client, environment, workspace, no_grading, politeness
):
    before = _row(politeness)
    clock = _clock(environment)

    response = _edit(
        env_client,
        environment.id,
        politeness.id,
        workspace,
        {"config": {"params": {"min_words": -1, "max_words": 8}}},
    )

    assert response.status_code == 400, response.content
    assert response.json() == {"detail": "min_words must be >= 0"}
    assert _row(politeness) == before
    assert _clock(environment) == clock


@pytest.mark.django_db
def test_edit_refuses_a_name_another_eval_of_this_environment_has(
    env_client, environment, workspace, no_grading, politeness, word_count
):
    SimulateEvalConfig.objects.create(
        name="Accuracy",
        eval_template=word_count,
        run_test=environment.run_test,
        mapping={"text": "transcript"},
    )
    before = _row(politeness)
    clock = _clock(environment)

    response = _edit(
        env_client, environment.id, politeness.id, workspace, {"name": "Accuracy"}
    )

    assert response.status_code == 400, response.content
    assert response.json() == {
        "detail": (
            "An evaluation config with the name 'Accuracy' already exists in "
            "this run test. Please use a different name."
        )
    }
    assert _row(politeness) == before
    assert _clock(environment) == clock


@pytest.mark.django_db
def test_edit_refuses_a_knowledge_base_outside_this_organization(
    env_client, environment, workspace, no_grading, politeness
):
    before = _row(politeness)
    clock = _clock(environment)

    other_organization = Organization.objects.create(name="Other org for KB scope")
    elsewhere = KnowledgeBaseFile.objects.create(
        name="Someone else's knowledge base", organization=other_organization
    )

    for kb_id in (str(elsewhere.id), str(uuid.uuid4())):
        response = _edit(
            env_client, environment.id, politeness.id, workspace, {"kb_id": kb_id}
        )

        assert response.status_code == 400, response.content
        assert response.json() == {"detail": "Knowledge base not found"}
        assert _row(politeness) == before
        assert _clock(environment) == clock


@pytest.mark.django_db
@pytest.mark.parametrize(
    "mapping", [pytest.param({}, id="empty"), pytest.param(None, id="null")]
)
def test_edit_refuses_an_edit_that_leaves_the_eval_without_inputs(
    env_client, environment, workspace, no_grading, politeness, mapping
):
    """The refusal names the stored name, not the one sent with it: the check
    runs on the changed but unsaved row."""
    before = _row(politeness)
    clock = _clock(environment)

    response = _edit(
        env_client,
        environment.id,
        politeness.id,
        workspace,
        {"name": "Renamed", "mapping": mapping},
    )

    assert response.status_code == 400, response.content
    assert response.json() == {"detail": "Politeness needs at least one input mapped"}
    assert _row(politeness) == before
    assert politeness.name == "Politeness"
    assert _clock(environment) == clock


@pytest.mark.django_db
def test_edit_saves_the_change_and_returns_the_row_as_the_run_test_lists_it(
    env_client, environment, workspace, no_grading, politeness
):
    bulk, single = no_grading
    clock = _clock(environment)

    response = _edit(
        env_client,
        environment.id,
        politeness.id,
        workspace,
        {
            "name": "Politeness v2",
            "config": {"params": {"min_words": 3, "max_words": 9}},
            "model": "gpt-4o-mini",
            "error_localizer": True,
            "filters": [],
        },
    )

    assert response.status_code == 200, response.content
    detail = env_client.get(
        f"/simulate/run-tests/{environment.run_test_id}/",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    assert detail.status_code == 200, detail.content
    item = next(
        i
        for i in detail.json()["simulate_eval_configs_detail"]
        if i["id"] == str(politeness.id)
    )
    body = response.json()
    assert body == item
    assert body["name"] == "Politeness v2"
    assert body["config"]["params"] == {"min_words": 3, "max_words": 9}
    assert body["model"] == "gpt-4o-mini"
    assert body["error_localizer"] is True
    assert body["mapping"] == {"text": "transcript"}
    assert body["editable"] is True
    assert body["regradable"] is True

    politeness.refresh_from_db()
    assert politeness.name == "Politeness v2"
    assert politeness.config["params"] == {"min_words": 3, "max_words": 9}
    assert politeness.model == "gpt-4o-mini"
    assert politeness.error_localizer is True
    assert politeness.mapping == {"text": "transcript"}
    assert politeness.filters == []
    bulk.assert_not_called()
    single.assert_not_called()
    moved = _clock(environment)
    assert moved is not None
    if clock is not None:
        assert moved > clock


@pytest.mark.django_db
def test_the_service_keeps_the_row_unsaved_when_the_hook_refuses(
    environment, politeness
):
    """``before_save`` must run before the save, with no atomic block of its own.

    The route wraps the call in ``transaction.atomic()``, which would roll an
    early save back and hide the wrong order; here the service is called
    bare so the order itself is what is checked.
    """
    before = _row(politeness)

    def refuse(edited):
        assert edited.mapping == {}, "the hook sees the changed, unsaved row"
        raise EvalConfigEditRefused("Politeness needs at least one input mapped")

    with pytest.raises(EvalConfigEditRefused):
        update_eval_config(
            politeness,
            {"mapping": {}},
            organization=environment.organization,
            workspace=None,
            before_save=refuse,
        )

    assert _row(politeness) == before


@pytest.mark.django_db
def test_the_service_refuses_another_organizations_knowledge_base_without_a_request(
    environment, politeness
):
    """The ``organization`` filter is the only guard when no workspace context is set.

    Over HTTP the base manager already scopes the lookup by the request's
    workspace; a task or a shell has no such context, so the service must
    refuse on its own.
    """
    other_organization = Organization.objects.create(name="Other org, no context")
    elsewhere = KnowledgeBaseFile.objects.create(
        name="Someone else's knowledge base", organization=other_organization
    )
    before = _row(politeness)
    clear_workspace_context()

    with pytest.raises(EvalConfigEditRefused, match="Knowledge base not found"):
        update_eval_config(
            politeness,
            {"kb_id": str(elsewhere.id)},
            organization=environment.organization,
            workspace=None,
        )

    assert _row(politeness) == before
