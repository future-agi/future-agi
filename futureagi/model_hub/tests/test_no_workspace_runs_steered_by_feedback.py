"""Runs on datasets and metrics with no workspace are steered by feedback.

A dataset saved with no workspace is visible only from the organization's
default workspace. A run a person starts on it reads the feedback of the
workspace it was started in, which can only be the default workspace, plus
same-organization feedback saved with no workspace before the release cutoff.
A scheduled run reads the workspace stored on its metric, or the
organization's default workspace when none is stored, whatever workspace its
request-like input names. Feedback of another workspace, of another
organization, and no-workspace feedback written after the cutoff is never
read, and every read filters through the one feedback scope clause.

Real ORM rows, views, readers and vector SQL; only inference, billing and the
evaluator are stubbed.
"""

import uuid
from types import SimpleNamespace

import pytest

from agentic_eval.core.embeddings import embedding_manager as em
from model_hub.tests.test_feedback_rag_consumer_scope import (
    _django_db_helper as _django_db_helper,
)
from model_hub.tests.test_feedback_rag_consumer_scope import (
    consumer_scope as consumer_scope,
)
from model_hub.tests.test_feedback_rag_consumer_scope import (
    standalone_recorder as standalone_recorder,
)
from model_hub.tests.test_feedback_rag_workspace_scope import (
    feedback_store as feedback_store,
)
from model_hub.tests.test_feedback_standalone_callers_scope import (
    _invoke,
)
from model_hub.tests.test_feedback_standalone_callers_scope import (
    callers as callers,
)
from model_hub.tests.test_no_workspace_feedback_steers_again import (
    _insert_production_shaped,
)
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

MARKERS = (
    "default-workspace",
    "second-workspace",
    "older-unscoped",
    "newer-unscoped",
    "other-org",
    "other-org-unscoped",
)


def _seed(scope):
    """Feedback rows as production and this build write them."""
    scope.insert("default-workspace", workspace_id=scope.workspace_id)
    scope.insert("second-workspace", workspace_id=scope.foreign_workspace_id)
    _insert_production_shaped(scope, "older-unscoped")
    scope.insert("newer-unscoped", workspace_id=None, written_after_cutoff=True)
    scope.insert(
        "other-org",
        workspace_id=scope.workspace_id,
        organization_id=scope.foreign_organization_id,
    )
    _insert_production_shaped(
        scope, "other-org-unscoped", organization_id=scope.foreign_organization_id
    )


def _read_markers(result):
    text = str(result)
    return {marker for marker in MARKERS if f"{marker}-comment" in text}


def _scheduled_metric_run(
    scope, *, metric_workspace_id, dataset_workspace_id, source_configs=None
):
    """A worker run built from the stored metric only, as the scheduler does."""
    from model_hub.views.eval_runner import EvaluationRunner

    metric_id = str(uuid.uuid4())
    runner = EvaluationRunner(
        user_eval_metric_id=metric_id,
        format_output=True,
        source="dataset_evaluation",
        source_configs=source_configs,
    )
    dataset = SimpleNamespace(
        organization_id=scope.organization_id, workspace_id=dataset_workspace_id
    )
    runner.dataset = dataset
    runner.user_eval_metric = SimpleNamespace(
        id=metric_id,
        organization_id=scope.organization_id,
        workspace_id=metric_workspace_id,
        dataset=dataset,
    )
    runner.eval_template = scope.template
    return runner.get_few_shot_examples(["current case"], ["output"])


@pytest.mark.parametrize("consumer", ["preview", "compare"])
def test_person_started_run_on_no_workspace_dataset_reads_default_workspace_feedback(
    callers, consumer
):
    scope = callers.scope
    _seed(scope)
    callers.dataset.workspace = None
    callers.dataset.save(update_fields=["workspace"])
    assert scope.workspace.is_default, "SETUP: the run starts in the default workspace"
    result = _invoke(consumer, callers, scope.template)
    assert _read_markers(result) == {"default-workspace", "older-unscoped"}, (
        "a run started in the default workspace on a dataset with no workspace "
        "reads that workspace's feedback and older same-organization feedback "
        "with no workspace, and nothing else"
    )


