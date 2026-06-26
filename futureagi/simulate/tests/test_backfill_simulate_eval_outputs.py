"""Tests for the ``backfill_simulate_eval_outputs`` command."""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from model_hub.models.evals_metric import EvalTemplate
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios
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


def _call(test_execution, eval_outputs, *, scenario=None):
    scenario = scenario or Scenarios.objects.create(
        name=f"bf scenario {test_execution.id}",
        organization=test_execution.run_test.organization,
        workspace=test_execution.run_test.workspace,
    )
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
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


@pytest.mark.parametrize(
    "output,multi_choice,raw_output,axis,expected",
    [
        ("score", False, 0.75, "output_float", 0.75),
        ("Pass/Fail", False, "Passed", "output_bool", True),
        ("choices", False, "always", "output_str_list", ["always"]),
        (
            "choices",
            True,
            ["polite", "concise"],
            "output_str_list",
            ["polite", "concise"],
        ),
    ],
)
def test_old_runner_output_routes_to_axis(
    db,
    organization,
    run_test,
    test_execution,
    output,
    multi_choice,
    raw_output,
    axis,
    expected,
):
    tpl = _template(
        f"tpl-{output}-{multi_choice}",
        organization,
        output=output,
        multi_choice=multi_choice,
    )
    cfg = _eval_config(tpl, run_test, "cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": raw_output,
                "reason": "",
                "output_type": output,
                "name": "cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry[axis] == expected


# ── post-choice_scores shapes (new dict format) ──────────────────────────


@pytest.mark.parametrize(
    "output,multi_choice,dict_output,expected_float,expected_list",
    [
        ("score", False, {"score": 0.66, "choice": "frequently"}, 0.66, ["frequently"]),
        ("choices", False, {"score": 1.0, "choice": "always"}, 1.0, ["always"]),
        (
            "choices",
            True,
            {"score": 0.5, "choices": ["polite", "concise"]},
            0.5,
            ["polite", "concise"],
        ),
    ],
)
def test_choice_scores_dict_populates_both_axes(
    db,
    organization,
    run_test,
    test_execution,
    output,
    multi_choice,
    dict_output,
    expected_float,
    expected_list,
):
    tpl = _template(
        f"tpl-cs-{output}-{multi_choice}",
        organization,
        output=output,
        multi_choice=multi_choice,
    )
    cfg = _eval_config(tpl, run_test, "cfg")
    call = _call(
        test_execution,
        {
            str(cfg.id): {
                "output": dict_output,
                "reason": "",
                "output_type": "choices",
                "name": "cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    entry = call.eval_outputs[str(cfg.id)]
    assert entry["output_float"] == pytest.approx(expected_float)
    assert entry["output_str_list"] == expected_list


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
    assert "output_float" not in call.eval_outputs[str(cfg.id)]


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
                "output_bool": None,
                "output_float": 999.0,
                "output_str_list": None,
                "reason": "",
                "output_type": "score",
                "name": "already cfg",
            },
        },
    )
    _run()
    call.refresh_from_db()
    assert call.eval_outputs[str(cfg.id)]["output_float"] == 999.0


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
    assert entry["output_bool"] is None
    assert entry["output_float"] is None
    assert entry["output_str_list"] is None
    assert entry["status"] == "pending"


def test_limit_caps_the_processed_call_count(
    db, organization, run_test, test_execution
):
    tpl = _template("limit", organization, output="score")
    cfg = _eval_config(tpl, run_test, "limit cfg")
    for _ in range(3):
        _call(
            test_execution,
            {
                str(cfg.id): {
                    "output": 0.5,
                    "reason": "",
                    "output_type": "score",
                    "name": "x",
                }
            },
        )
    out = _run(limit=2)
    assert "Pre-flight: 2 rows in scope" in out
    assert "updated_rows=2" in out


def test_dispatch_error_skips_one_entry_and_continues(
    db, organization, run_test, test_execution, monkeypatch
):
    tpl = _template("dispatch", organization, output="score")
    bad_cfg = _eval_config(tpl, run_test, "bad cfg")
    good_cfg = _eval_config(tpl, run_test, "good cfg")
    call = _call(
        test_execution,
        {
            str(bad_cfg.id): {
                "output": "bad",
                "reason": "",
                "output_type": "score",
                "name": "bad",
            },
            str(good_cfg.id): {
                "output": 0.42,
                "reason": "",
                "output_type": "score",
                "name": "good",
            },
        },
    )

    from simulate.management.commands import backfill_simulate_eval_outputs

    original = backfill_simulate_eval_outputs.resolve_eval_axes

    def _raise_on_bad(value, config_output):
        if value == "bad":
            raise TypeError("simulated dispatch failure")
        return original(value, config_output)

    monkeypatch.setattr(
        backfill_simulate_eval_outputs, "resolve_eval_axes", _raise_on_bad
    )

    out = _run()

    call.refresh_from_db()
    bad_entry = call.eval_outputs[str(bad_cfg.id)]
    good_entry = call.eval_outputs[str(good_cfg.id)]
    assert "output_float" not in bad_entry
    assert good_entry["output_float"] == pytest.approx(0.42)
    assert "skipped_dispatch_error=1" in out
