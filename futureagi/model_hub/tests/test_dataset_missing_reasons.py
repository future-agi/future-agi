"""Missing dataset reasons stay actionable after ORM collection filters blanks.

Only graded samples that should carry reasons count towards the ERROR: the
placeholders of a running evaluation, the stop or failure markers of a stopped
or crashed one, and evaluations not configured to write reasons did not lose
anything and stay INFO.
"""

import json
from unittest.mock import MagicMock

import numpy as np
import pytest
import structlog
from structlog.testing import capture_logs

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    EvalExplanationSummaryStatus,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.utils import eval_reasons
from model_hub.utils.eval_cell_status import mark_eval_cells_stopped
from model_hub.views.eval_runner import bulk_update_or_create_cells

pytestmark = [pytest.mark.integration, pytest.mark.requires_ee]


@pytest.fixture
def isolated_models(monkeypatch):
    from ee.agenthub.explanation_agent import exp_agent

    llm = MagicMock(name="synthetic_llm")
    embed = MagicMock(side_effect=AssertionError("Unexpected embedding request"))
    manager = MagicMock()
    manager.get_syn_embedding.return_value = embed
    monkeypatch.setattr(exp_agent, "LLM", MagicMock(return_value=llm))
    monkeypatch.setattr(exp_agent, "EmbeddingManager", MagicMock(return_value=manager))
    monkeypatch.setattr(exp_agent, "logger", structlog.get_logger(exp_agent.__name__))
    monkeypatch.setattr(
        eval_reasons, "logger", structlog.get_logger(eval_reasons.__name__)
    )
    assert eval_reasons.ExplanationAgent is exp_agent.ExplanationAgent
    return llm, embed


def create_graded_dataset(user, workspace, *, rows, nonempty_reasons):
    dataset = Dataset.objects.create(
        name="Synthetic graded dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )
    score_column = Column.objects.create(
        dataset=dataset,
        name="synthetic-evaluation",
        data_type=DataTypeChoices.FLOAT.value,
        source=SourceChoices.EVALUATION.value,
    )
    reason_column = Column.objects.create(
        dataset=dataset,
        name="synthetic-evaluation-reason",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION_REASON.value,
    )
    for i in range(rows):
        row = Row.objects.create(dataset=dataset, order=i)
        Cell.objects.create(dataset=dataset, row=row, column=score_column, value="1.0")
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=reason_column,
            value=(
                f"Synthetic reason {i}"
                if i < nonempty_reasons
                else (None if i % 2 else "")
            ),
        )
    return dataset


@pytest.mark.parametrize("nonempty_reasons", range(5))
def test_graded_dataset_missing_reasons_remains_error(
    nonempty_reasons, user, workspace, isolated_models, monkeypatch
):
    from ee.agenthub.explanation_agent import exp_agent

    dataset = create_graded_dataset(
        user, workspace, rows=15, nonempty_reasons=nonempty_reasons
    )
    collected = eval_reasons.get_eval_reasons(dataset_id=str(dataset.pk))[
        "synthetic-evaluation"
    ]
    assert len(collected["eval_values"]) == 15
    assert (
        len([v for v in collected["eval_reasons"] if v not in (None, "")])
        == nonempty_reasons
    )
    embed_path = MagicMock(side_effect=AssertionError("Too few reasons to embed"))
    monkeypatch.setattr(exp_agent.ExplanationAgent, "_embed", embed_path)
    with capture_logs() as records:
        eval_reasons.get_explanation_summary._original_func(str(dataset.pk))
    dataset.refresh_from_db()
    embed_path.assert_not_called()
    llm, embed = isolated_models
    embed.assert_not_called()
    llm._get_completion_content.assert_not_called()
    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    assert dataset.eval_reasons == {}
    errors = [
        r for r in records if r.get("event") == "explanation_summary_missing_reasons"
    ]
    assert len(errors) == 1, records
    assert errors[0]["log_level"] == "error"
    assert errors[0]["input_count"] == 15
    assert errors[0]["usable_count"] == nonempty_reasons
    assert not [r for r in records if r.get("reason") == "insufficient_samples"]


