import ast
import json
import sys
import uuid
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.serializers.run_prompt import (
    AddRunPromptSerializer,
    EditRunPromptColumnSerializer,
    PreviewRunPromptSerializer,
)
from model_hub.views import dynamic_columns as dynamic_columns_module
from model_hub.views import run_prompt as run_prompt_module
from model_hub.views.run_prompt import (
    JsonStr,
    PromptTemplateSyntaxError,
    RunPrompts,
    UnresolvedPromptPlaceholdersError,
    merge_run_prompt_config,
    normalize_public_template_format,
    normalize_run_prompt_config,
    populate_placeholders,
    process_text_with_media,
    render_template,
)
from model_hub.views.utils.utils import sanitize_uuid_for_jinja
from tfc.constants.api_calls import APICallStatusChoices


def test_experiment_run_prompt_placeholder_calls_are_fail_closed():
    source = (
        Path(__file__).resolve().parents[1] / "views" / "experiment_runner.py"
    ).read_text()
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "populate_placeholders"
    ]

    assert calls, "experiment runner should render run-prompt placeholders"
    for call in calls:
        fail_closed = {kw.arg: kw.value for kw in call.keywords}.get("fail_closed")
        assert isinstance(fail_closed, ast.Constant)
        assert fail_closed.value is True


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Placeholder Test Organization")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="placeholder-tests@example.com",
        password="testpassword123",
        name="Placeholder Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Placeholder Test Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def dataset(db, organization, workspace):
    dataset = Dataset.objects.create(
        name="Prompt Placeholder Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
        column_order=[],
    )
    return dataset


@pytest.fixture
def input_column(dataset):
    column = Column.objects.create(
        name="Input Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(column.id))
    dataset.save(update_fields=["column_order"])
    return column


@pytest.fixture
def output_column(dataset):
    return Column.objects.create(
        name="Output Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )


@pytest.fixture
def row(dataset):
    return Row.objects.create(dataset=dataset, order=0)


@pytest.fixture
def cell(dataset, input_column, row):
    return Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=row,
        value="Resolved input value",
    )


def _message(content: str) -> list[dict]:
    return [{"role": "user", "content": content}]


class ExplodingItemsMapping(Mapping):
    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def items(self):
        raise AssertionError("unused mapping should not be recursively wrapped")


def test_render_template_default_non_strict_keeps_blank_missing_placeholder():
    assert render_template("Answer {{missing_column}}", {}) == "Answer "


def test_render_template_strict_raises_for_unknown_jinja_default_filter_root():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "Answer {{ missing_column | default('fallback') }}",
            {},
            strict=True,
        )

    assert "missing_column" in str(exc_info.value)


def test_render_template_strict_preserves_known_jinja_default_filter_root():
    assert (
        render_template(
            "Answer {{ missing_column | default('fallback', true) }}",
            {"missing_column": ""},
            strict=True,
        )
        == "Answer fallback"
    )


def test_extract_jinja_variable_expressions_returns_empty_tuple_for_empty_input():
    assert run_prompt_module._extract_jinja_variable_expressions("") == ()


def test_normalize_run_prompt_config_preserves_legacy_template_format():
    assert normalize_run_prompt_config(
        {
            "configuration": {"template_format": "jinja"},
            "run_prompt_config": {"temperature": 0.1},
        }
    ) == {"temperature": 0.1, "template_format": "jinja"}


def test_normalize_public_template_format_keeps_frontend_contract_alias():
    assert normalize_public_template_format("jinja2") == "jinja"
    assert normalize_public_template_format("mustache") == "mustache"


def test_merge_run_prompt_config_preserves_existing_when_edit_omits_config():
    existing_config = {
        "template_format": "mustache",
        "temperature": 0.2,
        "reasoning": {"effort": "medium"},
    }

    assert merge_run_prompt_config(existing_config, {"messages": []}) == existing_config


def test_merge_run_prompt_config_overlays_current_and_legacy_edit_config():
    assert merge_run_prompt_config(
        {
            "template_format": "mustache",
            "temperature": 0.2,
            "reasoning": {"effort": "medium"},
        },
        {
            "run_prompt_config": {"temperature": 0.4},
            "configuration": {"template_format": "jinja"},
        },
    ) == {
        "template_format": "jinja",
        "temperature": 0.4,
        "reasoning": {"effort": "medium"},
    }


@pytest.mark.parametrize(
    "serializer_cls",
    [AddRunPromptSerializer, PreviewRunPromptSerializer, EditRunPromptColumnSerializer],
)
def test_run_prompt_boundary_serializers_accept_legacy_template_format(serializer_cls):
    payload = {
        "dataset_id": str(uuid.uuid4()),
        "config": {
            "configuration": {"template_format": "jinja", "ignored": "legacy"},
            "messages": [{"role": "user", "content": "Hello {{Input Column}}"}],
            "model": "gpt-4o-mini",
        },
    }
    if serializer_cls is AddRunPromptSerializer:
        payload["name"] = "Run Prompt"
    elif serializer_cls is PreviewRunPromptSerializer:
        payload.update({"name": "Run Prompt", "first_n_rows": 1})
    else:
        payload["column_id"] = str(uuid.uuid4())

    serializer = serializer_cls(data=payload)

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["config"]["configuration"]["template_format"] == (
        "jinja"
    )


def test_add_run_prompt_view_persists_legacy_template_format_public_alias(monkeypatch):
    import django.db

    dataset_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    run_prompter_id = uuid.uuid4()
    column_id = uuid.uuid4()
    created_kwargs = {}

    class NullAtomic:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class NoExistingColumnQuerySet:
        def exists(self):
            return False

    class RunPrompterManager:
        def create(self, **kwargs):
            created_kwargs.update(kwargs)
            return SimpleNamespace(
                id=run_prompter_id,
                name=kwargs["name"],
                output_format=kwargs["output_format"],
                response_format=kwargs["response_format"],
                tools=SimpleNamespace(set=lambda tools: None),
            )

        def filter(self, **kwargs):
            assert kwargs == {"id": str(run_prompter_id)}
            return SimpleNamespace(update=lambda **updates: None)

    monkeypatch.setattr(
        django.db.transaction,
        "atomic",
        lambda: NullAtomic(),
    )
    monkeypatch.setattr(
        run_prompt_module.Dataset.objects,
        "get",
        lambda **kwargs: SimpleNamespace(
            id=dataset_id,
            organization_id=organization_id,
            column_order=[],
            save=lambda: None,
        ),
    )
    monkeypatch.setattr(
        run_prompt_module.Column.objects,
        "filter",
        lambda **kwargs: NoExistingColumnQuerySet(),
    )
    monkeypatch.setattr(run_prompt_module.RunPrompter, "objects", RunPrompterManager())
    monkeypatch.setattr(
        run_prompt_module,
        "create_run_prompt_column",
        lambda **kwargs: (
            SimpleNamespace(id=column_id),
            True,
        ),
    )

    task_module = ModuleType("model_hub.tasks.run_prompt")
    task_module.process_prompts_single = SimpleNamespace(
        apply_async=lambda **kwargs: SimpleNamespace(id=uuid.uuid4())
    )
    monkeypatch.setitem(sys.modules, "model_hub.tasks.run_prompt", task_module)

    view = run_prompt_module.AddRunPromptColumnView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: pytest.fail(
            f"unexpected internal_server_error_response: {message}"
        ),
    )

    response = run_prompt_module.AddRunPromptColumnView.post.__wrapped__(
        view,
        SimpleNamespace(
            validated_data={
                "dataset_id": dataset_id,
                "name": "Prompt",
                "config": {
                    "configuration": {"template_format": "jinja"},
                    "messages": [{"role": "user", "content": "Hello"}],
                    "model": "gpt-4o-mini",
                    "output_format": "string",
                },
            },
            user=SimpleNamespace(organization=SimpleNamespace(id=organization_id)),
        ),
    )

    assert response == "Run prompt column added successfully"
    assert created_kwargs["run_prompt_config"]["template_format"] == "jinja"


def test_litellm_api_process_row_sanitizes_local_validation_errors(monkeypatch):
    raw_error = "Missing Column from https://signed.example.test/file?token=secret"
    persisted = {}

    def fail_populate(*args, **kwargs):
        assert kwargs["fail_closed"] is True
        raise UnresolvedPromptPlaceholdersError(raw_error)

    class ProviderShouldNotBeCalled:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RunPrompt should not be instantiated")

    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        fail_populate,
    )
    monkeypatch.setattr(run_prompt_module, "RunPrompt", ProviderShouldNotBeCalled)
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "update_or_create",
        lambda **kwargs: persisted.update(kwargs),
    )

    dataset = SimpleNamespace(
        id=uuid.uuid4(),
        workspace=SimpleNamespace(id=uuid.uuid4()),
    )

    view = run_prompt_module.LitellmAPIView()
    view.process_row(
        SimpleNamespace(id=uuid.uuid4()),
        {
            "messages": [{"role": "user", "content": "Hello {{Missing Column}}"}],
            "model": "gpt-4o-mini",
        },
        dataset,
        SimpleNamespace(id=uuid.uuid4()),
        SimpleNamespace(
            user=SimpleNamespace(organization=SimpleNamespace(id=uuid.uuid4()))
        ),
    )

    defaults = persisted["defaults"]
    assert defaults["status"] == CellStatus.ERROR.value
    assert defaults["value"] == run_prompt_module.RUN_PROMPT_LOCAL_VALIDATION_ERROR
    assert "Missing Column" not in defaults["value"]
    assert "signed.example.test" not in defaults["value"]
    assert json.loads(defaults["value_infos"]) == {
        "reason": run_prompt_module.RUN_PROMPT_LOCAL_VALIDATION_ERROR
    }


def test_resolve_request_tools_scopes_tools_to_request_workspace(monkeypatch):
    organization = SimpleNamespace(id=uuid.uuid4())
    workspace = SimpleNamespace(id=uuid.uuid4(), is_default=False)
    tool_id = uuid.uuid4()
    resolved_tool = SimpleNamespace(id=tool_id, config={"name": "allowed"})
    captured_filters = []

    class ToolManager:
        def __init__(self, result):
            self.result = result

        def filter(self, *args, **kwargs):
            captured_filters.append({"args": args, "kwargs": kwargs})
            return self.result

    request = SimpleNamespace(
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=organization),
    )

    monkeypatch.setattr(
        run_prompt_module.Tools, "objects", ToolManager([resolved_tool])
    )

    assert run_prompt_module._resolve_request_tools(
        request,
        [{"id": str(tool_id)}],
    ) == [resolved_tool]

    assert captured_filters
    assert captured_filters[0]["args"], "workspace Q filter must be applied"
    assert captured_filters[0]["kwargs"] == {
        "id__in": [str(tool_id)],
        "organization": organization,
        "deleted": False,
    }

    missing_tool_id = uuid.uuid4()
    monkeypatch.setattr(run_prompt_module.Tools, "objects", ToolManager([]))
    with pytest.raises(ValueError) as exc_info:
        run_prompt_module._resolve_request_tools(
            request,
            [{"id": str(missing_tool_id)}],
        )

    assert str(exc_info.value) == run_prompt_module.RUN_PROMPT_UNAVAILABLE_TOOL_ERROR
    assert str(missing_tool_id) not in str(exc_info.value)


def test_log_run_prompt_error_redacts_local_validation_details(monkeypatch):
    warnings = []
    exceptions = []

    monkeypatch.setattr(
        run_prompt_module.logger,
        "warning",
        lambda event, **kwargs: warnings.append((event, kwargs)),
    )
    monkeypatch.setattr(
        run_prompt_module.logger,
        "exception",
        lambda event, *args, **kwargs: exceptions.append((event, args, kwargs)),
    )

    run_prompt_module._log_run_prompt_error(
        "run_prompt_local_validation_failed",
        UnresolvedPromptPlaceholdersError(
            "Secret Column from https://signed.example.test/file?token=secret"
        ),
        phase="placeholder_validation",
    )

    assert exceptions == []
    assert warnings == [
        (
            "run_prompt_local_validation_failed",
            {
                "error_type": "UnresolvedPromptPlaceholdersError",
                "phase": "placeholder_validation",
                "is_llm_error": False,
            },
        )
    ]
    warning_payload = json.dumps(warnings, default=str)
    assert "Secret Column" not in warning_payload
    assert "signed.example.test" not in warning_payload
    assert "token=secret" not in warning_payload


