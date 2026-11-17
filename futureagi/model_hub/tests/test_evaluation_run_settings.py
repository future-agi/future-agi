"""Evaluations keep only their run settings.

A binding's settings (the ``config`` of a dataset, simulation, tracing or
external evaluation) are run settings. Every place that returns them gives
the run settings only: credential fields (``evaluations.run_settings``) are
left out at any depth, and every other field, the names the user chose
included, is returned as stored. The evaluation engine prepares an
evaluator's settings on a copy, so the settings a caller hands it are left as
they were given.

The census below lists every place that returns these settings. A census
test fails when a new one appears without a row. Every value here is
synthetic.
"""

import ast
import copy
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.urls import reverse

from evaluations.engine import instance
from evaluations.engine.registry import get_eval_class
from evaluations.engine.tests.test_version_model_needs_its_own_key import (
    OTHER_MODEL,
    STORED_KEY,
    make_template,
    transports,
)
from model_hub.models.api_key import ApiKey
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from simulate.models.eval_config import SimulateEvalConfig
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.external_eval_config import ExternalEvalConfig

FUTUREAGI_ROOT = Path(__file__).resolve().parents[2]

STORED_SETTINGS_VALUE = "sk-test-SYNTHETIC-STALE-0003"
PRIVATE_KEY = "-----SYNTHETIC-----"
AWS_SECRET = "aws-test-SYNTHETIC"
CONNECTION_KEY = "sk-test-SYNTHETIC-CONNECTION-0004"
CLIENT_SECRET = "synthetic-client-secret-0005"
ENDPOINT_KEY = "sk-test-SYNTHETIC-ENDPOINT-0006"
ENDPOINT_BASE = "https://judge.synthetic.test/v1"
SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "synthetic-run-settings-project",
    "private_key_id": "synthetic-run-settings-key-id-0003",
    "private_key": PRIVATE_KEY,
    "client_email": "judge@synthetic-run-settings-project.iam.gserviceaccount.com",
}
LEFT_OUT = [
    STORED_SETTINGS_VALUE,
    PRIVATE_KEY,
    AWS_SECRET,
    CONNECTION_KEY,
    CLIENT_SECRET,
    ENDPOINT_KEY,
    SERVICE_ACCOUNT["private_key_id"],
    SERVICE_ACCOUNT["client_email"],
]


def stored_settings():
    """Binding settings holding run settings and, at several depths, fields
    that are not run settings."""
    return {
        "api_key": STORED_SETTINGS_VALUE,
        "provider": "openai",
        "reason_column": True,
        # Template variable names and choice labels are the user's own.
        "mapping": {"output": "column-1", "key": "column-2"},
        "choice_scores": {"key": 1.0, "other": 0.0},
        "params": {
            # An ordinary name: params holds no credential field.
            "threshold": 0.5,
            "key": "label",
            "connection": {
                "key": CONNECTION_KEY,
                "client_secret": CLIENT_SECRET,
                "aws_secret_access_key": AWS_SECRET,
            },
        },
        "run_config": {
            "pass_threshold": 0.7,
            "tools": {"search": {"key": "web"}},
            "knowledge_bases": [{"id": "kb-1", "api_key": STORED_SETTINGS_VALUE}],
            "credentials": dict(SERVICE_ACCOUNT),
            "endpoint": {"key": ENDPOINT_KEY, "api_base": ENDPOINT_BASE},
        },
        "config": {
            "check_internet": False,
            "provider": "vertex_ai",
            "api_key": dict(SERVICE_ACCOUNT),
            "vertex_credentials": json.dumps(SERVICE_ACCOUNT),
        },
    }


# stored_settings() as every census place returns it.
RUN_SETTINGS = {
    "provider": "openai",
    "reason_column": True,
    "mapping": {"output": "column-1", "key": "column-2"},
    "choice_scores": {"key": 1.0, "other": 0.0},
    "params": {"threshold": 0.5, "key": "label", "connection": {}},
    "run_config": {
        "pass_threshold": 0.7,
        "tools": {"search": {"key": "web"}},
        "knowledge_bases": [{"id": "kb-1"}],
        "endpoint": {"api_base": ENDPOINT_BASE},
    },
    "config": {"check_internet": False, "provider": "vertex_ai"},
}