def test_truly_small_agent_input_remains_info(isolated_models):
    agent = eval_reasons.ExplanationAgent()
    with capture_logs() as records:
        result = agent.evaluate(
            explanation=["synthetic one", "synthetic two"], eval_values=["1", "1"]
        )
    assert result == []
    assert any(
        r.get("reason") == "insufficient_samples" and r["log_level"] == "info"
        for r in records
    )
    assert not [r for r in records if r.get("log_level") in ("error", "critical")]
    isolated_models[1].assert_not_called()


def test_unfiltered_missing_reasons_control(isolated_models):
    agent = eval_reasons.ExplanationAgent()
    with capture_logs() as records:
        assert agent.evaluate(explanation=[{} for _ in range(15)]) == []
    assert any(
        r.get("event") == "explanation_summary_missing_reasons"
        and r["log_level"] == "error"
        for r in records
    )
    isolated_models[1].assert_not_called()


def test_truly_small_dataset_is_insufficient_data(user, workspace, isolated_models):
    dataset = create_graded_dataset(user, workspace, rows=2, nonempty_reasons=2)
    with capture_logs() as records:
        eval_reasons.get_explanation_summary._original_func(str(dataset.pk))
    dataset.refresh_from_db()
    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.INSUFFICIENT_DATA
    assert any(
        r.get("event") == "insufficient_data_for_critical_issues"
        and r["log_level"] == "info"
        for r in records
    )
    assert not [r for r in records if r.get("log_level") in ("error", "critical")]


def test_five_valid_reasons_reach_clustering(isolated_models, monkeypatch):
    import numpy as np

    agent = eval_reasons.ExplanationAgent()
    vectors = np.eye(5)
    embed = MagicMock(return_value=vectors)
    cluster = MagicMock(
        return_value=(
            np.zeros(5, dtype=int),
            None,
            {
                "min_cluster_size": 5,
                "min_samples": 2,
            },
        )
    )
    monkeypatch.setattr(agent, "_embed", embed)
    monkeypatch.setattr(agent, "simple_hdbscan", cluster)
    monkeypatch.setattr(
        agent, "representative_indices", lambda *a, **kw: {0: [0, 2, 4]}
    )
    monkeypatch.setattr(
        agent,
        "summarize_cluster",
        lambda *a, **kw: {
            "kind": "failure",
            "theme": "Synthetic theme",
            "status": "accepted",
        },
    )
    reasons = [f"Synthetic reason {i}" for i in range(5)]
    with capture_logs() as records:
        summaries = agent.evaluate(explanation=reasons, eval_values=["1"] * 15)
    embed.assert_called_once_with(reasons)
    cluster.assert_called_once()
    assert summaries[0]["size"] == 5
    assert summaries[0]["member_ids"] == [str(i) for i in range(5)]
    assert not [r for r in records if r.get("log_level") in ("error", "critical")]


# --------------------------------------------------------------------------
# Graded samples, not stored cells, decide whether reasons went missing.
# --------------------------------------------------------------------------

GATE_ROWS = eval_reasons.MIN_ROWS_FOR_CRITICAL_ISSUES


@pytest.fixture
def stub_clustering(isolated_models, monkeypatch):
    """Let five or more real reasons reach clustering without embeddings or LLM."""
    from ee.agenthub.explanation_agent import exp_agent

    agent_cls = exp_agent.ExplanationAgent
    monkeypatch.setattr(agent_cls, "_embed", lambda self, texts: np.eye(len(texts)))
    monkeypatch.setattr(
        agent_cls,
        "simple_hdbscan",
        lambda self, X, *a, **kw: (
            np.zeros(len(X), dtype=int),
            None,
            {"min_cluster_size": 5, "min_samples": 2},
        ),
    )
    monkeypatch.setattr(
        agent_cls,
        "representative_indices",
        lambda self, X, labels, *a, **kw: {0: [0, 1, 2]},
    )
    monkeypatch.setattr(
        agent_cls,
        "summarize_cluster",
        lambda self, *a, **kw: {
            "kind": "failure",
            "theme": "Synthetic theme",
            "status": "accepted",
        },
    )


