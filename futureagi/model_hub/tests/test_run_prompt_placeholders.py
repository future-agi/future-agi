import sys
import uuid
from collections.abc import Mapping
from types import ModuleType, SimpleNamespace

import pytest
from jinja2 import TemplateSyntaxError

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views import run_prompt as run_prompt_module
from model_hub.views.run_prompt import (
    JsonStr,
    RunPrompts,
    UnresolvedPromptPlaceholdersError,
    populate_placeholders,
    process_text_with_media,
    render_template,
)
from model_hub.views.utils.utils import sanitize_uuid_for_jinja
from tfc.constants.api_calls import APICallStatusChoices


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
        )

    assert "Missing Column" in str(exc_info.value)


def test_process_text_with_media_strict_fallback_raises_for_syntax_unresolved_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            "Answer {{ Missing Column | default('x') }}",
            {},
            {},
            0,
            "gpt-4o",
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
        )

    assert "Missing Column" in str(exc_info.value)


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
    with pytest.raises(TemplateSyntaxError):
        process_text_with_media(
            "Hello {{ foo | }}",
            {},
            {},
            0,
            "gpt-4o",
        )


@pytest.mark.parametrize("template", ["Hello {{ foo", "{% if %}"])
def test_process_text_with_media_broken_jinja_raises_instead_of_returning_original_text(
    template,
):
    with pytest.raises(TemplateSyntaxError):
        process_text_with_media(
            template,
            {},
            {},
            0,
            "gpt-4o",
        )


def test_populate_placeholders_reraises_template_syntax_error_without_db(monkeypatch):
    monkeypatch.setattr(
        Dataset.objects,
        "get",
        lambda **kwargs: SimpleNamespace(column_order=[]),
    )

    with pytest.raises(TemplateSyntaxError):
        populate_placeholders(
            _message("Hello {{ foo | }}"),
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            "gpt-4o",
        )


def test_process_text_with_media_hyphenated_column_error_uses_full_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        process_text_with_media(
            "Hello {{customer-name}}",
            {},
            {},
            0,
            "gpt-4o",
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
        )

    assert "Missing Column" in str(exc_info.value)


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
        )

    assert str(input_column.id) in str(exc_info.value)


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
        created_cells.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs)

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
    assert created_cells[0]["status"] == CellStatus.ERROR.value



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
        created_cells.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4(), **kwargs)

    monkeypatch.setattr(run_prompt_module.Cell.objects, "create", fake_create)

    dataset = SimpleNamespace(id=uuid.uuid4(), workspace=SimpleNamespace(id=uuid.uuid4()))
    runner = _build_process_row_runner(dataset)

    with pytest.raises(RuntimeError, match="success save failed"):
        runner.process_row(
            SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert emitted_events == []
    assert attempted_statuses == [
        APICallStatusChoices.SUCCESS.value,
        APICallStatusChoices.ERROR.value,
    ]
    assert api_call_log_row.status == APICallStatusChoices.ERROR.value
    assert created_cells[0]["status"] == CellStatus.PASS.value


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


def test_run_prompts_process_row_unresolved_placeholders_skip_provider_and_create_error_cell(
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

    def fail_populate(*args, **kwargs):
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
    assert attempted_statuses == [APICallStatusChoices.ERROR.value]
    assert api_call_log_row.status == APICallStatusChoices.ERROR.value
    assert len(created_cells) == 1
    assert created_cells[0]["status"] == CellStatus.ERROR.value
    assert "Missing Column" in created_cells[0]["value"]


def test_run_prompts_process_row_error_save_failure_raises_without_error_cell(
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

    with pytest.raises(RuntimeError, match="error save failed"):
        runner.process_row(
            SimpleNamespace(id=uuid.uuid4(), dataset=dataset),
            SimpleNamespace(id=uuid.uuid4()),
        )

    assert api_call_log_row.status == APICallStatusChoices.PROCESSING.value
    assert attempted_statuses == [APICallStatusChoices.ERROR.value]
    assert created_cells == []
