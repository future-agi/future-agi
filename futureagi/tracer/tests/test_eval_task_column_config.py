"""Unit tests for eval_task / target_type clustering in column config.

Covers ``update_column_config_based_on_eval_config`` enrichment used by the
``list_traces_of_session`` evals response: each eval column carries the
``eval_task`` that ran it and the ``target_type`` it was applied at. CHOICES
sub-columns inherit the parent config's mapping. When no map is supplied the
three fields stay ``None`` (backwards compatible).
"""

import pytest

from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.utils.helper import (
    build_eval_task_map,
    update_column_config_based_on_eval_config,
)


@pytest.fixture
def choices_eval_config(db, project, organization, workspace):
    """A CHOICES-output eval config (template + config)."""
    template = EvalTemplate.objects.create(
        name="Sentiment",
        description="Sentiment choices template",
        organization=organization,
        workspace=workspace,
        config={"output": "choices", "choices_map": {"Positive": 1, "Negative": 0}},
        choices=["Positive", "Negative"],
    )
    return CustomEvalConfig.objects.create(
        name="Sentiment Eval",
        project=project,
        eval_template=template,
        config={},
        mapping={"input": "input", "output": "output"},
        filters={},
    )


def _eval_columns(config):
    """Filter a built column config down to the Evaluation Metrics columns."""
    return [c for c in config if c.get("group_by") == "Evaluation Metrics"]


@pytest.mark.django_db
def test_eval_column_without_map_has_none_task_fields(custom_eval_config):
    """No eval_task_map -> task/target fields default to None (non-breaking)."""
    config = update_column_config_based_on_eval_config([], [custom_eval_config])

    cols = _eval_columns(config)
    assert len(cols) == 1
    col = cols[0]
    assert col["id"] == str(custom_eval_config.id)
    assert col["eval_task_id"] is None
    assert col["eval_task_name"] is None
    assert col["target_type"] is None


@pytest.mark.django_db
def test_eval_column_carries_task_and_target_type(custom_eval_config):
    """A supplied map stamps eval_task_id/name and target_type on the column."""
    eval_task_map = {
        str(custom_eval_config.id): {
            "eval_task_id": "task-123",
            "eval_task_name": "Nightly QA",
            "target_type": "trace",
        }
    }

    config = update_column_config_based_on_eval_config(
        [], [custom_eval_config], eval_task_map=eval_task_map
    )

    cols = _eval_columns(config)
    assert len(cols) == 1
    col = cols[0]
    assert col["eval_task_id"] == "task-123"
    assert col["eval_task_name"] == "Nightly QA"
    assert col["target_type"] == "trace"


@pytest.mark.django_db
def test_skip_choices_yields_single_choices_column(choices_eval_config):
    """skip_choices=True (list_traces_of_session) -> ONE column per CHOICES
    eval, carrying output_type='choices' + the choices list for chip render,
    instead of one column per choice."""
    config = update_column_config_based_on_eval_config(
        [], [choices_eval_config], skip_choices=True
    )

    cols = _eval_columns(config)
    assert len(cols) == 1
    col = cols[0]
    assert col["id"] == str(choices_eval_config.id)  # no "**choice" suffix
    assert col["output_type"] == "choices"
    assert col["choices"] == ["Positive", "Negative"]


@pytest.mark.django_db
def test_choices_subcolumns_inherit_task_mapping(choices_eval_config):
    """Every per-choice sub-column inherits the parent config's task mapping."""
    eval_task_map = {
        str(choices_eval_config.id): {
            "eval_task_id": "task-xyz",
            "eval_task_name": "Sentiment Task",
            "target_type": "span",
        }
    }

    config = update_column_config_based_on_eval_config(
        [], [choices_eval_config], eval_task_map=eval_task_map
    )

    cols = _eval_columns(config)
    # One column per choice.
    assert len(cols) == 2
    expected_ids = {
        f"{choices_eval_config.id}**Positive",
        f"{choices_eval_config.id}**Negative",
    }
    assert {c["id"] for c in cols} == expected_ids
    for col in cols:
        assert col["eval_task_id"] == "task-xyz"
        assert col["eval_task_name"] == "Sentiment Task"
        assert col["target_type"] == "span"


