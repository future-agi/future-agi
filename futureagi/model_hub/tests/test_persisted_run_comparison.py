import uuid

import pytest

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.models.experiments import ExperimentRun, ExperimentRunItem
from model_hub.services.experiment_run_writes import _digest
from model_hub.services.persisted_run_comparison import (
    RunComparisonError,
    RunCounts,
    compare_experiment_runs,
)


@pytest.fixture
def comparison_fixture(organization, workspace, user):
    dataset = Dataset.objects.create(
        name="Persisted comparison dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.objects.create(
            dataset_version=version,
            input={"x": value},
            expected={"y": value},
        )
        for value in (1, 2, 3, 4)
    ]
    identities = [
        {"example_id": str(item.example_id), "trial_number": 1} for item in examples
    ]
    identities.sort(key=lambda item: (item["example_id"], item["trial_number"]))
    plan = {"identities": identities, "plan_hash": _digest(identities)}
    metric_id, evaluator_id = uuid.uuid4(), uuid.uuid4()
    binding = {
        "metric_id": str(metric_id),
        "evaluator_version_id": str(evaluator_id),
        "template_id": str(uuid.uuid4()),
        "version_number": 1,
        "binding_config": {"threshold": 0.5},
        "binding_model": "test",
        "prompt_messages": [],
        "config_snapshot": {"temperature": 0},
        "criteria": "quality",
        "model": "test",
        "output_type_normalized": "float",
        "pass_threshold": 0.5,
        "choice_scores": {},
    }

    def make_run():
        return ExperimentRun.objects.create(
            dataset_version=version,
            task_kind="external",
            metadata={"_futureagi_run_write": {"plan": plan}},
            evaluator_versions=[binding],
        )

    return (
        dataset,
        version,
        examples,
        make_run,
        metric_id,
        evaluator_id,
        binding,
        {
            "organization_id": organization.id,
            "workspace_id": workspace.id,
        },
    )


def _ref(metric_id, evaluator_id, value, **extra):
    return {
        "metric_id": str(metric_id),
        "evaluator_version_id": str(evaluator_id),
        "status": "assessed",
        "value": value,
        **extra,
    }


def _item(run, example, status="completed", refs=None, trace=None, error=None):
    return ExperimentRunItem.objects.create(
        run=run,
        example_id=example.example_id,
        trial_number=1,
        status=status,
        score_refs=[] if refs is None else refs,
        trace_id=None if trace is None else f"trace-{trace}",
        root_span_id=None if trace is None else f"span-{trace}",
        error=error,
    )


@pytest.mark.django_db
def test_comparison_uses_exact_pins_counts_missing_both_and_trace_refs(
    comparison_fixture,
):
    _, version, examples, make_run, metric_id, evaluator_id, _, scope = (
        comparison_fixture
    )
    baseline, candidate = make_run(), make_run()
    _item(baseline, examples[0], refs=[_ref(metric_id, evaluator_id, 0.2)], trace="b0")
    _item(baseline, examples[1], refs=[_ref(metric_id, evaluator_id, 0.8)], trace="b1")
    _item(candidate, examples[0], refs=[_ref(metric_id, evaluator_id, 0.7)], trace="c0")
    _item(candidate, examples[2], status="failed", trace="c2", error={"message": "x"})

    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric_id,
        passing_score=0.5,
        **scope,
    )

    assert result.dataset_version_id == str(version.id)
    assert result.baseline_counts == RunCounts(4, 2, 2, 2, 0, 2)
    assert result.candidate_counts == RunCounts(4, 2, 2, 1, 1, 1)
    assert result.missing_by_side["baseline"] == tuple(
        sorted((str(examples[index].example_id), 1) for index in (2, 3))
    )
    assert result.missing_by_side["candidate"] == tuple(
        sorted((str(examples[index].example_id), 1) for index in (1, 3))
    )
    by_id = {
        (case.example_id, case.trial_number): case for case in result.comparison.cases
    }
    assert by_id[(str(examples[0].example_id), 1)].status == "improved"
    assert by_id[(str(examples[1].example_id), 1)].status == "missing"
    assert by_id[(str(examples[2].example_id), 1)].status == "missing"
    refs = {(ref.example_id, ref.trial_number): ref for ref in result.trace_refs}
    assert refs[(str(examples[0].example_id), 1)].baseline.trace_id == "trace-b0"
    assert refs[(str(examples[2].example_id), 1)].baseline is None


