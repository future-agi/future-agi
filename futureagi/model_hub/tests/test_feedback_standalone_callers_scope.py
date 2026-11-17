"""Standalone consumer regressions with real ORM, feedback readers and vector SQL.

Only scheduling, inference, billing and unrelated ground-truth work are stubbed.
The transactional helper is re-exported locally, as required by the CH fixtures.
"""

import uuid
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from model_hub.tests.test_feedback_rag_consumer_scope import (
    _django_db_helper as _django_db_helper,
)
from model_hub.tests.test_feedback_rag_consumer_scope import (
    _orchestrator,
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
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


class _InlineExecutor:
    """Run the actual preview worker, without a second test DB connection."""

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def submit(self, fn, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except Exception as exc:
            future.set_exception(exc)
        return future


@pytest.fixture
def callers(consumer_scope, standalone_recorder, monkeypatch, user):
    from ee.usage.services import emitter
    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
    from model_hub.models.run_prompt import (
        PromptEvalConfig,
        PromptTemplate,
        PromptVersion,
    )
    from model_hub.views import develop_dataset, prompt_template
    from model_hub.views.eval_runner import EvaluationRunner

    scope = consumer_scope
    clear_workspace_context()
    dataset = Dataset.no_workspace_objects.create(
        name="feedback-preview",
        organization=scope.organization,
        workspace=scope.workspace,
    )
    column = Column.no_workspace_objects.create(
        name="answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=dataset, column=column, row=row, value="current case"
    )
    prompt = PromptTemplate.no_workspace_objects.create(
        name="feedback-prompt",
        organization=scope.organization,
        workspace=scope.workspace,
    )
    PromptVersion.no_workspace_objects.create(
        original_template=prompt,
        template_version="v1",
        is_draft=True,
    )
    evaluation = PromptEvalConfig.no_workspace_objects.create(
        name="feedback-prompt-eval",
        prompt_template=prompt,
        eval_template=scope.template,
        mapping={"output": "output_prompt"},
        config={},
    )
    monkeypatch.setattr(develop_dataset, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(prompt_template, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(emitter, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        EvaluationRunner,
        "_handle_api_call",
        lambda *args, **kwargs: SimpleNamespace(config="{}", save=Mock()),
    )
    return SimpleNamespace(
        scope=scope,
        recorder=standalone_recorder,
        user=user,
        dataset=dataset,
        column=column,
        row=row,
        prompt=prompt,
        evaluation=evaluation,
    )


def _preview_request(world, payload):
    from rest_framework.test import APIRequestFactory, force_authenticate

    clear_workspace_context()
    request = APIRequestFactory().post("/", payload, format="json")
    request.organization = world.scope.organization
    request.workspace = world.scope.workspace
    force_authenticate(request, user=world.user)
    return request


def _invoke(consumer, world, template, *, dataset_ids=None, dataset_info=None):
    from model_hub.views import develop_dataset, prompt_template

    before = len(world.recorder.calls)
    clear_workspace_context()
    if consumer == "prompt":
        world.evaluation.eval_template = template
        prompt_template.PromptTemplateViewSet().run_evaluation(
            world.evaluation,
            "current case",
            [{"role": "user", "content": "question"}],
            {},
            world.scope.organization_id,
            template=world.prompt,
        )
    else:
        payload = {
            "template_id": str(template.id),
            # The preview refuses Turing models before any row runs when the
            # deployment has no Turing entitlement; any other model reaches the
            # evaluator in every deployment.
            "model": "gpt-4" if consumer == "preview" else "turing_large",
            "config": {
                "mapping": {
                    "output": str(world.column.id)
                    if consumer == "preview"
                    else "answer"
                },
                "config": {},
            },
        }
        if consumer == "preview":
            response = develop_dataset.PreviewRunEvalView.as_view()(
                _preview_request(world, payload), dataset_id=world.dataset.id
            )
        else:
            payload.update(
                dataset_ids=dataset_ids or [str(world.dataset.id)],
                dataset_info=dataset_info
                or {"base": {str(world.dataset.id): str(world.row.id)}},
            )
            response = develop_dataset.ComparePreviewRunEvalView.as_view()(
                _preview_request(world, payload)
            )
        assert response.status_code == 200, "SETUP: preview request admitted"
    calls = world.recorder.calls[before:]
    assert calls, "SETUP: real consumer reached evaluator"
    return [params["few_shots"] for params in calls]


def _other_template(scope):
    from model_hub.models.evals_metric import EvalTemplate

    return EvalTemplate.no_workspace_objects.create(
        name=f"feedback-other-{uuid.uuid4().hex}",
        owner=scope.template.owner,
        organization=None,
        workspace=None,
        config=dict(scope.template.config),
    )


@pytest.mark.parametrize("consumer", ["prompt", "preview", "compare"])
def test_standalone_consumers_retrieve_only_execution_workspace(callers, consumer):
    scope = callers.scope
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)
    scope.insert(
        "other-org",
        workspace_id=scope.workspace_id,
        organization_id=scope.foreign_organization_id,
    )
    scope.insert(
        "other-org-null",
        workspace_id=None,
        organization_id=scope.foreign_organization_id,
    )
    local = _invoke(consumer, callers, scope.template)
    # Same-organization feedback saved with no workspace before the release
    # cutoff is admitted.
    assert (
        "local-comment" in str(local)
        and "legacy-null-comment" in str(local)
        and all(
            marker not in str(local)
            for marker in ("foreign-", "other-org-", "after-cutoff-null-")
        )
    ), (
        "standalone consumers retain executing-workspace feedback and "
        "same-org pre-cutoff no-workspace feedback only"
    )

    foreign_template = _other_template(scope)
    scope.insert(
        "foreign-only",
        workspace_id=scope.foreign_workspace_id,
        eval_id=str(foreign_template.id),
    )
    absent_template = _other_template(scope)
    foreign = _invoke(consumer, callers, foreign_template)
    absent = _invoke(consumer, callers, absent_template)
    assert foreign == absent == [[]], (
        "SETUP: foreign and nonexistent feedback share canonical empty shape"
    )
    if consumer == "prompt":
        callers.prompt.workspace = None
        callers.prompt.save(update_fields=["workspace"])
        assert _invoke(consumer, callers, scope.template) == [[]], (
            "a prompt with no workspace still runs with no feedback"
        )
    else:
        # A dataset with no workspace is seen only from the default workspace,
        # which started this run: that workspace's feedback and older
        # same-organization feedback with no workspace steer it.
        callers.dataset.workspace = None
        callers.dataset.save(update_fields=["workspace"])
        unscoped = str(_invoke(consumer, callers, scope.template))
        assert (
            "local-comment" in unscoped
            and "legacy-null-comment" in unscoped
            and all(
                marker not in unscoped
                for marker in ("foreign-", "other-org-", "after-cutoff-null-")
            )
        ), (
            "a dataset with no workspace previewed from the default workspace reads "
            "that workspace's feedback and older no-workspace feedback only"
        )


def test_compare_preview_does_not_share_first_dataset_binding(callers):
    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    scope = callers.scope
    clear_workspace_context()
    legacy = Dataset.no_workspace_objects.create(
        name="legacy-preview", organization=scope.organization, workspace=None
    )
    column = Column.no_workspace_objects.create(
        name="answer",
        dataset=legacy,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.no_workspace_objects.create(dataset=legacy, order=0)
    Cell.no_workspace_objects.create(
        dataset=legacy, column=column, row=row, value="legacy case"
    )
    scope.insert("local", workspace_id=scope.workspace_id)
    scope.insert("legacy-null", workspace_id=None)
    scope.insert("foreign", workspace_id=scope.foreign_workspace_id)
    scope.insert("after-cutoff-null", workspace_id=None, written_after_cutoff=True)
    assert scope.workspace.is_default, "SETUP: the run starts in the default workspace"
    before = len(callers.recorder.calls)
    _invoke(
        "compare",
        callers,
        scope.template,
        dataset_ids=[str(callers.dataset.id), str(legacy.id)],
        dataset_info={
            "base": {
                str(callers.dataset.id): str(callers.row.id),
                str(legacy.id): str(row.id),
            }
        },
    )
    by_output = {
        params["output"]: params["few_shots"]
        for params in callers.recorder.calls[before:]
    }
    assert set(by_output) == {"current case", "legacy case"}, (
        "SETUP: both admitted datasets evaluated"
    )
    current = str(by_output["current case"])
    legacy = str(by_output["legacy case"])
    # Each compared dataset binds its own feedback; the dataset with no
    # workspace binds the default workspace that started the run.
    assert (
        "local-comment" in current
        and "local-comment" in legacy
        and "legacy-null-comment" in legacy
        and all(
            marker not in current + legacy
            for marker in ("foreign-", "after-cutoff-null-")
        )
    ), (
        "each compared dataset reads only its started-in workspace's feedback and "
        "older no-workspace feedback; never a second workspace's"
    )


@pytest.mark.parametrize("boundary", ["discovery", "tool"])
@pytest.mark.parametrize("missing_org", [None, ""])
def test_missing_organization_logs_one_value_free_refusal(
    consumer_scope, monkeypatch, boundary, missing_org
):
    from ee.agenthub.eval_orchestrator import utils

    scope = consumer_scope
    scope.insert("local", workspace_id=scope.workspace_id)
    if boundary == "discovery":
        local = utils.gather_available_resources(
            scope.organization_id, str(scope.template.id), workspace=scope.workspace
        )
        assert local["feedback_count"] == 1, "SETUP: bound discovery positive control"
    else:
        local = _orchestrator(scope, workspace=scope.workspace)._tool_get_feedback(
            str(scope.template.id)
        )
        assert "local-comment" in str(local), (
            "SETUP: bound feedback tool positive control"
        )
    foreign_template = _other_template(scope)
    scope.insert(
        "foreign-only",
        workspace_id=scope.foreign_workspace_id,
        eval_id=str(foreign_template.id),
    )
    warning = Mock()
    monkeypatch.setattr(utils.logger, "warning", warning)
    results = []
    for template_id in (scope.template.id, foreign_template.id, uuid.uuid4()):
        warning.reset_mock()
        if boundary == "discovery":
            result = utils.gather_available_resources(
                missing_org, str(template_id), workspace=scope.workspace
            )
            assert result == {
                "knowledge_bases": [],
                "feedback_count": 0,
                "mcp_tools": [],
            }, "SETUP: missing-org discovery retains empty contract"
        else:
            orchestrator = _orchestrator(
                scope, workspace=scope.workspace, template_id=template_id
            )
            orchestrator.eval_config.organization_id = missing_org
            result = orchestrator._tool_get_feedback(
                str(template_id), context_hint="synthetic-input-not-for-logs"
            )
            assert result == {"fewshots": "", "count": 0}, (
                "SETUP: missing-org tool retains empty contract"
            )
        results.append(result)
        assert warning.call_args_list == [
            call("feedback_retrieval_missing_organization")
        ], "missing-organization boundary must emit one fixed value-free event"
    assert results[0] == results[1] == results[2], (
        "SETUP: missing-org foreign and nonexistent records refuse identically"
    )


def test_existing_feedback_tool_positive_fixture_has_admitted_binding():
    from ee.agenthub.eval_orchestrator.test_orchestrator import (
        TestOrchestratorBuildToolList,
    )

    case = TestOrchestratorBuildToolList(
        "test_feedback_tool_added_when_feedback_count_positive"
    )
    failed = False
    try:
        case.test_feedback_tool_added_when_feedback_count_positive()
    except AssertionError:
        failed = True
    assert not failed, (
        "existing feedback-tool positive fixture must supply admitted scope"
    )
