"""Templates in the f-string format are rendered by a strict renderer.

A field inserts a named value, one of its dictionary keys or a list index.
None, dates, times, decimals and ids insert the same text as before. Any
other field is refused with a plain message, and the mustache and jinja2
formats render as before.
"""

import datetime
import decimal
import uuid

import pytest

from model_hub.utils.prompt_rendering import (
    add_template_context_value,
    render_template,
)

REFUSED = "This template can only insert a named value or one of its keys."
PROFILE = '{"city": "Paris", "tags": ["red", "blue"]}'
DAY = datetime.date(2026, 11, 10)
CLOCK = datetime.time(14, 5, 6)
WHEN = datetime.datetime(2026, 11, 10, 14, 5, 6, 789)
PRICE = decimal.Decimal("1234.50")
ORDER_ID = uuid.UUID("12345678-1234-5678-1234-567812345678")
RESTORED_VALUES = [
    pytest.param(DAY, id="date"),
    pytest.param(CLOCK, id="time"),
    pytest.param(WHEN, id="datetime"),
    pytest.param(PRICE, id="decimal"),
    pytest.param(ORDER_ID, id="uuid"),
]


def fstring(template, context):
    return render_template(template, context, "f-string")


@pytest.fixture
def context():
    values = {}
    add_template_context_value(values, "question", "What is two plus two?", "f-string")
    add_template_context_value(values, "customer name", "Ana", "f-string")
    add_template_context_value(values, "profile", PROFILE, "f-string")
    values["score"] = 3.14159
    values["width"] = 8
    return values


class TestFStringRenderer:
    def test_plain_name(self, context):
        assert fstring("Q: {question}", context) == "Q: What is two plus two?"

    def test_name_with_a_space(self, context):
        assert fstring("Hi {customer name}!", context) == "Hi Ana!"

    def test_key_of_a_json_cell(self, context):
        assert fstring("City: {profile[city]}", context) == "City: Paris"

    def test_list_index_inside_a_json_cell(self, context):
        assert fstring("{profile[tags][0]}, {profile[tags][1]}", context) == "red, blue"

    def test_a_json_cell_inserts_its_original_text(self, context):
        assert fstring("Profile: {profile}", context) == f"Profile: {PROFILE}"

    def test_doubled_braces_stay_literal(self, context):
        rendered = fstring("{{question}} = {question}", context)
        assert rendered == "{question} = What is two plus two?"

    def test_format_spec_on_a_number(self, context):
        assert fstring("Score: {score:.2f}", context) == "Score: 3.14"

    def test_missing_name_still_raises_key_error(self, context):
        with pytest.raises(KeyError):
            fstring("Hi {missing}", context)

    @pytest.mark.parametrize(
        "template", ["{question.__class__}", "{profile[__class__]}"]
    )
    def test_a_field_that_is_not_a_key_or_index_is_refused(self, context, template):
        with pytest.raises(ValueError) as refused:
            fstring(template, context)
        assert str(refused.value) == REFUSED

    @pytest.mark.parametrize(
        "template", ["{profile!r}", "{profile:>60}", "{score:{width}}"]
    )
    def test_unsupported_conversion_or_format_spec_is_refused(self, context, template):
        with pytest.raises(ValueError) as refused:
            fstring(template, context)
        assert str(refused.value) == REFUSED

    @pytest.mark.parametrize("template", ["{}", "{0}"])
    def test_an_empty_or_numeric_name_is_refused(self, context, template):
        context[""] = "blank"
        context["0"] = "zero"
        with pytest.raises(ValueError) as refused:
            fstring(template, context)
        assert str(refused.value) == REFUSED

    @pytest.mark.parametrize(
        "value, spec",
        [
            pytest.param("Ana", ">" + "0" * 999 + "5", id="str"),
            pytest.param(WHEN, "%Y" * 500 + "!", id="datetime"),
            pytest.param(PRICE, ">" + "0" * 999 + "5", id="decimal"),
        ],
    )
    def test_a_format_spec_over_1000_characters_is_refused(self, value, spec):
        assert len(spec) == 1001
        with pytest.raises(ValueError) as refused:
            fstring("{value:" + spec + "}", {"value": value})
        assert str(refused.value) == REFUSED

    @pytest.mark.parametrize(
        "value, spec",
        [
            pytest.param("Ana", ">" + "0" * 998 + "5", id="str"),
            pytest.param(WHEN, "%Y" * 500, id="datetime"),
        ],
    )
    def test_a_format_spec_of_1000_characters_renders(self, value, spec):
        assert len(spec) == 1000
        rendered = fstring("{value:" + spec + "}", {"value": value})
        assert rendered == format(value, spec)

    @pytest.mark.parametrize(
        "digits",
        [pytest.param(21, id="21-digits"), pytest.param(5000, id="5000-digits")],
    )
    def test_a_list_index_over_20_digits_is_refused(self, context, digits):
        with pytest.raises(ValueError) as refused:
            fstring("{profile[tags][" + "1" * digits + "]}", context)
        assert str(refused.value) == REFUSED

    @pytest.mark.parametrize("template", ["Hi {question", "Hi }", "Hi {"])
    def test_malformed_braces_raise_a_plain_value_error(self, context, template):
        with pytest.raises(ValueError, match="brace"):
            fstring(template, context)

    def test_render_template_uses_the_strict_renderer(self, context):
        from model_hub.utils.prompt_rendering import render_fstring_template

        template = "{customer name} lives in {profile[city]}"
        assert render_fstring_template(template, context) == "Ana lives in Paris"
        assert fstring(template, context) == "Ana lives in Paris"


