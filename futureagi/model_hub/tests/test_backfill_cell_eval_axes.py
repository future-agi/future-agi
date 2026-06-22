from __future__ import annotations

import json
import uuid
from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import DataTypeChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Org")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="cell-backfill@test.com",
        password="x",
        name="Cell Backfill",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="ws",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def dataset(db, organization, workspace):
    return Dataset.objects.create(
        name="cell-backfill ds",
        organization=organization,
        workspace=workspace,
    )


def _template(*, organization, output: str, multi_choice: bool = False) -> EvalTemplate:
    return EvalTemplate.objects.create(
        name=f"tpl-{output}-{uuid.uuid4()}",
        config={"output": output},
        organization=organization,
        multi_choice=multi_choice,
    )


def _user_eval_metric(*, dataset, organization, workspace, template) -> UserEvalMetric:
    return UserEvalMetric.objects.create(
        name=f"uem-{uuid.uuid4()}",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        template=template,
        status=StatusType.NOT_STARTED.value,
    )


def _eval_column(*, dataset, user_eval_metric) -> Column:
    return Column.objects.create(
        name=f"eval-col-{uuid.uuid4()}",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(user_eval_metric.id),
    )


def _eval_cell(*, dataset, column, value: str, value_infos: dict | str | None = None) -> Cell:
    row = Row.objects.create(dataset=dataset, order=0)
    return Cell.objects.create(
        dataset=dataset,
        column=column,
        row=row,
        value=value,
        value_infos=(
            json.dumps(value_infos) if isinstance(value_infos, dict) else value_infos
        ),
    )


def _run(**flags) -> str:
    out = StringIO()
    call_command("backfill_cell_eval_axes", stdout=out, **flags)
    return out.getvalue()


def _decode(cell: Cell) -> dict:
    cell.refresh_from_db()
    raw = cell.value_infos
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


