"""An SDK async evaluation whose Temporal start fails ends failed with the
reason a person reads, "This evaluation could not start.", instead of waiting
with no reason. The API call that created it answers as before, and the start's
own error text is in nothing the evaluation or the run's results return.

A start that succeeds leaves the evaluation pending for the worker.

The real dispatch, serializers, view and run creation run; only the Temporal
start is replaced.
"""

from uuid import uuid4

import pytest

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils import async_evaluations
from tfc.temporal import evaluations as temporal_evaluations

pytestmark = pytest.mark.django_db

URL = "/sdk/api/v1/evaluate-pipeline/"
CONFIG = {"eval_type_id": "TestEval", "required_keys": ["query"], "output": "score"}
COULD_NOT_START = "This evaluation could not start."
# What the failed start carried: text that must reach no caller.
START_TEXT = "frontend refused the start: connection reset by 10.0.0.7:7233"


def template(organization, workspace):
    """A customer template saved with exactly this organization and workspace."""
    name = f"start_failure_{uuid4().hex[:8]}"
    row = EvalTemplate.no_workspace_objects.create(
        name=name,
        owner="user",
        organization=organization,
        workspace=workspace,
        config=dict(CONFIG),
    )
    EvalTemplate.no_workspace_objects.filter(pk=row.pk).update(
        organization=organization, workspace=workspace, name=name
    )
    row.refresh_from_db()
    return row


def start_that(fails):
    def start(evaluation_id):
        if fails:
            raise RuntimeError(START_TEXT)
        return f"workflow-{evaluation_id}"

    return start


@pytest.mark.parametrize(
    "start_fails", [True, False], ids=["start-fails", "start-succeeds"]
)
def test_an_async_evaluation_whose_start_fails_ends_failed_with_its_sentence(
    user, organization, workspace, monkeypatch, start_fails
):
    monkeypatch.setattr(
        temporal_evaluations, "start_evaluation_workflow", start_that(start_fails)
    )

    result = async_evaluations._handle_single_async_eval(
        template(organization, workspace),
        {"query": "q"},
        None,
        user,
        workspace,
        None,
        None,
        False,
        {},
    )

    row = Evaluation.no_workspace_objects.get(pk=result["eval_id"])
    if start_fails:
        assert row.status == StatusChoices.FAILED
        assert row.reason == COULD_NOT_START
        assert row.error_message == COULD_NOT_START
    else:
        assert row.status == StatusChoices.PENDING
        assert row.reason is None
        assert row.error_message is None


def test_a_cicd_run_whose_starts_fail_lists_them_as_could_not_start(
    auth_client,
    organization,
    workspace,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from tracer.models.project import Project

    project, _ = Project.objects.get_or_create(
        name="start-failure-project",
        organization=organization,
        trace_type="observe",
        defaults={"model_type": "GenerativeLLM"},
    )
    row = template(organization, workspace)
    monkeypatch.setattr(
        temporal_evaluations, "start_evaluation_workflow", start_that(True)
    )
    version = f"v-{uuid4().hex[:8]}"

    # The run's evaluations start when its transaction commits.
    with django_capture_on_commit_callbacks(execute=True):
        posted = auth_client.post(
            URL,
            {
                "project_name": project.name,
                "version": version,
                "eval_data": [
                    {"eval_template": row.name, "inputs": {"query": ["q1", "q2"]}}
                ],
            },
            format="json",
        )
    assert posted.status_code == 200, posted.content.decode()[:500]
    assert START_TEXT not in posted.content.decode()

    response = auth_client.get(f"{URL}?project_name={project.name}&versions={version}")
    assert START_TEXT not in response.content.decode()
    result = response.json()["result"]
    assert result["status"] == "completed"
    [entry] = result["evaluation_runs"]
    assert entry["failed_evaluations"] == [
        {"eval_template": row.name, "reason": COULD_NOT_START, "count": 2}
    ]
