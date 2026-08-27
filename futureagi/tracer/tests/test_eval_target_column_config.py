"""Unit tests for the eval column target_type enrichment.

Covers ``build_eval_target_map`` (most-recent target_type per config, from the
eval_logger discovery scan) and its stamping through
``update_column_config_based_on_eval_config`` — the S/T source glyph the
Observe trace/span lists render per eval column. Task grouping
(eval_task_id / eval_task_name) is deliberately NOT on the wire — scoped out
of this change.
"""

from datetime import datetime, timedelta

import pytest

from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.utils.helper import (
    build_eval_target_map,
    update_column_config_based_on_eval_config,
)

NOW = datetime(2026, 8, 20, 12, 0, 0)


@pytest.fixture
def choices_eval_config(db, project, organization, workspace):
    """A CHOICES-output eval config (template + config)."""
    template = EvalTemplate.objects.create(
        name="Sentiment",
        description="Sentiment choices template",
        organization=organization,
        workspace=workspace,
        config={
            "output": "choices",
            "choices_map": {"Positive": "pass", "Negative": "fail"},
        },
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


class TestBuildEvalTargetMap:
    def test_resolves_target_per_config(self):
        rows = [("c1", "span", NOW), ("c2", "trace", NOW)]
        assert build_eval_target_map(rows, ["c1", "c2"]) == {
            "c1": "span",
            "c2": "trace",
        }

    def test_picks_most_recent_target(self):
        """A config applied at several target types keeps the newest one —
        one identifier per eval column."""
        rows = [
            ("c1", "span", NOW - timedelta(days=2)),
            ("c1", "trace", NOW),
        ]
        assert build_eval_target_map(rows, ["c1"]) == {"c1": "trace"}

    def test_drops_config_not_in_alive_ids(self):
        rows = [("dead", "span", NOW), ("c1", "trace", NOW)]
        assert build_eval_target_map(rows, ["c1"]) == {"c1": "trace"}

    def test_handles_null_target_and_null_config(self):
        rows = [(None, "span", NOW), ("c1", None, NOW), ("c1", "", NOW)]
        assert build_eval_target_map(rows, ["c1"]) == {"c1": None}

    def test_empty_rows_yield_empty_map(self):
        assert build_eval_target_map([], ["c1"]) == {}


@pytest.mark.django_db
class TestColumnConfigTargetType:
    def test_eval_column_without_map_has_none_target(self, custom_eval_config):
        """No eval_target_map -> target_type defaults to None (non-breaking)."""
        config = update_column_config_based_on_eval_config([], [custom_eval_config])

        cols = _eval_columns(config)
        assert len(cols) == 1
        assert cols[0]["id"] == str(custom_eval_config.id)
        assert cols[0]["target_type"] is None

    def test_eval_column_carries_target_type(self, custom_eval_config):
        config = update_column_config_based_on_eval_config(
            [],
            [custom_eval_config],
            eval_target_map={str(custom_eval_config.id): "trace"},
        )

        cols = _eval_columns(config)
        assert len(cols) == 1
        assert cols[0]["target_type"] == "trace"

    def test_config_absent_from_map_gets_none(self, custom_eval_config):
        config = update_column_config_based_on_eval_config(
            [],
            [custom_eval_config],
            eval_target_map={"some-other-config": "span"},
        )

        assert _eval_columns(config)[0]["target_type"] is None

    def test_skip_choices_yields_single_choices_column(self, choices_eval_config):
        """The Observe lists render ONE chip column per Choices eval."""
        config = update_column_config_based_on_eval_config(
            [],
            [choices_eval_config],
            skip_choices=True,
            eval_target_map={str(choices_eval_config.id): "span"},
        )

        cols = _eval_columns(config)
        assert len(cols) == 1
        col = cols[0]
        assert col["id"] == str(choices_eval_config.id)
        assert col["choices"] == ["Positive", "Negative"]
        assert col["target_type"] == "span"

    def test_choices_subcolumns_inherit_target_type(self, choices_eval_config):
        """Without skip_choices, every per-choice sub-column inherits the
        parent config's target_type."""
        config = update_column_config_based_on_eval_config(
            [],
            [choices_eval_config],
            eval_target_map={str(choices_eval_config.id): "trace"},
        )

        cols = _eval_columns(config)
        assert len(cols) == 2
        assert {c["id"] for c in cols} == {
            f"{choices_eval_config.id}**Positive",
            f"{choices_eval_config.id}**Negative",
        }
        assert all(c["target_type"] == "trace" for c in cols)

    def test_no_eval_task_fields_on_the_wire(self, custom_eval_config):
        """Task grouping is scoped out — the column config must not
        grow eval_task_id / eval_task_name back."""
        config = update_column_config_based_on_eval_config(
            [],
            [custom_eval_config],
            eval_target_map={str(custom_eval_config.id): "span"},
        )

        col = _eval_columns(config)[0]
        assert "eval_task_id" not in col
        assert "eval_task_name" not in col