def test_edit_run_prompt_view_preserves_config_and_terminalizes_enqueue_failure(
    monkeypatch,
):
    import django.db

    dataset_id = uuid.uuid4()
    column_id = uuid.uuid4()
    run_prompter_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    cell_updates = []
    run_prompter_updates = []
    tool_sets = []

    class NullAtomic:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class SingleObjectQuerySet:
        def __init__(self, value):
            self.value = value

        def filter(self, **kwargs):
            return self

        def first(self):
            return self.value

        def select_for_update(self, *args, **kwargs):
            return self

    class CellQuerySet:
        def update(self, **kwargs):
            cell_updates.append(kwargs)
            return 1

    class RunPrompterManager:
        def filter(self, **kwargs):
            assert kwargs == {"id": str(run_prompter_id)}
            return SimpleNamespace(
                update=lambda **updates: run_prompter_updates.append(updates)
            )

    dataset = SimpleNamespace(id=dataset_id, organization_id=organization_id)
    column = SimpleNamespace(
        id=column_id,
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=run_prompter_id,
    )
    run_prompter = SimpleNamespace(
        id=run_prompter_id,
        dataset=dataset,
        status="completed",
        run_prompt_config={
            "template_format": "mustache",
            "reasoning": {"effort": "medium"},
        },
        messages=[{"role": "user", "content": "Old"}],
        name="Old Prompt",
        model="old-model",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        output_format="string",
        concurrency=5,
        tools=SimpleNamespace(
            clear=lambda: pytest.fail("tools.clear should not be called"),
            set=lambda tools: tool_sets.append(tools),
        ),
        save=lambda: None,
    )

    monkeypatch.setattr(
        django.db.transaction,
        "atomic",
        lambda: NullAtomic(),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_dataset_queryset",
        lambda request: SingleObjectQuerySet(dataset),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_column_queryset",
        lambda request: SingleObjectQuerySet(column),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_run_prompter_queryset",
        lambda request: SingleObjectQuerySet(run_prompter),
    )
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "filter",
        lambda **kwargs: CellQuerySet(),
    )
    monkeypatch.setattr(run_prompt_module.RunPrompter, "objects", RunPrompterManager())
    monkeypatch.setattr(
        run_prompt_module,
        "update_column_for_rerun",
        lambda **kwargs: None,
    )

    task_module = ModuleType("model_hub.tasks.run_prompt")

    def fail_apply_async(**kwargs):
        raise RuntimeError("broker contains https://signed.example/file?token=secret")

    task_module.process_prompts_single = SimpleNamespace(apply_async=fail_apply_async)
    monkeypatch.setitem(sys.modules, "model_hub.tasks.run_prompt", task_module)

    view = run_prompt_module.EditRunPromptColumnView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: {
            "error": message,
        },
    )

    response = run_prompt_module.EditRunPromptColumnView.post.__wrapped__(
        view,
        SimpleNamespace(
            validated_data={
                "dataset_id": dataset_id,
                "column_id": column_id,
                "config": {
                    "configuration": {"template_format": "jinja"},
                    "messages": [{"role": "user", "content": "New"}],
                },
            },
            user=SimpleNamespace(organization=SimpleNamespace(id=organization_id)),
        ),
    )

    assert response == {"error": "Failed to start run prompt workflow"}
    assert run_prompter.run_prompt_config == {
        "template_format": "jinja",
        "reasoning": {"effort": "medium"},
    }
    assert tool_sets == []
    assert run_prompter_updates == [
        {"status": run_prompt_module.StatusType.FAILED.value}
    ]
    assert cell_updates == [
        {
            "value": "",
            "value_infos": json.dumps({}),
            "status": CellStatus.RUNNING.value,
        },
        {
            "value": None,
            "value_infos": json.dumps(
                {"reason": "Failed to start run prompt workflow"}
            ),
            "status": CellStatus.ERROR.value,
        },
    ]


def test_edit_run_prompt_view_name_only_edit_does_not_rerun_or_clear_tools(monkeypatch):
    import django.db

    dataset_id = uuid.uuid4()
    column_id = uuid.uuid4()
    run_prompter_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    saved_update_fields = []
    column_updates = []

    class NullAtomic:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class SingleObjectQuerySet:
        def __init__(self, value):
            self.value = value

        def filter(self, **kwargs):
            return self

        def first(self):
            return self.value

        def select_for_update(self, *args, **kwargs):
            return self

    dataset = SimpleNamespace(id=dataset_id, organization_id=organization_id)
    column = SimpleNamespace(
        id=column_id,
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=run_prompter_id,
    )
    run_prompter = SimpleNamespace(
        id=run_prompter_id,
        dataset=dataset,
        status="completed",
        run_prompt_config={"template_format": "mustache"},
        messages=[{"role": "user", "content": "Old"}],
        name="Old Prompt",
        model="old-model",
        output_format="string",
        response_format=None,
        tools=SimpleNamespace(
            clear=lambda: pytest.fail("name-only edit should preserve tools"),
            set=lambda tools: pytest.fail("name-only edit should preserve tools"),
        ),
        save=lambda **kwargs: saved_update_fields.append(kwargs.get("update_fields")),
    )

    monkeypatch.setattr(django.db.transaction, "atomic", lambda: NullAtomic())
    monkeypatch.setattr(
        run_prompt_module,
        "_request_dataset_queryset",
        lambda request: SingleObjectQuerySet(dataset),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_column_queryset",
        lambda request: SingleObjectQuerySet(column),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_run_prompter_queryset",
        lambda request: SingleObjectQuerySet(run_prompter),
    )
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "filter",
        lambda **kwargs: pytest.fail("name-only edit should not requeue cells"),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "update_column_for_rerun",
        lambda **kwargs: column_updates.append(kwargs),
    )

    task_module = ModuleType("model_hub.tasks.run_prompt")
    task_module.process_prompts_single = SimpleNamespace(
        apply_async=lambda **kwargs: pytest.fail("name-only edit should not rerun")
    )
    monkeypatch.setitem(sys.modules, "model_hub.tasks.run_prompt", task_module)

    view = run_prompt_module.EditRunPromptColumnView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: pytest.fail(
            f"unexpected internal_server_error_response: {message}"
        ),
    )

    response = run_prompt_module.EditRunPromptColumnView.post.__wrapped__(
        view,
        SimpleNamespace(
            validated_data={
                "dataset_id": dataset_id,
                "column_id": column_id,
                "name": "Renamed Prompt",
            },
            user=SimpleNamespace(organization=SimpleNamespace(id=organization_id)),
        ),
    )

    assert response == "Run prompt column updated successfully"
    assert run_prompter.name == "Renamed Prompt"
    assert saved_update_fields == [["name"]]
    assert column_updates == [
        {
            "column": column,
            "output_format": "string",
            "response_format": None,
            "name": "Renamed Prompt",
            "status": None,
            "extract_derived_vars": False,
        }
    ]


def test_edit_run_prompt_view_explicit_empty_tools_replaces_existing_tools(monkeypatch):
    import django.db

    dataset_id = uuid.uuid4()
    column_id = uuid.uuid4()
    run_prompter_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    tool_filters = []
    tool_sets = []

    class NullAtomic:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class SingleObjectQuerySet:
        def __init__(self, value):
            self.value = value

        def filter(self, **kwargs):
            return self

        def first(self):
            return self.value

        def select_for_update(self, *args, **kwargs):
            return self

    class CellQuerySet:
        def update(self, **kwargs):
            return 1

    class ToolManager:
        def filter(self, **kwargs):
            tool_filters.append(kwargs)
            return []

    dataset = SimpleNamespace(id=dataset_id, organization_id=organization_id)
    column = SimpleNamespace(
        id=column_id,
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=run_prompter_id,
    )
    run_prompter = SimpleNamespace(
        id=run_prompter_id,
        dataset=dataset,
        status="completed",
        run_prompt_config={"template_format": "mustache"},
        messages=[{"role": "user", "content": "Old"}],
        name="Old Prompt",
        model="old-model",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        output_format="string",
        concurrency=5,
        tools=SimpleNamespace(
            clear=lambda: pytest.fail("tools.set([]) replaces without clear"),
            set=lambda tools: tool_sets.append(tools),
        ),
        save=lambda: None,
    )

    monkeypatch.setattr(django.db.transaction, "atomic", lambda: NullAtomic())
    monkeypatch.setattr(
        run_prompt_module,
        "_request_dataset_queryset",
        lambda request: SingleObjectQuerySet(dataset),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_column_queryset",
        lambda request: SingleObjectQuerySet(column),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_run_prompter_queryset",
        lambda request: SingleObjectQuerySet(run_prompter),
    )
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "filter",
        lambda **kwargs: CellQuerySet(),
    )
    monkeypatch.setattr(run_prompt_module.Tools, "objects", ToolManager())
    monkeypatch.setattr(
        run_prompt_module,
        "update_column_for_rerun",
        lambda **kwargs: None,
    )

    task_module = ModuleType("model_hub.tasks.run_prompt")
    task_module.process_prompts_single = SimpleNamespace(
        apply_async=lambda **kwargs: SimpleNamespace(id=uuid.uuid4())
    )
    monkeypatch.setitem(sys.modules, "model_hub.tasks.run_prompt", task_module)

    view = run_prompt_module.EditRunPromptColumnView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: pytest.fail(
            f"unexpected internal_server_error_response: {message}"
        ),
    )

    response = run_prompt_module.EditRunPromptColumnView.post.__wrapped__(
        view,
        SimpleNamespace(
            validated_data={
                "dataset_id": dataset_id,
                "column_id": column_id,
                "config": {
                    "messages": [{"role": "user", "content": "New"}],
                    "tools": [],
                },
            },
            user=SimpleNamespace(organization=SimpleNamespace(id=organization_id)),
        ),
    )

    assert response == "Run prompt column updated successfully"
    assert tool_filters == []
    assert tool_sets == [[]]


def test_retrieve_run_prompt_view_returns_public_template_format_alias(monkeypatch):
    dataset_id = uuid.uuid4()
    column_id = uuid.uuid4()
    run_prompter_id = uuid.uuid4()
    dataset = SimpleNamespace(id=dataset_id)
    column = SimpleNamespace(
        id=column_id,
        dataset=dataset,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=run_prompter_id,
    )
    run_prompter = SimpleNamespace(
        id=run_prompter_id,
        dataset=dataset,
        name="Prompt",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Hello"}],
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        max_tokens=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        output_format="string",
        concurrency=5,
        run_prompt_config={"template_format": "jinja2"},
        tools=SimpleNamespace(all=lambda: []),
    )

    class SingleObjectQuerySet:
        def __init__(self, value):
            self.value = value

        def filter(self, **kwargs):
            return self

        def first(self):
            return self.value

    monkeypatch.setattr(
        run_prompt_module,
        "_request_column_queryset",
        lambda request: SingleObjectQuerySet(column),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "_request_run_prompter_queryset",
        lambda request: SingleObjectQuerySet(run_prompter),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "convert_uuids_to_column_names",
        lambda messages, dataset_id: messages,
    )

    view = run_prompt_module.RetrieveRunPromptColumnConfigView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: pytest.fail(
            f"unexpected internal_server_error_response: {message}"
        ),
    )

    response = view.get(
        SimpleNamespace(query_params={"column_id": str(column_id)}),
    )

    assert response["config"]["run_prompt_config"]["template_format"] == "jinja"


@pytest.mark.parametrize(
    "template",
    [
        "{{ missing_column == none }}",
        "{% if missing_column != none %}masked{% endif %}",
        "{% if missing_column in [] %}masked{% endif %}",
    ],
)
def test_render_template_jinja_comparisons_raise_for_missing_root(template):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(template, {}, strict=True)

    assert "missing_column" in str(exc_info.value)


@pytest.mark.parametrize(
    "template",
    [
        "{{ missing_column == none }}",
        "{% if missing_column != none %}masked{% endif %}",
        "{% if missing_column in [] %}masked{% endif %}",
    ],
)
def test_render_template_jinja_comparisons_raise_for_unresolved_root(template):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            template,
            {"missing_column": ""},
            strict=True,
            unresolved_placeholders={"missing_column"},
        )

    assert "missing_column" in str(exc_info.value)