def assert_holds_none_of(value, values=LEFT_OUT):
    text = json.dumps(value, default=str)
    for left_out in values:
        assert left_out not in text


# ---------------------------------------------------------------------------
# The shared helper


def test_run_settings_leave_out_credential_fields_at_any_depth():
    from evaluations.run_settings import run_settings

    settings = stored_settings()
    kept = run_settings(settings)

    assert kept == RUN_SETTINGS
    assert_holds_none_of(kept)
    # The value handed in is left as it was.
    assert settings == stored_settings()


def test_run_settings_leave_out_the_key_field_of_a_credential_object():
    """'key' is an ordinary name, except in an object that also holds a
    credential field or a credential configuration's field: there it is left
    out with them."""
    from evaluations.run_settings import run_settings

    assert run_settings({"key": "label", "threshold": 0.5}) == {
        "key": "label",
        "threshold": 0.5,
    }
    assert run_settings(
        {
            "key": STORED_SETTINGS_VALUE,
            "aws_secret_access_key": AWS_SECRET,
            "threshold": 0.5,
        }
    ) == {"threshold": 0.5}
    assert run_settings({"key": ENDPOINT_KEY, "api_base": ENDPOINT_BASE}) == {
        "api_base": ENDPOINT_BASE
    }


def test_run_settings_keep_the_names_the_user_chose():
    """A template variable or a choice label keeps its name, whatever it is;
    the values under it are filtered like any other."""
    from evaluations.run_settings import run_settings

    settings = {
        "mapping": {
            "api_key": "column-1",
            "credentials": {"name": "column-2", "private_key": PRIVATE_KEY},
        },
        "choice_scores": {"service_account": 1.0, "key": 0.0},
        "label": {"key": "kept"},
    }

    assert run_settings(settings) == {
        "mapping": {"api_key": "column-1", "credentials": {"name": "column-2"}},
        "choice_scores": {"service_account": 1.0, "key": 0.0},
        "label": {"key": "kept"},
    }


def test_run_settings_return_other_values_as_they_are():
    from evaluations.run_settings import run_settings

    for value in (None, "text", 3, 0.5, True):
        assert run_settings(value) == value
    assert run_settings([{"api_key": STORED_SETTINGS_VALUE, "id": 1}]) == [{"id": 1}]


# ---------------------------------------------------------------------------
# Census: every place that returns a binding's settings.

BINDING_MODELS = (
    UserEvalMetric,
    SimulateEvalConfig,
    CustomEvalConfig,
    ExternalEvalConfig,
)
BINDING_MODEL_NAMES = {model.__name__ for model in BINDING_MODELS}

# Serializers whose fields include a binding's settings.
SERIALIZER_CENSUS = {
    "model_hub.serializers.develop_optimisation.UserEvalMetricSerializer",
    "simulate.serializers.run_test.SimulateEvalConfigSimpleSerializer",
    # Inherits SimulateEvalConfigSimpleSerializer's representation.
    "simulate.serializers.response.run_test_evals.EvalConfigResponseSerializer",
    "tracer.serializers.custom_eval_config.CustomEvalConfigSerializer",
}
SERIALIZER_DIRECTORIES = (
    "model_hub/serializers",
    "simulate/serializers",
    "tracer/serializers",
    "sdk/serializers",
)
# A Meta value that is not a literal: counted as including the settings.
NOT_A_LITERAL = "<not a literal>"


