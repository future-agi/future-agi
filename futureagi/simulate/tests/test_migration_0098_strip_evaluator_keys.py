"""Tests for the evaluator-key strip on simulate eval attachments.

The strip function is exercised directly; migration 0098 and the
``strip_eval_config_evaluator_keys`` command are thin wrappers over it, and one
test drives each wrapper.
"""

import copy
import importlib
from io import StringIO

import pytest
from django.apps import apps as global_apps
from django.core.management import call_command

from model_hub.models.choices import DatasetSourceChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.eval_config import SimulateEvalConfig
from simulate.models.run_test import RunTest
from simulate.services.eval_config_repair import strip_evaluator_keys

migration_module = importlib.import_module(
    "simulate.migrations.0098_strip_evaluator_keys_from_eval_config"
)

LEAKED_NESTED = {
    "rule_prompt": "old",
    "model": "gpt-4o",
    "api_key": "sk-test",
    "provider": "openai",
    "organization_id": "o",
}

MIXED_CONFIG = {
    "run_config": {"model": "m"},
    "params": {"k": 1},
    "rule_prompt": "top",
    "config": {"rule_prompt": "old", "code": "return 1", "my_key": "keep"},
}


@pytest.fixture
def agent_definition(db, organization, workspace):
    return AgentDefinition.objects.create(
        agent_name="Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+15551230000",
        inbound=True,
        description="Test agent for the evaluator-key strip",
        organization=organization,
        workspace=workspace,
        languages=["en"],
    )


@pytest.fixture
def dataset_for_scenario(db, organization, user, workspace):
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    col = Column.objects.create(
        dataset=dataset,
        name="situation",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order = [str(col.id)]
    dataset.save()
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(dataset=dataset, column=col, row=row, value="row value")
    return dataset


@pytest.fixture
def scenario(db, organization, workspace, dataset_for_scenario, agent_definition):
    return Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset_for_scenario,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )


@pytest.fixture
def run_test(db, organization, workspace, agent_definition, scenario):
    rt = RunTest.objects.create(
        name="Test Run",
        description="Test run",
        agent_definition=agent_definition,
        organization=organization,
        workspace=workspace,
    )
    rt.scenarios.add(scenario)
    return rt


@pytest.fixture
def eval_template(db, organization):
    return EvalTemplate.objects.create(
        name="strip-keys-eval",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "rule_prompt": "t",
            "output": "Pass/Fail",
            "config": {},
        },
        organization=organization,
    )


@pytest.fixture
def make_row(run_test, eval_template):
    def _make(config, deleted=False):
        return SimulateEvalConfig.objects.create(
            name="Attached eval",
            eval_template=eval_template,
            run_test=run_test,
            config=config,
            deleted=deleted,
        )

    return _make


@pytest.fixture
def leaked_rows(make_row):
    """One live and one soft-deleted attachment, both carrying leaked keys."""
    return [
        make_row({"config": copy.deepcopy(LEAKED_NESTED)}),
        make_row({"config": copy.deepcopy(LEAKED_NESTED)}, deleted=True),
    ]


def _reload(row):
    return SimulateEvalConfig.all_objects.get(id=row.id)


def _assert_leaked_rows_stripped(rows):
    for row in rows:
        assert _reload(row).config == {"config": {}}


@pytest.mark.unit
@pytest.mark.django_db
class TestStripEvaluatorKeys:
    def test_strips_evaluator_keys_including_soft_deleted_rows(self, leaked_rows):
        stamps_before = [row.updated_at for row in leaked_rows]

        strip_evaluator_keys(SimulateEvalConfig)

        assert [_reload(row).updated_at for row in leaked_rows] == stamps_before

        assert _reload(leaked_rows[1]).deleted is True
        _assert_leaked_rows_stripped(leaked_rows)

    def test_keeps_keys_outside_the_set_and_all_top_level_keys(self, make_row):
        row = make_row(copy.deepcopy(MIXED_CONFIG))

        strip_evaluator_keys(SimulateEvalConfig)

        assert _reload(row).config == {
            "run_config": {"model": "m"},
            "params": {"k": 1},
            "rule_prompt": "top",
            "config": {"code": "return 1", "my_key": "keep"},
        }

    @pytest.mark.parametrize(
        "shape",
        [
            None,
            "x",
            "config",
            ["config"],
            {"run_config": {"model": "m"}},
            {"config": "not-a-dict"},
        ],
        ids=[
            "null",
            "not-a-dict",
            "string-matching-has-key",
            "array-matching-has-key",
            "no-nested-key",
            "nested-not-a-dict",
        ],
    )
    def test_rows_without_nested_dict_untouched(self, make_row, shape):
        row = make_row(copy.deepcopy(shape))
        before = _reload(row)

        strip_evaluator_keys(SimulateEvalConfig)

        after = _reload(row)
        assert after.config == shape
        assert after.updated_at == before.updated_at

    def test_idempotent(self, leaked_rows, make_row):
        rows = [*leaked_rows, make_row(copy.deepcopy(MIXED_CONFIG))]

        _, first_changed = strip_evaluator_keys(SimulateEvalConfig)
        after_first = {row.id: _reload(row).config for row in rows}
        scanned, second_changed = strip_evaluator_keys(SimulateEvalConfig)
        after_second = {row.id: _reload(row).config for row in rows}

        assert first_changed == 3
        assert after_second == after_first
        assert scanned == 3
        assert second_changed == 0

    def test_skips_a_row_edited_while_it_runs(self, make_row):
        row = make_row({"config": copy.deepcopy(LEAKED_NESTED), "run_config": {}})
        real_manager = SimulateEvalConfig._base_manager

        class RowsEditedUnderneath:
            """Yields each row as read, after someone else has saved an edit to it."""

            def __init__(self, queryset):
                self.queryset = queryset

            def only(self, *fields):
                self.queryset = self.queryset.only(*fields)
                return self

            def iterator(self):
                for read_row in self.queryset.iterator():
                    SimulateEvalConfig.all_objects.filter(pk=read_row.pk).update(
                        config={**read_row.config, "run_config": {"model": "edited"}}
                    )
                    yield read_row

        class RacingManager:
            def filter(self, *args, **kwargs):
                queryset = real_manager.filter(*args, **kwargs)
                if "config__has_key" in kwargs:
                    return RowsEditedUnderneath(queryset)
                return queryset

        class RacingModel:
            _base_manager = RacingManager()

        assert strip_evaluator_keys(RacingModel) == (1, 0)
        after = _reload(row).config
        assert after["run_config"] == {"model": "edited"}
        assert "api_key" in after["config"]
        assert strip_evaluator_keys(SimulateEvalConfig) == (1, 1)
        assert _reload(row).config == {"config": {}, "run_config": {"model": "edited"}}

    def test_migration_wrapper_strips_rows(self, leaked_rows):
        migration_module.forwards(global_apps, None)

        _assert_leaked_rows_stripped(leaked_rows)

    def test_command_strips_rows_and_prints_counts(self, leaked_rows):
        buf = StringIO()

        call_command("strip_eval_config_evaluator_keys", stdout=buf)

        _assert_leaked_rows_stripped(leaked_rows)
        assert buf.getvalue().split() == ["scanned=2", "changed=2"]
