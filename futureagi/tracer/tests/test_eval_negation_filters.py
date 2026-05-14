"""Tests for eval metric negation in the ClickHouse filter builder.

Regression coverage for TH-4359: verifies that negation operators
produce correct SQL for all three output types (PASS_FAIL, CHOICE, SCORE).
"""

import pytest

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def pass_fail_eval(db, organization, workspace, project):
    tmpl = EvalTemplate.no_workspace_objects.create(
        name="pf_eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail"},
        visible_ui=True,
    )
    CustomEvalConfig.objects.create(
        name="pf_cfg",
        project=project,
        eval_template=tmpl,
        config={},
        mapping={},
        filters={},
    )
    return tmpl


@pytest.fixture
def choice_eval(db, organization, workspace, project):
    tmpl = EvalTemplate.no_workspace_objects.create(
        name="choice_eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "choices"},
        visible_ui=True,
    )
    CustomEvalConfig.objects.create(
        name="choice_cfg",
        project=project,
        eval_template=tmpl,
        config={},
        mapping={},
        filters={},
    )
    return tmpl


@pytest.fixture
def score_eval(db, organization, workspace, project):
    tmpl = EvalTemplate.no_workspace_objects.create(
        name="score_eval",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score"},
        visible_ui=True,
    )
    CustomEvalConfig.objects.create(
        name="score_cfg",
        project=project,
        eval_template=tmpl,
        config={},
        mapping={},
        filters={},
    )
    return tmpl


# ── PASS_FAIL ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestPassFailNegation:

    def test_positive_equals_passed(self, pass_fail_eval):
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(pass_fail_eval.id), "equals", "Passed")
        assert "output_bool IN" in sql
        assert "trace_id IN (" in sql

    def test_positive_equals_failed(self, pass_fail_eval):
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(pass_fail_eval.id), "equals", "Failed")
        assert "output_bool IN" in sql

    @pytest.mark.parametrize("op", ["not_equals", "is_not", "not_in"])
    def test_negation_passed(self, pass_fail_eval, op):
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(pass_fail_eval.id), op, "Passed")
        assert "output_bool NOT IN" in sql
        # Must use outer IN (not NOT IN) so unevaluated traces are excluded
        assert "trace_id IN (" in sql
        assert "trace_id NOT IN" not in sql

    @pytest.mark.parametrize("op", ["not_equals", "is_not", "not_in"])
    def test_negation_failed(self, pass_fail_eval, op):
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(pass_fail_eval.id), op, "Failed")
        assert "output_bool NOT IN" in sql


# ── CHOICE ───────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestChoiceNegation:

    def test_positive_equals(self, choice_eval):
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(choice_eval.id), "equals", "Good")
        assert "output_str =" in sql
        assert "NOT (" not in sql

    @pytest.mark.parametrize("op", ["not_equals", "is_not", "not_in"])
    def test_negation_wraps_in_not(self, choice_eval, op):
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(choice_eval.id), op, "Good")
        assert "NOT (" in sql
        assert "output_str =" in sql
        assert "trace_id IN (" in sql
        assert "trace_id NOT IN" not in sql


# ── SCORE ────────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestScoreNegation:

    def test_positive_equals(self, score_eval):
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(score_eval.id), "equals", 75)
        assert "output_float =" in sql
        assert "output_float !=" not in sql
        # 75 / 100 = 0.75
        score_params = [v for v in b._params.values() if v == 0.75]
        assert score_params, "Expected 0.75 in params (75 / 100)"

    @pytest.mark.parametrize("op", ["not_equals", "is_not"])
    def test_negation_uses_not_equal(self, score_eval, op):
        """not_equals and is_not (alias) produce output_float !=."""
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(score_eval.id), op, 75)
        assert "output_float !=" in sql
        assert "trace_id IN (" in sql
        assert "trace_id NOT IN" not in sql

    def test_not_in_uses_not_in(self, score_eval):
        """not_in produces output_float NOT IN for multi-value support."""
        b = ClickHouseFilterBuilder()
        sql = b._build_eval_condition(str(score_eval.id), "not_in", 75)
        assert "output_float NOT IN" in sql


# ── Error clause always present ──────────────────────────────────────


@pytest.mark.django_db
class TestErrorExclusion:
    """Errored eval rows should be excluded from both positive and negative filters."""

    @pytest.mark.parametrize("op", ["equals", "not_equals", "is_not"])
    def test_error_clause_present(self, pass_fail_eval, choice_eval, score_eval, op):
        for tmpl, value in [
            (pass_fail_eval, "Passed"),
            (choice_eval, "Good"),
            (score_eval, 75),
        ]:
            b = ClickHouseFilterBuilder()
            sql = b._build_eval_condition(str(tmpl.id), op, value)
            assert "error = 0" in sql, (
                f"error exclusion missing for {tmpl.name} with op={op}"
            )
