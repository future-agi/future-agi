"""Standalone cardinality regressions and unchanged CI/CD batch controls.

Run real serializers, dispatchers, row persistence and (for standalone async)
the real synchronous activity body. Only the engine, billing I/O, analytics,
ground-truth enrichment and workflow transport are replaced. No batch runner or
single-evaluation runner is mocked. CI/CD checks do not execute queued work.
"""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices
from sdk.utils import evaluations as sdk_evaluations
from tfc.constants.api_calls import APICallStatusChoices
from tracer.models.eval_ci_cd import EvaluationResult, EvaluationRun
from tracer.models.project import Project

pytestmark = pytest.mark.django_db


@pytest.fixture
def shape_template(user):
    return EvalTemplate.objects.create(
        name=f"standalone-shape-{uuid4().hex}",
        organization=None,
        workspace=None,
        owner="system",
        config={
            "eval_type_id": "TestEval",
            "required_keys": ["query", "context"],
            "output": "score",
        },
    )


def _ordered(inputs, reverse):
    items = list(inputs.items())
    return dict(reversed(items) if reverse else items)


@pytest.fixture
def standalone(auth_client, shape_template, monkeypatch):
    from evaluations import engine
    from model_hub.services.ground_truth_service import GroundTruthService
    from sdk.views import evaluations as views
    from tfc.temporal import evaluations as temporal_evaluations
    from tfc.temporal.evaluations import activities

    # The root conftest supplies these same interfaces in OSS runs.
    from ee.usage.services import config, emitter, metering

    ledger_rows = []
    engine_inputs = []

    def ledger_write(**kwargs):
        row = SimpleNamespace(
            status=APICallStatusChoices.PROCESSING.value,
            config="{}",
            log_id=uuid4(),
            save=Mock(),
        )
        ledger_rows.append(row)
        return row

    def engine_run(request):
        engine_inputs.append(deepcopy(request.inputs))
        return SimpleNamespace(
            data=deepcopy(request.inputs),
            failure=None,
            reason="shape test",
            runtime=0.01,
            model_used="turing_large",
            metrics={},
            metadata={},
            output_type="score",
            value="0.75",
            cost={"total_cost": 0.03},
            token_usage={},
            evaluator=None,
        )

    ledger = Mock(side_effect=ledger_write)
    engine_call = Mock(side_effect=engine_run)
    usage = Mock()
    start = Mock()
    billing = SimpleNamespace(
        get_eval_per_run_fee=lambda: 0.02,
        calculate_ai_credits=lambda cost: 5.0,
    )
    monkeypatch.setattr(
        sdk_evaluations, "log_and_deduct_cost_for_api_request", ledger
    )
    monkeypatch.setattr(engine, "run_eval", engine_call)
    monkeypatch.setattr(config.BillingConfig, "get", Mock(return_value=billing))
    monkeypatch.setattr(emitter, "emit", usage)
    monkeypatch.setattr(
        metering, "check_usage", Mock(return_value=SimpleNamespace(allowed=True))
    )
    monkeypatch.setattr(GroundTruthService, "inject_context", Mock())
    monkeypatch.setattr(views, "get_mixpanel_properties", lambda **kwargs: kwargs)
    monkeypatch.setattr(views, "track_mixpanel_event", Mock())
    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", start)
    # Keep pytest's transaction connection open when draining the activity body.
    monkeypatch.setattr(activities, "close_old_connections", lambda: None)

    def post(inputs, is_async):
        shape_template.config["required_keys"] = list(inputs)
        shape_template.save(update_fields=["config"])
        return auth_client.post(
            "/sdk/api/v1/new-eval/",
            {
                "eval_name": shape_template.name,
                "inputs": inputs,
                "model": "turing_large",
                "is_async": is_async,
            },
            format="json",
        )

    def assert_completed(response, expected, is_async):
        assert response.status_code == 200, response.data
        results = response.data["result"][0]["evaluations"]
        ids = [str(item["eval_id"]) for item in results]
        assert len(ids) == len(set(ids)) == len(expected)
        rows = {
            str(row.id): row
            for row in Evaluation.no_workspace_objects.filter(
                eval_template=shape_template
            )
        }
        assert set(rows) == set(ids)
        assert [rows[eval_id].input_data for eval_id in ids] == expected

        if is_async:
            assert start.call_count == len(expected)
            assert [call.kwargs["evaluation_id"] for call in start.call_args_list] == ids
            # Async admission does not bill; the queued execution does.
            assert engine_call.call_count == ledger.call_count == usage.call_count == 0
            for eval_id in ids:
                result = activities._run_single_evaluation_sync(eval_id)
                assert result["status"] == StatusChoices.COMPLETED, result
        else:
            start.assert_not_called()

        assert engine_inputs == expected
        assert (
            engine_call.call_count
            == ledger.call_count
            == usage.call_count
            == len(expected)
        )
        assert [
            call.kwargs["config"]["mappings"] for call in ledger.call_args_list
        ] == expected
        assert all(
            row.status == APICallStatusChoices.SUCCESS.value for row in ledger_rows
        )
        events = [call.args[0] for call in usage.call_args_list]
        assert [event.properties["log_id"] for event in events] == [
            str(row.log_id) for row in ledger_rows
        ]
        assert all(event.amount == 5.0 for event in events)
        assert all(event.properties["source"] == "standalone_v2" for event in events)
        for row in rows.values():
            row.refresh_from_db()
            assert row.status == StatusChoices.COMPLETED

    def assert_rejected(response, message):
        assert response.status_code == 400, response.data
        assert response.data == {
            "status": False,
            "type": "validation_error",
            "code": "invalid",
            "detail": "Validation failed",
            "message": "Validation failed",
            "error": "Validation failed",
            "result": "Validation failed",
            "attr": "inputs",
            "details": {"inputs": [message]},
            "errors": {"inputs": [message]},
        }
        assert not Evaluation.no_workspace_objects.filter(
            eval_template=shape_template
        ).exists()
        engine_call.assert_not_called()
        ledger.assert_not_called()
        usage.assert_not_called()
        start.assert_not_called()

    return SimpleNamespace(
        post=post, assert_completed=assert_completed, assert_rejected=assert_rejected
    )


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("reverse", [False, True], ids=["context-first", "query-first"])
@pytest.mark.parametrize("query", ["Where is the answer?", "Q"], ids=["long", "short"])
def test_scalar_query_list_context_is_one_metered_evaluation(
    standalone, is_async, reverse, query
):
    inputs = _ordered(
        {"context": ["document one", "document two"], "query": query}, reverse
    )
    response = standalone.post(inputs, is_async)
    standalone.assert_completed(response, [inputs], is_async)


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "inputs, expected",
    [
        (
            {"context": "document", "query": "question"},
            [{"context": "document", "query": "question"}],
        ),
        (
            {"query": ["q1", "q2"]},
            [{"query": "q1"}, {"query": "q2"}],
        ),
        (
            {"query": ["q1", "q2"], "context": ["a", "b"]},
            [{"query": "q1", "context": "a"}, {"query": "q2", "context": "b"}],
        ),
        (
            {"context": [["a", "b", "c", "d"], ["e"]], "query": ["q1", "q2"]},
            [
                {"context": ["a", "b", "c", "d"], "query": "q1"},
                {"context": ["e"], "query": "q2"},
            ],
        ),
        # Preserve the base's cap scope: it applies only with at least two
        # flat string columns, not to one column or exclusively nested columns.
        (
            {"query": ["q1", "q2", "q3", "q4"]},
            [{"query": "q1"}, {"query": "q2"}, {"query": "q3"}, {"query": "q4"}],
        ),
        (
            {"query": [["q1"], ["q2"], ["q3"], ["q4"]]},
            [
                {"query": ["q1"]},
                {"query": ["q2"]},
                {"query": ["q3"]},
                {"query": ["q4"]},
            ],
        ),
        ({"query": [], "context": []}, []),
    ],
    ids=[
        "scalars", "one-column", "flat", "nested", "single-four", "nested-four",
        "empty-columns",
    ],
)
def test_existing_standalone_shapes_keep_row_cardinality(
    standalone, is_async, reverse, inputs, expected
):
    response = standalone.post(_ordered(inputs, reverse), is_async)
    standalone.assert_completed(response, expected, is_async)


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "inputs",
    [
        {"context": [["a"], ["b"]], "query": ["q1"]},
        {"context": [["a"]], "query": ["q1", "q2"]},
        {"context": [["a"], ["b"]], "query": [["q1"]]},
        {"context": [], "query": [["q1"]]},
    ],
    ids=["nested-long", "nested-short", "both-nested", "empty-vs-nested"],
)
def test_unequal_nested_columns_rejected_before_execution(
    standalone, is_async, reverse, inputs
):
    response = standalone.post(_ordered(inputs, reverse), is_async)
    standalone.assert_rejected(response, "All input lists must have the same length.")


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "inputs, message",
    [
        (
            {"context": ["a", "b"], "query": ["q1"]},
            "All input lists must have the same length.",
        ),
        (
            {"context": [["a"], "b"], "query": ["q1", "q2"]},
            "All input values must be either strings, lists of strings, or lists of lists of strings.",
        ),
        (
            {"context": [{"text": "a"}], "query": ["q1"]},
            "All input values must be either strings, lists of strings, or lists of lists of strings.",
        ),
        (
            {"context": [42], "query": ["q1"]},
            "All input values must be either strings, lists of strings, or lists of lists of strings.",
        ),
        (
            {"query": ["q1", "q2", "q3", "q4"], "context": ["a", "b", "c", "d"]},
            "Input lists cannot have more than 3 items, but found length 4.",
        ),
    ],
    ids=["flat-unequal", "mixed-cells", "object-cells", "numeric-cells", "flat-cap"],
)
def test_standalone_base_value_and_cap_policy_is_unchanged(
    standalone, is_async, reverse, inputs, message
):
    response = standalone.post(_ordered(inputs, reverse), is_async)
    standalone.assert_rejected(response, message)