def _error_records(records):
    return [
        r
        for r in records
        if r.get("log_level") in ("error", "critical", "exception")
        or r.get("event") == "explanation_summary_missing_reasons"
    ]


def _dataset_with_rows(user, workspace):
    dataset = Dataset.objects.create(
        name="Synthetic evaluated dataset",
        organization=user.organization,
        workspace=workspace,
        user=user,
    )
    rows = [Row.objects.create(dataset=dataset, order=i) for i in range(GATE_ROWS)]
    return dataset, rows


def _dataset_eval(user, workspace, dataset, *, name, reason_column):
    """Store an evaluation the way the dataset add-eval path does: an
    EVALUATION column keyed by the metric id and, only when
    config.reason_column is set, a `<name>-reason` EVALUATION_REASON column
    whose source_id is `<eval column id>-sourceid-<metric id>`."""
    template = EvalTemplate.no_workspace_objects.create(
        name=f"synthetic-template-{name}",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score"},
        criteria="Synthetic criteria",
    )
    metric = UserEvalMetric.objects.create(
        name=name,
        organization=user.organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {}, "reason_column": reason_column},
        user=user,
    )
    eval_col = Column.objects.create(
        name=name,
        data_type=DataTypeChoices.FLOAT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        dataset=dataset,
    )
    reason_col = None
    if reason_column:
        reason_col = Column.objects.create(
            name=f"{name}-reason",
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION_REASON.value,
            source_id=f"{eval_col.id}-sourceid-{metric.id}",
            dataset=dataset,
        )
    return metric, eval_col, reason_col


def _grade(dataset, row, eval_col, reason_col=None, reason="Synthetic reason"):
    Cell.objects.update_or_create(
        dataset=dataset,
        row=row,
        column=eval_col,
        defaults={"value": "1.0", "status": CellStatus.PASS.value},
    )
    if reason_col is not None:
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=reason_col,
            value=reason,
            status=CellStatus.PASS.value,
        )


def _run_summary(dataset):
    with capture_logs() as records:
        eval_reasons.get_explanation_summary._original_func(str(dataset.pk))
    dataset.refresh_from_db()
    return records


@pytest.mark.parametrize("finished_rows", [0, 3])
@pytest.mark.parametrize("placeholder", ["", None], ids=["run-start", "re-run"])
def test_running_evaluation_placeholders_are_not_missing_reasons(
    placeholder, finished_rows, user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    _, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="synthetic-running", reason_column=True
    )
    # Run start writes a RUNNING placeholder for every row with the runner's
    # own helper ("" at run start, None when a re-run resets the column).
    bulk_update_or_create_cells(
        [row.id for row in rows],
        eval_col.id,
        dataset.id,
        {
            "value_infos": json.dumps({}),
            "value": placeholder,
            "status": CellStatus.RUNNING.value,
        },
    )
    for row in rows[:finished_rows]:
        _grade(dataset, row, eval_col, reason_col, f"Synthetic reason {row.order}")

    collected = eval_reasons.get_eval_reasons(dataset_id=str(dataset.pk))[
        "synthetic-running"
    ]
    assert len(collected["eval_values"]) == GATE_ROWS
    assert len(collected["eval_reasons"]) == finished_rows

    records = _run_summary(dataset)

    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    assert dataset.eval_reasons == {}
    assert not _error_records(records), records
    skips = [r for r in records if r.get("reason") == "insufficient_samples"]
    assert len(skips) == 1, records
    assert skips[0]["log_level"] == "info"
    assert skips[0]["input_count"] == finished_rows
    assert skips[0]["usable_count"] == finished_rows


