import uuid

import pytest

from model_hub.models.choices import (
    DataTypeChoices,
    DatasetSourceChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.views.run_prompt import (
    UnresolvedPromptPlaceholdersError,
    populate_placeholders,
    process_text_with_media,
    render_template,
)
from model_hub.views.utils.utils import sanitize_uuid_for_jinja


@pytest.fixture
def dataset(organization, workspace):
    dataset = Dataset.objects.create(
        name="Placeholder Dataset",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    dataset.column_order = []
    dataset.save()
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
    column = Column.objects.create(
        name="Output Column",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
    )
    return column


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


def test_render_template_default_non_strict_keeps_blank_missing_placeholder():
    assert render_template("Answer {{missing_column}}", {}) == "Answer "


def test_render_template_strict_preserves_jinja_default_filter():
    assert (
        render_template(
            "Answer {{ missing_column | default('fallback') }}",
            {},
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


def test_render_template_mustache_strict_preserves_section_scope():
    assert (
        render_template(
            "{{#items}}{{name}}{{/items}}{{^missing}} fallback{{/missing}}",
            {"items": [{"name": "Nested"}]},
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


def test_render_template_strict_raises_for_null_backing_placeholder():
    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        render_template(
            "Answer {{Input Column}}",
            {"Input Column": ""},
            strict=True,
            unresolved_placeholders={"Input Column"},
        )

    assert "Input Column" in str(exc_info.value)


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


@pytest.mark.django_db
def test_run_prompt_populates_known_column_name_placeholder(
    dataset, input_column, output_column, row, cell
):
    messages = [{"role": "user", "content": "Answer {{Input Column}}"}]

    result = populate_placeholders(
        messages,
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
    messages = [{"role": "user", "content": f"Answer {{{{{input_column.id}}}}}"}]

    result = populate_placeholders(
        messages,
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
    messages = [{"role": "user", "content": "Answer {{Missing Column}}"}]

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            messages,
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
    messages = [{"role": "user", "content": f"Answer {{{{{missing_uuid}}}}}"}]

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            messages,
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
    messages = [{"role": "user", "content": f"Answer {{{{{missing_uuid}}}}}"}]

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            messages,
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
    messages = [{"role": "user", "content": "Answer {{Input Column}}"}]

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            messages,
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
    messages = [{"role": "user", "content": f"Answer {{{{{input_column.id}}}}}"}]

    with pytest.raises(UnresolvedPromptPlaceholdersError) as exc_info:
        populate_placeholders(
            messages,
            dataset.id,
            row.id,
            output_column.id,
            "gpt-4o",
        )

    assert str(input_column.id) in str(exc_info.value)
