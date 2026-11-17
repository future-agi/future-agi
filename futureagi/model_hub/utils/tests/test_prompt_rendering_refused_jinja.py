"""A Jinja part the sandbox does not allow is refused with a plain message.

The default render leaves such a part out as empty text: "TYPE {{
name.__class__ }} END" renders "TYPE  END", and the person running the prompt
is told nothing. With refuse_unsafe the render stops with one plain line that
names the part and says what to do. Allowed templates, missing names and the
other formats render as before, and the default render is unchanged.
"""

import pytest

from model_hub.utils.prompt_rendering import render_template

ADA = {"name": "Ada"}


def refused_line(attribute):
    return (
        f"Not run: the prompt reads {attribute}, which a template is not "
        "allowed to read. Remove it from the prompt and run again."
    )


REFUSED_TEMPLATES = [
    pytest.param("TYPE {{ name.__class__ }} END", "__class__", id="attribute"),
    pytest.param("TYPE {{ name['__class__'] }} END", "__class__", id="subscript"),
    pytest.param(
        "TYPE {{ name | attr('__class__') }} END", "__class__", id="attr-filter"
    ),
    pytest.param("TYPE {{ name.__len__() }} END", "__len__", id="called"),
    pytest.param(
        "{% for c in name.__class__.__mro__ %}{{ c }}{% endfor %}",
        "__class__",
        id="in-a-loop",
    ),
]


def strict(template, context):
    return render_template(template, context, "jinja2", refuse_unsafe=True)


class TestARefusedPartStopsTheRender:
    @pytest.mark.parametrize(("template", "attribute"), REFUSED_TEMPLATES)
    def test_the_line_names_the_part_and_says_what_to_do(self, template, attribute):
        with pytest.raises(ValueError) as refused:
            strict(template, ADA)
        assert str(refused.value) == refused_line(attribute)

    def test_the_line_is_plain_words_not_the_sandbox_sentence(self):
        with pytest.raises(ValueError) as refused:
            strict("TYPE {{ name.__len__() }} END", ADA)
        message = str(refused.value)
        for raw in ("unsafe", "object", "'str'", "SecurityError", "<class"):
            assert raw not in message

    def test_a_refused_part_on_a_key_of_a_json_value_is_refused_too(self):
        context = {"order": {"tags": ["tea"]}}
        with pytest.raises(ValueError) as refused:
            strict("{{ order.tags.__class__ }}", context)
        assert str(refused.value) == refused_line("__class__")


class TestAllowedTemplatesRenderAsBefore:
    @pytest.mark.parametrize(
        ("template", "expected"),
        [
            pytest.param("HELLO {{ name }}", "HELLO Ada", id="spaced"),
            pytest.param("HELLO {{name}}", "HELLO Ada", id="unspaced"),
            pytest.param("{{ name | upper }}", "ADA", id="filter"),
            pytest.param("{{ name.upper() }}", "ADA", id="string-method"),
            pytest.param("{{ name | length }}", "3", id="length"),
        ],
    )
    def test_an_allowed_template(self, template, expected):
        assert strict(template, ADA) == expected

    def test_a_key_that_starts_with_an_underscore_still_reads_the_key(self):
        context = {"order": {"_id": "A-7781"}}
        assert strict("Order {{ order._id }}", context) == "Order A-7781"

    def test_a_missing_name_is_still_left_blank(self):
        assert strict("Hi {{ missing }}!", ADA) == "Hi !"

    def test_a_loop_over_a_list(self):
        context = {"tags": ["red", "blue"]}
        template = "{% for t in tags %}{{ loop.index }}:{{ t }} {% endfor %}"
        assert strict(template, context) == "1:red 2:blue "

    @pytest.mark.parametrize(
        ("template", "template_format", "expected"),
        [
            pytest.param("Hi {{name}}", "mustache", "Hi Ada", id="mustache"),
            pytest.param("Hi {name}", "f-string", "Hi Ada", id="f-string"),
        ],
    )
    def test_the_other_formats(self, template, template_format, expected):
        rendered = render_template(template, ADA, template_format, refuse_unsafe=True)
        assert rendered == expected


class TestTheDefaultRenderIsUnchanged:
    def test_guard_passes_on_base_too_the_default_render_still_leaves_it_out(self):
        rendered = render_template("TYPE {{ name.__class__ }} END", ADA, "jinja2")
        assert rendered == "TYPE  END"