def test_evaluation_without_reason_column_is_skipped_at_info(
    user, workspace, isolated_models, monkeypatch
):
    from ee.agenthub.explanation_agent import exp_agent

    dataset, rows = _dataset_with_rows(user, workspace)
    metric, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="synthetic-no-reasons", reason_column=False
    )
    assert reason_col is None
    for row in rows:
        _grade(dataset, row, eval_col)

    collected = eval_reasons.get_eval_reasons(dataset_id=str(dataset.pk))[
        "synthetic-no-reasons"
    ]
    assert "eval_reasons" not in collected
    assert len(collected["eval_values"]) == GATE_ROWS

    evaluate = MagicMock(side_effect=AssertionError("Nothing to summarise"))
    monkeypatch.setattr(exp_agent.ExplanationAgent, "evaluate", evaluate)
    records = _run_summary(dataset)

    evaluate.assert_not_called()
    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    assert dataset.eval_reasons == {}
    assert not _error_records(records), records
    skips = [r for r in records if r.get("reason") == "no_reason_column"]
    assert len(skips) == 1, records
    assert skips[0]["log_level"] == "info"
    assert skips[0]["user_eval_metric_id"] == str(metric.id)


def test_reason_column_pairs_with_evaluation_whose_name_contains_reason(
    user, workspace, stub_clustering
):
    dataset, rows = _dataset_with_rows(user, workspace)
    metric, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="answer-reasoning", reason_column=True
    )
    for row in rows:
        _grade(dataset, row, eval_col, reason_col, f"Synthetic reason {row.order}")

    collected = eval_reasons.get_eval_reasons(dataset_id=str(dataset.pk))
    assert list(collected) == ["answer-reasoning"]
    entry = collected["answer-reasoning"]
    assert len(entry["eval_reasons"]) == GATE_ROWS
    assert len(entry["eval_values"]) == GATE_ROWS
    assert entry["user_eval_metric_id"] == str(metric.id)

    records = _run_summary(dataset)

    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    assert list(dataset.eval_reasons) == ["answer-reasoning"]
    clusters = dataset.eval_reasons["answer-reasoning"]
    assert clusters and clusters[0]["eval_config_id"] == str(metric.id)
    assert not _error_records(records), records


def test_source_paired_graded_run_with_blank_reasons_remains_error(
    user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    _, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="synthetic-blank-reasons", reason_column=True
    )
    for row in rows:
        _grade(dataset, row, eval_col, reason_col, reason="")

    records = _run_summary(dataset)

    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    errors = [
        r for r in records if r.get("event") == "explanation_summary_missing_reasons"
    ]
    assert len(errors) == 1, records
    assert errors[0]["log_level"] == "error"
    assert errors[0]["input_count"] == GATE_ROWS
    assert errors[0]["usable_count"] == 0


@pytest.mark.parametrize(
    ("eval_values", "graded"),
    [
        ([""] * 12 + ["1.0"] * 3, 3),
        ([None] * 15, 0),
        ([0, False, 0.0, "0", "  "], 4),
    ],
    ids=["placeholders", "reset", "falsy-graded-values"],
)
def test_agent_counts_only_graded_values(eval_values, graded, isolated_models):
    agent = eval_reasons.ExplanationAgent()
    with capture_logs() as records:
        assert agent.evaluate(explanation=[], eval_values=eval_values) == []
    assert not _error_records(records), records
    skips = [r for r in records if r.get("reason") == "insufficient_samples"]
    assert len(skips) == 1, records
    assert skips[0]["input_count"] == graded
    isolated_models[1].assert_not_called()


def test_agent_keeps_error_for_falsy_graded_values(isolated_models):
    agent = eval_reasons.ExplanationAgent()
    with capture_logs() as records:
        assert agent.evaluate(explanation=[], eval_values=[0, False] * 3) == []
    errors = _error_records(records)
    assert len(errors) == 1, records
    assert errors[0]["log_level"] == "error"
    assert errors[0]["input_count"] == 6


# --------------------------------------------------------------------------
# Stopped or crashed runs, and evaluations that never write reasons.
# --------------------------------------------------------------------------

