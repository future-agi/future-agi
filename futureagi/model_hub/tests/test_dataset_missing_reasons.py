"""Missing dataset reasons stay actionable after ORM collection filters blanks."""

from unittest.mock import MagicMock

import pytest
import structlog
from structlog.testing import capture_logs

from model_hub.models.choices import (
    DataTypeChoices,
    EvalExplanationSummaryStatus,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils import eval_reasons

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
