"""Integration tests for the ``backfill_simulate_eval_outputs`` command.

Creates real ``SimulateEvalConfig`` + ``CallExecution`` rows with both
pre-``choice_scores`` (plain scalar / list) and post-``choice_scores``
(dict) shapes, runs the command, and asserts the four axis keys land on
the right values per the stored template config.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from model_hub.models.evals_metric import EvalTemplate
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.test_execution import CallExecution, TestExecution


def _template(name: str, organization, *, output: str, multi_choice: bool = False):
    return EvalTemplate.objects.create(
        name=name,
        config={"output": output},
        organization=organization,
        multi_choice=multi_choice,
    )


def _eval_config(template, run_test, name: str = "bf cfg"):
    return SimulateEvalConfig.objects.create(
        name=name,
        eval_template=template,
        run_test=run_test,
        config={},
        mapping={},
    )


def _call(test_execution, eval_outputs):
    return CallExecution.objects.create(
        test_execution=test_execution,
        eval_outputs=eval_outputs,
    )


def _run(**flags) -> str:
    out = StringIO()
    call_command("backfill_simulate_eval_outputs", stdout=out, **flags)
    return out.getvalue()


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def run_test(db, organization, workspace):
    return RunTest.objects.create(
        name="bf run test",
        description="",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def test_execution(db, run_test):
    return TestExecution.objects.create(
        run_test=run_test,
        status=TestExecution.ExecutionStatus.RUNNING,
    )


# ── pre-choice_scores shapes (old data) ──────────────────────────────────


def test_old_score_plain_float_lands_on_output_score(
    db, organization, run_test, test_execution
):
    tpl = _template("score old", organization, output="score")
    cfg = _eval_config(tpl, run_test, "score old cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": 0.75,
                "reason": "",
                "output_type": "score",
                "name": "score old cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_score"] == pytest.approx(0.75)
    assert entry["output_pass"] is None
    assert entry["output_choice"] is None
    assert entry["output_choices"] is None


def test_old_pass_fail_string_lands_on_output_pass(
    db, organization, run_test, test_execution
):
    tpl = _template("pf old", organization, output="Pass/Fail")
    cfg = _eval_config(tpl, run_test, "pf old cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": "Passed",
                "reason": "",
                "output_type": "Pass/Fail",
                "name": "pf old cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_pass"] is True
    assert entry["output_score"] is None


def test_old_choices_single_plain_string_lands_on_output_choice(
    db, organization, run_test, test_execution
):
    tpl = _template("ch1 old", organization, output="choices", multi_choice=False)
    cfg = _eval_config(tpl, run_test, "ch1 old cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": "always",
                "reason": "",
                "output_type": "choices",
                "name": "ch1 old cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_choice"] == "always"
    assert entry["output_choices"] is None
    assert entry["output_score"] is None


def test_old_choices_multi_plain_list_lands_on_output_choices(
    db, organization, run_test, test_execution
):
    tpl = _template("chm old", organization, output="choices", multi_choice=True)
    cfg = _eval_config(tpl, run_test, "chm old cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": ["polite", "concise"],
                "reason": "",
                "output_type": "choices",
                "name": "chm old cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_choices"] == ["polite", "concise"]
    assert entry["output_choice"] is None


# ── post-choice_scores shapes (new dict format) ──────────────────────────


def test_score_dict_with_choice_scores_extracts_score(
    db, organization, run_test, test_execution
):
    tpl = _template("score new", organization, output="score")
    cfg = _eval_config(tpl, run_test, "score new cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": {"score": 0.66, "choice": "frequently"},
                "reason": "",
                "output_type": "choices",
                "name": "score new cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_score"] == pytest.approx(0.66)
    assert entry["output_choice"] is None


def test_choices_single_dict_with_choice_scores_extracts_choice(
    db, organization, run_test, test_execution
):
    tpl = _template("ch1 new", organization, output="choices", multi_choice=False)
    cfg = _eval_config(tpl, run_test, "ch1 new cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": {"score": 1.0, "choice": "always"},
                "reason": "",
                "output_type": "choices",
                "name": "ch1 new cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_choice"] == "always"
    assert entry["output_score"] is None


def test_choices_multi_dict_with_choice_scores_extracts_choices(
    db, organization, run_test, test_execution
):
    tpl = _template("chm new", organization, output="choices", multi_choice=True)
    cfg = _eval_config(tpl, run_test, "chm new cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": {"score": 0.5, "choices": ["polite", "concise"]},
                "reason": "",
                "output_type": "choices",
                "name": "chm new cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_choices"] == ["polite", "concise"]
    assert entry["output_choice"] is None
    assert entry["output_score"] is None


# ── operational safety ──────────────────────────────────────────────────


def test_dry_run_writes_nothing(db, organization, run_test, test_execution):
    tpl = _template("dry", organization, output="score")
    cfg = _eval_config(tpl, run_test, "dry cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": 0.5,
                "reason": "",
                "output_type": "score",
                "name": "dry cfg",
            },
        },
    )
    _run(dry_run=True)
    call.refresh_from_db()
    assert "output_score" not in call.eval_outputs[str(cfg.id)]


def test_rerun_is_idempotent(db, organization, run_test, test_execution):
    tpl = _template("idem", organization, output="score")
    cfg = _eval_config(tpl, run_test, "idem cfg")
    _call(
        test_execution,
        {
            str(cfg.id): {
                "output": 0.5,
                "reason": "",
                "output_type": "score",
                "name": "idem cfg",
            },
        },
    )
    _run()
    second = _run()
    assert "updated_rows=0" in second


def test_entries_already_canonical_are_skipped(
    db, organization, run_test, test_execution
):
    tpl = _template("already", organization, output="score")
    cfg = _eval_config(tpl, run_test, "already cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": 0.5,
                "output_pass": None,
                "output_score": 999.0,
                "output_choice": None,
                "output_choices": None,
                "reason": "",
                "output_type": "score",
                "name": "already cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    assert call.eval_outputs[str(cfg.id)]["output_score"] == 999.0


def test_pending_placeholder_gets_all_none_axes(
    db, organization, run_test, test_execution
):
    tpl = _template("pending", organization, output="score")
    cfg = _eval_config(tpl, run_test, "pending cfg")
    call = _call(
        test_execution,
        {str(cfg.id): {"status": "pending"}},
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_pass"] is None
    assert entry["output_score"] is None
    assert entry["output_choice"] is None
    assert entry["output_choices"] is None
    assert entry["status"] == "pending"


def test_eval_config_id_flag_scopes_to_one_entry(
    db, organization, run_test, test_execution
):
    tpl = _template("scope", organization, output="score")
    cfg_a = _eval_config(tpl, run_test, "a cfg")
    cfg_b = _eval_config(tpl, run_test, "b cfg")
    call = _call(
        test_execution,
        {
            str(cfg_a.id): {
                "output": 0.1,
                "reason": "",
                "output_type": "score",
                "name": "a",
            },
            str(cfg_b.id): {
                "output": 0.2,
                "reason": "",
                "output_type": "score",
                "name": "b",
            },
        },
    )
    _run(eval_config_id=str(cfg_a.id))
    call.refresh_from_db()
    assert "output_score" in call.eval_outputs[str(cfg_a.id)]
    assert "output_score" not in call.eval_outputs[str(cfg_b.id)]
