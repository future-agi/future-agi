"""When the evaluations of an SDK CI/CD run (/sdk/api/v1/evaluate-pipeline/)
start, and what the evaluation worker does with an evaluation it cannot read.

create_evaluations creates every item's evaluations inside one transaction. The
worker reads each evaluation in its own database connection, so each workflow
starts when that transaction commits: none starts while it is open, and every
evaluation of the run starts exactly once.

The worker given an evaluation that is not there ends with status MISSING and
raises nothing; there is no row for it to mark.

The real serializers, view, run creation and evaluation dispatch run; only the
Temporal start is replaced.
"""

from uuid import uuid4

import pytest
from django.db import connection

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation
from tfc.temporal import evaluations as temporal_evaluations
from tfc.temporal.evaluations import activities
from tracer.models.eval_ci_cd import EvaluationResult, EvaluationRun

pytestmark = pytest.mark.django_db

URL = "/sdk/api/v1/evaluate-pipeline/"
CONFIG = {"eval_type_id": "TestEval", "required_keys": ["query"], "output": "score"}


def template(organization, workspace):
    """A customer template saved with exactly this organization and workspace."""
    name = f"cicd_commit_{uuid4().hex[:8]}"
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


def test_each_evaluation_of_a_run_starts_once_after_the_run_is_saved(
    auth_client,
    organization,
    workspace,
    monkeypatch,
    django_capture_on_commit_callbacks,
):
    from tracer.models.project import Project

    project, _ = Project.objects.get_or_create(
        name="cicd-start-after-commit-project",
        organization=organization,
        trace_type="observe",
        defaults={"model_type": "GenerativeLLM"},
    )
    first = template(organization, workspace)
    second = template(organization, workspace)
    # Depth of open transactions and savepoints while the request runs, as the
    # test sees it before the request.
    depth = len(connection.savepoint_ids)
    started = []

    def start(evaluation_id):
        started.append((evaluation_id, len(connection.savepoint_ids)))
        return f"workflow-{evaluation_id}"

    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", start)

    with django_capture_on_commit_callbacks(execute=True):
        response = auth_client.post(
            URL,
            {
                "project_name": project.name,
                "version": f"v-{uuid4().hex[:8]}",
                "eval_data": [
                    {"eval_template": row.name, "inputs": {"query": ["q1", "q2"]}}
                    for row in (first, second)
                ],
            },
            format="json",
        )
        # Nothing starts while the request's transaction is open.
        assert started == []

    assert response.status_code == 200, response.content.decode()[:500]
    run = EvaluationRun.no_workspace_objects.get(
        id=response.json()["result"]["evaluation_run_id"]
    )
    linked = {
        str(evaluation_id)
        for evaluation_id in EvaluationResult.no_workspace_objects.filter(
            evaluation_run=run
        ).values_list("evaluation_id", flat=True)
    }
    assert len(linked) == 4
    assert sorted(evaluation_id for evaluation_id, _ in started) == sorted(linked)
    assert {open_depth for _, open_depth in started} == {depth}


def test_the_worker_ends_cleanly_when_its_evaluation_is_not_there(monkeypatch):
    # Connection cleanup is transport plumbing; keep pytest's DB transaction open.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)
    missing = str(uuid4())

    result = activities._run_single_evaluation_sync(missing)

    assert result["evaluation_id"] == missing
    assert result["status"] == "MISSING"
    assert not Evaluation.no_workspace_objects.filter(pk=missing).exists()