@pytest.mark.parametrize("test_name", ["defined", "undefined"])
def test_render_template_jinja_defined_tests_raise_for_missing_root(test_name):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            f"{{% if missing_column is {test_name} %}}masked{{% endif %}}",
            {},
            strict=True,
        )

    assert "missing_column" in str(exc_info.value)


@pytest.mark.parametrize("test_name", ["defined", "undefined"])
def test_render_template_jinja_defined_tests_raise_for_unresolved_root(test_name):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            f"{{% if missing_column is {test_name} %}}masked{{% endif %}}",
            {"missing_column": ""},
            strict=True,
            unresolved_placeholders={"missing_column"},
        )

    assert "missing_column" in str(exc_info.value)


@pytest.mark.parametrize("test_name", ["defined", "undefined"])
def test_render_template_jinja_defined_tests_preserve_loop_locals(test_name):
    assert (
        render_template(
            "{% for item in items %}"
            f"{{% if item is {test_name} %}}{{{{ item }}}}{{% endif %}}"
            "{% endfor %}",
            {"items": ["kept"]},
            strict=True,
        )
        == ("kept" if test_name == "defined" else "")
    )


@pytest.mark.parametrize("test_name", ["defined", "undefined"])
def test_render_template_jinja_defined_tests_preserve_set_locals(test_name):
    assert (
        render_template(
            "{% set item = 'kept' %}"
            f"{{% if item is {test_name} %}}{{{{ item }}}}{{% endif %}}",
            {},
            strict=True,
        )
        == ("kept" if test_name == "defined" else "")
    )


def test_render_template_jinja_truthiness_raises_for_missing_root():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{% if missing_column %}masked{% endif %}",
            {},
            strict=True,
        )

    assert "missing_column" in str(exc_info.value)


def test_render_template_jinja_iteration_raises_for_missing_root():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{% for x in missing_items %}{{ x }}{% endfor %}",
            {},
            strict=True,
        )

    assert "missing_items" in str(exc_info.value)


def test_render_template_mustache_strict_preserves_valid_substitution():
    assert (
        render_template(
            "Answer {{Input Column}} / {{account.name}}",
            {"Input Column": "Resolved", "account": {"name": "Nested"}},
            template_format="mustache",
            strict=True,
        )
        == "Answer Resolved / Nested"
    )


def test_render_template_mustache_strict_preserves_section_scope_with_explicit_falsey_inverted_guard():
    assert (
        render_template(
            "{{#items}}{{name}}{{/items}}{{^missing}} fallback{{/missing}}",
            {"items": [{"name": "Nested"}], "missing": False},
            template_format="mustache",
            strict=True,
        )
        == "Nested fallback"
    )


@pytest.mark.parametrize("falsey_value", [False, 0, "", [], None])
def test_render_template_mustache_strict_skips_missing_variable_in_falsey_section(
    falsey_value,
):
    assert (
        render_template(
            "{{#enabled}}{{missing}}{{/enabled}}Rendered",
            {"enabled": falsey_value},
            template_format="mustache",
            strict=True,
        )
        == "Rendered"
    )


def test_render_template_mustache_strict_raises_for_missing_variable_in_inverted_section():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{^enabled}}{{missing}}{{/enabled}}",
            {"enabled": False},
            template_format="mustache",
            strict=True,
        )

    assert "missing" in str(exc_info.value)


def test_render_template_mustache_strict_raises_for_missing_column():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "Answer {{Missing Column}}",
            {},
            template_format="mustache",
            strict=True,
        )

    assert "Missing Column" in str(exc_info.value)


def test_render_template_fstring_raises_for_missing_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "Answer {missing_column}",
            {},
            template_format="f-string",
            strict=True,
        )

    assert "missing_column" in str(exc_info.value)


def test_render_template_fstring_raises_for_unresolved_null_backed_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "Answer {Input Column}",
            {"Input Column": ""},
            template_format="f-string",
            strict=True,
            unresolved_placeholders={"Input Column"},
        )

    assert "Input Column" in str(exc_info.value)


def test_render_template_fstring_raises_template_syntax_error_for_bad_format():
    with pytest.raises(PromptTemplateSyntaxError):
        render_template(
            "Answer {missing_column",
            {"missing_column": "value"},
            template_format="f-string",
            strict=True,
        )


def test_render_template_mustache_strict_raises_for_absent_section_guard():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{#Missing Column}}Rendered{{/Missing Column}}",
            {},
            template_format="mustache",
            strict=True,
        )

    assert "Missing Column" in str(exc_info.value)


def test_render_template_mustache_strict_raises_for_absent_inverted_section_guard():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{^Missing Column}}fallback{{/Missing Column}}",
            {},
            template_format="mustache",
            strict=True,
        )

    assert "Missing Column" in str(exc_info.value)


def test_render_template_mustache_strict_raises_for_null_backed_section_guard():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{#enabled}}Rendered{{/enabled}}",
            {"enabled": ""},
            template_format="mustache",
            strict=True,
            unresolved_placeholders={"enabled"},
        )

    assert "enabled" in str(exc_info.value)


def test_render_template_mustache_strict_raises_for_null_backed_inverted_section_guard():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{^enabled}}Rendered{{/enabled}}",
            {"enabled": ""},
            template_format="mustache",
            strict=True,
            unresolved_placeholders={"enabled"},
        )

    assert "enabled" in str(exc_info.value)


def test_render_template_mustache_strict_raises_for_missing_uuid():
    missing_uuid = uuid.uuid4()
    sanitized_uuid = sanitize_uuid_for_jinja(str(missing_uuid))

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            f"Answer {{{{{sanitized_uuid}}}}}",
            {},
            template_format="mustache",
            strict=True,
            placeholder_display_map={sanitized_uuid: str(missing_uuid)},
        )

    assert str(missing_uuid) in str(exc_info.value)



def test_render_template_mustache_strict_preserves_raw_uuid_section_tags():
    raw_uuid = str(uuid.uuid4())
    sanitized_uuid = sanitize_uuid_for_jinja(raw_uuid)

    assert (
        render_template(
            f"{{{{#{raw_uuid}}}}}Resolved{{{{/{raw_uuid}}}}}",
            {sanitized_uuid: True},
            template_format="mustache",
            strict=True,
        )
        == "Resolved"
    )


def test_render_template_mustache_strict_missing_raw_uuid_section_uses_display_uuid():
    raw_uuid = str(uuid.uuid4())

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            f"{{{{#{raw_uuid}}}}}Resolved{{{{/{raw_uuid}}}}}",
            {},
            template_format="mustache",
            strict=True,
        )

    assert raw_uuid in str(exc_info.value)


def test_render_template_jinja_runtime_division_by_zero_is_not_unresolved_placeholder():
    with pytest.raises(ZeroDivisionError):
        render_template("{{ 1 / 0 }}", {}, strict=True)

@pytest.mark.parametrize("placeholder", ["items", "keys", "values"])
def test_render_template_mustache_strict_raises_for_missing_dict_attribute_collisions(
    placeholder,
):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            f"Answer {{{{{placeholder}}}}}",
            {},
            template_format="mustache",
            strict=True,
        )

    assert placeholder in str(exc_info.value)


def test_render_template_mustache_strict_raises_for_missing_nested_dict_attribute_collision():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "Answer {{account.items}}",
            {"account": {}},
            template_format="mustache",
            strict=True,
        )

    assert "account.items" in str(exc_info.value)


@pytest.mark.parametrize("placeholder", ["items", "keys", "values"])
def test_render_template_jinja_strict_prefers_mapping_keys_over_dict_methods(
    placeholder,
):
    assert (
        render_template(
            f"{{{{ account.{placeholder} }}}}",
            {"account": {placeholder: "Resolved"}},
            strict=True,
        )
        == "Resolved"
    )


@pytest.mark.parametrize("placeholder", ["items", "keys", "values"])
def test_render_template_mustache_strict_prefers_mapping_keys_over_dict_methods(
    placeholder,
):
    assert (
        render_template(
            f"{{{{account.{placeholder}}}}}",
            {"account": {placeholder: "Resolved"}},
            template_format="mustache",
            strict=True,
        )
        == "Resolved"
    )


def test_render_template_jinja_strict_prefers_mapping_keys_in_loop_items():
    assert (
        render_template(
            "{% for account in accounts %}{{ account.items }}{% endfor %}",
            {"accounts": [{"items": "Resolved"}]},
            strict=True,
        )
        == "Resolved"
    )


def test_render_template_jinja_strict_does_not_wrap_unreferenced_mapping_roots():
    assert (
        render_template(
            "{{ used.items }}",
            {
                "used": {"items": "Resolved"},
                "unused": ExplodingItemsMapping({"items": "Do not touch"}),
            },
            strict=True,
        )
        == "Resolved"
    )


def test_render_template_mustache_strict_does_not_wrap_unreferenced_mapping_roots():
    assert (
        render_template(
            "{{ used.items }}",
            {
                "used": {"items": "Resolved"},
                "unused": ExplodingItemsMapping({"items": "Do not touch"}),
            },
            template_format="mustache",
            strict=True,
        )
        == "Resolved"
    )


def test_render_template_jinja_strict_preserves_jsonstr_raw_and_key_access():
    raw_json = '{"items":"Resolved","nested":{"keys":"Nested"}}'

    assert (
        render_template(
            "{{ col }} / {{ col.items }} / {{ col.nested.keys }}",
            {
                "col": JsonStr(
                    {"items": "Resolved", "nested": {"keys": "Nested"}},
                    raw_json,
                )
            },
            strict=True,
        )
        == f"{raw_json} / Resolved / Nested"
    )


def test_render_template_mustache_strict_preserves_explicit_items_section():
    assert (
        render_template(
            "{{#items}}{{name}}{{/items}}",
            {"items": [{"name": "Kept"}]},
            template_format="mustache",
            strict=True,
        )
        == "Kept"
    )


def test_render_template_strict_raises_for_null_backing_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "Answer {{Input Column}}",
            {"Input Column": ""},
            strict=True,
            unresolved_placeholders={"Input Column"},
        )

    assert "Input Column" in str(exc_info.value)


def test_render_template_strict_unresolved_false_branch_does_not_fail_source_wide():
    assert (
        render_template(
            "{% if false %}{{ Input Column }}{% endif %}Rendered",
            {"Input Column": ""},
            strict=True,
            unresolved_placeholders={"Input Column"},
        )
        == "Rendered"
    )


def test_render_template_strict_unresolved_evaluated_placeholder_still_fails():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{% if true %}{{ Input Column }}{% endif %}",
            {"Input Column": ""},
            strict=True,
            unresolved_placeholders={"Input Column"},
        )

    assert "Input Column" in str(exc_info.value)


def test_render_template_strict_preserves_raw_literal_braces_with_unresolved_placeholder_text():
    assert (
        render_template(
            "Use {% raw %}{{Input Column}}{% endraw %} literally",
            {"Input Column": ""},
            strict=True,
            unresolved_placeholders={"Input Column"},
        )
        == "Use {{Input Column}} literally"
    )


def test_render_template_strict_preserves_string_literal_braces_with_unresolved_placeholder_text():
    assert (
        render_template(
            "Use {{ '{{Input Column}}' }} literally",
            {"Input Column": ""},
            strict=True,
            unresolved_placeholders={"Input Column"},
        )
        == "Use {{Input Column}} literally"
    )


def test_render_template_strict_space_placeholder_value_with_jinja_syntax_renders_literally():
    assert (
        render_template(
            "Answer {{Input Column}}",
            {"Input Column": "{{other}}", "other": "SECRET"},
            strict=True,
        )
        == "Answer {{other}}"
    )


def test_render_template_strict_hyphen_placeholder_value_with_jinja_syntax_renders_literally():
    assert (
        render_template(
            "Answer {{customer-name}}",
            {"customer-name": "{{other}}", "other": "SECRET"},
            strict=True,
        )
        == "Answer {{other}}"
    )