def _last_name(node):
    """``Name`` of a name, or the last name of a dotted name; else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _meta_literal(node):
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return NOT_A_LITERAL
    if isinstance(value, str):
        return value
    try:
        return frozenset(value)
    except TypeError:
        return NOT_A_LITERAL


def _own_meta(class_node):
    """A class's own Meta: its model, fields and exclude as written, and the
    classes whose Meta it extends (``class Meta(Parent.Meta)``); else None."""
    for item in class_node.body:
        if isinstance(item, ast.ClassDef) and item.name == "Meta":
            meta = {
                "parents": [
                    _last_name(base.value)
                    for base in item.bases
                    if isinstance(base, ast.Attribute)
                ]
            }
            for statement in item.body:
                if not isinstance(statement, ast.Assign):
                    continue
                for target in statement.targets:
                    if isinstance(target, ast.Name) and target.id == "model":
                        meta["model"] = _last_name(statement.value)
                    elif isinstance(target, ast.Name) and target.id in (
                        "fields",
                        "exclude",
                    ):
                        meta[target.id] = _meta_literal(statement.value)
            return meta
    return None


def _serializer_classes():
    """Every top-level class of the serializer packages, read from source
    (nothing is imported): {class name: [(path, base names, own Meta)]}."""
    classes = {}
    for directory in SERIALIZER_DIRECTORIES:
        for path in sorted((FUTUREAGI_ROOT / directory).rglob("*.py")):
            module = path.relative_to(FUTUREAGI_ROOT).with_suffix("").as_posix()
            module = module.replace("/", ".").removesuffix(".__init__")
            for node in ast.parse(path.read_text()).body:
                if isinstance(node, ast.ClassDef):
                    classes.setdefault(node.name, []).append(
                        (
                            f"{module}.{node.name}",
                            [_last_name(base) for base in node.bases],
                            _own_meta(node),
                        )
                    )
    return classes


def _meta_values(record, name, classes, seen=frozenset()):
    """Every value Meta ``name`` of a class may take: its own Meta's, else
    that of the Meta it extends, or (with no Meta of its own) that of each
    base class of that name."""
    path, bases, meta = record
    if path in seen:
        return []
    if meta is not None and name in meta:
        return [meta[name]]
    parents = meta["parents"] if meta is not None else bases
    values = []
    for parent in parents:
        for parent_record in classes.get(parent, ()):
            values += _meta_values(parent_record, name, classes, seen | {path})
    return values


def _includes_the_settings(fields, exclude):
    if fields in (NOT_A_LITERAL, "__all__"):
        return True
    if fields is None:
        return exclude is None or exclude == NOT_A_LITERAL or "config" not in exclude
    return "config" in fields


def _binding_settings_serializers():
    classes = _serializer_classes()
    found = set()
    for records in classes.values():
        for record in records:
            models = set(_meta_values(record, "model", classes))
            if not models & BINDING_MODEL_NAMES:
                continue
            fields = _meta_values(record, "fields", classes) or [None]
            exclude = _meta_values(record, "exclude", classes) or [None]
            if any(_includes_the_settings(f, e) for f in fields for e in exclude):
                found.add(record[0])
    return found


def test_every_serializer_of_binding_settings_is_in_the_census():
    """Read from source, so nothing is imported: every serializer whose Meta
    (its own, one it extends or one it inherits) names a binding model and
    whose fields include its settings is a census row, and every row is
    found."""
    found = _binding_settings_serializers()

    assert sorted(found - SERIALIZER_CENSUS) == []
    assert sorted(SERIALIZER_CENSUS - found) == []


# Every entry built from an object's ``.config`` in product code: under
# "config", "params" or "run_config" whatever part of it is read, and under
# any other key when it holds the whole ``.config``. A binding's settings go
# through run_settings (RETURNED); the other rows are not a binding's
# settings, or never leave the server (each says why).
DICT_CENSUS = {
    # RETURNED: through run_settings.
    ("ee/agenthub/cluster_rca/agent.py", "cfg"): "RETURNED",
    ("model_hub/serializers/experiments.py", "m"): "RETURNED",
    # An experiment run's evaluator pin keeps the binding's run settings;
    # ExternalRunBindingSerializer and ExperimentRunSerializer return them.
    ("model_hub/services/experiment_run_creation.py", "metric"): "RETURNED",
    ("model_hub/utils/eval_list.py", "user_eval"): "RETURNED",
    ("model_hub/views/develop_dataset.py", "evaluation"): "RETURNED",
    ("model_hub/views/develop_dataset.py", "eval"): "RETURNED",
    ("simulate/utils/agent_optimiser.py", "config"): "RETURNED",
    ("tracer/views/eval_task.py", "eval_config"): "RETURNED",
    ("tracer/views/replay_session.py", "config"): "RETURNED",
    # A digest of the binding; the settings themselves are not sent.
    ("model_hub/services/native_run_binding.py", "metric"): "DIGEST",
    # Copied into the new dataset's own binding (returned through the rows
    # above).
    (
        "model_hub/views/datasets/create/dataset_from_experiment.py",
        "source_eval_metric",
    ): "COPIED",
    # Adding as a new dataset copies each evaluation into the new dataset.
    ("model_hub/views/develop_dataset.py", "source_binding"): "COPIED",
    # The workflow that runs the evaluation reads the settings it runs with.
    ("tfc/temporal/agent_prompt_optimiser/eval_activities.py", "eval_config"): "RUN",
    ("tfc/temporal/agent_prompt_optimiser/workflows.py", "ec"): "RUN",
    # Not a binding's settings.
    ("model_hub/views/develop_dataset.py", "template"): "TEMPLATE",
    ("model_hub/views/prompt_template.py", "eval_template"): "TEMPLATE",
    ("sdk/views/evaluations.py", "template"): "TEMPLATE",
    ("simulate/services/alk_simulate_ingestion.py", "template"): "TEMPLATE",
    ("simulate/utils/agent_optimiser.py", "template"): "TEMPLATE",
    ("tfc/temporal/agent_prompt_optimiser/eval_activities.py", "eval_template"): (
        "TEMPLATE"
    ),
    ("tracer/services/dashboard_metrics_catalog.py", "tmpl"): "TEMPLATE",
    ("model_hub/views/separate_evals.py", "link"): "COMPOSITE_CHILD",
    ("tracer/services/eval_tasks/config_hash.py", "link"): "COMPOSITE_CHILD",
    ("model_hub/views/run_prompt.py", "tool"): "TOOL",
    ("model_hub/views/run_prompt.py", "empty_tool"): "TOOL",
    ("tfc/temporal/agent_playground/workflows.py", "sink"): "SINK",
    ("tfc/temporal/agent_playground/workflows.py", "s"): "SINK",
    ("tracer/views/shared_link.py", "project"): "PROJECT",
}
SCANNED = ("model_hub", "simulate", "tracer", "sdk", "ee", "tfc", "evaluations")
SETTINGS_KEYS = {"config", "params", "run_config"}
# An entry may span lines (the formatter wraps long ones).
SETTINGS_ENTRY = re.compile(
    r"""["'](?P<key>[A-Za-z_][A-Za-z_0-9]*)["']\s*:\s*\(?\s*"""
    r"""(?P<filtered>run_settings\(\s*\(?\s*)?"""
    r"""(?P<name>[A-Za-z_][A-Za-z_0-9]*)\.config\b"""
    # A part of it: an attribute, item or call of ``.config`` or of
    # ``(<name>.config or {})``.
    r"""(?P<part>\s*[.\[(]|\s+or\s+\{\}\s*\)\s*[.\[])?"""
)