@pytest.mark.django_db
def test_config_absent_from_map_gets_none(custom_eval_config, choices_eval_config):
    """Configs missing from the map keep None; mapped ones are populated."""
    eval_task_map = {
        str(custom_eval_config.id): {
            "eval_task_id": "task-1",
            "eval_task_name": "Task One",
            "target_type": "session",
        }
    }

    config = update_column_config_based_on_eval_config(
        [], [custom_eval_config, choices_eval_config], eval_task_map=eval_task_map
    )

    by_id = {c["id"]: c for c in _eval_columns(config)}
    mapped = by_id[str(custom_eval_config.id)]
    assert mapped["eval_task_id"] == "task-1"
    assert mapped["target_type"] == "session"

    # choices_eval_config not in map -> all sub-columns None
    for choice in ("Positive", "Negative"):
        sub = by_id[f"{choices_eval_config.id}**{choice}"]
        assert sub["eval_task_id"] is None
        assert sub["eval_task_name"] is None
        assert sub["target_type"] is None


# ---------------------------------------------------------------------------
# build_eval_task_map — the shared discovery → clustering helper.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_build_map_resolves_name_and_target(eval_task, custom_eval_config):
    """A discovery row resolves to the task's name + the applied target_type."""
    cid = str(custom_eval_config.id)
    rows = [(cid, str(eval_task.id), "trace", 100)]

    result = build_eval_task_map(rows, alive_config_ids=[cid])

    assert result[cid]["eval_task_id"] == str(eval_task.id)
    assert result[cid]["eval_task_name"] == eval_task.name
    assert result[cid]["target_type"] == "trace"


@pytest.mark.django_db
def test_build_map_picks_most_recent_task_target(eval_task, custom_eval_config):
    """When a config spans multiple (task, target_type) groups, keep newest."""
    cid = str(custom_eval_config.id)
    rows = [
        (cid, "older-task", "span", 10),
        (cid, str(eval_task.id), "trace", 99),  # newest last_seen wins
    ]

    result = build_eval_task_map(rows, alive_config_ids=[cid])

    assert result[cid]["eval_task_id"] == str(eval_task.id)
    assert result[cid]["target_type"] == "trace"


@pytest.mark.django_db
def test_build_map_drops_config_not_in_alive_ids(eval_task, custom_eval_config):
    """A config whose CustomEvalConfig is soft-deleted (absent from alive ids)
    is excluded even if it still has surviving logger rows."""
    cid = str(custom_eval_config.id)
    rows = [(cid, str(eval_task.id), "span", 5)]

    result = build_eval_task_map(rows, alive_config_ids=[])

    assert cid not in result
    assert result == {}


@pytest.mark.django_db
def test_build_map_unknown_task_id_keeps_id_with_none_name(custom_eval_config):
    """An eval_task_id with no matching EvalTask row keeps the id, name=None."""
    cid = str(custom_eval_config.id)
    rows = [(cid, "00000000-0000-0000-0000-000000000000", "session", 1)]

    result = build_eval_task_map(rows, alive_config_ids=[cid])

    assert result[cid]["eval_task_id"] == "00000000-0000-0000-0000-000000000000"
    assert result[cid]["eval_task_name"] is None
    assert result[cid]["target_type"] == "session"


@pytest.mark.django_db
def test_build_map_handles_null_task_and_target(custom_eval_config):
    """Rows with no eval_task_id/target_type yield None fields (not crash)."""
    cid = str(custom_eval_config.id)
    rows = [(cid, None, None, 1)]

    result = build_eval_task_map(rows, alive_config_ids=[cid])

    assert result[cid]["eval_task_id"] is None
    assert result[cid]["eval_task_name"] is None
    assert result[cid]["target_type"] is None
