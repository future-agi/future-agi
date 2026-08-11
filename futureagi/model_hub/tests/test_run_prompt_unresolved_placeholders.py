"""Regression coverage for #321.

`populate_placeholders()` used to swallow every non-media exception and return
the original, unsubstituted messages. Unresolved `{{column}}` tokens then
reached the provider verbatim, and callers had no way to tell a fully rendered
prompt from a fallback.

These tests pin the fail-closed behaviour at the layer where it is decided —
`render_template()` and the unresolved-token scanner — without needing a
database, so they stay fast and do not depend on dataset fixtures.
"""

import pytest

from model_hub.views.run_prompt import (
    TEMPLATE_FORMAT_FSTRING,
    TEMPLATE_FORMAT_JINJA2,
    TEMPLATE_FORMAT_MUSTACHE,
    UnresolvedPromptPlaceholdersError,
    find_unresolved_placeholders,
    render_template,
)


class TestFindUnresolvedPlaceholders:
    def test_finds_a_surviving_token_in_a_string(self):
        assert find_unresolved_placeholders("Hello {{name}}") == ["name"]

    def test_ignores_fully_rendered_text(self):
        assert find_unresolved_placeholders("Hello Ada") == []

    def test_walks_message_content_lists_and_dicts(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize {{ input_column }}"},
                    {"type": "text", "text": "already rendered"},
                ],
            }
        ]

        assert find_unresolved_placeholders(messages) == ["input_column"]

    def test_reports_every_unresolved_token(self):
        found = find_unresolved_placeholders("{{a}} and {{b}} and {{a}}")

        assert sorted(set(found)) == ["a", "b"]

    def test_handles_empty_and_non_string_values(self):
        assert find_unresolved_placeholders("") == []
        assert find_unresolved_placeholders(None) == []
        assert find_unresolved_placeholders(42) == []


class TestRenderTemplateFailsClosed:
    """Each format must raise rather than quietly produce a wrong prompt."""

    def test_jinja_raises_on_a_missing_variable(self):
        with pytest.raises(UnresolvedPromptPlaceholdersError) as exc:
            render_template(
                "Summarize {{ input_column }}",
                {},
                template_format=TEMPLATE_FORMAT_JINJA2,
                strict=True,
            )

        assert "input_column" in exc.value.placeholders

    def test_jinja_names_every_missing_variable_not_just_the_first(self):
        with pytest.raises(UnresolvedPromptPlaceholdersError) as exc:
            render_template(
                "{{ alpha }} then {{ beta }}",
                {},
                template_format=TEMPLATE_FORMAT_JINJA2,
                strict=True,
            )

        assert "alpha" in exc.value.placeholders
        assert "beta" in exc.value.placeholders

    def test_mustache_raises_instead_of_rendering_an_empty_string(self):
        # chevron renders an unknown key as "", which is precisely how a wrong
        # prompt reaches the provider without anything looking broken.
        assert (
            render_template(
                "Summarize {{missing}}",
                {},
                template_format=TEMPLATE_FORMAT_MUSTACHE,
            )
            == "Summarize "
        )

        with pytest.raises(UnresolvedPromptPlaceholdersError) as exc:
            render_template(
                "Summarize {{missing}}",
                {},
                template_format=TEMPLATE_FORMAT_MUSTACHE,
                strict=True,
            )

        assert "missing" in exc.value.placeholders

    def test_fstring_raises_on_a_missing_key(self):
        with pytest.raises(UnresolvedPromptPlaceholdersError) as exc:
            render_template(
                "Summarize {input_column}",
                {},
                template_format=TEMPLATE_FORMAT_FSTRING,
                strict=True,
            )

        assert "input_column" in exc.value.placeholders


class TestRenderTemplateStillRendersValidPrompts:
    """Fail-closed must not break prompts that resolve correctly."""

    @pytest.mark.parametrize(
        "template_format,template",
        [
            (TEMPLATE_FORMAT_JINJA2, "Summarize {{ input_column }}"),
            (TEMPLATE_FORMAT_MUSTACHE, "Summarize {{input_column}}"),
            (TEMPLATE_FORMAT_FSTRING, "Summarize {input_column}"),
        ],
    )
    def test_resolved_placeholders_render_unchanged(
        self, template_format, template
    ):
        rendered = render_template(
            template,
            {"input_column": "the transcript"},
            template_format=template_format,
            strict=True,
        )

        assert rendered == "Summarize the transcript"
        assert find_unresolved_placeholders(rendered) == []

    def test_mustache_sections_are_not_mistaken_for_missing_values(self):
        # `#`, `/` and `^` are control syntax, not value substitution.
        rendered = render_template(
            "{{#items}}{{name}} {{/items}}",
            {"items": [{"name": "one"}, {"name": "two"}]},
            template_format=TEMPLATE_FORMAT_MUSTACHE,
            strict=True,
        )

        assert rendered.strip() == "one two"

    def test_jinja_dotted_access_resolves(self):
        rendered = render_template(
            "{{ account.name }}",
            {"account": {"name": "Acme"}},
            template_format=TEMPLATE_FORMAT_JINJA2,
            strict=True,
        )

        assert rendered == "Acme"

    def test_empty_template_is_returned_as_is(self):
        assert render_template("", {}, TEMPLATE_FORMAT_JINJA2, strict=True) == ""


class TestUnresolvedPromptPlaceholdersError:
    def test_message_names_the_offending_placeholders(self):
        error = UnresolvedPromptPlaceholdersError(["beta", "alpha"])

        assert error.placeholders == ["alpha", "beta"]
        assert "alpha" in str(error)
        assert "beta" in str(error)

    def test_duplicates_are_collapsed(self):
        assert UnresolvedPromptPlaceholdersError(["a", "a", "b"]).placeholders == [
            "a",
            "b",
        ]

    def test_is_a_valueerror_so_existing_handlers_still_catch_it(self):
        # Callers wrap prompt execution in `except Exception` / `except ValueError`;
        # this must not slip past them and reach the provider.
        assert isinstance(UnresolvedPromptPlaceholdersError(["a"]), ValueError)