@pytest.mark.parametrize(
    ("column_name", "value"),
    [
        ("Cost ($)", "12.50"),
        ("Input / Output", "Resolved"),
        ("User: Email", "user@example.com"),
    ],
)
def test_render_template_strict_punctuated_column_names_render_by_exact_context_match(
    column_name,
    value,
):
    assert (
        render_template(
            f"Answer {{{{{column_name}}}}}",
            {column_name: value},
            strict=True,
        )
        == f"Answer {value}"
    )


def test_render_template_strict_preserves_spaced_arithmetic_expression():
    assert render_template("{{ foo - bar }}", {"foo": 10, "bar": 3}, strict=True) == "7"


def test_render_template_strict_preserves_compact_subtraction_expression():
    assert render_template("{{ foo-bar }}", {"foo": 10, "bar": 3}, strict=True) == "7"


def test_render_template_strict_unresolved_hyphen_placeholder_fails_before_arithmetic():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{customer-name}}",
            {"customer": 10, "name": 3},
            strict=True,
            unresolved_placeholders={"customer-name"},
        )

    assert "customer-name" in str(exc_info.value)


def test_render_template_strict_preserves_compact_numeric_subtraction_expression():
    assert render_template("{{ foo-1 }}", {"foo": 10}, strict=True) == "9"


def test_render_template_strict_preserves_dotted_arithmetic_expression():
    assert (
        render_template(
            "{{ account.total - reserve }}",
            {"account": {"total": 10}, "reserve": 4},
            strict=True,
        )
        == "6"
    )


def test_render_template_strict_preserves_negative_numeric_literal():
    assert render_template("{{ -1 }}", {}, strict=True) == "-1"


def test_render_template_strict_space_placeholder_rewrite_preserves_raw_and_string_literals():
    assert (
        render_template(
            "{% raw %}{{Input Column}}{% endraw %} / {{ '{{Input Column}}' }} / {{Input Column}}",
            {"Input Column": "{{other}}", "other": "SECRET"},
            strict=True,
        )
        == "{{Input Column}} / {{Input Column}} / {{other}}"
    )


def test_render_template_strict_raises_for_null_backing_placeholder_in_filter():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{ input | default('fallback') }}",
            {"input": ""},
            strict=True,
            unresolved_placeholders={"input"},
        )

    assert "input" in str(exc_info.value)


def test_render_template_strict_raises_for_nested_null_backing_placeholder_in_filter():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{ account.name | default('fallback') }}",
            {"account": {"name": ""}},
            strict=True,
            unresolved_placeholders={"account.name"},
        )

    assert "account.name" in str(exc_info.value)


def test_render_template_strict_ignores_unresolved_loop_local_placeholder_name():
    assert (
        render_template(
            "{% for item in items %}{{ item.name }}{% endfor %}",
            {"items": [{"name": "Kept"}]},
            strict=True,
            unresolved_placeholders={"item"},
        )
        == "Kept"
    )


def test_render_template_strict_raises_for_unresolved_loop_iterable_external_root():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{% for item in items %}{{ item.name }}{% endfor %}",
            {"items": [{"name": "Kept"}]},
            strict=True,
            unresolved_placeholders={"items"},
        )

    assert "items" in str(exc_info.value)


def test_render_template_strict_ignores_unresolved_set_local_placeholder_name():
    assert (
        render_template(
            "{% set item = 'local value' %}{{ item }}",
            {},
            strict=True,
            unresolved_placeholders={"item"},
        )
        == "local value"
    )


def test_render_template_mustache_strict_preserves_intentional_empty_string():
    assert (
        render_template(
            "Answer '{{Input Column}}'",
            {"Input Column": ""},
            template_format="mustache",
            strict=True,
        )
        == "Answer ''"
    )


def test_process_text_with_media_reraises_unresolved_placeholders():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            "Answer {{Missing Column}}",
            {},
            {},
            0,
            "gpt-4o",
            fail_closed=True,
        )

    assert "Missing Column" in str(exc_info.value)


def test_process_text_with_media_non_fail_closed_preserves_legacy_blank_fallback():
    assert process_text_with_media(
        "Answer {{missing_column}}",
        {},
        {},
        0,
        "gpt-4o",
    ) == [{"type": "text", "text": "Answer "}]


def test_process_text_with_media_strict_fallback_raises_for_syntax_unresolved_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            "Answer {{ Missing Column | default('x') }}",
            {},
            {},
            0,
            "gpt-4o",
            fail_closed=True,
        )

    assert "Missing Column" in str(exc_info.value)


def test_process_text_with_media_mustache_reraises_unresolved_placeholders():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            "Answer {{Missing Column}}",
            {},
            {},
            0,
            "gpt-4o",
            template_format="mustache",
            fail_closed=True,
        )

    assert "Missing Column" in str(exc_info.value)


def test_process_text_with_media_fstring_reraises_unresolved_placeholders():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            "Answer {missing_column}",
            {},
            {},
            0,
            "gpt-4o",
            template_format="f-string",
            fail_closed=True,
        )

    assert "missing_column" in str(exc_info.value)


def test_process_text_with_media_populates_uuid_placeholder_without_db():
    column_id = uuid.uuid4()

    result = process_text_with_media(
        f"Answer {{{{{column_id}}}}}",
        {},
        {sanitize_uuid_for_jinja(str(column_id)): "Resolved UUID value"},
        0,
        "gpt-4o",
    )

    assert result == [{"type": "text", "text": "Answer Resolved UUID value"}]


def test_process_text_with_media_missing_uuid_error_uses_original_uuid():
    missing_uuid = uuid.uuid4()

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            f"Answer {{{{ {missing_uuid} }}}}",
            {},
            {},
            0,
            "gpt-4o",
            fail_closed=True,
        )

    assert str(missing_uuid) in str(exc_info.value)


def test_process_text_with_media_preserves_raw_literal_braces():
    result = process_text_with_media(
        "Use {% raw %}{{example}}{% endraw %} literally",
        {},
        {},
        0,
        "gpt-4o",
    )

    assert result == [{"type": "text", "text": "Use {{example}} literally"}]


def test_process_text_with_media_preserves_expression_generated_literal_braces():
    result = process_text_with_media(
        "Use {{ '{{example}}' }} literally",
        {},
        {},
        0,
        "gpt-4o",
    )

    assert result == [{"type": "text", "text": "Use {{example}} literally"}]


def test_process_text_with_media_malformed_jinja_filter_raises_template_syntax_error():
    with pytest.raises(PromptTemplateSyntaxError):
        process_text_with_media(
            "Hello {{ foo | }}",
            {},
            {},
            0,
            "gpt-4o",
            fail_closed=True,
        )


@pytest.mark.parametrize("template", ["Hello {{ foo", "{% if %}"])
def test_process_text_with_media_broken_jinja_raises_instead_of_returning_original_text(
    template,
):
    with pytest.raises(PromptTemplateSyntaxError):
        process_text_with_media(
            template,
            {},
            {},
            0,
            "gpt-4o",
        )


@pytest.mark.parametrize(
    ("template", "column_id"),
    [
        ("Summarize {{Doc}}", "doc-column"),
        ("Summarize {{ Doc }}", "doc-column"),
        (
            "Summarize {{ 550e8400-e29b-41d4-a716-446655440000 }}",
            "550e8400-e29b-41d4-a716-446655440000",
        ),
    ],
)
def test_process_text_with_media_document_prevalidation_marks_media_required(
    monkeypatch,
    template,
    column_id,
):
    monkeypatch.setattr(
        run_prompt_module.litellm.utils,
        "supports_pdf_input",
        lambda model: True,
    )

    content = process_text_with_media(
        template,
        {
            column_id: {
                "data_type": "document",
                "value": "https://example.test/file.pdf",
                "name": "Doc",
            }
        },
        {},
        0,
        "gpt-4o-mini",
        process_media=False,
        fail_closed=True,
    )

    assert any(
        item["type"] == "text" and "__PDF_MARKER_" in item["text"]
        for item in content
    )
    assert run_prompt_module._messages_require_media_processing(
        [{"role": "user", "content": content}]
    )


def test_populate_placeholders_normalizes_template_syntax_error_without_db(monkeypatch):
    monkeypatch.setattr(
        Dataset.objects,
        "get",
        lambda **kwargs: SimpleNamespace(column_order=[]),
    )

    with pytest.raises(PromptTemplateSyntaxError):
        populate_placeholders(
            _message("Hello {{ foo | }}"),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
        )


def test_populate_placeholders_fstring_missing_value_raises_without_db(monkeypatch):
    monkeypatch.setattr(
        Dataset.objects,
        "get",
        lambda **kwargs: SimpleNamespace(column_order=[]),
    )

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message("Hello {missing_column}"),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
            template_format="f-string",
            fail_closed=True,
        )

    assert "missing_column" in str(exc_info.value)


def test_populate_placeholders_fail_closed_reraises_unexpected_setup_errors(
    monkeypatch,
):
    messages = _message("Hello")

    def fail_dataset_get(**kwargs):
        raise RuntimeError("dataset lookup failed")

    monkeypatch.setattr(Dataset.objects, "get", fail_dataset_get)

    assert (
        populate_placeholders(
            messages,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
        )
        == messages
    )

    with pytest.raises(RuntimeError, match="dataset lookup failed"):
        populate_placeholders(
            messages,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
            fail_closed=True,
        )


def test_process_text_with_media_hyphenated_column_error_uses_full_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            "Hello {{customer-name}}",
            {},
            {},
            0,
            "gpt-4o",
            fail_closed=True,
        )

    assert "customer-name" in str(exc_info.value)
    assert "{{customer-name}}" in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_populates_known_column_name_placeholder(
    dataset, input_column, output_column, row, cell
):
    result = populate_placeholders(
        _message("Answer {{Input Column}}"),
        dataset.id,
        row.id,
        output_column.id,
        "gpt-4o",
    )

    assert result[0]["content"][0]["text"] == "Answer Resolved input value"


@pytest.mark.django_db
def test_run_prompt_populates_punctuated_column_name_placeholder(
    dataset, output_column, row
):
    column = Column.objects.create(
        name="Cost ($)",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(column.id))
    dataset.save(update_fields=["column_order"])
    Cell.objects.create(dataset=dataset, column=column, row=row, value="12.50")

    result = populate_placeholders(
        _message("Answer {{Cost ($)}}"),
        dataset.id,
        row.id,
        output_column.id,
        "gpt-4o",
    )

    assert result[0]["content"][0]["text"] == "Answer 12.50"


@pytest.mark.django_db
def test_run_prompt_populates_known_uuid_placeholder(
    dataset, input_column, output_column, row, cell
):
    result = populate_placeholders(
        _message(f"Answer {{{{{input_column.id}}}}}"),
        dataset.id,
        row.id,
        output_column.id,
        "gpt-4o",
    )

    assert result[0]["content"][0]["text"] == "Answer Resolved input value"


@pytest.mark.django_db
def test_run_prompt_raises_for_missing_column_name_placeholder(
    dataset, input_column, output_column, row, cell
):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message("Answer {{Missing Column}}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            fail_closed=True,
        )

    assert "Missing Column" in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_non_fail_closed_keeps_legacy_blank_missing_placeholder(
    dataset, input_column, output_column, row, cell
):
    result = populate_placeholders(
        _message("Answer {{Missing Column}}"),
        dataset.id,
        row.id,
        output_column.id,
        "gpt-4o",
    )

    assert result[0]["content"][0]["text"] == "Answer "