class Stamp(datetime.datetime):
    label = "stamp"


class Amount(decimal.Decimal):
    label = "amount"


class TestNoneDatesNumbersAndIdsRenderAsBefore:
    def test_a_none_value_inserts_none(self):
        assert fstring("Note: {note}", {"note": None}) == "Note: " + str(None)

    @pytest.mark.parametrize("template", ["{order[note]}", "{order[tags][0]}"])
    def test_a_key_or_index_that_is_none_inserts_none(self, template):
        values = {}
        add_template_context_value(
            values, "order", '{"note": null, "tags": [null]}', "f-string"
        )
        assert fstring(template, values) == str(None)

    @pytest.mark.parametrize("value", RESTORED_VALUES)
    def test_a_plain_field_inserts_the_value_text(self, value):
        assert fstring("Value: {value}", {"value": value}) == "Value: " + str(value)

    @pytest.mark.parametrize("value", RESTORED_VALUES)
    def test_a_key_that_holds_the_value_inserts_its_text(self, value):
        assert fstring("{order[value]}", {"order": {"value": value}}) == str(value)

    @pytest.mark.parametrize("value", RESTORED_VALUES)
    @pytest.mark.parametrize(
        "conversion, builtin",
        [
            pytest.param("s", str, id="s"),
            pytest.param("r", repr, id="r"),
            pytest.param("a", ascii, id="a"),
        ],
    )
    def test_a_conversion_uses_the_matching_builtin(self, value, conversion, builtin):
        rendered = fstring("{value!" + conversion + "}", {"value": value})
        assert rendered == builtin(value)

    @pytest.mark.parametrize(
        "value, spec",
        [
            pytest.param(DAY, "%d %B %Y", id="date"),
            pytest.param(CLOCK, "%H:%M", id="time"),
            pytest.param(WHEN, "%Y-%m-%d %H:%M:%S", id="datetime"),
            pytest.param(PRICE, ",.1f", id="decimal"),
        ],
    )
    def test_a_format_spec_uses_the_builtin_format(self, value, spec):
        rendered = fstring("{value:" + spec + "}", {"value": value})
        assert rendered == format(value, spec)

    def test_a_conversion_and_format_spec_on_an_id(self):
        rendered = fstring("{value!s:>40}", {"value": ORDER_ID})
        assert rendered == format(str(ORDER_ID), ">40")

    @pytest.mark.parametrize("template", ["{value:>40}", "{value:x}"])
    def test_a_format_spec_on_an_id_gets_the_plain_refusal(self, template):
        with pytest.raises(ValueError) as refused:
            fstring(template, {"value": ORDER_ID})
        assert str(refused.value) == REFUSED

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(Stamp(2026, 11, 10, 14, 5, 6), id="datetime"),
            pytest.param(Amount("1234.50"), id="decimal"),
        ],
    )
    @pytest.mark.parametrize("template", ["{value}", "{value!s}", "{value:>20}"])
    def test_a_subclass_with_its_own_attribute_is_refused(self, value, template):
        with pytest.raises(ValueError) as refused:
            fstring(template, {"value": value})
        assert str(refused.value) == REFUSED


class TestOtherFormatsRenderAsBefore:
    def test_mustache(self):
        values = {}
        add_template_context_value(values, "profile", PROFILE, "mustache")
        add_template_context_value(values, "name", "Ana", "mustache")
        rendered = render_template(
            "Hi {{name}} from {{profile.city}}", values, "mustache"
        )
        assert rendered == "Hi Ana from Paris"

    def test_jinja2(self):
        values = {}
        add_template_context_value(values, "profile", PROFILE, "jinja2")
        add_template_context_value(values, "customer name", "Ana", "jinja2")
        rendered = render_template(
            "Hi {{customer name}} from {{ profile.city | upper }}", values, "jinja2"
        )
        assert rendered == "Hi Ana from PARIS"

    def test_default_format_is_jinja2(self):
        assert render_template("Hi {{ name }}", {"name": "Ana"}) == "Hi Ana"

    def test_unknown_format_is_still_refused(self):
        with pytest.raises(ValueError, match="Unknown template_format"):
            render_template("Hi {name}", {"name": "Ana"}, "handlebars")
