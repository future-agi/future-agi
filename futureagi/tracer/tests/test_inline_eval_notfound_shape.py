"""Paired inline-target admission and worker refusal tests.

The SDK accepts span_id/trace_eval at the top level and persists them as
Evaluation.trace_data. Sync and async requests use real serializers, models and
managers; sync requests run the deterministic RecallAtK evaluator, not an LLM.
Only the Temporal transport is replaced. HTTP comparisons mask only fresh,
caller-scoped evaluation IDs and numeric runtime at their exact response paths.
All other values, including UUID-shaped semantic output, remain unchanged.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from structlog.testing import capture_logs

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.custom_eval_config import (
    CustomEvalConfig,
    InlineEval,
    InLineEvalStatus,
)
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.utils.inline_evals import process_in_line_evals, trigger_inline_eval

pytestmark = pytest.mark.django_db
REASON = "inline_eval_target_not_in_scope"
SDK_URL = "/sdk/api/v1/new-eval/"


def _span(project):
    trace = Trace.no_workspace_objects.create(project=project, name="NF inline trace")
    return ObservationSpan.no_workspace_objects.create(
        id=uuid.uuid4().hex[:16],
        project=project,
        trace=trace,
        name="NF inline span",
        observation_type="llm",
        status="OK",
    )


@pytest.fixture
def inline_world(organization, workspace, user, project):
    clear_workspace_context()
    foreign_org = Organization.objects.create(name="NF inline foreign organization")
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="NF inline foreign workspace",
        organization=foreign_org,
        created_by=user,
        is_active=True,
    )
    sibling_workspace = Workspace.no_workspace_objects.create(
        name="NF inline sibling workspace",
        organization=organization,
        created_by=user,
        is_active=True,
    )
    spans = {"local": _span(project)}
    for name, org, ws in (
        ("organization", foreign_org, foreign_workspace),
        ("workspace", organization, sibling_workspace),
    ):
        foreign_project = Project.no_workspace_objects.create(
            name=f"NF inline {name} project",
            organization=org,
            workspace=ws,
            model_type=project.model_type,
            trace_type=project.trace_type,
        )
        spans[name] = _span(foreign_project)
    template = EvalTemplate.no_workspace_objects.create(
        name=f"nf_inline_recall_{uuid.uuid4().hex}",
        description="NF SDK request validation control",
        eval_id=999036,
        owner="system",
        organization=None,
        config={
            "eval_type_id": "RecallAtK",
            "required_keys": ["hypothesis", "reference"],
            "output": "score",
            "function_eval": True,
            "function_params_schema": {
                "k": {
                    "type": "integer",
                    "default": None,
                    "nullable": True,
                    "minimum": 1,
                }
            },
        },
    )
    assert spans["organization"].project.organization_id != organization.pk, (
        "SETUP_AQ-XWS-NF: foreign span must cross the organization boundary"
    )
    assert spans["workspace"].project.organization_id == organization.pk, (
        "SETUP_AQ-XWS-NF: sibling span must share the caller organization"
    )
    assert spans["workspace"].project.workspace_id != workspace.pk, (
        "SETUP_AQ-XWS-NF: sibling span must cross the workspace boundary"
    )
    assert not InlineEval.no_workspace_objects.exists(), (
        "SETUP_AQ-XWS-NF: no pre-existing inline jobs may hide admission writes"
    )
    assert not CustomEvalConfig.no_workspace_objects.exists(), (
        "SETUP_AQ-XWS-NF: no pre-existing custom configs may hide worker writes"
    )
    assert not EvalLogger.no_workspace_objects.exists(), (
        "SETUP_AQ-XWS-NF: no pre-existing loggers may hide worker writes"
    )
    yield SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=user,
        spans=spans,
        template=template,
    )
    clear_workspace_context()


def _targets(world, foreign_scope):
    foreign_id = world.spans[foreign_scope].pk
    missing_id = uuid.uuid4().hex[:16]
    assert ObservationSpan.no_workspace_objects.filter(pk=foreign_id).exists(), (
        "SETUP_AQ-XWS-NF: foreign span must actually exist"
    )
    assert not ObservationSpan.no_workspace_objects.filter(pk=missing_id).exists(), (
        "SETUP_AQ-XWS-NF: comparison span ID must never have been used"
    )
    return foreign_id, missing_id


def _evaluation(world, span_id):
    return Evaluation.no_workspace_objects.create(
        organization=world.organization,
        workspace=world.workspace,
        user=world.user,
        eval_template=world.template,
        status=StatusChoices.COMPLETED,
        error_localizer_enabled=False,
        value="Passed",
        reason="NF completed evaluation",
        trace_data={"span_id": span_id, "custom_eval_name": "NF inline result"},
    )


def _assert_no_result_writes(queries):
    for model in (CustomEvalConfig, EvalLogger):
        assert not model.no_workspace_objects.exists(), (
            "AQ-XWS-NF: unavailable spans must not create custom configs or loggers"
        )
        assert not any(
            f'{verb} "{model._meta.db_table}"' in query["sql"]
            for query in queries
            for verb in ("INSERT INTO", "UPDATE", "DELETE FROM")
        ), "AQ-XWS-NF: unavailable spans must not attempt result or config writes"


def _normalize_sdk_envelope(value, validated_eval_ids):
    """Mask only result[*].evaluations[*].{eval_id,runtime}, with validation."""
    assert isinstance(value.get("result"), list) and value["result"], (
        "SETUP_AQ-XWS-W2C: the SDK response must contain evaluation results"
    )
    normalized_results = []
    for result in value["result"]:
        assert isinstance(result.get("evaluations"), list) and result["evaluations"], (
            "SETUP_AQ-XWS-W2C: each SDK result must contain evaluations"
        )
        normalized_evaluations = []
        for evaluation in result["evaluations"]:
            assert evaluation.get("eval_id") in validated_eval_ids, (
                "AQ-XWS-W2C: masked eval_id must identify a fresh caller-scoped Evaluation"
            )
            normalized = {**evaluation, "eval_id": "<generated-evaluation-id>"}
            if "runtime" in evaluation:
                assert isinstance(
                    evaluation["runtime"], (int, float)
                ) and not isinstance(evaluation["runtime"], bool), (
                    "AQ-XWS-W2C: masked runtime must be a number, not a boolean or string"
                )
                normalized["runtime"] = "<numeric-runtime>"
            normalized_evaluations.append(normalized)
        normalized_results.append({**result, "evaluations": normalized_evaluations})
    return {**value, "result": normalized_results}


@pytest.mark.parametrize("field", ["reason", "output", "duration", "created_at"])
def test_sdk_comparator_keeps_uuid_semantic_differences(field):
    # Synthetic envelopes exercise the comparator, not the product scope guard.
    eval_id = str(uuid.uuid4())
    first_uuid, second_uuid = str(uuid.uuid4()), str(uuid.uuid4())
    first = {"result": [{"evaluations": [{"eval_id": eval_id, field: first_uuid}]}]}
    second = {"result": [{"evaluations": [{"eval_id": eval_id, field: second_uuid}]}]}
    assert _normalize_sdk_envelope(first, {eval_id}) != _normalize_sdk_envelope(
        second, {eval_id}
    ), "AQ-XWS-W2C: comparator must expose UUID-valued semantic differences"


@pytest.mark.parametrize("replacement", ["0.25", True, None])
def test_sdk_comparator_rejects_runtime_type_changes(replacement):
    eval_id = str(uuid.uuid4())
    numeric = {"result": [{"evaluations": [{"eval_id": eval_id, "runtime": 0.25}]}]}
    changed = {
        "result": [{"evaluations": [{"eval_id": eval_id, "runtime": replacement}]}]
    }
    # The valid numeric side must be accepted before testing the invalid side.
    _normalize_sdk_envelope(numeric, {eval_id})
    try:
        _normalize_sdk_envelope(changed, {eval_id})
    except AssertionError as exc:
        # pytest rewrites asserts in test modules and appends its explanation after
        # the first line; compare the guard's own message only.
        assert str(exc).splitlines()[0] == (
            "AQ-XWS-W2C: masked runtime must be a number, not a boolean or string"
        ), "AQ-XWS-W2C: runtime type rejection must come from its numeric guard"
    else:
        pytest.fail("AQ-XWS-W2C: comparator must reject a runtime type change")


def test_sdk_comparator_masks_only_identified_volatile_paths():
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    semantic_uuid = str(uuid.uuid4())
    envelopes = [
        {
            "result": [
                {
                    "evaluations": [
                        {
                            "eval_id": eval_id,
                            "runtime": runtime,
                            "reason": semantic_uuid,
                            "output": {"runtime": 7, "eval_id": semantic_uuid},
                        }
                    ]
                }
            ],
            "runtime": 11,
        }
        for eval_id, runtime in ((first_id, 0.25), (second_id, 0.5))
    ]
    first = _normalize_sdk_envelope(envelopes[0], {first_id})
    second = _normalize_sdk_envelope(envelopes[1], {second_id})
    assert first == second, (
        "AQ-XWS-W2C: fresh eval ids and numeric runtimes are the only volatile values"
    )
    assert first["result"][0]["evaluations"][0]["reason"] == semantic_uuid, (
        "AQ-XWS-W2C: comparator must preserve UUID-valued reasons verbatim"
    )
    assert first["result"][0]["evaluations"][0]["output"] == {
        "runtime": 7,
        "eval_id": semantic_uuid,
    }, "AQ-XWS-W2C: comparator must not mask nested semantic runtime or eval_id fields"
    assert first["runtime"] == 11, (
        "AQ-XWS-W2C: comparator must not mask runtime outside the evaluation path"
    )


@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
@pytest.mark.parametrize("batched", [False, True], ids=["single", "batch"])
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_sdk_inline_targets_have_identical_refusal_envelopes(
    auth_client, inline_world, foreign_scope, batched, is_async
):
    world = inline_world
    targets = _targets(world, foreign_scope)
    inputs = {"hypothesis": '["A", "B"]', "reference": '["A", "C"]'}
    if batched:
        inputs = {key: [value] for key, value in inputs.items()}
    payload = {
        "eval_name": world.template.name,
        "inputs": inputs,
        "config": {"params": {"k": 1}},
        "is_async": is_async,
        "trace_eval": True,
        "custom_eval_name": "NF inline result",
        "error_localizer": False,
    }
    with patch("tfc.temporal.evaluations.start_evaluation_workflow") as dispatch:
        control = auth_client.post(
            SDK_URL, {**payload, "span_id": world.spans["local"].pk}, format="json"
        )
        assert control.status_code == 200 and dispatch.call_count == int(is_async), (
            "SETUP_AQ-XWS-NF: valid SDK request must complete or dispatch as requested"
        )
        control_id = control.json()["result"][0]["evaluations"][0]["eval_id"]
        evaluation = Evaluation.no_workspace_objects.get(pk=control_id)
        assert evaluation.trace_data == {
            "span_id": world.spans["local"].pk,
            "custom_eval_name": payload["custom_eval_name"],
        }, "SETUP_AQ-XWS-NF: SDK must persist the submitted target in trace_data"
        if is_async:
            assert evaluation.workspace_id == world.workspace.pk, (
                "SETUP_AQ-XWS-NF: async SDK evaluation must retain caller workspace"
            )
        else:
            assert evaluation.status == StatusChoices.COMPLETED, (
                "SETUP_AQ-XWS-NF: synchronous local evaluator must actually succeed"
            )
            assert InlineEval.no_workspace_objects.filter(
                evaluation=evaluation
            ).exists(), (
                "SETUP_AQ-XWS-NF: synchronous local request must reach inline admission"
            )
        before_inline = list(InlineEval.no_workspace_objects.order_by("id").values())
        dispatch.reset_mock()
        responses = []
        fresh_eval_ids_by_response = []
        for span_id in targets:
            before_evaluation_ids = set(
                Evaluation.no_workspace_objects.values_list("pk", flat=True)
            )
            responses.append(
                auth_client.post(
                    SDK_URL, {**payload, "span_id": span_id}, format="json"
                )
            )
            fresh_eval_ids_by_response.append(
                {
                    str(pk)
                    for pk in Evaluation.no_workspace_objects.filter(
                        organization_id=world.organization.pk,
                        workspace_id=world.workspace.pk,
                    )
                    .exclude(pk__in=before_evaluation_ids)
                    .values_list("pk", flat=True)
                }
            )
    # The SDK contract is unchanged: an unavailable inline target is
    # refused silently at admission, so the evaluation response itself must not
    # differ between a foreign and an absent span (no existence oracle).
    observed = [(response.status_code, response.json()) for response in responses]
    assert [status for status, _ in observed] == [control.status_code] * 2, (
        "AQ-XWS-NF: foreign and absent inline SDK targets must keep the unchanged SDK status"
    )
    normalized = [
        _normalize_sdk_envelope(body, fresh_eval_ids)
        for (_, body), fresh_eval_ids in zip(
            observed, fresh_eval_ids_by_response, strict=True
        )
    ]
    assert normalized[0] == normalized[1], (
        "AQ-XWS-NF: foreign and absent inline SDK responses must be identical apart from generated ids"
    )
    assert (
        list(InlineEval.no_workspace_objects.order_by("id").values()) == before_inline
    ), (
        "AQ-XWS-NF: foreign and absent SDK targets must not create or alter any InlineEval"
    )
    assert dispatch.call_count == 2 * int(is_async), (
        "AQ-XWS-NF: foreign and absent SDK targets must dispatch exactly like a valid request"
    )


@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_inline_admission_foreign_and_absent_have_identical_reason(
    inline_world, foreign_scope
):
    targets = _targets(inline_world, foreign_scope)
    observed = []
    for span_id in targets:
        evaluation = _evaluation(inline_world, span_id)
        clear_workspace_context()
        with capture_logs() as logs:
            trigger_inline_eval(evaluation)
        reasons = [
            (entry.get("event"), entry.get("reason"))
            for entry in logs
            if entry.get("evaluation_id") == str(evaluation.pk)
        ]
        observed.append(reasons)
        assert not InlineEval.no_workspace_objects.filter(
            evaluation=evaluation
        ).exists(), (
            "AQ-XWS-NF: admission must refuse both foreign and absent span targets"
        )
    assert (
        observed[0]
        == observed[1]
        == [("Skipping inline eval creation with unavailable target", REASON)]
    ), "AQ-XWS-NF: admission must log the same fixed event and reason for both targets"


@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_inline_worker_foreign_and_absent_have_identical_terminal_refusal(
    inline_world, foreign_scope
):
    jobs = []
    for span_id in _targets(inline_world, foreign_scope):
        evaluation = _evaluation(inline_world, span_id)
        job = InlineEval.no_workspace_objects.create(
            organization=inline_world.organization,
            workspace=inline_world.workspace,
            evaluation=evaluation,
            span_id=span_id,
            custom_eval_name=evaluation.trace_data["custom_eval_name"],
        )
        assert job.status == InLineEvalStatus.PENDING, (
            "SETUP_AQ-XWS-NF: pre-inserted inline job must begin pending"
        )
        jobs.append(job)
    clear_workspace_context()
    with CaptureQueriesContext(connection) as queries, capture_logs() as logs:
        # Only bypass transport connection cleanup, as in target-scope tests.
        # The real transaction, selectors, locks, guards and writes all run.
        process_in_line_evals._original_func()
    observed = []
    for job in jobs:
        job.refresh_from_db()
        reasons = [
            (entry.get("event"), entry.get("reason"))
            for entry in logs
            if entry.get("inline_eval_id") == str(job.pk)
        ]
        observed.append((job.status, reasons))
    expected = (
        InLineEvalStatus.FAILED,
        [("Skipping inline eval with unavailable target", REASON)],
    )
    assert observed[0] == observed[1] == expected, (
        "AQ-XWS-NF: worker must terminate foreign and absent targets with the same reason"
    )
    _assert_no_result_writes(queries)


def test_inline_local_target_admission_and_worker_control(inline_world):
    evaluation = _evaluation(inline_world, inline_world.spans["local"].pk)
    clear_workspace_context()
    trigger_inline_eval(evaluation)
    assert InlineEval.no_workspace_objects.filter(evaluation=evaluation).count() == 1, (
        "AQ-XWS-NF: local control must be admitted by the real inline guard"
    )
    process_in_line_evals._original_func()
    job = InlineEval.no_workspace_objects.get(evaluation=evaluation)
    assert job.status == InLineEvalStatus.COMPLETED, (
        "AQ-XWS-NF: real worker must complete a same-scope control"
    )
    assert (
        EvalLogger.no_workspace_objects.filter(
            observation_span=inline_world.spans["local"],
            custom_eval_config__name=evaluation.trace_data["custom_eval_name"],
        ).count()
        == 1
    ), "AQ-XWS-NF: local control must produce exactly one result logger"