@pytest.mark.django_db
def test_run_prompt_raises_for_missing_uuid_placeholder(
    dataset, input_column, output_column, row, cell
):
    missing_uuid = uuid.uuid4()

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message(f"Answer {{{{{missing_uuid}}}}}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            fail_closed=True,
        )

    assert str(missing_uuid) in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_raises_for_column_order_uuid_without_column_record(
    dataset, input_column, output_column, row, cell
):
    missing_uuid = uuid.uuid4()
    Dataset.objects.filter(id=dataset.id).update(column_order=[str(missing_uuid)])

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message(f"Answer {{{{{missing_uuid}}}}}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            fail_closed=True,
        )

    assert str(missing_uuid) in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_raises_for_null_cell_column_name_placeholder(
    dataset, input_column, output_column, row, cell
):
    Cell.objects.filter(id=cell.id).update(value=None)

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message("Answer {{Input Column}}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            fail_closed=True,
        )

    assert "Input Column" in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_raises_for_null_cell_uuid_placeholder(
    dataset, input_column, output_column, row, cell
):
    Cell.objects.filter(id=cell.id).update(value=None)

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message(f"Answer {{{{{input_column.id}}}}}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            fail_closed=True,
        )

    assert str(input_column.id) in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_fstring_raises_for_null_cell_placeholder(
    dataset, input_column, output_column, row, cell
):
    Cell.objects.filter(id=cell.id).update(value=None)

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message("Answer {Input Column}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            template_format="f-string",
            fail_closed=True,
        )

    assert "Input Column" in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_raises_for_missing_cell_even_with_jinja_default(
    dataset, input_column, output_column, row
):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message("Answer {{ Input Column | default('fallback') }}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            fail_closed=True,
        )

    assert "Input Column" in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_raises_for_missing_cell_mustache_section_guard(
    dataset, input_column, output_column, row
):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message("{{#Input Column}}Rendered{{/Input Column}}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            template_format="mustache",
            fail_closed=True,
        )

    assert "Input Column" in str(exc_info.value)


@pytest.mark.django_db
def test_run_prompt_raises_for_missing_cell_mustache_inverted_guard(
    dataset, input_column, output_column, row
):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            _message("{{^Input Column}}fallback{{/Input Column}}"),
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
            template_format="mustache",
            fail_closed=True,
        )

    assert "Input Column" in str(exc_info.value)


def test_run_prompts_process_row_marks_api_call_error_after_provider_dispatch_exception(
    monkeypatch,
):
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
        saved_statuses=[],
    )

    def save_api_call_log_row():
        api_call_log_row.saved_statuses.append(api_call_log_row.status)

    api_call_log_row.save = save_api_call_log_row

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: api_call_log_row,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        lambda *args, **kwargs: [{"role": "user", "content": "hi"}],
    )
    monkeypatch.setattr(
        run_prompt_module,
        "get_specific_error_message",
        lambda exc, is_llm_error=False: str(exc),
    )

    class FailingRunPrompt:
        def __init__(self, *args, **kwargs):
            pass

        def litellm_response(self):
            raise RuntimeError("provider dispatch failed")

    monkeypatch.setattr(run_prompt_module, "RunPrompt", FailingRunPrompt)

    created_cells = []

    def fake_create(**kwargs):
        cell = SimpleNamespace(id=uuid.uuid4(), **kwargs)
        cell.save = lambda: None
        created_cells.append(cell)
        return cell

    monkeypatch.setattr(run_prompt_module.Cell.objects, "create", fake_create)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = RunPrompts(uuid.uuid4())
    runner.is_editing = False
    runner.tools_config = []
    runner.run_prompt_model = SimpleNamespace(
        organization=SimpleNamespace(id=uuid.uuid4()),
        messages=[{"role": "user", "content": "hi"}],
        dataset=dataset,
        model="gpt-4o-mini",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        output_format=None,
        run_prompt_config={},
    )

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert api_call_log_row.status == APICallStatusChoices.ERROR.value
    assert api_call_log_row.saved_statuses == [APICallStatusChoices.ERROR.value]
    assert created_cells[0].status == CellStatus.ERROR.value


def _install_usage_emit_modules(monkeypatch, emitted_events):
    ee_module = ModuleType("ee")
    usage_module = ModuleType("ee.usage")
    schemas_module = ModuleType("ee.usage.schemas")
    events_module = ModuleType("ee.usage.schemas.events")
    services_module = ModuleType("ee.usage.services")
    emitter_module = ModuleType("ee.usage.services.emitter")

    class UsageEvent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def emit(event):
        emitted_events.append(event)

    events_module.UsageEvent = UsageEvent
    emitter_module.emit = emit

    monkeypatch.setitem(sys.modules, "ee", ee_module)
    monkeypatch.setitem(sys.modules, "ee.usage", usage_module)
    monkeypatch.setitem(sys.modules, "ee.usage.schemas", schemas_module)
    monkeypatch.setitem(sys.modules, "ee.usage.schemas.events", events_module)
    monkeypatch.setitem(sys.modules, "ee.usage.services", services_module)
    monkeypatch.setitem(sys.modules, "ee.usage.services.emitter", emitter_module)


def _build_process_row_runner(dataset):
    runner = RunPrompts(uuid.uuid4())
    runner.is_editing = False
    runner.tools_config = []
    runner.run_prompt_model = SimpleNamespace(
        organization=SimpleNamespace(id=uuid.uuid4()),
        messages=[{"role": "user", "content": "hi"}],
        dataset=dataset,
        model="gpt-4o-mini",
        temperature=None,
        frequency_penalty=None,
        presence_penalty=None,
        top_p=None,
        response_format=None,
        tool_choice=None,
        output_format=None,
        run_prompt_config={},
    )
    return runner


def test_run_prompts_process_row_success_save_failure_skips_usage_after_cell_save(
    monkeypatch,
):
    emitted_events = []
    _install_usage_emit_modules(monkeypatch, emitted_events)

    attempted_statuses = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )

    def save_api_call_log_row():
        attempted_statuses.append(api_call_log_row.status)
        if api_call_log_row.status == APICallStatusChoices.SUCCESS.value:
            raise RuntimeError("success save failed")

    api_call_log_row.save = save_api_call_log_row

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: api_call_log_row,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        lambda *args, **kwargs: [{"role": "user", "content": "hi"}],
    )
    monkeypatch.setattr(
        run_prompt_module,
        "get_specific_error_message",
        lambda exc, is_llm_error=False: str(exc),
    )

    class SuccessfulRunPrompt:
        def __init__(self, *args, **kwargs):
            pass

        def litellm_response(self):
            return "provider response", {"data": {"response": "provider response"}}

    monkeypatch.setattr(run_prompt_module, "RunPrompt", SuccessfulRunPrompt)

    created_cells = []

    def fake_create(**kwargs):
        cell = SimpleNamespace(id=uuid.uuid4(), **kwargs)
        cell.save = lambda: None
        created_cells.append(cell)
        return cell

    monkeypatch.setattr(run_prompt_module.Cell.objects, "create", fake_create)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert emitted_events == []
    assert attempted_statuses == [
        APICallStatusChoices.SUCCESS.value,
        APICallStatusChoices.SUCCESS.value,
        APICallStatusChoices.ERROR.value,
    ]
    assert api_call_log_row.status == APICallStatusChoices.ERROR.value
    assert created_cells[0].status == CellStatus.ERROR.value
    assert created_cells[0].value == (
        "Provider response was saved, but final API call status could not be persisted."
    )
    assert "Failed to persist API call success status" not in created_cells[0].value


def test_run_prompts_process_row_cell_create_failure_marks_api_call_error_and_skips_usage(
    monkeypatch,
):
    emitted_events = []
    _install_usage_emit_modules(monkeypatch, emitted_events)

    attempted_statuses = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )

    def save_api_call_log_row():
        attempted_statuses.append(api_call_log_row.status)

    api_call_log_row.save = save_api_call_log_row

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: api_call_log_row,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        lambda *args, **kwargs: [{"role": "user", "content": "hi"}],
    )

    class SuccessfulRunPrompt:
        def __init__(self, *args, **kwargs):
            pass

        def litellm_response(self):
            return "provider response", {"data": {"response": "provider response"}}

    monkeypatch.setattr(run_prompt_module, "RunPrompt", SuccessfulRunPrompt)

    def fake_create(**kwargs):
        raise RuntimeError("cell create failed")

    monkeypatch.setattr(run_prompt_module.Cell.objects, "create", fake_create)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    with pytest.raises(RuntimeError, match="cell create failed"):
        runner.process_row(
            SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert emitted_events == []
    assert attempted_statuses == [APICallStatusChoices.ERROR.value]
    assert api_call_log_row.status == APICallStatusChoices.ERROR.value


def test_run_prompts_process_row_validates_without_media_before_accounting_then_processes_media_before_provider(
    monkeypatch,
):
    order = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )

    def save_api_call_log_row():
        order.append(("api_status", api_call_log_row.status))

    api_call_log_row.save = save_api_call_log_row

    def fake_log_and_deduct(*args, **kwargs):
        order.append(("accounting", None))
        return api_call_log_row

    def fake_populate(*args, **kwargs):
        process_media = kwargs.get("process_media", True)
        order.append(("populate", process_media))
        if process_media is False:
            assert ("accounting", None) not in order
            assert kwargs.get("fail_closed") is True
            return [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "__IMAGE_MARKER_12345678__"}
                    ],
                }
            ]
        assert ("accounting", None) in order
        assert kwargs.get("fail_closed") is True
        return [{"role": "user", "content": "provider ready"}]

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        fake_log_and_deduct,
    )
    monkeypatch.setattr(run_prompt_module, "populate_placeholders", fake_populate)

    class SuccessfulRunPrompt:
        def __init__(self, *args, **kwargs):
            order.append(("provider_init", kwargs["messages"]))
            assert kwargs["messages"] == [
                {"role": "user", "content": "provider ready"}
            ]

        def litellm_response(self):
            order.append(("provider_call", None))
            return "provider response", {"data": {"response": "provider response"}}

    monkeypatch.setattr(run_prompt_module, "RunPrompt", SuccessfulRunPrompt)
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "create",
        lambda **kwargs: SimpleNamespace(id=uuid.uuid4(), **kwargs),
    )

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert order[:4] == [
        ("populate", False),
        ("accounting", None),
        ("populate", True),
        ("provider_init", [{"role": "user", "content": "provider ready"}]),
    ]
    assert ("provider_call", None) in order
    assert order[-1] == ("api_status", APICallStatusChoices.SUCCESS.value)


def test_run_prompts_process_row_materializes_document_media_after_accounting(
    monkeypatch,
):
    order = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )

    def save_api_call_log_row():
        order.append(("api_status", api_call_log_row.status))

    api_call_log_row.save = save_api_call_log_row

    def fake_log_and_deduct(*args, **kwargs):
        order.append(("accounting", None))
        return api_call_log_row

    def fake_populate(*args, **kwargs):
        process_media = kwargs.get("process_media", True)
        order.append(("populate", process_media))
        if process_media is False:
            assert ("accounting", None) not in order
            assert kwargs.get("fail_closed") is True
            return [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "__PDF_MARKER_12345678__"}],
                }
            ]
        assert ("accounting", None) in order
        assert kwargs.get("fail_closed") is True
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize Doc"},
                    {
                        "type": "pdf_url",
                        "pdf_url": {
                            "url": "https://example.test/file.pdf",
                            "pdf_name": "Doc",
                            "file_name": "Doc",
                        },
                    },
                ],
            }
        ]

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        fake_log_and_deduct,
    )
    monkeypatch.setattr(run_prompt_module, "populate_placeholders", fake_populate)

    class SuccessfulRunPrompt:
        def __init__(self, *args, **kwargs):
            order.append(("provider_init", kwargs["messages"]))
            assert kwargs["messages"][0]["content"][1]["type"] == "pdf_url"

        def litellm_response(self):
            order.append(("provider_call", None))
            return "provider response", {"data": {"response": "provider response"}}

    monkeypatch.setattr(run_prompt_module, "RunPrompt", SuccessfulRunPrompt)
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "create",
        lambda **kwargs: SimpleNamespace(id=uuid.uuid4(), **kwargs),
    )

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert order[:4] == [
        ("populate", False),
        ("accounting", None),
        ("populate", True),
        (
            "provider_init",
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Summarize Doc"},
                        {
                            "type": "pdf_url",
                            "pdf_url": {
                                "url": "https://example.test/file.pdf",
                                "pdf_name": "Doc",
                                "file_name": "Doc",
                            },
                        },
                    ],
                }
            ],
        ),
    ]
    assert ("provider_call", None) in order
    assert order[-1] == ("api_status", APICallStatusChoices.SUCCESS.value)