STOP_MARKER = "Evaluation stopped by user"  # StopUserEvalView's reason
CRASH_MARKER = "Synthetic runner failure: connection reset"  # str(e)[:500]


def _start_run(dataset, rows, eval_col, reason_col, *, finished_rows, reasons=None):
    """Write the runner's RUNNING run-start placeholders, then finish the first
    `finished_rows` rows; only the first `reasons` of them keep a reason."""
    bulk_update_or_create_cells(
        [row.id for row in rows],
        eval_col.id,
        dataset.id,
        {
            "value_infos": json.dumps({}),
            "value": "",
            "status": CellStatus.RUNNING.value,
        },
    )
    reasons = finished_rows if reasons is None else reasons
    for row in rows[:finished_rows]:
        _grade(
            dataset,
            row,
            eval_col,
            reason_col if row.order < reasons else None,
            f"Synthetic reason {row.order}",
        )


def _assert_stopped_cells(eval_col, finished_rows, marker):
    cells = list(Cell.objects.filter(column=eval_col).values("value", "status"))
    assert len(cells) == GATE_ROWS
    stopped = [c for c in cells if c["status"] == CellStatus.ERROR.value]
    assert len(stopped) == GATE_ROWS - finished_rows
    # The markers are non-blank values, so only their status sets them apart.
    assert {c["value"] for c in stopped} == {marker}


def _assert_insufficient_samples(records, input_count):
    assert not _error_records(records), records
    skips = [r for r in records if r.get("reason") == "insufficient_samples"]
    assert len(skips) == 1, records
    assert skips[0]["log_level"] == "info"
    assert skips[0]["input_count"] == input_count


@pytest.mark.parametrize("finished_rows", [0, 3])
@pytest.mark.parametrize(
    "marker", [STOP_MARKER, CRASH_MARKER], ids=["user-stop", "runner-crash"]
)
def test_stopped_or_crashed_run_markers_are_not_missing_reasons(
    marker, finished_rows, user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    # The reason column exists from the start, as AddUserEvalView(run=true)
    # creates it, so a stop before the first finished row leaves it empty.
    metric, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="synthetic-stopped", reason_column=True
    )
    _start_run(dataset, rows, eval_col, reason_col, finished_rows=finished_rows)

    # The helper StopUserEvalView and a crashing runner both call.
    assert mark_eval_cells_stopped(metric, reason=marker) == (GATE_ROWS - finished_rows)
    _assert_stopped_cells(eval_col, finished_rows, marker)

    records = _run_summary(dataset)

    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    assert dataset.eval_reasons == {}
    _assert_insufficient_samples(records, finished_rows)
    # Every cell is collected, but only the finished grades count as graded.
    collected = eval_reasons.get_eval_reasons(dataset_id=str(dataset.pk))[
        "synthetic-stopped"
    ]
    assert len(collected["eval_values"]) == GATE_ROWS
    assert collected["graded_values"] == ["1.0"] * finished_rows
    assert len(collected["eval_reasons"]) == finished_rows


def test_stop_endpoint_markers_are_not_missing_reasons(
    auth_client, user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    metric, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="synthetic-http-stop", reason_column=True
    )
    metric.status = StatusType.RUNNING.value
    metric.save(update_fields=["status"])
    _start_run(dataset, rows, eval_col, reason_col, finished_rows=3)

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/stop_user_eval/{metric.id}/",
        {},
        format="json",
    )

    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.status == StatusType.ERROR.value
    _assert_stopped_cells(eval_col, 3, STOP_MARKER)
    records = _run_summary(dataset)
    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    _assert_insufficient_samples(records, 3)