@pytest.mark.django_db
@pytest.mark.parametrize(
    "change", ["dataset", "version", "plan", "binding", "binding_config"]
)
def test_incompatible_runs_are_rejected(comparison_fixture, change):
    dataset, version, examples, make_run, metric_id, _, binding, scope = (
        comparison_fixture
    )
    baseline, candidate = make_run(), make_run()
    _item(baseline, examples[0], refs=[])
    _item(candidate, examples[0], refs=[])
    if change == "dataset":
        other = Dataset.objects.create(
            name="other",
            organization=dataset.organization,
            workspace=dataset.workspace,
            user=dataset.user,
            column_order=[],
            column_config={},
        )
        other_version = DatasetVersion.objects.create(dataset=other)
        candidate.dataset_version = other_version
        candidate.save(update_fields=["dataset_version"])
    elif change == "version":
        candidate.dataset_version = DatasetVersion.objects.create(dataset=dataset)
        candidate.save(update_fields=["dataset_version"])
    elif change == "plan":
        metadata = candidate.metadata
        metadata["_futureagi_run_write"]["plan"]["identities"] = metadata[
            "_futureagi_run_write"
        ]["plan"]["identities"][:-1]
        plan = metadata["_futureagi_run_write"]["plan"]
        plan["plan_hash"] = _digest(plan["identities"])
        candidate.metadata = metadata
        candidate.save(update_fields=["metadata"])
    else:
        changed = dict(binding)
        changed["config_snapshot" if change == "binding" else "binding_config"] = {
            "temperature": 1
        }
        candidate.evaluator_versions = [changed]
        candidate.save(update_fields=["evaluator_versions"])
    with pytest.raises(RunComparisonError):
        compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=candidate.id,
            metric_id=metric_id,
            **scope,
        )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate",
        "version",
        "metric",
        "shape",
        "status",
        "boolean",
        "missing_value",
        "no_status",
    ],
)
def test_malformed_or_duplicate_score_evidence_is_invalid(
    comparison_fixture, corruption
):
    _, _, examples, make_run, metric_id, evaluator_id, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    valid = _ref(metric_id, evaluator_id, 0.2)
    refs = [_ref(metric_id, evaluator_id, 0.8)]
    if corruption == "duplicate":
        refs.append(_ref(metric_id, evaluator_id, 0.9))
    elif corruption == "version":
        refs[0]["evaluator_version_id"] = str(uuid.uuid4())
    elif corruption == "metric":
        refs[0]["metric_id"] = str(uuid.uuid4())
    elif corruption == "shape":
        refs.append(None)
    elif corruption == "status":
        refs[0]["status"] = "improved"
    elif corruption == "boolean":
        refs[0]["value"] = True
    elif corruption == "missing_value":
        refs[0].pop("value")
    else:
        refs[0].pop("status")
    _item(baseline, examples[0], refs=[valid])
    _item(candidate, examples[0], refs=refs)
    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric_id,
        **scope,
    )
    assert result.comparison.cases[0].status == "invalid"
    assert result.candidate_counts.assessed == 0
    assert result.comparison.cases[0].score_delta is None


@pytest.mark.django_db
def test_unrelated_bad_reference_is_not_ignored(comparison_fixture):
    _, _, examples, make_run, metric_id, evaluator_id, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    _item(baseline, examples[0], refs=[_ref(metric_id, evaluator_id, 0.2)])
    bad_metric, bad_version = uuid.uuid4(), uuid.uuid4()
    _item(
        candidate,
        examples[0],
        refs=[_ref(metric_id, evaluator_id, 0.8), _ref(bad_metric, bad_version, 0.1)],
    )
    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric_id,
        **scope,
    )
    assert result.comparison.cases[0].status == "invalid"


@pytest.mark.django_db
def test_unrelated_reference_is_validated_against_its_own_binding(comparison_fixture):
    _, _, examples, make_run, metric_id, evaluator_id, binding, scope = (
        comparison_fixture
    )
    baseline, candidate = make_run(), make_run()
    other_metric, other_version = uuid.uuid4(), uuid.uuid4()
    candidate.evaluator_versions = [
        binding,
        {
            **binding,
            "metric_id": str(other_metric),
            "evaluator_version_id": str(other_version),
        },
    ]
    candidate.save(update_fields=["evaluator_versions"])
    _item(baseline, examples[0], refs=[_ref(metric_id, evaluator_id, 0.2)])
    _item(
        candidate,
        examples[0],
        refs=[
            _ref(other_metric, other_version, 0.1),
            _ref(metric_id, evaluator_id, 0.8),
        ],
    )
    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric_id,
        **scope,
    )
    assert result.comparison.cases[0].status == "improved"
    assert result.candidate_counts.assessed == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "item_status,score_status,error,expected",
    [
        ("failed", "assessed", None, "error"),
        ("running", "assessed", None, "not_assessed"),
        ("skipped", "assessed", None, "not_assessed"),
        ("completed", "unknown", None, "unknown"),
        ("completed", "error", None, "error"),
        ("completed", "not_assessed", None, "not_assessed"),
        ("completed", "unsupported", None, "unsupported"),
        ("completed", "assessed", {"message": "task error"}, "error"),
    ],
)
def test_execution_and_score_states_never_invent_comparable_results(
    comparison_fixture, item_status, score_status, error, expected
):
    _, _, examples, make_run, metric_id, evaluator_id, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    _item(baseline, examples[0], refs=[_ref(metric_id, evaluator_id, 0.2)])
    _item(
        candidate,
        examples[0],
        status=item_status,
        refs=[_ref(metric_id, evaluator_id, 0.9, status=score_status)],
        error=error,
    )
    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric_id,
        **scope,
    )
    assert result.comparison.cases[0].status == expected
    assert result.comparison.cases[0].score_delta is None
    assert result.candidate_counts.assessed == 0
    assert result.candidate_counts.failed == int(
        item_status == "failed" or error is not None
    )
    assert result.comparison.candidate.score_average is None
    assert result.comparison.comparable_count == 0