def _product_files():
    for top in SCANNED:
        for path in sorted((FUTUREAGI_ROOT / top).rglob("*.py")):
            relative = path.relative_to(FUTUREAGI_ROOT).as_posix()
            parts = relative.split("/")
            if "tests" in parts or "migrations" in parts:
                continue
            if parts[-1].startswith("test_") or parts[-1] == "conftest.py":
                continue
            yield relative, path


def _settings_entries():
    for relative, path in _product_files():
        text = path.read_text()
        for match in SETTINGS_ENTRY.finditer(text):
            if match.group("key") not in SETTINGS_KEYS and match.group("part"):
                continue
            number = text.count("\n", 0, match.start()) + 1
            yield relative, number, match


def test_every_returned_binding_settings_entry_is_in_the_census():
    unknown = []
    unfiltered = []
    for relative, number, match in _settings_entries():
        row = DICT_CENSUS.get((relative, match.group("name")))
        entry = f"{relative}:{number}: {' '.join(match.group(0).split())}"
        if row is None:
            unknown.append(entry)
        elif row == "RETURNED" and not match.group("filtered"):
            unfiltered.append(entry)
    assert unknown == []
    assert unfiltered == []


def _template():
    return EvalTemplate(
        name=f"run-settings-{uuid.uuid4().hex[:8]}",
        owner="user",
        eval_type="llm",
        criteria="Is {{output}} correct?",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "output": "Pass/Fail",
            "required_keys": ["output"],
        },
    )