def test_no_workspace_dataset_binds_only_the_default_workspace(consumer_scope):
    from model_hub.utils.feedback_run_scope import (
        STARTED_BY_PERSON,
        STARTED_BY_SCHEDULE,
        feedback_workspace_for_run,
    )

    scope = consumer_scope
    org = scope.organization_id

    def person(workspace_id, target=None, organization_id=org):
        return feedback_workspace_for_run(
            organization_id,
            target,
            started_by=STARTED_BY_PERSON,
            workspace_id=workspace_id,
        )

    assert person(scope.workspace_id) == scope.workspace_id, (
        "a dataset with no workspace is run from the default workspace"
    )
    assert person(scope.foreign_workspace_id) is None, (
        "a dataset with no workspace started from a second workspace reads nothing"
    )
    assert person(None) is None, "a run with no started-in workspace reads no feedback"
    assert person(scope.workspace_id, organization_id=str(uuid.uuid4())) is None, (
        "another organization's run never binds this organization's default workspace"
    )
    assert person(scope.workspace_id, target=scope.foreign_workspace_id) is None, (
        "a dataset of a second workspace is never bound to the default workspace"
    )
    assert (
        feedback_workspace_for_run(
            org,
            None,
            started_by=STARTED_BY_SCHEDULE,
            workspace_id=scope.foreign_workspace_id,
        )
        is None
    ), "a stored second workspace never binds a dataset with no workspace"


def test_second_workspace_metric_on_no_workspace_dataset_reads_nothing(consumer_scope):
    scope = consumer_scope
    _seed(scope)
    result = _scheduled_metric_run(
        scope,
        metric_workspace_id=scope.foreign_workspace_id,
        dataset_workspace_id=None,
    )
    assert result == [], (
        "a dataset with no workspace is bound only to the default workspace"
    )


@pytest.mark.parametrize("stored", ["no-workspace", "default-workspace"])
def test_scheduled_run_on_no_workspace_metric_reads_org_default_workspace(
    consumer_scope, stored
):
    scope = consumer_scope
    _seed(scope)
    result = _scheduled_metric_run(
        scope,
        metric_workspace_id=None if stored == "no-workspace" else scope.workspace_id,
        dataset_workspace_id=None,
    )
    assert _read_markers(result) == {"default-workspace", "older-unscoped"}, (
        "a scheduled run on a dataset and metric with no workspace reads the "
        "organization's default workspace and older same-organization feedback "
        "with no workspace, and nothing else"
    )


@pytest.mark.parametrize("stored", ["no-workspace", "second-workspace"])
def test_scheduled_run_ignores_the_workspace_its_request_like_input_names(
    consumer_scope, stored
):
    scope = consumer_scope
    _seed(scope)
    if stored == "no-workspace":
        named = scope.foreign_workspace
        expected = {"default-workspace", "older-unscoped"}
        metric_workspace_id = dataset_workspace_id = None
    else:
        named = scope.workspace
        expected = {"second-workspace", "older-unscoped"}
        metric_workspace_id = dataset_workspace_id = scope.foreign_workspace_id
    set_workspace_context(workspace=named, organization=scope.organization)
    try:
        result = _scheduled_metric_run(
            scope,
            metric_workspace_id=metric_workspace_id,
            dataset_workspace_id=dataset_workspace_id,
            source_configs={"workspace_id": str(named.id), "source": "dataset"},
        )
    finally:
        clear_workspace_context()
    assert _read_markers(result) == expected, (
        "a scheduled run takes its workspace from the stored metric, or the "
        "organization's default workspace, never from the workspace its input names"
    )


def test_every_run_read_goes_through_the_one_scope_clause(consumer_scope, monkeypatch):
    scope = consumer_scope
    _seed(scope)
    real_scope_sql = em._feedback_scope_sql
    calls = []

    def recording_scope_sql(organization_id, workspace_id):
        calls.append((str(organization_id), str(workspace_id)))
        return real_scope_sql(organization_id, workspace_id)

    monkeypatch.setattr(em, "_feedback_scope_sql", recording_scope_sql)
    result = _scheduled_metric_run(
        scope, metric_workspace_id=None, dataset_workspace_id=None
    )
    assert _read_markers(result) == {"default-workspace", "older-unscoped"}, (
        "SETUP: the scheduled run reads feedback"
    )
    assert len(calls) >= 2 and set(calls) == {
        (scope.organization_id, scope.workspace_id)
    }, (
        "the count and the similarity read both filter through the one scope "
        "clause, bound to the run's organization and workspace"
    )