@pytest.fixture
def pipeline(auth_client, shape_template, organization, workspace, monkeypatch):
    from tfc.temporal import evaluations as temporal_evaluations

    project = Project.objects.create(
        name=f"shape-pipeline-{uuid4().hex}",
        organization=organization,
        workspace=workspace,
        trace_type="observe",
        model_type="GenerativeLLM",
    )
    start = Mock()
    monkeypatch.setattr(temporal_evaluations, "start_evaluation_workflow", start)

    def post_and_assert(items, expected):
        response = auth_client.post(
            "/sdk/api/v1/evaluate-pipeline/",
            {
                "project_name": project.name,
                "version": "shape-control",
                "eval_data": [
                    {"eval_template": shape_template.name, "inputs": inputs}
                    for inputs in items
                ],
            },
            format="json",
        )
        assert response.status_code == 200, response.data
        run = EvaluationRun.no_workspace_objects.get(
            id=response.data["result"]["evaluation_run_id"]
        )
        assert run.project_id == project.id
        assert run.organization_id == organization.id
        rows = {
            str(row.id): row
            for row in Evaluation.no_workspace_objects.filter(
                eval_template=shape_template
            )
        }
        started = [call.kwargs["evaluation_id"] for call in start.call_args_list]
        assert len(rows) == len(started) == len(set(started)) == len(expected)
        assert set(rows) == set(started)
        assert [rows[eval_id].input_data for eval_id in started] == expected
        assert all(row.status == StatusChoices.PENDING for row in rows.values())
        links = list(EvaluationResult.no_workspace_objects.filter(evaluation_run=run))
        assert len(links) == len(expected)
        assert {str(link.evaluation_id) for link in links} == set(rows)
        assert all(link.organization_id == organization.id for link in links)

    return post_and_assert


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("row_count", [4, 7])
def test_pipeline_large_batch_preserves_rows_links_and_workflow_ids(
    pipeline, reverse, row_count
):
    rows = [{"query": f"q{i}", "context": f"c{i}"} for i in range(row_count)]
    columns = {key: [row[key] for row in rows] for key in ("query", "context")}
    pipeline([_ordered(columns, reverse)], rows)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    "rows",
    [
        [
            {"query": {"id": 1}, "context": {"text": "a"}},
            {"query": {"id": 2}, "context": {"text": "b"}},
        ],
        [{"query": 0, "context": 0.5}, {"query": 42, "context": -2}],
    ],
    ids=["json-object-values", "numeric-values"],
)
def test_pipeline_typed_batch_preserves_rows_links_and_workflow_ids(
    pipeline, reverse, rows
):
    columns = {key: [row[key] for row in rows] for key in ("query", "context")}
    pipeline([_ordered(columns, reverse)], rows)


@pytest.mark.parametrize("reverse_keys", [False, True])
@pytest.mark.parametrize("empty_first", [False, True], ids=["empty-last", "empty-first"])
def test_pipeline_empty_item_does_not_roll_back_its_sibling(
    pipeline, reverse_keys, empty_first
):
    nonempty = _ordered({"query": ["q1", "q2"], "context": ["a", "b"]}, reverse_keys)
    empty = _ordered({"query": [], "context": []}, reverse_keys)
    items = [empty, nonempty] if empty_first else [nonempty, empty]
    pipeline(items, [{"query": "q1", "context": "a"}, {"query": "q2", "context": "b"}])
