"""Removing the evaluator settings grading leaked into simulate eval attachments, and
putting the copies back.

Two copies of the strip rule exist: the service the ``strip_eval_config_evaluator_keys``
command runs, and the frozen copy inside migration 0114, run here over the migration's own
historical models. Every strip test runs against both, and the golden table pins both to
one literal list of inputs and expected removals, so a later service change must edit the
service's expectations on purpose while the migration's never change.
"""

import ast
import copy
import importlib
import inspect
import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.models import QuerySet
from django.test import override_settings
from django.utils import timezone

from model_hub.models.choices import DatasetSourceChoices, StatusType
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import EvalTemplate
from simulate.models import AgentDefinition, Scenarios
from simulate.models.eval_config import (
    SimulateEvalConfig,
    SimulateEvalConfigRemovedKeys,
)
from simulate.models.run_test import RunTest
from simulate.services import eval_config_repair
from simulate.services.eval_config_repair import restore, strip_rows

MIGRATION_NAME = "0114_remove_evaluator_keys_from_eval_config"
migration_module = importlib.import_module(f"simulate.migrations.{MIGRATION_NAME}")

# Written out here, not imported, so a key dropped from either copy fails a test.
EVALUATOR_WRITTEN_KEYS = [
    "api_key", "provider", "model", "model_type", "rule_prompt", "system_prompt",
    "output_type", "messages", "few_shot_examples", "check_internet", "multi_choice",
    "choices", "choice_scores", "pass_threshold", "reverse_output", "knowledge_base_id",
    "agent_mode", "tools", "knowledge_bases", "data_injection", "summary",
    "organization_id", "workspace_id", "custom_eval", "param_modalities",
    "required_keys", "template_format",
]  # fmt: skip

EVERY_KEY = {key: f"leaked-{key}" for key in EVALUATOR_WRITTEN_KEYS}

NO_PARAMETER_LIST = {
    "eval_type_id": "CustomPromptEvaluator",
    "rule_prompt": "t",
    "output": "Pass/Fail",
}
CHECK_INTERNET_PARAMETER = {
    "eval_type_id": "AgentEvaluator",
    "config": {"check_internet": {"type": "boolean"}},
}

# (id, template config, nested dict as read, nested dict after, the copy kept)
# `None` for the copy means no copy row is written.
GOLDEN = [
    (
        "agent-leak-with-api-key",
        NO_PARAMETER_LIST,
        {
            "pass_threshold": 0.5,
            "model": "gpt-4o",
            "rule_prompt": "stale",
            "api_key": "sk-1",
            "mine": 1,
        },
        {"mine": 1},
        {"pass_threshold": 0.5, "model": "gpt-4o", "rule_prompt": "stale"},
    ),
    (
        "custom-prompt-before-pass-threshold-existed",
        NO_PARAMETER_LIST,
        {"model": "gpt-4o", "output_type": "Pass/Fail", "rule_prompt": "stale"},
        {},
        {"model": "gpt-4o", "output_type": "Pass/Fail", "rule_prompt": "stale"},
    ),
    (
        "custom-prompt-partial-leak-no-api-key",
        NO_PARAMETER_LIST,
        {"model": "gpt-4o", "output_type": "Pass/Fail", "provider": "openai"},
        {},
        {"model": "gpt-4o", "output_type": "Pass/Fail", "provider": "openai"},
    ),
    (
        "deterministic-model-and-provider",
        NO_PARAMETER_LIST,
        {"model": "m", "provider": "p", "choices": ["a"], "code": "return 1"},
        {"code": "return 1"},
        {"model": "m", "provider": "p", "choices": ["a"]},
    ),
    (
        "no-signal-only-api-key-goes",
        NO_PARAMETER_LIST,
        {"rule_prompt": "the caller's", "pass_threshold": 0.7, "api_key": "sk-2"},
        {"rule_prompt": "the caller's", "pass_threshold": 0.7},
        None,
    ),
    (
        "no-signal-nothing-changes",
        NO_PARAMETER_LIST,
        {"rule_prompt": "the caller's", "provider": "openai"},
        {"rule_prompt": "the caller's", "provider": "openai"},
        None,
    ),
    (
        "template-parameter-is-kept",
        CHECK_INTERNET_PARAMETER,
        {
            "pass_threshold": 0.5,
            "model": "m",
            "check_internet": True,
            "rule_prompt": "r",
        },
        {"check_internet": True},
        {"pass_threshold": 0.5, "model": "m", "rule_prompt": "r"},
    ),
    (
        "none-values-are-removed-not-copied",
        NO_PARAMETER_LIST,
        {"pass_threshold": 0.5, "model": "m", "rule_prompt": None},
        {},
        {"pass_threshold": 0.5, "model": "m"},
    ),
    (
        "every-written-key",
        NO_PARAMETER_LIST,
        {**EVERY_KEY, "code": "return 1"},
        {"code": "return 1"},
        {k: v for k, v in EVERY_KEY.items() if k != "api_key"},
    ),
    (
        "parameter-list-not-a-dict",
        {**NO_PARAMETER_LIST, "config": "not-a-dict"},
        {"pass_threshold": 0.5, "model": "m"},
        {},
        {"pass_threshold": 0.5, "model": "m"},
    ),
]