class TestAxisRouting:
    def test_score_value_populates_output_float(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(dataset=dataset, column=col, value="0.7", value_infos={})

        _run()

        infos = _decode(cell)
        assert infos["output_float"] == pytest.approx(0.7)
        assert infos["output_bool"] is None
        assert infos["output_str_list"] is None

    def test_choices_value_populates_output_str_list(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="choices")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(dataset=dataset, column=col, value="frequently", value_infos={})

        _run()

        infos = _decode(cell)
        assert infos["output_str_list"] == ["frequently"]
        assert infos["output_float"] is None
        assert infos["output_bool"] is None

    def test_passfail_value_populates_output_bool(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="Pass/Fail")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(dataset=dataset, column=col, value="Passed", value_infos={})

        _run()

        infos = _decode(cell)
        assert infos["output_bool"] is True
        assert infos["output_float"] is None
        assert infos["output_str_list"] is None

    def test_choice_scores_dict_populates_both_axes(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(
            dataset=dataset,
            column=col,
            value="{'score': 0.8, 'choice': 'good'}",
            value_infos={},
        )

        _run()

        infos = _decode(cell)
        assert infos["output_float"] == pytest.approx(0.8)
        assert infos["output_str_list"] == ["good"]


class TestOperationalSafety:
    def test_dry_run_does_not_mutate(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(dataset=dataset, column=col, value="0.7", value_infos={})

        out = _run(dry_run=True)

        infos = _decode(cell)
        assert "output_float" not in infos
        assert "dry_run=True" in out

    def test_rerun_is_idempotent(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(dataset=dataset, column=col, value="0.7", value_infos={})

        _run()
        first = _decode(cell)
        out = _run()
        second = _decode(cell)

        assert first == second
        assert "skipped_rows=1" in out

    def test_pre_populated_axes_are_preserved(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(
            dataset=dataset,
            column=col,
            value="0.7",
            value_infos={
                "output_bool": None,
                "output_float": 0.1,
                "output_str_list": None,
            },
        )

        out = _run()

        infos = _decode(cell)
        assert infos["output_float"] == pytest.approx(0.1)
        assert "skipped_rows=1" in out

    def test_cell_without_resolvable_template_is_skipped(
        self, db, dataset, organization, workspace
    ):
        col = Column.objects.create(
            name="orphan-col",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(uuid.uuid4()),
        )
        cell = _eval_cell(dataset=dataset, column=col, value="0.7", value_infos={})

        out = _run()

        infos = _decode(cell)
        assert "output_float" not in infos
        assert "skipped_rows=1" in out

    def test_malformed_value_infos_is_skipped(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(
            dataset=dataset, column=col, value="0.7", value_infos="not-json"
        )

        out = _run()

        cell.refresh_from_db()
        assert cell.value_infos == "not-json"
        assert "skipped_rows=1" in out

    def test_existing_non_axis_keys_are_preserved(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column(dataset=dataset, user_eval_metric=uem)
        cell = _eval_cell(
            dataset=dataset,
            column=col,
            value="0.7",
            value_infos={"reason": "looks ok", "name": "scoring eval"},
        )

        _run()

        infos = _decode(cell)
        assert infos["reason"] == "looks ok"
        assert infos["name"] == "scoring eval"
        assert infos["output_float"] == pytest.approx(0.7)


class TestFiltering:
    def test_column_id_scopes_to_one_column(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        scoped_col = _eval_column(dataset=dataset, user_eval_metric=uem)
        unscoped_col = _eval_column(dataset=dataset, user_eval_metric=uem)
        scoped = _eval_cell(
            dataset=dataset, column=scoped_col, value="0.7", value_infos={}
        )
        unscoped = _eval_cell(
            dataset=dataset, column=unscoped_col, value="0.5", value_infos={}
        )

        _run(column_id=str(scoped_col.id))

        assert _decode(scoped)["output_float"] == pytest.approx(0.7)
        assert "output_float" not in _decode(unscoped)

    def test_dataset_id_scopes_to_one_dataset(
        self, db, organization, workspace
    ):
        ds_a = Dataset.objects.create(
            name="ds-a", organization=organization, workspace=workspace
        )
        ds_b = Dataset.objects.create(
            name="ds-b", organization=organization, workspace=workspace
        )
        tpl = _template(organization=organization, output="score")
        uem_a = _user_eval_metric(
            dataset=ds_a, organization=organization, workspace=workspace, template=tpl
        )
        uem_b = _user_eval_metric(
            dataset=ds_b, organization=organization, workspace=workspace, template=tpl
        )
        col_a = _eval_column(dataset=ds_a, user_eval_metric=uem_a)
        col_b = _eval_column(dataset=ds_b, user_eval_metric=uem_b)
        cell_a = _eval_cell(dataset=ds_a, column=col_a, value="0.7", value_infos={})
        cell_b = _eval_cell(dataset=ds_b, column=col_b, value="0.5", value_infos={})

        _run(dataset_id=str(ds_a.id))

        assert _decode(cell_a)["output_float"] == pytest.approx(0.7)
        assert "output_float" not in _decode(cell_b)


def _eval_column_with_source(
    *, dataset, user_eval_metric, source: str, source_id: str
) -> Column:
    """Mirror ``_eval_column`` but force the source / source_id pair.

    Used for experiment_evaluation / optimisation_evaluation columns whose
    source_id is a composite of ``{prefix}-sourceid-{user_eval_metric_id}``
    rather than a bare UUID.
    """
    return Column.objects.create(
        name=f"eval-col-{uuid.uuid4()}",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=source,
        source_id=source_id,
    )


class TestMultiSourceCoverage:
    """The backfill must cover all three cell sources that hold eval rows."""

    def test_experiment_evaluation_cell_axes_are_backfilled(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        prefix = uuid.uuid4()
        composite_id = f"{prefix}-sourceid-{uem.id}"
        col = _eval_column_with_source(
            dataset=dataset,
            user_eval_metric=uem,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=composite_id,
        )
        cell = _eval_cell(dataset=dataset, column=col, value="0.7", value_infos={})

        out = _run()

        assert _decode(cell)["output_float"] == pytest.approx(0.7)
        assert "updated_rows=1" in out

    def test_optimisation_evaluation_cell_axes_are_backfilled(
        self, db, dataset, organization, workspace
    ):
        tpl = _template(organization=organization, output="choices")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        composite_id = f"{uuid.uuid4()}-sourceid-{uem.id}"
        col = _eval_column_with_source(
            dataset=dataset,
            user_eval_metric=uem,
            source=SourceChoices.OPTIMISATION_EVALUATION.value,
            source_id=composite_id,
        )
        cell = _eval_cell(
            dataset=dataset, column=col, value="frequently", value_infos={}
        )

        out = _run()

        assert _decode(cell)["output_str_list"] == ["frequently"]
        assert "updated_rows=1" in out

    def test_mixed_sources_in_one_run_all_get_backfilled(
        self, db, dataset, organization, workspace
    ):
        score_tpl = _template(organization=organization, output="score")
        passfail_tpl = _template(organization=organization, output="Pass/Fail")
        choices_tpl = _template(organization=organization, output="choices")
        score_uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=score_tpl
        )
        passfail_uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=passfail_tpl
        )
        choices_uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=choices_tpl
        )
        dataset_col = _eval_column(dataset=dataset, user_eval_metric=score_uem)
        experiment_col = _eval_column_with_source(
            dataset=dataset,
            user_eval_metric=passfail_uem,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{uuid.uuid4()}-sourceid-{passfail_uem.id}",
        )
        optimisation_col = _eval_column_with_source(
            dataset=dataset,
            user_eval_metric=choices_uem,
            source=SourceChoices.OPTIMISATION_EVALUATION.value,
            source_id=f"{uuid.uuid4()}-sourceid-{choices_uem.id}",
        )
        dataset_cell = _eval_cell(
            dataset=dataset, column=dataset_col, value="0.42", value_infos={}
        )
        experiment_cell = _eval_cell(
            dataset=dataset, column=experiment_col, value="Passed", value_infos={}
        )
        optimisation_cell = _eval_cell(
            dataset=dataset, column=optimisation_col, value="always", value_infos={}
        )

        out = _run()

        assert _decode(dataset_cell)["output_float"] == pytest.approx(0.42)
        assert _decode(experiment_cell)["output_bool"] is True
        assert _decode(optimisation_cell)["output_str_list"] == ["always"]
        assert "updated_rows=3" in out

    def test_composite_source_id_without_separator_falls_back_to_direct_lookup(
        self, db, dataset, organization, workspace
    ):
        """Defensive: experiment column whose source_id happens to be a plain
        ``UserEvalMetric.id`` (no -sourceid- prefix) still resolves via the
        direct lookup. Mirrors the dataset surface fallback."""
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        col = _eval_column_with_source(
            dataset=dataset,
            user_eval_metric=uem,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=str(uem.id),  # no composite prefix
        )
        cell = _eval_cell(dataset=dataset, column=col, value="0.5", value_infos={})

        _run()

        assert _decode(cell)["output_float"] == pytest.approx(0.5)

    def test_composite_source_id_with_unknown_metric_id_is_skipped(
        self, db, dataset, organization, workspace
    ):
        """If the tail of the composite source_id is not a valid metric uuid,
        the cell is skipped (no template => can't route the value)."""
        tpl = _template(organization=organization, output="score")
        uem = _user_eval_metric(
            dataset=dataset, organization=organization, workspace=workspace, template=tpl
        )
        # Real source_id format but tail is a different UUID that won't resolve
        col = _eval_column_with_source(
            dataset=dataset,
            user_eval_metric=uem,
            source=SourceChoices.EXPERIMENT_EVALUATION.value,
            source_id=f"{uuid.uuid4()}-sourceid-{uuid.uuid4()}",
        )
        cell = _eval_cell(dataset=dataset, column=col, value="0.5", value_infos={})

        out = _run()

        infos = _decode(cell)
        assert "output_float" not in infos
        assert "skipped_rows=1" in out