@pytest.mark.django_db
def test_empty_recorded_set_keeps_both_missing_without_inventing_cases(
    comparison_fixture,
):
    _, _, examples, make_run, metric_id, _, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric_id,
        **scope,
    )
    expected = tuple(sorted((str(item.example_id), 1) for item in examples))
    assert result.missing_by_side == {"baseline": expected, "candidate": expected}
    assert (
        result.baseline_counts == result.candidate_counts == RunCounts(4, 0, 4, 0, 0, 0)
    )
    assert result.comparison.cases == ()
    assert all(
        ref.baseline is None and ref.candidate is None for ref in result.trace_refs
    )


@pytest.mark.django_db
@pytest.mark.parametrize(
    "restriction",
    ["organization", "workspace", "deleted_run", "deleted_version", "deleted_dataset"],
)
def test_both_runs_are_scoped_to_authoritative_dataset(comparison_fixture, restriction):
    dataset, version, _, make_run, metric_id, _, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    if restriction in {"organization", "workspace"}:
        scope[f"{restriction}_id"] = uuid.uuid4()
    elif restriction == "deleted_run":
        ExperimentRun.all_objects.filter(id=candidate.id).update(deleted=True)
    elif restriction == "deleted_version":
        DatasetVersion.all_objects.filter(id=version.id).update(deleted=True)
    else:
        Dataset.all_objects.filter(id=dataset.id).update(deleted=True)
    with pytest.raises(RunComparisonError, match="not found in this tenant"):
        compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=candidate.id,
            metric_id=metric_id,
            **scope,
        )


@pytest.mark.django_db
def test_recorded_item_outside_plan_is_rejected(comparison_fixture):
    _, version, _, make_run, metric_id, _, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    other = DatasetVersionItem.objects.create(dataset_version=version, input={})
    _item(candidate, other)
    with pytest.raises(RunComparisonError, match="outside the pinned run plan"):
        compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=candidate.id,
            metric_id=metric_id,
            **scope,
        )


@pytest.mark.django_db
def test_limit_plus_one_refuses_excess_rows_without_truncating(
    comparison_fixture, django_assert_num_queries
):
    _, _, examples, make_run, metric_id, _, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    ExperimentRunItem.objects.bulk_create(
        [
            ExperimentRunItem(
                run=candidate, example_id=examples[0].example_id, trial_number=trial
            )
            for trial in range(1, 10_002)
        ]
    )
    with (
        django_assert_num_queries(4),
        pytest.raises(RunComparisonError, match="more than 10000 recorded items"),
    ):
        compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=candidate.id,
            metric_id=metric_id,
            **scope,
        )


@pytest.mark.django_db
def test_plan_size_is_bounded_before_projection(comparison_fixture, monkeypatch):
    from model_hub.services import persisted_run_comparison

    _, _, _, make_run, metric_id, _, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    monkeypatch.setattr(persisted_run_comparison, "_MAX_ITEMS", 3)
    with pytest.raises(RunComparisonError, match="at most 3 identities"):
        compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=candidate.id,
            metric_id=metric_id,
            **scope,
        )


@pytest.mark.django_db
def test_trials_align_independently_with_assessed_only_pass_rates(comparison_fixture):
    _, _, examples, make_run, metric_id, evaluator_id, _, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    identities = [
        {"example_id": str(examples[0].example_id), "trial_number": trial}
        for trial in (1, 2)
    ]
    for run, values in ((baseline, (0.2, 0.8)), (candidate, (0.7, 0.3))):
        run.metadata = {
            "_futureagi_run_write": {
                "plan": {"identities": identities, "plan_hash": _digest(identities)}
            }
        }
        run.save(update_fields=["metadata"])
        for trial, value in enumerate(values, 1):
            ExperimentRunItem.objects.create(
                run=run,
                example_id=examples[0].example_id,
                trial_number=trial,
                status="completed",
                score_refs=[_ref(metric_id, evaluator_id, value)],
            )
    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric_id,
        passing_score=0.5,
        higher_is_better=False,
        **scope,
    )
    assert [(case.trial_number, case.status) for case in result.comparison.cases] == [
        (1, "regressed"),
        (2, "improved"),
    ]
    assert (
        result.comparison.baseline.pass_rate
        == result.comparison.candidate.pass_rate
        == 0.5
    )
    assert result.comparison.comparable_count == 2
    assert (
        result.baseline_counts == result.candidate_counts == RunCounts(2, 2, 0, 2, 0, 2)
    )