def test_stopped_run_that_lost_finished_reasons_remains_error(
    user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    metric, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="synthetic-stopped-lost", reason_column=True
    )
    # Ten rows finished but only three kept their reason before the stop.
    _start_run(dataset, rows, eval_col, reason_col, finished_rows=10, reasons=3)
    mark_eval_cells_stopped(metric, reason=STOP_MARKER)

    records = _run_summary(dataset)

    errors = [
        r for r in records if r.get("event") == "explanation_summary_missing_reasons"
    ]
    assert len(errors) == 1, records
    assert errors[0]["log_level"] == "error"
    assert errors[0]["input_count"] == 10
    assert errors[0]["usable_count"] == 3


def _add_user_eval(auth_client, dataset, template, *, name, run):
    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": name,
            "template_id": str(template.id),
            "config": {"mapping": {}, "config": {}, "reason_column": False},
            "run": run,
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.objects.get(dataset=dataset, name=name, deleted=False)
    assert metric.config.get("reason_column") is False, metric.config
    return metric


def test_start_evals_process_reasonless_eval_is_skipped_at_info(
    auth_client, user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    template = EvalTemplate.no_workspace_objects.create(
        name="synthetic-template-reasonless",
        organization=user.organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score"},
        criteria="Synthetic criteria",
    )
    name = "synthetic-reasonless"
    metric = _add_user_eval(auth_client, dataset, template, name=name, run=False)

    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/start_evals_process/",
        {"user_eval_ids": [str(metric.id)]},
        format="json",
    )

    assert response.status_code == 200, response.content
    eval_col = Column.objects.get(
        dataset=dataset,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        deleted=False,
    )
    # StartEvalsProcess pairs a reason column even though reasons are off.
    reason_col = Column.objects.get(
        dataset=dataset, source=SourceChoices.EVALUATION_REASON.value
    )
    assert reason_col.source_id == f"{eval_col.id}-sourceid-{metric.id}"
    # The runner only writes reason cells when config.reason_column is set.
    for row in rows:
        _grade(dataset, row, eval_col)
    collected = eval_reasons.get_eval_reasons(dataset_id=str(dataset.pk))[name]
    assert collected["eval_reasons"] == []
    assert len(collected["eval_values"]) == GATE_ROWS

    records = _run_summary(dataset)

    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    assert dataset.eval_reasons == {}
    assert not _error_records(records), records
    skips = [r for r in records if r.get("event") == "explanation_summary_skipped"]
    assert [r["reason"] for r in skips] == ["no_reason_column"], records
    assert skips[0]["log_level"] == "info"
    assert skips[0]["user_eval_metric_id"] == str(metric.id)


def _add_reason_column(dataset, metric, eval_col):
    return Column.objects.create(
        name=f"{metric.name}-reason",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION_REASON.value,
        source_id=f"{eval_col.id}-sourceid-{metric.id}",
        dataset=dataset,
    )


def test_reasonless_eval_with_a_few_stale_reasons_is_not_error(
    user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    metric, eval_col, _ = _dataset_eval(
        user, workspace, dataset, name="synthetic-stale", reason_column=False
    )
    reason_col = _add_reason_column(dataset, metric, eval_col)
    # Reasons left over from before reasons were turned off.
    for row in rows:
        _grade(dataset, row, eval_col, reason_col if row.order < 3 else None)

    records = _run_summary(dataset)

    assert dataset.eval_reason_status == EvalExplanationSummaryStatus.COMPLETED
    _assert_insufficient_samples(records, 3)


def test_reasons_on_with_empty_reason_column_remains_error(
    user, workspace, isolated_models
):
    dataset, rows = _dataset_with_rows(user, workspace)
    _, eval_col, reason_col = _dataset_eval(
        user, workspace, dataset, name="synthetic-lost-all", reason_column=True
    )
    # Every row finished but no reason cell was ever written.
    for row in rows:
        _grade(dataset, row, eval_col)
    assert not Cell.objects.filter(column=reason_col).exists()

    records = _run_summary(dataset)

    errors = [
        r for r in records if r.get("event") == "explanation_summary_missing_reasons"
    ]
    assert len(errors) == 1, records
    assert errors[0]["log_level"] == "error"
    assert errors[0]["input_count"] == GATE_ROWS
    assert errors[0]["usable_count"] == 0