_historical_apps = None


def _migration_apps():
    """The historical app registry as of migration 0114, built once per module."""
    global _historical_apps
    if _historical_apps is None:
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(connection)
        _historical_apps = loader.project_state([("simulate", MIGRATION_NAME)]).apps
    return _historical_apps


class _ServiceCopy:
    module = eval_config_repair

    @staticmethod
    def run():
        return strip_rows(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)


class _MigrationCopy:
    module = migration_module

    @staticmethod
    def run():
        with connection.schema_editor(atomic=False) as editor:
            migration_module.forwards(_migration_apps(), editor)


@pytest.fixture(params=[_ServiceCopy, _MigrationCopy], ids=["service", "migration"])
def copy_under_test(request):
    return request.param


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
def run_test(db, organization, workspace, agent_definition):
    dataset = Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.SCENARIO.value,
    )
    scenario = Scenarios.objects.create(
        name="Test Scenario",
        description="Test scenario",
        source="Test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        agent_definition=agent_definition,
        status=StatusType.COMPLETED.value,
    )
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
def make_template(db, organization):
    def _make(config):
        return EvalTemplate.objects.create(
            name=f"strip-keys-eval-{uuid.uuid4().hex[:8]}",
            config=copy.deepcopy(config),
            organization=organization,
        )

    return _make


@pytest.fixture
def make_row(run_test, make_template):
    def _make(config, deleted=False, template_config=NO_PARAMETER_LIST, pk=None):
        fields = {"pk": pk} if pk is not None else {}
        return SimulateEvalConfig.objects.create(
            name="Attached eval",
            eval_template=make_template(template_config),
            run_test=run_test,
            config=config,
            deleted=deleted,
            **fields,
        )

    return _make


def _reload(row):
    return SimulateEvalConfig.all_objects.get(id=row.id)


def _copies(row):
    return list(SimulateEvalConfigRemovedKeys.objects.filter(eval_config_id=row.id))


@pytest.mark.unit
@pytest.mark.django_db
class TestGoldenTable:
    @pytest.mark.parametrize(
        "template_config, before, after, kept_copy",
        [case[1:] for case in GOLDEN],
        ids=[case[0] for case in GOLDEN],
    )
    def test_the_rule_matches_the_golden_table(
        self, copy_under_test, make_row, template_config, before, after, kept_copy
    ):
        row = make_row(
            {"config": copy.deepcopy(before)}, template_config=template_config
        )

        copy_under_test.run()

        assert _reload(row).config == {"config": after}
        copies = _copies(row)
        if kept_copy is None:
            assert copies == []
        else:
            assert [c.removed for c in copies] == [kept_copy]
            assert "api_key" not in copies[0].removed
            assert None not in copies[0].removed.values()