@pytest.mark.django_db
@pytest.mark.parametrize("changed", ["missing", "duplicate", "malformed"])
def test_selected_metric_must_be_unambiguously_pinned(comparison_fixture, changed):
    _, _, _, make_run, metric_id, _, binding, scope = comparison_fixture
    baseline, candidate = make_run(), make_run()
    candidate.evaluator_versions = (
        []
        if changed == "missing"
        else [binding, binding]
        if changed == "duplicate"
        else [{**binding, "metric_id": "bad"}]
    )
    candidate.save(update_fields=["evaluator_versions"])
    with pytest.raises(RunComparisonError):
        compare_experiment_runs(
            baseline_run_id=baseline.id,
            candidate_run_id=candidate.id,
            metric_id=metric_id,
            **scope,
        )


@pytest.mark.django_db(transaction=True)
def test_snapshot_header_task_evaluator_and_persisted_comparison_flow(
    comparison_fixture, user, organization, workspace
):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from model_hub.models.evals_metric import (
        EvalTemplate,
        EvalTemplateVersion,
        UserEvalMetric,
    )
    from model_hub.services.dataset_version_service import (
        DatasetVersionItemInput,
        create_dataset_version,
    )
    from model_hub.services.experiment_run_creation import create_experiment_run
    from model_hub.services.python_task_adapter import (
        EvaluatorResult,
        run_python_experiment,
    )

    dataset, _, _, _, _, _, _, scope = comparison_fixture
    example_ids = [uuid.uuid4(), uuid.uuid4()]
    version = create_dataset_version(
        dataset=dataset,
        created_by=user,
        items=[
            DatasetVersionItemInput(
                example_id=example_id,
                input={"case": index},
                expected={"answer": "correct"},
            )
            for index, example_id in enumerate(example_ids)
        ],
    )
    template = EvalTemplate.objects.create(
        name="comparison-flow-evaluator",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    evaluator_version = EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        organization=organization,
        workspace=workspace,
        config_snapshot={"expected_key": "answer"},
    )
    metric = UserEvalMetric.objects.create(
        name="answer-match",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        user=user,
    )
    baseline, candidate = [
        create_experiment_run(
            run_id=uuid.uuid4(),
            dataset_version=version,
            created_by=user,
            task_kind="external",
            expected_items=[(example_id, 1) for example_id in example_ids],
            evaluator_metrics=[metric],
        )
        for _ in range(2)
    ]
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("persisted-comparison-flow")

    def evaluator(output, context):
        assert context.evaluator_version_id == evaluator_version.id
        assert context.snapshot["config_snapshot"]["expected_key"] == "answer"
        return EvaluatorResult(
            status="assessed", value=float(output == context.expected)
        )

    def task(value, context):
        with context.tracer.start_as_current_span("retrieval"):
            with context.tracer.start_as_current_span("tool"):
                correct = value["case"] == (0 if context.run_id == baseline.id else 1)
                return {"answer": "correct" if correct else "incorrect"}

    for run in (baseline, candidate):
        completed = run_python_experiment(
            run_id=run.id,
            task=task,
            evaluators={metric.id: evaluator},
            tracer=tracer,
            **scope,
        )
        assert completed.status == "completed"
    result = compare_experiment_runs(
        baseline_run_id=baseline.id,
        candidate_run_id=candidate.id,
        metric_id=metric.id,
        passing_score=0.5,
        **scope,
    )
    by_id = {case.example_id: case for case in result.comparison.cases}
    assert by_id[str(example_ids[0])].status == "regressed"
    assert by_id[str(example_ids[1])].status == "improved"
    assert (
        result.baseline_counts == result.candidate_counts == RunCounts(2, 2, 0, 2, 0, 2)
    )
    assert result.missing_by_side == {"baseline": (), "candidate": ()}
    roots = {
        f"{span.context.trace_id:032x}": f"{span.context.span_id:016x}"
        for span in exporter.get_finished_spans()
        if span.name == "futureagi.experiment.python_task"
    }
    assert len(roots) == 4
    for refs in result.trace_refs:
        assert roots[refs.baseline.trace_id] == refs.baseline.root_span_id
        assert roots[refs.candidate.trace_id] == refs.candidate.root_span_id