def test_run_prompts_process_row_reuses_text_only_prevalidation_messages(monkeypatch):
    populate_calls = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )
    api_call_log_row.save = lambda: None

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: api_call_log_row,
    )

    def fake_populate(*args, **kwargs):
        populate_calls.append(kwargs.copy())
        assert kwargs["process_media"] is False
        assert kwargs["fail_closed"] is True
        return [{"role": "user", "content": "provider ready"}]

    monkeypatch.setattr(run_prompt_module, "populate_placeholders", fake_populate)

    class SuccessfulRunPrompt:
        def __init__(self, *args, **kwargs):
            assert kwargs["messages"] == [
                {"role": "user", "content": "provider ready"}
            ]

        def litellm_response(self):
            return "provider response", {"data": {"response": "provider response"}}

    monkeypatch.setattr(run_prompt_module, "RunPrompt", SuccessfulRunPrompt)
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "create",
        lambda **kwargs: SimpleNamespace(id=uuid.uuid4(), **kwargs),
    )

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert len(populate_calls) == 1


def test_run_prompts_process_row_edit_existing_cell_save_failure_marks_api_call_error_and_skips_usage(
    monkeypatch,
):
    emitted_events = []
    _install_usage_emit_modules(monkeypatch, emitted_events)

    attempted_statuses = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )

    def save_api_call_log_row():
        attempted_statuses.append(api_call_log_row.status)

    api_call_log_row.save = save_api_call_log_row

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: api_call_log_row,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        lambda *args, **kwargs: [{"role": "user", "content": "hi"}],
    )

    class SuccessfulRunPrompt:
        def __init__(self, *args, **kwargs):
            pass

        def litellm_response(self):
            return "provider response", {"data": {"response": "provider response"}}

    monkeypatch.setattr(run_prompt_module, "RunPrompt", SuccessfulRunPrompt)

    fake_cell = SimpleNamespace(
        id=uuid.uuid4(),
        value=None,
        value_infos=None,
        status=None,
        prompt_tokens=None,
        completion_tokens=None,
        response_time=None,
    )

    def fail_save_cell():
        raise RuntimeError("cell save failed")

    fake_cell.save = fail_save_cell
    monkeypatch.setattr(run_prompt_module.Cell.objects, "get", lambda **kwargs: fake_cell)
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "create",
        lambda **kwargs: pytest.fail("Cell.objects.create should not be called"),
    )

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    with pytest.raises(RuntimeError, match="cell save failed"):
        runner.process_row(
            SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
            SimpleNamespace(id=uuid.uuid4()),
            edit_mode=True,
        )

    assert emitted_events == []
    assert attempted_statuses == [APICallStatusChoices.ERROR.value]
    assert APICallStatusChoices.SUCCESS.value not in attempted_statuses
    assert api_call_log_row.status == APICallStatusChoices.ERROR.value


def test_run_prompts_process_row_edit_existing_cell_success_persists_api_success_then_emits_usage(
    monkeypatch,
):
    emitted_events = []
    _install_usage_emit_modules(monkeypatch, emitted_events)

    order = []
    attempted_statuses = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )

    def save_api_call_log_row():
        attempted_statuses.append(api_call_log_row.status)
        if api_call_log_row.status == APICallStatusChoices.SUCCESS.value:
            assert "cell_save" in order
            order.append("api_success_save")

    api_call_log_row.save = save_api_call_log_row

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: api_call_log_row,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        lambda *args, **kwargs: [{"role": "user", "content": "hi"}],
    )

    class SuccessfulRunPrompt:
        def __init__(self, *args, **kwargs):
            pass

        def litellm_response(self):
            return "provider response", {
                "data": {"response": "provider response"},
                "metadata": {
                    "usage": {"prompt_tokens": 3, "completion_tokens": 5},
                    "response_time": 1.25,
                },
            }

    monkeypatch.setattr(run_prompt_module, "RunPrompt", SuccessfulRunPrompt)

    fake_cell = SimpleNamespace(
        id=uuid.uuid4(),
        value=None,
        value_infos=None,
        status=None,
        prompt_tokens=None,
        completion_tokens=None,
        response_time=None,
    )

    def save_cell():
        order.append("cell_save")

    fake_cell.save = save_cell
    monkeypatch.setattr(run_prompt_module.Cell.objects, "get", lambda **kwargs: fake_cell)
    monkeypatch.setattr(
        run_prompt_module.Cell.objects,
        "create",
        lambda **kwargs: pytest.fail("Cell.objects.create should not be called"),
    )

    def emit_usage(event):
        assert order[-1] == "api_success_save"
        order.append("usage_emit")
        emitted_events.append(event)

    monkeypatch.setattr(sys.modules["ee.usage.services.emitter"], "emit", emit_usage)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
        edit_mode=True,
    )

    assert fake_cell.value == "provider response"
    assert fake_cell.status == CellStatus.PASS.value
    assert fake_cell.prompt_tokens == 3
    assert fake_cell.completion_tokens == 5
    assert fake_cell.response_time == 1.25
    value_infos = json.loads(fake_cell.value_infos)
    assert value_infos["data"]["response"] == "provider response"
    assert value_infos["reason"] == "provider response"
    assert attempted_statuses == [APICallStatusChoices.SUCCESS.value]
    assert api_call_log_row.status == APICallStatusChoices.SUCCESS.value
    assert len(emitted_events) == 1
    assert order == ["cell_save", "api_success_save", "usage_emit"]


def test_run_prompts_process_row_unresolved_placeholders_skip_provider_and_create_error_cell(
    monkeypatch,
):
    emitted_events = []
    _install_usage_emit_modules(monkeypatch, emitted_events)

    def fail_log_and_deduct(*args, **kwargs):
        raise AssertionError("API accounting should not run for local placeholder errors")

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        fail_log_and_deduct,
    )

    populate_calls = []

    def fail_populate(*args, **kwargs):
        populate_calls.append({"args": args, "kwargs": kwargs})
        assert kwargs["process_media"] is False
        assert kwargs["fail_closed"] is True
        raise UnresolvedPromptPlaceholdersError("Missing Column")

    monkeypatch.setattr(run_prompt_module, "populate_placeholders", fail_populate)
    monkeypatch.setattr(
        run_prompt_module,
        "get_specific_error_message",
        lambda exc, is_llm_error=False: str(exc),
    )

    class ProviderShouldNotBeCalled:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RunPrompt should not be instantiated")

    monkeypatch.setattr(run_prompt_module, "RunPrompt", ProviderShouldNotBeCalled)

    created_cells = []

    def fake_create(**kwargs):
        created_cells.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs)

    monkeypatch.setattr(run_prompt_module.Cell.objects, "create", fake_create)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert emitted_events == []
    assert len(populate_calls) == 1
    assert len(created_cells) == 1
    assert created_cells[0]["status"] == CellStatus.ERROR.value
    assert created_cells[0]["value"] == run_prompt_module.RUN_PROMPT_LOCAL_VALIDATION_ERROR
    assert "Missing Column" not in created_cells[0]["value"]
    assert json.loads(created_cells[0]["value_infos"]) == {
        "reason": run_prompt_module.RUN_PROMPT_LOCAL_VALIDATION_ERROR
    }


def test_run_prompts_process_row_fstring_validation_failure_skips_accounting_and_provider(
    monkeypatch,
):
    def fail_log_and_deduct(*args, **kwargs):
        raise AssertionError("API accounting should not run for f-string validation")

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        fail_log_and_deduct,
    )

    populate_calls = []

    def fail_populate(*args, **kwargs):
        populate_calls.append(kwargs)
        assert kwargs["template_format"] == "f-string"
        assert kwargs["process_media"] is False
        assert kwargs["fail_closed"] is True
        raise UnresolvedPromptPlaceholdersError("missing_column")

    monkeypatch.setattr(run_prompt_module, "populate_placeholders", fail_populate)
    monkeypatch.setattr(
        run_prompt_module,
        "get_specific_error_message",
        lambda exc, is_llm_error=False: str(exc),
    )

    class ProviderShouldNotBeCalled:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RunPrompt should not be instantiated")

    monkeypatch.setattr(run_prompt_module, "RunPrompt", ProviderShouldNotBeCalled)

    created_cells = []

    def fake_create(**kwargs):
        created_cells.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs)

    monkeypatch.setattr(run_prompt_module.Cell.objects, "create", fake_create)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)
    runner.run_prompt_model.run_prompt_config = {"template_format": "f-string"}

    runner.process_row(
        SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
        SimpleNamespace(id=uuid.uuid4()),
    )

    assert len(populate_calls) == 1
    assert len(created_cells) == 1
    assert created_cells[0]["status"] == CellStatus.ERROR.value
    assert created_cells[0]["value"] == run_prompt_module.RUN_PROMPT_LOCAL_VALIDATION_ERROR
    assert "missing_column" not in created_cells[0]["value"]
    assert json.loads(created_cells[0]["value_infos"]) == {
        "reason": run_prompt_module.RUN_PROMPT_LOCAL_VALIDATION_ERROR
    }


