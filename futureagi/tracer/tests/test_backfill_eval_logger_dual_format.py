"""
Tests for the ``backfill_eval_logger_dual_format`` management command.

Verifies the same gating rules as the write-side helper:
  * ``score`` rows: re-populate ``output_float`` from ``output_str`` dict;
    never touch ``output_str_list``.
  * ``choices`` rows: re-populate ``output_str_list`` from the dict /
    plain-string ``output_str``; never touch ``output_float``.
  * ``Pass/Fail`` and other ``output`` types are untouched.
  * Re-running the command is a no-op.
"""

import json

import pytest  # noqa: E402
from django.core.management import call_command  # noqa: E402

# Break the import cycle (see test_eval_logger_schema.py for the canonical
# comment).
import model_hub.tasks  # noqa: F401
from model_hub.models.evals_metric import EvalTemplate  # noqa: E402
from tracer.models.custom_eval_config import CustomEvalConfig  # noqa: E402
from tracer.models.observation_span import (  # noqa: E402
    EvalLogger,
    EvalTargetType,
)


def _make_config(project, organization, workspace, output_type):
    template = EvalTemplate.objects.create(
        name=f"Template ({output_type})",
        description="",
        organization=organization,
        workspace=workspace,
        config={"output": output_type},
    )
    return CustomEvalConfig.objects.create(
        name=f"Eval ({output_type})",
        project=project,
        eval_template=template,
        config={},
        mapping={},
        filters={},
    )


def _make_row(observation_span, custom_eval_config, **kwargs):
    return EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=observation_span,
        trace=observation_span.trace,
        custom_eval_config=custom_eval_config,
        **kwargs,
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestBackfillEvalLoggerDualFormat:
    def test_score_dict_python_repr_populates_output_float_and_rewrites_json(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "score")
        # Simulate the buggy on-disk shape: str(dict) → single quotes.
        row = _make_row(
            observation_span,
            cfg,
            output_str=str({"score": 0.7, "choice": "Choice 1"}),
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_float == 0.7
        assert row.output_str_list == []  # gated: score rows do not write list
        # The on-disk form is normalised to JSON.
        assert json.loads(row.output_str) == {"score": 0.7, "choice": "Choice 1"}

    def test_choices_dict_populates_output_str_list_not_float(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "choices")
        row = _make_row(
            observation_span,
            cfg,
            output_str=json.dumps({"score": 0.7, "choice": "Choice 1"}),
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_str_list == ["Choice 1"]
        assert row.output_float is None  # gated: choices rows do not write float

    def test_choices_multi_choice_list_populated(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "choices")
        row = _make_row(
            observation_span,
            cfg,
            output_str=json.dumps({"score": 1.0, "choices": ["A", "B"]}),
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_str_list == ["A", "B"]
        assert row.output_float is None

    def test_choices_plain_string_populates_output_str_list(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "choices")
        row = _make_row(
            observation_span,
            cfg,
            output_str="Choice 1",
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_str_list == ["Choice 1"]
        assert row.output_str == "Choice 1"  # plain string left as-is
        assert row.output_float is None

    def test_passfail_row_is_untouched(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "Pass/Fail")
        row = _make_row(observation_span, cfg, output_bool=True, output_str="")

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_bool is True
        assert row.output_float is None
        assert row.output_str_list == []
        assert row.output_str == ""

    def test_error_sentinel_rows_are_skipped(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "score")
        row = _make_row(
            observation_span,
            cfg,
            output_str="ERROR",
            error=True,
            error_message="something failed",
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_str == "ERROR"
        assert row.output_float is None

    def test_dry_run_does_not_modify_rows(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "score")
        row = _make_row(
            observation_span,
            cfg,
            output_str=str({"score": 0.7, "choice": "Choice 1"}),
        )
        before = row.output_str

        call_command("backfill_eval_logger_dual_format", "--dry-run")

        row.refresh_from_db()
        assert row.output_float is None
        assert row.output_str == before

    def test_rerun_is_idempotent(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "choices")
        row = _make_row(
            observation_span,
            cfg,
            output_str=json.dumps({"score": 0.7, "choice": "Choice 1"}),
        )

        call_command("backfill_eval_logger_dual_format")
        row.refresh_from_db()
        list_after_first = row.output_str_list
        str_after_first = row.output_str

        call_command("backfill_eval_logger_dual_format")
        row.refresh_from_db()
        assert row.output_str_list == list_after_first
        assert row.output_str == str_after_first

    # ── Backfill mirrors the helper's new list handling ────────────────

    def test_score_list_in_output_str_averages_into_output_float(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "score")
        row = _make_row(
            observation_span,
            cfg,
            output_str=json.dumps([0.4, 0.6, 0.8]),
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_float == pytest.approx(0.6)
        assert row.output_str_list == []  # gated: score never writes list

    def test_score_list_of_score_dicts_averages_into_output_float(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "score")
        row = _make_row(
            observation_span,
            cfg,
            output_str=json.dumps(
                [
                    {"score": 0.4, "choice": "A"},
                    {"score": 0.8, "choice": "B"},
                ]
            ),
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_float == pytest.approx(0.6)
        assert row.output_str_list == []

    def test_choices_list_of_dicts_flattens_and_dedupes(
        self, observation_span, project, organization, workspace
    ):
        cfg = _make_config(project, organization, workspace, "choices")
        row = _make_row(
            observation_span,
            cfg,
            output_str=json.dumps([{"choice": "A"}, {"choice": "B"}, {"choice": "A"}]),
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_str_list == ["A", "B"]
        assert row.output_float is None

    def test_choices_existing_output_str_list_is_deduped_in_place(
        self, observation_span, project, organization, workspace
    ):
        """Rows written by the old buggy dispatch could have duplicate choices
        already sitting in output_str_list; the backfill cleans those up."""
        cfg = _make_config(project, organization, workspace, "choices")
        row = _make_row(
            observation_span,
            cfg,
            output_str=json.dumps({"choices": ["A", "B", "A"]}),
            output_str_list=["A", "B", "A"],
        )

        call_command("backfill_eval_logger_dual_format")

        row.refresh_from_db()
        assert row.output_str_list == ["A", "B"]