def _dataset_evaluation_settings():
    from model_hub.serializers.develop_optimisation import UserEvalMetricSerializer

    binding = UserEvalMetric(
        name="dataset evaluation",
        template=_template(),
        config=stored_settings(),
    )
    with patch.object(UserEvalMetricSerializer, "get_eval_group", return_value=None):
        return UserEvalMetricSerializer(binding).data["config"]


def _simulation_evaluation_settings():
    from simulate.serializers.run_test import SimulateEvalConfigSimpleSerializer

    binding = SimulateEvalConfig(
        name="simulation evaluation",
        eval_template=_template(),
        config=stored_settings(),
        mapping={},
    )
    with (
        patch.object(
            SimulateEvalConfigSimpleSerializer, "get_eval_group", return_value=None
        ),
        patch.object(
            SimulateEvalConfigSimpleSerializer, "get_regradable", return_value=True
        ),
        patch.object(
            SimulateEvalConfigSimpleSerializer, "get_editable", return_value=True
        ),
    ):
        return SimulateEvalConfigSimpleSerializer(binding).data["config"]


def _tracing_evaluation_settings():
    from tracer.serializers.custom_eval_config import CustomEvalConfigSerializer

    binding = CustomEvalConfig(
        name="tracing evaluation",
        eval_template=_template(),
        config=stored_settings(),
        mapping={},
    )
    with patch.object(CustomEvalConfigSerializer, "get_eval_group", return_value=None):
        return CustomEvalConfigSerializer(binding).data["config"]


def _optimizer_evaluation_settings():
    from simulate.utils.agent_optimiser import _build_evaluation_data

    template = _template()
    # No inputs to look up on the call: only the settings are under test.
    template.config = {**template.config, "required_keys": []}
    binding = SimpleNamespace(
        id=uuid.uuid4(),
        name="simulation evaluation",
        eval_template=template,
        mapping={},
        config=stored_settings(),
        model=None,
    )
    call = SimpleNamespace(call_metadata={}, eval_outputs={})
    return _build_evaluation_data(binding, call)["config"]


RETURNED_SETTINGS = {
    "dataset-evaluation-serializer": _dataset_evaluation_settings,
    "simulation-evaluation-serializer": _simulation_evaluation_settings,
    "tracing-evaluation-serializer": _tracing_evaluation_settings,
    "simulation-optimizer-evaluation-data": _optimizer_evaluation_settings,
}


@pytest.mark.django_db
@pytest.mark.parametrize("place", sorted(RETURNED_SETTINGS))
def test_returned_binding_settings_hold_only_run_settings(place):
    returned = RETURNED_SETTINGS[place]()

    assert_holds_none_of(returned)
    assert returned == RUN_SETTINGS


@pytest.mark.django_db
def test_the_evaluation_column_settings_hold_only_run_settings(
    organization, workspace, user
):
    """A dataset evaluation column's settings, as the column config view
    returns them."""
    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Column, Dataset
    from model_hub.views.develop_dataset import ColumnConfigView

    template = make_template(organization, workspace, model=OTHER_MODEL)
    dataset = Dataset.no_workspace_objects.create(
        name=f"run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
    )
    binding = UserEvalMetric.objects.create(
        name=f"run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        user=user,
        model=OTHER_MODEL,
        config=stored_settings(),
    )
    column = Column.no_workspace_objects.create(
        name="evaluation",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(binding.id),
    )

    returned = ColumnConfigView.get_evaluation_config(ColumnConfigView(), column)

    assert_holds_none_of(returned)
    assert returned["config"] == RUN_SETTINGS
    binding.refresh_from_db()
    assert binding.config == stored_settings()