def test_run_prompts_process_row_error_save_failure_raises_after_error_cell_without_usage(
    monkeypatch,
):
    attempted_statuses = []
    api_call_log_row = SimpleNamespace(
        id=uuid.uuid4(),
        status=APICallStatusChoices.PROCESSING.value,
    )

    def save_api_call_log_row():
        attempted_statuses.append(api_call_log_row.status)
        if api_call_log_row.status == APICallStatusChoices.ERROR.value:
            raise RuntimeError("error save failed")

    api_call_log_row.save = save_api_call_log_row

    monkeypatch.setattr(
        run_prompt_module,
        "log_and_deduct_cost_for_api_request",
        lambda *args, **kwargs: api_call_log_row,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        lambda *args, **kwargs: [{"role": "user", "content": "hi"}],
    )

    class FailingRunPrompt:
        def __init__(self, *args, **kwargs):
            pass

        def litellm_response(self):
            raise RuntimeError("provider dispatch failed")

    monkeypatch.setattr(run_prompt_module, "RunPrompt", FailingRunPrompt)

    created_cells = []

    def fake_create(**kwargs):
        created_cells.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs)

    monkeypatch.setattr(run_prompt_module.Cell.objects, "create", fake_create)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    with pytest.raises(RuntimeError, match="Failed to persist API call error status"):
        runner.process_row(
            SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert api_call_log_row.status == APICallStatusChoices.ERROR.value
    assert attempted_statuses == [
        APICallStatusChoices.ERROR.value,
        APICallStatusChoices.ERROR.value,
        APICallStatusChoices.ERROR.value,
        APICallStatusChoices.ERROR.value,
    ]
    assert len(created_cells) == 1
    assert created_cells[0]["status"] == CellStatus.ERROR.value
    assert "provider dispatch failed" in created_cells[0]["value"]


def test_render_template_jinja_missing_root_in_false_branch_does_not_fail_source_wide():
    assert render_template(
        "{% if false %}{{ missing_column }}{% endif %}Rendered",
        {},
        strict=True,
    ) == "Rendered"


def test_render_template_jinja_missing_comparison_in_false_branch_does_not_fail():
    assert render_template(
        "{% if false %}{% if missing_column in [] %}hidden{% endif %}{% endif %}"
        "Rendered",
        {},
        strict=True,
    ) == "Rendered"


def test_conditional_run_prompt_placeholder_error_returns_error_metadata(monkeypatch):
    def fail_populate(*args, **kwargs):
        raise UnresolvedPromptPlaceholdersError(
            "Missing Column from https://signed.example/file.pdf?token=secret"
        )

    monkeypatch.setattr(dynamic_columns_module, "populate_placeholders", fail_populate)
    monkeypatch.setattr(
        dynamic_columns_module,
        "RunPrompt",
        lambda *args, **kwargs: pytest.fail("RunPrompt should not be instantiated"),
    )

    row = SimpleNamespace(
        id=uuid.uuid4(),
        dataset=SimpleNamespace(id=uuid.uuid4(), workspace=None),
    )
    value, value_infos = dynamic_columns_module.ConditionalColumnView()._process_branch(
        row,
        {
            "branch_node_config": {
                "type": "run_prompt",
                "config": {
                    "messages": [{"role": "user", "content": "{{Missing Column}}"}],
                    "model": "gpt-4o-mini",
                    "configuration": {"template_format": "jinja2"},
                },
            }
        },
        org_id=uuid.uuid4(),
    )

    assert value is None
    assert value_infos == {
        "reason": dynamic_columns_module.RUN_PROMPT_BRANCH_VALIDATION_ERROR
    }
    assert "signed.example" not in value_infos["reason"]


def test_conditional_process_row_propagates_run_prompt_placeholder_error_with_template_precedence(
    monkeypatch,
):
    populate_calls = []
    dataset_id = uuid.uuid4()
    row_id = uuid.uuid4()
    org_id = uuid.uuid4()
    messages = [{"role": "user", "content": "Hello {{Missing Column}}"}]

    def fail_populate(*args, **kwargs):
        populate_calls.append({"args": args, "kwargs": kwargs})
        raise UnresolvedPromptPlaceholdersError("Missing Column")

    monkeypatch.setattr(dynamic_columns_module, "populate_placeholders", fail_populate)
    monkeypatch.setattr(
        dynamic_columns_module,
        "RunPrompt",
        lambda *args, **kwargs: pytest.fail("RunPrompt should not be instantiated"),
    )

    view = dynamic_columns_module.ConditionalColumnView()
    monkeypatch.setattr(view, "_evaluate_condition", lambda *args, **kwargs: True)

    row = SimpleNamespace(
        id=row_id,
        dataset=SimpleNamespace(
            id=dataset_id,
            workspace=SimpleNamespace(id=uuid.uuid4()),
        ),
    )
    value, value_infos = view._process_row(
        row,
        [
            {
                "branch_type": "if",
                "condition": "always true",
                "branch_node_config": {
                    "type": "run_prompt",
                    "config": {
                        "messages": messages,
                        "model": "gpt-4o-mini",
                        "run_prompt_config": {"template_format": "mustache"},
                        "configuration": {"template_format": "jinja2"},
                    },
                },
            },
            {
                "branch_type": "else",
                "branch_node_config": {
                    "type": "static_value",
                    "config": {"value": "should not be used"},
                },
            },
        ],
        org_id=org_id,
    )

    assert value is None
    assert value_infos == {
        "reason": dynamic_columns_module.RUN_PROMPT_BRANCH_VALIDATION_ERROR
    }
    assert populate_calls == [
        {
            "args": (messages,),
            "kwargs": {
                "dataset_id": dataset_id,
                "row_id": row_id,
                "col_id": None,
                "model_name": "gpt-4o-mini",
                "template_format": "mustache",
                "process_media": False,
                "fail_closed": True,
            },
        }
    ]


def test_conditional_run_prompt_validates_without_media_before_provider(monkeypatch):
    populate_calls = []

    def fake_populate(*args, **kwargs):
        populate_calls.append(kwargs.get("process_media", True))
        if kwargs.get("process_media") is False:
            assert kwargs["fail_closed"] is True
            return [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "audio_url",
                            "audio_url": {"url": "http://example.test/a.wav"},
                        }
                    ],
                }
            ]
        assert kwargs["fail_closed"] is True
        return [{"role": "user", "content": "provider ready"}]

    monkeypatch.setattr(dynamic_columns_module, "populate_placeholders", fake_populate)

    class FakeRunPrompt:
        def __init__(self, *args, **kwargs):
            assert kwargs["messages"] == [
                {"role": "user", "content": "provider ready"}
            ]

        def litellm_response(self):
            return "ok", {"reason": "ok"}

    monkeypatch.setattr(dynamic_columns_module, "RunPrompt", FakeRunPrompt)

    row = SimpleNamespace(
        id=uuid.uuid4(),
        dataset=SimpleNamespace(id=uuid.uuid4(), workspace=None),
    )
    value, value_infos = dynamic_columns_module.ConditionalColumnView()._process_branch(
        row,
        {
            "branch_node_config": {
                "type": "run_prompt",
                "config": {
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "audio_url",
                                    "audio_url": {
                                        "url": "http://example.test/a.wav"
                                    },
                                },
                                {"type": "text", "text": "{{Input Column}}"},
                            ],
                        }
                    ],
                    "model": "gpt-4o-audio-preview",
                    "run_prompt_config": {"template_format": "jinja2"},
                },
            }
        },
        org_id=uuid.uuid4(),
    )

    assert (value, value_infos) == ("ok", {"reason": "ok"})
    assert populate_calls == [False, True]


def test_conditional_run_prompt_reuses_text_only_prevalidation_messages(monkeypatch):
    populate_calls = []

    def fake_populate(*args, **kwargs):
        populate_calls.append(kwargs.get("process_media", True))
        assert kwargs["fail_closed"] is True
        return [{"role": "user", "content": "provider ready"}]

    monkeypatch.setattr(dynamic_columns_module, "populate_placeholders", fake_populate)

    class FakeRunPrompt:
        def __init__(self, *args, **kwargs):
            assert kwargs["messages"] == [
                {"role": "user", "content": "provider ready"}
            ]

        def litellm_response(self):
            return "ok", {"reason": "ok"}

    monkeypatch.setattr(dynamic_columns_module, "RunPrompt", FakeRunPrompt)

    row = SimpleNamespace(
        id=uuid.uuid4(),
        dataset=SimpleNamespace(id=uuid.uuid4(), workspace=None),
    )
    value, value_infos = dynamic_columns_module.ConditionalColumnView()._process_branch(
        row,
        {
            "branch_node_config": {
                "type": "run_prompt",
                "config": {
                    "messages": [{"role": "user", "content": "{{Input Column}}"}],
                    "model": "gpt-4o-mini",
                    "run_prompt_config": {"template_format": "jinja2"},
                },
            }
        },
        org_id=uuid.uuid4(),
    )

    assert (value, value_infos) == ("ok", {"reason": "ok"})
    assert populate_calls == [False]


def test_conditional_column_async_rerun_missing_cell_persists_placeholder_error(
    monkeypatch,
):
    dataset_id = uuid.uuid4()
    new_column_id = uuid.uuid4()
    row = SimpleNamespace(id=uuid.uuid4())
    organization_id = uuid.uuid4()
    created_cells = []

    monkeypatch.setattr(
        dynamic_columns_module,
        "Row",
        SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda **kwargs: [row],
            )
        ),
    )
    monkeypatch.setattr(
        dynamic_columns_module,
        "Dataset",
        SimpleNamespace(
            objects=SimpleNamespace(
                get=lambda **kwargs: SimpleNamespace(
                    organization=SimpleNamespace(id=organization_id)
                )
            )
        ),
    )
    monkeypatch.setattr(
        dynamic_columns_module,
        "Column",
        SimpleNamespace(
            objects=SimpleNamespace(
                filter=lambda **kwargs: SimpleNamespace(update=lambda **updates: None)
            )
        ),
    )

    class FakeCell:
        objects = SimpleNamespace(filter=lambda **kwargs: [])

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.saved = False
            created_cells.append(self)

        def save(self):
            self.saved = True

    monkeypatch.setattr(dynamic_columns_module, "Cell", FakeCell)
    monkeypatch.setattr(dynamic_columns_module, "wrap_for_thread", lambda fn: fn)
    monkeypatch.setattr(
        dynamic_columns_module.ConditionalColumnView,
        "_process_row",
        lambda self, row, config, org_id: (
            None,
            {"reason": "Unresolved prompt placeholders: Missing Column"},
        ),
    )

    class FakeFuture:
        def __init__(self, result):
            self._result = result

        def result(self):
            return self._result

    class FakeExecutor:
        def __init__(self, max_workers):
            self.max_workers = max_workers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def submit(self, fn, *args, **kwargs):
            return FakeFuture(fn(*args, **kwargs))

    monkeypatch.setattr(dynamic_columns_module, "ThreadPoolExecutor", FakeExecutor)
    monkeypatch.setattr(dynamic_columns_module, "as_completed", lambda futures: futures)

    conditional_column_async = getattr(
        dynamic_columns_module.conditional_column_async,
        "__wrapped__",
        dynamic_columns_module.conditional_column_async,
    )
    conditional_column_async({}, dataset_id, 1, new_column_id, is_rerun=True)

    assert len(created_cells) == 1
    assert created_cells[0].value is None
    assert json.loads(created_cells[0].value_infos) == {
        "reason": "Unresolved prompt placeholders: Missing Column"
    }
    assert created_cells[0].status == CellStatus.ERROR.value
    assert created_cells[0].saved is True


@pytest.mark.parametrize(
    ("run_prompt_config", "configuration", "expected_template_format"),
    [
        (
            {"template_format": "mustache"},
            {"template_format": "jinja2"},
            "mustache",
        ),
        (
            {},
            {"template_format": "jinja2"},
            "jinja2",
        ),
    ],
)
def test_preview_run_prompt_column_view_passes_effective_template_format_to_populate_placeholders(
    monkeypatch,
    run_prompt_config,
    configuration,
    expected_template_format,
):
    dataset_id = uuid.uuid4()
    row_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    messages = [{"role": "user", "content": "Hello {{Input Column}}"}]
    populate_calls = []

    class OrderQuerySet:
        def order_by(self, *args):
            assert args == ("order",)
            return self

        def values_list(self, *args, **kwargs):
            assert args == ("order",)
            assert kwargs == {"flat": True}
            return [0]

    class DatasetQuerySet:
        def first(self):
            return SimpleNamespace(
                id=dataset_id,
                organization_id=organization_id,
                workspace=SimpleNamespace(id=workspace_id),
            )

    class RowQuerySet:
        def __bool__(self):
            return True

        def __iter__(self):
            return iter([SimpleNamespace(id=row_id)])

    def fake_row_filter(**kwargs):
        if "order__in" in kwargs:
            assert kwargs == {
                "dataset_id": dataset_id,
                "order__in": [0],
                "deleted": False,
            }
            return RowQuerySet()
        assert kwargs == {"dataset_id": dataset_id, "deleted": False}
        return OrderQuerySet()

    def fake_populate_placeholders(*args, **kwargs):
        populate_calls.append({"args": args, "kwargs": kwargs})
        return [{"role": "user", "content": "Hello Resolved"}]

    class FakeRunPrompt:
        def __init__(self, **kwargs):
            assert kwargs["messages"] == [{"role": "user", "content": "Hello Resolved"}]

        def litellm_response(self):
            return "preview response", {
                "metadata": {
                    "usage": {"prompt_tokens": 1},
                    "cost": {"total_cost": 0},
                }
            }

    monkeypatch.setattr(run_prompt_module.Row.objects, "filter", fake_row_filter)
    monkeypatch.setattr(
        run_prompt_module.Dataset.objects,
        "filter",
        lambda **kwargs: DatasetQuerySet(),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        fake_populate_placeholders,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "remove_empty_text_from_messages",
        lambda received_messages: received_messages,
    )
    monkeypatch.setattr(run_prompt_module, "RunPrompt", FakeRunPrompt)

    view = run_prompt_module.PreviewRunPromptColumnView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: pytest.fail(
            f"unexpected internal_server_error_response: {message}"
        ),
    )
    request = SimpleNamespace(
        validated_data={
            "dataset_id": dataset_id,
            "config": {
                "messages": messages,
                "model": "gpt-4o-mini",
                "run_prompt_config": run_prompt_config,
                "configuration": configuration,
            },
            "first_n_rows": 1,
        },
        user=SimpleNamespace(organization=SimpleNamespace(id=organization_id)),
    )

    response = run_prompt_module.PreviewRunPromptColumnView.post.__wrapped__(
        view,
        request,
    )

    assert response["responses"] == ["preview response"]
    assert len(populate_calls) == 1
    assert populate_calls[0] == {
        "args": (messages,),
        "kwargs": {
            "dataset_id": dataset_id,
            "row_id": row_id,
            "col_id": None,
            "model_name": "gpt-4o-mini",
            "template_format": expected_template_format,
            "process_media": False,
            "fail_closed": True,
        },
    }