@pytest.mark.unit
@pytest.mark.django_db
class TestStrip:
    def test_cleans_soft_deleted_rows_without_touching_updated_at(
        self, copy_under_test, make_row
    ):
        leaked = {"pass_threshold": 0.5, "model": "m", "rule_prompt": "old"}
        rows = [
            make_row({"config": dict(leaked)}),
            make_row({"config": dict(leaked)}, deleted=True),
        ]
        stamps = [row.updated_at for row in rows]

        copy_under_test.run()

        assert [_reload(row).updated_at for row in rows] == stamps
        assert _reload(rows[1]).deleted is True
        for row in rows:
            assert _reload(row).config == {"config": {}}
            assert len(_copies(row)) == 1

    def test_keeps_keys_outside_the_set_and_all_top_level_keys(
        self, copy_under_test, make_row
    ):
        row = make_row(
            {
                "run_config": {"model": "m"},
                "rule_prompt": "top",
                "config": {
                    "model": "m",
                    "provider": "p",
                    "code": "return 1",
                    "mine": 1,
                },
            }
        )

        copy_under_test.run()

        assert _reload(row).config == {
            "run_config": {"model": "m"},
            "rule_prompt": "top",
            "config": {"code": "return 1", "mine": 1},
        }

    @pytest.mark.parametrize(
        "shape",
        [None, "x", "config", ["config"], {"run_config": {}}, {"config": "not-a-dict"}],
        ids=["null", "string", "string-matching-has-key", "array-matching-has-key",
             "no-nested-key", "nested-not-a-dict"],
    )  # fmt: skip
    def test_rows_without_a_nested_dict_are_untouched(
        self, copy_under_test, make_row, shape
    ):
        row = make_row(copy.deepcopy(shape))
        before = _reload(row)

        copy_under_test.run()

        after = _reload(row)
        assert after.config == shape
        assert after.updated_at == before.updated_at
        assert _copies(row) == []

    def test_skips_a_row_edited_while_it_runs(
        self, copy_under_test, make_row, monkeypatch
    ):
        row = make_row(
            {
                "config": {"pass_threshold": 0.5, "model": "m", "api_key": "k"},
                "run_config": {},
            }
        )
        module = copy_under_test.module
        real_read_page = module._read_page

        def read_then_someone_saves_an_edit(model, after_pk, using):
            page = real_read_page(model, after_pk, using)
            for read_row in page:
                SimulateEvalConfig.all_objects.filter(pk=read_row.pk).update(
                    config={**read_row.config, "run_config": {"model": "edited"}}
                )
            return page

        monkeypatch.setattr(module, "_read_page", read_then_someone_saves_an_edit)
        copy_under_test.run()

        after = _reload(row).config
        assert after["run_config"] == {"model": "edited"}
        assert "api_key" in after["config"]
        assert _copies(row) == []

        monkeypatch.setattr(module, "_read_page", real_read_page)
        copy_under_test.run()

        assert _reload(row).config == {"config": {}, "run_config": {"model": "edited"}}
        assert len(_copies(row)) == 1

    def test_reads_every_page_when_the_first_page_is_clean(
        self, copy_under_test, make_row, monkeypatch
    ):
        module = copy_under_test.module
        monkeypatch.setattr(module, "PAGE_SIZE", 2)
        clean = [
            make_row({"config": {"code": "return 1"}}, pk=uuid.UUID(int=k))
            for k in (1, 2)
        ]
        dirty = [
            make_row({"config": {"api_key": "k"}}, pk=uuid.UUID(int=k))
            for k in (3, 4, 5)
        ]

        copy_under_test.run()

        assert [_reload(r).config for r in clean] == [
            {"config": {"code": "return 1"}}
        ] * 2
        assert [_reload(r).config for r in dirty] == [{"config": {}}] * 3

    def test_a_second_run_changes_nothing_and_adds_no_second_copy(
        self, copy_under_test, make_row
    ):
        row = make_row(
            {"config": {"pass_threshold": 0.5, "model": "m", "rule_prompt": "x"}}
        )

        copy_under_test.run()
        first = (_reload(row).config, [c.pk for c in _copies(row)])
        copy_under_test.run()

        assert (_reload(row).config, [c.pk for c in _copies(row)]) == first

    def test_service_counts(self, make_row):
        make_row({"config": {"api_key": "k"}})
        make_row({"config": {"code": "x"}})
        make_row({"run_config": {}})

        assert strip_rows(SimulateEvalConfig, SimulateEvalConfigRemovedKeys) == (2, 1)
        assert strip_rows(SimulateEvalConfig, SimulateEvalConfigRemovedKeys) == (2, 0)

    def test_dry_run_counts_and_writes_nothing(self, make_row):
        row = make_row({"config": {"api_key": "k"}})

        counts = strip_rows(
            SimulateEvalConfig, SimulateEvalConfigRemovedKeys, dry_run=True
        )

        assert counts == (1, 1)
        assert _reload(row).config == {"config": {"api_key": "k"}}

    def test_a_restored_row_is_not_stripped_again(self, make_row):
        row = make_row(
            {"config": {"pass_threshold": 0.5, "model": "m", "rule_prompt": "mine"}}
        )
        strip_rows(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)
        restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)
        after_restore = _reload(row).config

        # Something wrote the signal back, as an old pod would.
        SimulateEvalConfig.all_objects.filter(pk=row.pk).update(
            config={
                "config": {
                    **after_restore["config"],
                    "pass_threshold": 0.5,
                    "model": "m",
                }
            }
        )
        before_strip = _reload(row).config
        counts = strip_rows(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert counts == (1, 0)
        assert _reload(row).config == before_strip


@pytest.mark.unit
@pytest.mark.django_db
class TestRestore:
    def _stripped(self, make_row, nested):
        row = make_row({"config": nested})
        strip_rows(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)
        return row

    def test_honoured_keys_come_back_and_derived_keys_and_the_signal_do_not(
        self, make_row
    ):
        row = self._stripped(
            make_row,
            {
                "pass_threshold": 0.5,
                "model": "m",
                "provider": "p",
                "output_type": "o",
                "api_key": "k",
                "rule_prompt": "text",
                "choices": ["a"],
            },
        )

        report = restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert report == {"restored": [row.id], "missing": [], "raced": []}
        assert _reload(row).config == {
            "config": {"rule_prompt": "text", "choices": ["a"]}
        }
        [copy_row] = _copies(row)
        assert copy_row.restored_at is not None
        assert copy_row.restored_keys == ["choices", "rule_prompt"]

    def test_a_key_the_row_holds_now_is_never_overwritten(self, make_row):
        row = self._stripped(
            make_row, {"pass_threshold": 0.5, "model": "m", "rule_prompt": "old"}
        )
        SimulateEvalConfig.all_objects.filter(pk=row.pk).update(
            config={"config": {"rule_prompt": "new"}}
        )

        restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert _reload(row).config == {"config": {"rule_prompt": "new"}}

    def test_copies_are_applied_oldest_first(self, make_row):
        row = make_row({"config": {}})
        now = timezone.now()
        for text, age in (("newer", 1), ("older", 5)):
            made = SimulateEvalConfigRemovedKeys.objects.create(
                eval_config_id=row.id, removed={"rule_prompt": text}
            )
            SimulateEvalConfigRemovedKeys.objects.filter(pk=made.pk).update(
                created_at=now - timedelta(days=age)
            )

        restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert _reload(row).config == {"config": {"rule_prompt": "older"}}

    def test_keys_narrow_the_set_and_cannot_widen_it(self, make_row):
        row = self._stripped(
            make_row,
            {"pass_threshold": 0.5, "model": "m", "rule_prompt": "t", "choices": ["a"]},
        )

        restore(
            SimulateEvalConfig,
            SimulateEvalConfigRemovedKeys,
            keys=["rule_prompt", "model"],
        )

        assert _reload(row).config == {"config": {"rule_prompt": "t"}}

    def test_a_missing_row_is_reported(self, make_row):
        gone = uuid.uuid4()
        SimulateEvalConfigRemovedKeys.objects.create(
            eval_config_id=gone, removed={"rule_prompt": "x"}
        )

        report = restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert report == {"restored": [], "missing": [gone], "raced": []}

    def test_a_soft_deleted_row_is_restored_and_a_missing_nested_dict_created(
        self, make_row
    ):
        row = make_row({"run_config": {}}, deleted=True)
        SimulateEvalConfigRemovedKeys.objects.create(
            eval_config_id=row.id, removed={"rule_prompt": "x"}
        )

        restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys, ids=[row.id])

        assert _reload(row).config == {"run_config": {}, "config": {"rule_prompt": "x"}}

    def test_a_lost_race_is_reported_and_can_be_retried(self, make_row, monkeypatch):
        row = self._stripped(
            make_row, {"pass_threshold": 0.5, "model": "m", "rule_prompt": "t"}
        )
        real_update = QuerySet.update
        edited = []

        def edit_before_the_write(queryset, **kwargs):
            if "config" in kwargs and not edited:
                edited.append(1)
                real_update(
                    SimulateEvalConfig.all_objects.filter(pk=row.pk),
                    config={"config": {}, "run_config": {"x": 1}},
                )
            return real_update(queryset, **kwargs)

        monkeypatch.setattr(QuerySet, "update", edit_before_the_write)
        first = restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)
        monkeypatch.undo()
        second = restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert first["raced"] == [row.id]
        assert second["restored"] == [row.id]
        assert _reload(row).config["config"] == {"rule_prompt": "t"}

    def test_restore_then_strip_leaves_the_row_unchanged(self, make_row):
        row = self._stripped(
            make_row, {"pass_threshold": 0.5, "model": "m", "rule_prompt": "t"}
        )
        restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)
        restored = _reload(row).config

        strip_rows(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert _reload(row).config == restored == {"config": {"rule_prompt": "t"}}

    def test_a_restored_row_never_carries_the_signal(self, make_row):
        row = self._stripped(
            make_row, {"pass_threshold": 0.5, "model": "m", "rule_prompt": "t"}
        )
        restore(SimulateEvalConfig, SimulateEvalConfigRemovedKeys)

        assert not eval_config_repair.has_leak_signal(_reload(row).config["config"])


@pytest.mark.unit
@pytest.mark.django_db
class TestCommand:
    def test_strip_dry_run_and_restore(self, make_row):
        row = make_row(
            {"config": {"pass_threshold": 0.5, "model": "m", "rule_prompt": "t"}}
        )
        out = StringIO()

        call_command("strip_eval_config_evaluator_keys", "--dry-run", stdout=out)
        assert out.getvalue().split() == ["scanned=1", "changed=1"]
        assert _reload(row).config["config"]["model"] == "m"

        out = StringIO()
        call_command("strip_eval_config_evaluator_keys", stdout=out)
        assert out.getvalue().split() == ["scanned=1", "changed=1"]
        assert _reload(row).config == {"config": {}}

        out = StringIO()
        call_command(
            "strip_eval_config_evaluator_keys", "--restore", str(row.id), stdout=out
        )
        assert out.getvalue().split() == ["restored=1", "missing=0", "raced=0"]
        assert _reload(row).config == {"config": {"rule_prompt": "t"}}

    def test_restore_all_with_keys(self, make_row):
        row = make_row(
            {
                "config": {
                    "pass_threshold": 0.5,
                    "model": "m",
                    "rule_prompt": "t",
                    "choices": ["a"],
                }
            }
        )
        call_command("strip_eval_config_evaluator_keys", stdout=StringIO())

        call_command(
            "strip_eval_config_evaluator_keys",
            "--restore-all",
            "--keys",
            "choices",
            stdout=StringIO(),
        )

        assert _reload(row).config == {"config": {"choices": ["a"]}}


@pytest.mark.unit
class TestMigrationStandsAlone:
    def test_imports_no_live_simulate_code(self):
        tree = ast.parse(inspect.getsource(migration_module))
        imported = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        ] + [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]

        assert imported
        assert not [name for name in imported if name.split(".")[0] == "simulate"]

    def test_runs_outside_one_transaction(self):
        assert migration_module.Migration.atomic is False
        [operation] = migration_module.Migration.operations
        assert operation.atomic is False

    def test_follows_the_copy_table_migration(self):
        assert migration_module.Migration.dependencies == [
            ("simulate", "0113_simulate_eval_config_removed_keys")
        ]


@pytest.mark.unit
@pytest.mark.django_db
def test_the_models_need_no_further_simulate_migration():
    """The state the migrations build matches the models (makemigrations --check, simulate only)."""
    from django.apps import apps
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.state import ProjectState

    with override_settings(MIGRATION_MODULES={}):
        loader = MigrationLoader(connection)
    autodetector = MigrationAutodetector(
        loader.project_state(), ProjectState.from_apps(apps)
    )
    changes = autodetector.changes(graph=loader.graph, trim_to_apps={"simulate"})

    assert changes == {}
