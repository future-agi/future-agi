from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.evaluation import Evaluation, StatusChoices


def _template(name: str, organization, *, output: str, multi_choice: bool = False):
    return EvalTemplate.objects.create(
        name=name,
        config={"output": output},
        organization=organization,
        multi_choice=multi_choice,
    )


def _legacy_eval(
    *,
    user,
    organization,
    workspace,
    template,
    value: str,
    output_type: str | None = None,
) -> Evaluation:
    ev = Evaluation.objects.create(
        user=user,
        organization=organization,
        workspace=workspace,
        eval_template=template,
        status=StatusChoices.COMPLETED,
    )
    Evaluation.objects.filter(id=ev.id).update(
        value=value,
        output_type=output_type,
        output_bool=None,
        output_float=None,
        output_str_list=None,
        output_str=None,
    )
    ev.refresh_from_db()
    return ev


def _run(**flags) -> str:
    out = StringIO()
    call_command("backfill_evaluation_dual_format", stdout=out, **flags)
    return out.getvalue()


@pytest.fixture
def tpl_score(db, organization):
    return _template("score tpl", organization, output="score")


class TestAxisRouting:
    @pytest.mark.parametrize(
        "output,value,axis,expected",
        [
            ("score", "0.7", "output_float", 0.7),
            ("choices", "frequently", "output_str_list", ["frequently"]),
            ("Pass/Fail", "Passed", "output_bool", True),
        ],
    )
    def test_value_routes_to_axis(
        self, db, user, organization, workspace, output, value, axis, expected
    ):
        tpl = _template(f"tpl-{output}", organization, output=output)
        ev = _legacy_eval(
            user=user,
            organization=organization,
            workspace=workspace,
            template=tpl,
            value=value,
        )
        _run()
        ev.refresh_from_db()
        assert getattr(ev, axis) == expected

    def test_choice_scores_dict_populates_both_axes(
        self, db, user, organization, workspace, tpl_score
    ):
        ev = _legacy_eval(
            user=user,
            organization=organization,
            workspace=workspace,
            template=tpl_score,
            value="{'score': 0.8, 'choice': 'good'}",
        )
        _run()
        ev.refresh_from_db()
        assert ev.output_float == pytest.approx(0.8)
        assert ev.output_str_list == ["good"]


class TestOperationalSafety:
    def test_dry_run_does_not_mutate(
        self, db, user, organization, workspace, tpl_score
    ):
        ev = _legacy_eval(
            user=user,
            organization=organization,
            workspace=workspace,
            template=tpl_score,
            value="0.7",
        )
        out = _run(dry_run=True)
        ev.refresh_from_db()
        assert ev.output_float is None
        assert "dry_run=True" in out

    def test_rerun_is_idempotent(self, db, user, organization, workspace, tpl_score):
        ev = _legacy_eval(
            user=user,
            organization=organization,
            workspace=workspace,
            template=tpl_score,
            value="0.7",
        )
        _run()
        first = (
            Evaluation.objects.filter(id=ev.id)
            .values_list("output_float", flat=True)
            .first()
        )
        out = _run()
        ev.refresh_from_db()
        assert ev.output_float == first
        assert "updated_rows=0" in out

    def test_already_populated_row_is_skipped(
        self, db, user, organization, workspace, tpl_score
    ):
        ev = _legacy_eval(
            user=user,
            organization=organization,
            workspace=workspace,
            template=tpl_score,
            value="0.7",
        )
        Evaluation.objects.filter(id=ev.id).update(output_float=0.1)
        out = _run()
        ev.refresh_from_db()
        assert ev.output_float == pytest.approx(0.1)
        assert "skipped_unchanged=1" in out

    def test_limit_caps_the_processed_row_count(
        self, db, user, organization, workspace, tpl_score
    ):
        for _ in range(3):
            _legacy_eval(
                user=user,
                organization=organization,
                workspace=workspace,
                template=tpl_score,
                value="0.7",
            )
        out = _run(limit=2)
        assert "Pre-flight: 2 rows in scope" in out
        assert "updated_rows=2" in out

    def test_multi_batch_flush_processes_all_rows(
        self, db, user, organization, workspace, tpl_score
    ):
        for _ in range(5):
            _legacy_eval(
                user=user,
                organization=organization,
                workspace=workspace,
                template=tpl_score,
                value="0.7",
            )
        out = _run(batch_size=2)
        assert "updated_rows=5" in out

    def test_dispatch_error_skips_one_row_and_continues(
        self, db, user, organization, workspace, tpl_score, monkeypatch
    ):
        bad = _legacy_eval(
            user=user,
            organization=organization,
            workspace=workspace,
            template=tpl_score,
            value="bad",
        )
        good = _legacy_eval(
            user=user,
            organization=organization,
            workspace=workspace,
            template=tpl_score,
            value="0.42",
        )

        from model_hub.management.commands import backfill_evaluation_dual_format

        original = backfill_evaluation_dual_format.resolve_eval_axes

        def _raise_on_bad(value, config_output, *, include_output_str=False):
            if value == "bad":
                raise TypeError("simulated dispatch failure")
            return original(value, config_output, include_output_str=include_output_str)

        monkeypatch.setattr(
            backfill_evaluation_dual_format, "resolve_eval_axes", _raise_on_bad
        )

        out = _run()

        bad.refresh_from_db()
        good.refresh_from_db()
        assert bad.output_float is None
        assert good.output_float == pytest.approx(0.42)
        assert "skipped_dispatch_error=1" in out
        assert "updated_rows=1" in out