def test_preview_run_prompt_column_view_sanitizes_local_placeholder_errors(monkeypatch):
    dataset_id = uuid.uuid4()
    row_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    raw_error = "Missing Column from https://signed.example.test/secret-token"

    class OrderQuerySet:
        def order_by(self, *args):
            return self

        def values_list(self, *args, **kwargs):
            return [0]

    class DatasetQuerySet:
        def first(self):
            return SimpleNamespace(
                id=dataset_id,
                organization_id=organization_id,
                workspace=SimpleNamespace(id=workspace_id),
            )

    class RowQuerySet:
        def __bool__(self):
            return True

        def __iter__(self):
            return iter([SimpleNamespace(id=row_id)])

    def fake_row_filter(**kwargs):
        if "order__in" in kwargs:
            return RowQuerySet()
        return OrderQuerySet()

    def fail_populate_placeholders(*args, **kwargs):
        assert kwargs["process_media"] is False
        assert kwargs["fail_closed"] is True
        raise UnresolvedPromptPlaceholdersError(raw_error)

    class ProviderShouldNotBeCalled:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RunPrompt should not be instantiated")

    monkeypatch.setattr(run_prompt_module.Row.objects, "filter", fake_row_filter)
    monkeypatch.setattr(
        run_prompt_module.Dataset.objects,
        "filter",
        lambda **kwargs: DatasetQuerySet(),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        fail_populate_placeholders,
    )
    monkeypatch.setattr(run_prompt_module, "RunPrompt", ProviderShouldNotBeCalled)

    view = run_prompt_module.PreviewRunPromptColumnView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: pytest.fail(
            f"unexpected internal_server_error_response: {message}"
        ),
    )
    request = SimpleNamespace(
        validated_data={
            "dataset_id": dataset_id,
            "config": {
                "messages": [{"role": "user", "content": "Hello {{Missing Column}}"}],
                "model": "gpt-4o-mini",
            },
            "first_n_rows": 1,
        },
        user=SimpleNamespace(organization=SimpleNamespace(id=organization_id)),
    )

    response = run_prompt_module.PreviewRunPromptColumnView.post.__wrapped__(
        view,
        request,
    )

    assert response["responses"] == [
        run_prompt_module.RUN_PROMPT_LOCAL_VALIDATION_ERROR
    ]
    assert "Missing Column" not in response["responses"][0]
    assert "signed.example.test" not in response["responses"][0]


def test_preview_run_prompt_column_view_materializes_document_media_after_validation(
    monkeypatch,
):
    dataset_id = uuid.uuid4()
    row_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    messages = [{"role": "user", "content": "Summarize {{Doc}}"}]
    populate_calls = []

    class OrderQuerySet:
        def order_by(self, *args):
            assert args == ("order",)
            return self

        def values_list(self, *args, **kwargs):
            assert args == ("order",)
            assert kwargs == {"flat": True}
            return [0]

    class DatasetQuerySet:
        def first(self):
            return SimpleNamespace(
                id=dataset_id,
                organization_id=organization_id,
                workspace=SimpleNamespace(id=workspace_id),
            )

    class RowQuerySet:
        def __bool__(self):
            return True

        def __iter__(self):
            return iter([SimpleNamespace(id=row_id)])

    def fake_row_filter(**kwargs):
        if "order__in" in kwargs:
            assert kwargs == {
                "dataset_id": dataset_id,
                "order__in": [0],
                "deleted": False,
            }
            return RowQuerySet()
        assert kwargs == {"dataset_id": dataset_id, "deleted": False}
        return OrderQuerySet()

    def fake_populate_placeholders(*args, **kwargs):
        populate_calls.append(kwargs.get("process_media", True))
        if kwargs.get("process_media") is False:
            assert kwargs["fail_closed"] is True
            return [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "__PDF_MARKER_12345678__"}],
                }
            ]
        assert kwargs["fail_closed"] is True
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize Doc"},
                    {
                        "type": "pdf_url",
                        "pdf_url": {
                            "url": "https://example.test/file.pdf",
                            "pdf_name": "Doc",
                            "file_name": "Doc",
                        },
                    },
                ],
            }
        ]

    class FakeRunPrompt:
        def __init__(self, **kwargs):
            assert kwargs["messages"][0]["content"][1]["type"] == "pdf_url"

        def litellm_response(self):
            return "preview response", {
                "metadata": {
                    "usage": {"prompt_tokens": 1},
                    "cost": {"total_cost": 0},
                }
            }

    monkeypatch.setattr(run_prompt_module.Row.objects, "filter", fake_row_filter)
    monkeypatch.setattr(
        run_prompt_module.Dataset.objects,
        "filter",
        lambda **kwargs: DatasetQuerySet(),
    )
    monkeypatch.setattr(
        run_prompt_module,
        "populate_placeholders",
        fake_populate_placeholders,
    )
    monkeypatch.setattr(
        run_prompt_module,
        "remove_empty_text_from_messages",
        lambda received_messages: received_messages,
    )
    monkeypatch.setattr(run_prompt_module, "RunPrompt", FakeRunPrompt)

    view = run_prompt_module.PreviewRunPromptColumnView()
    view._gm = SimpleNamespace(
        success_response=lambda payload: payload,
        not_found=lambda message: pytest.fail(f"unexpected not_found: {message}"),
        bad_request=lambda message: pytest.fail(f"unexpected bad_request: {message}"),
        internal_server_error_response=lambda message: pytest.fail(
            f"unexpected internal_server_error_response: {message}"
        ),
    )
    request = SimpleNamespace(
        validated_data={
            "dataset_id": dataset_id,
            "config": {
                "messages": messages,
                "model": "gpt-4o-mini",
                "run_prompt_config": {"template_format": "jinja2"},
            },
            "first_n_rows": 1,
        },
        user=SimpleNamespace(organization=SimpleNamespace(id=organization_id)),
    )

    response = run_prompt_module.PreviewRunPromptColumnView.post.__wrapped__(
        view,
        request,
    )

    assert response["responses"] == ["preview response"]
    assert populate_calls == [False, True]


def test_render_template_strict_unresolved_placeholder_in_false_branch_renders():
    assert (
        render_template(
            "{% if false %}{{ input_column }}{% endif %}Rendered",
            {},
            strict=True,
            unresolved_placeholders={"input_column"},
        )
        == "Rendered"
    )


def test_render_template_strict_unresolved_placeholder_in_evaluated_print_raises():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "{{ input_column }}",
            {},
            strict=True,
            unresolved_placeholders={"input_column"},
        )

    assert "input_column" in str(exc_info.value)


@pytest.mark.parametrize(
    "template",
    [
        "{{ missing == none }}",
        "{% if missing != none %}Rendered{% endif %}",
        "{% if missing in [] %}Rendered{% endif %}",
    ],
)
def test_render_template_strict_missing_comparison_and_membership_raise(template):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(template, {}, strict=True)

    assert "missing" in str(exc_info.value)


@pytest.mark.parametrize(
    "template",
    [
        "{{ input_column == none }}",
        "{% if input_column != none %}Rendered{% endif %}",
        "{% if input_column in [] %}Rendered{% endif %}",
    ],
)
def test_render_template_strict_unresolved_comparison_and_membership_raise(template):
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            template,
            {"input_column": ""},
            strict=True,
            unresolved_placeholders={"input_column"},
        )

    assert "input_column" in str(exc_info.value)


@pytest.mark.django_db
def test_populate_placeholders_punctuated_column_name_renders(
    dataset, output_column, row
):
    punctuated_column = Column.objects.create(
        name="Cost ($)",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    dataset.column_order.append(str(punctuated_column.id))
    dataset.save(update_fields=["column_order"])
    Cell.objects.create(
        dataset=dataset,
        column=punctuated_column,
        row=row,
        value="12.50",
    )

    result = populate_placeholders(
        _message("Cost is {{Cost ($)}}"),
        dataset.id,
        row.id,
        output_column.id,
        "gpt-4o",
    )

    assert result[0]["content"][0]["text"] == "Cost is 12.50"


def test_populate_placeholders_fail_closed_reraises_per_column_errors(monkeypatch):
    messages = _message("Hello")
    column_id = uuid.uuid4()
    monkeypatch.setattr(
        Dataset.objects,
        "get",
        lambda **kwargs: SimpleNamespace(column_order=[str(column_id)]),
    )

    def fail_column_get(**kwargs):
        raise RuntimeError("column lookup failed")

    monkeypatch.setattr(Column.objects, "get", fail_column_get)

    assert (
        populate_placeholders(
            messages,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
        )
        == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
    )

    with pytest.raises(RuntimeError, match="column lookup failed"):
        populate_placeholders(
            messages,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
            fail_closed=True,
        )


def test_populate_placeholders_fail_closed_ignores_unreferenced_stale_column_order(
    monkeypatch,
):
    messages = _message("Hello")
    missing_column_id = uuid.uuid4()
    monkeypatch.setattr(
        Dataset.objects,
        "get",
        lambda **kwargs: SimpleNamespace(column_order=[str(missing_column_id)]),
    )
    monkeypatch.setattr(
        Column.objects,
        "get",
        lambda **kwargs: (_ for _ in ()).throw(Column.DoesNotExist()),
    )

    assert populate_placeholders(
        messages,
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "gpt-4o",
        fail_closed=True,
    ) == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]


def test_populate_placeholders_fail_closed_reports_referenced_stale_column_order(
    monkeypatch,
):
    missing_column_id = uuid.uuid4()
    messages = _message(f"Hello {{{{{missing_column_id}}}}}")
    monkeypatch.setattr(
        Dataset.objects,
        "get",
        lambda **kwargs: SimpleNamespace(column_order=[str(missing_column_id)]),
    )
    monkeypatch.setattr(
        Column.objects,
        "get",
        lambda **kwargs: (_ for _ in ()).throw(Column.DoesNotExist()),
    )

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            messages,
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
            fail_closed=True,
        )

    assert str(missing_column_id) in str(exc_info.value)


def test_conditional_column_async_rerun_missing_cell_error_creates_error_cell(monkeypatch):
    saved_cells = []
    row = SimpleNamespace(id=uuid.uuid4())
    dataset_id = uuid.uuid4()
    new_column_id = uuid.uuid4()

    class FakeCell:
        class objects:
            @staticmethod
            def filter(**kwargs):
                return []

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def save(self):
            saved_cells.append(self)

    class FakeRowObjects:
        @staticmethod
        def filter(**kwargs):
            return [row]

    class FakeDatasetObjects:
        @staticmethod
        def get(**kwargs):
            return SimpleNamespace(organization=SimpleNamespace(id=uuid.uuid4()))

    class FakeColumnQuerySet:
        def update(self, **kwargs):
            return 1

    class FakeColumnObjects:
        @staticmethod
        def filter(**kwargs):
            return FakeColumnQuerySet()

    monkeypatch.setattr(dynamic_columns_module, "Cell", FakeCell)
    monkeypatch.setattr(
        dynamic_columns_module.Row,
        "objects",
        FakeRowObjects(),
    )
    monkeypatch.setattr(
        dynamic_columns_module.Dataset,
        "objects",
        FakeDatasetObjects(),
    )
    monkeypatch.setattr(
        dynamic_columns_module.Column,
        "objects",
        FakeColumnObjects(),
    )
    monkeypatch.setattr(dynamic_columns_module, "wrap_for_thread", lambda fn: fn)
    monkeypatch.setattr(
        dynamic_columns_module.ConditionalColumnView,
        "_process_row",
        lambda self, row, config, org_id=None: (None, {"reason": "validation failed"}),
    )

    dynamic_columns_module.conditional_column_async(
        [], dataset_id, 1, new_column_id, is_rerun=True
    )

    assert len(saved_cells) == 1
    assert saved_cells[0].value is None
    assert json.loads(saved_cells[0].value_infos) == {"reason": "validation failed"}
    assert saved_cells[0].status == CellStatus.ERROR.value


def _mark_prompt_placeholder_tests_for_bin_test_workflows():
    """Keep this focused regression suite visible to documented bin/test modes."""

    def marker_names(test_func):
        names = set()
        for mark in getattr(test_func, "pytestmark", []):
            mark_name = getattr(mark, "name", None)
            if mark_name is None and hasattr(mark, "mark"):
                mark_name = mark.mark.name
            if mark_name:
                names.add(mark_name)
        return names

    for name, test_func in list(globals().items()):
        if not name.startswith("test_") or not callable(test_func):
            continue

        names = marker_names(test_func)
        if "django_db" in names:
            test_func.pytestmark = [
                *getattr(test_func, "pytestmark", []),
                pytest.mark.integration,
            ]
        else:
            test_func.pytestmark = [
                *getattr(test_func, "pytestmark", []),
                pytest.mark.unit,
            ]


_mark_prompt_placeholder_tests_for_bin_test_workflows()