@pytest.mark.django_db
@pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")
def test_an_experiment_run_returns_its_evaluator_bindings_run_settings(
    auth_client, organization, workspace, user
):
    """An external experiment run pins each evaluator binding it is created
    with. Creating the run and reading it return each binding's run
    settings."""
    from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
    from model_hub.models.develop_dataset import Dataset
    from model_hub.models.evals_metric import EvalTemplateVersion

    dataset = Dataset.no_workspace_objects.create(
        name=f"run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    example = DatasetVersionItem.no_workspace_objects.create(
        dataset_version=version, input={"case": 1}, expected={"answer": 1}
    )
    template = EvalTemplate.no_workspace_objects.create(
        name=f"run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner="user",
        eval_type="code",
    )
    EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=1,
        is_default=True,
        config_snapshot={"threshold": 0.5},
        organization=organization,
        workspace=workspace,
    )
    binding = UserEvalMetric.no_workspace_objects.create(
        name=f"run-settings-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        template=template,
        dataset=dataset,
        user=user,
        config=stored_settings(),
    )
    run_id = str(uuid.uuid4())

    created = auth_client.post(
        reverse("external-experiment-run-create"),
        {
            "run_id": run_id,
            "dataset_version_id": str(version.id),
            "expected_items": [
                {"example_id": str(example.example_id), "trial_number": 1}
            ],
            "evaluator_metric_ids": [str(binding.id)],
            "model_config": {"provider": "fixture", "model": "deterministic"},
            "metadata": {"code_revision": "fixture-v1"},
        },
        format="json",
    )
    read = auth_client.get(reverse("experiment-run-detail", kwargs={"run_id": run_id}))

    for response in (created, read):
        assert response.status_code == 200, response.json()
        assert_holds_none_of(response.json())
        (returned,) = response.json()["result"]["evaluator_bindings"]
        assert returned["metric_id"] == str(binding.id)
        assert returned["binding_config"] == RUN_SETTINGS


# ---------------------------------------------------------------------------
# The engine prepares on a copy.


@pytest.mark.django_db
def test_the_engine_prepares_the_evaluator_on_a_copy_of_the_settings(
    organization, workspace
):
    """The settings a caller hands the engine (a binding's settings, saved
    again after a simulation run) are left as they were given; the evaluator
    still gets the model and its settings."""
    template = make_template(organization, workspace, model=OTHER_MODEL)
    ApiKey.objects.create(
        organization=organization,
        workspace=workspace,
        provider="openai",
        key=STORED_KEY,
    )
    settings = {"check_internet": False}
    given = copy.deepcopy(settings)

    with transports():
        evaluator, _criteria = instance.create_eval_instance(
            eval_class=get_eval_class("CustomPromptEvaluator"),
            eval_template=template,
            config=settings,
            model=OTHER_MODEL,
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )

    assert evaluator._model == OTHER_MODEL
    # The stored key was found for the model.
    assert evaluator.api_key
    assert settings == given


@pytest.mark.parametrize(
    "binding_flag, template_flag, override, selected, ordinary, shared",
    [
        (False, True, False, False, True, False),
        (True, True, False, False, True, True),
        (False, False, True, True, False, True),
        (False, True, None, True, True, False),
        (True, False, None, True, True, True),
        (False, False, None, False, False, False),
        (True, False, False, False, True, True),
        (False, False, False, False, False, False),
    ],
)
def test_localization_override_is_scoped_to_selected_dataset_rows(
    binding_flag, template_flag, override, selected, ordinary, shared
):
    from model_hub.services.error_localizer_service import error_localizer_enabled
    from model_hub.utils.eval_list import build_run_config_view
    from model_hub.views.eval_runner import EvaluationRunner

    run_config = {} if override is None else {"error_localizer_enabled": override}
    binding = SimpleNamespace(
        error_localizer=binding_flag,
        config={"run_config": run_config},
        template=SimpleNamespace(error_localizer_enabled=template_flag),
    )
    before = copy.deepcopy(binding.config)
    runner = EvaluationRunner.__new__(EvaluationRunner)
    runner.user_eval_metric = binding

    assert runner._error_localizer_enabled(selected_row=True) is selected
    assert runner._error_localizer_enabled() is ordinary
    assert error_localizer_enabled(binding) is shared
    assert build_run_config_view(binding)["error_localizer_enabled"] is shared
    assert binding.config == before
