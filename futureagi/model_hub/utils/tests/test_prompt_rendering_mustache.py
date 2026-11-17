"""Templates in the mustache format are rendered by a strict renderer.

A name resolves by dictionary key and list index only, a value is never
called, and a partial tag never reads a file. Every correct template renders
the same text as before.
"""

import chevron
import pytest

from model_hub.utils.prompt_rendering import (
    add_template_context_value,
    render_template,
)

PROFILE = '{"city": "Paris", "tags": ["red", "blue"], "zip": null}'
MARKUP = 'Tom & Jerry <b>"hi"</b>'
ORDER = {
    "customer": "Ana",
    "items": [{"name": "tea", "qty": 2}, {"name": "cake", "qty": 1}],
}


def mustache(template, context):
    return render_template(template, context, "mustache")


def _json_cell(name, raw):
    values = {}
    add_template_context_value(values, name, raw, "mustache")
    return values


# Correct templates and their contexts. The expected text of each is what the
# mustache format rendered before, computed in the test.
CORRECT_TEMPLATES = [
    pytest.param("Hello {{name}}!", {"name": "Ana"}, id="variable"),
    pytest.param("{{  name  }}", {"name": "Ana"}, id="variable-with-spaces"),
    pytest.param("{{Input Column}}", {"Input Column": "x"}, id="name-with-a-space"),
    pytest.param("{{text}}", {"text": MARKUP}, id="html-escaping"),
    pytest.param("{{{text}}}", {"text": MARKUP}, id="triple-brace"),
    pytest.param("{{& text}}", {"text": MARKUP}, id="ampersand"),
    pytest.param("[{{missing}}]", {"name": "Ana"}, id="missing-name"),
    pytest.param("{{price}} {{count}}", {"price": 1.5, "count": 3}, id="numbers"),
    pytest.param("{{#items}}{{name}} x{{qty}}; {{/items}}", ORDER, id="list-of-dicts"),
    pytest.param(
        "{{#items}}{{name}} for {{customer}}. {{/items}}", ORDER, id="outer-scope"
    ),
    pytest.param(
        "{{#order}}{{#items}}[{{name}}]{{/items}}{{/order}}",
        {"order": ORDER},
        id="nested-sections",
    ),
    pytest.param(
        "{{#a}}{{#b}}{{c}}/{{d}}{{/b}}{{/a}}",
        {"a": {"b": {"c": "inner"}}, "d": "outer"},
        id="nested-dict-sections",
    ),
    pytest.param("{{#name}}Hi {{name}}{{/name}}", {"name": "Ana"}, id="text-section"),
    pytest.param(
        "{{# flag }}on{{/ flag }}{{^flag}}off{{/flag}}",
        {"flag": True},
        id="true-section",
    ),
    pytest.param("{{#zero}}shown{{/zero}}[{{zero}}]", {"zero": 0}, id="zero"),
    pytest.param("{{#flag}}shown{{/flag}}[{{flag}}]", {"flag": False}, id="false"),
    pytest.param(
        "{{#nothing}}shown{{/nothing}}[{{nothing}}]{{^nothing}}none{{/nothing}}",
        {"nothing": None},
        id="none",
    ),
    pytest.param(
        "{{^items}}no items{{/items}}", {"items": []}, id="inverted-empty-list"
    ),
    pytest.param("{{^items}}no items{{/items}}", ORDER, id="inverted-list"),
    pytest.param(
        "{{^missing}}fallback {{customer}}{{/missing}}", ORDER, id="inverted-missing"
    ),
    pytest.param("a{{! a comment }}b", {}, id="comment"),
    pytest.param(
        "{{#tags}}<{{.}}>{{/tags}}", {"tags": ["red", "blue"]}, id="implicit-iterator"
    ),
    pytest.param("{{#nums}}{{.}},{{/nums}}", {"nums": [1, 0, 2]}, id="list-of-numbers"),
    pytest.param("{{a.b.c}}", {"a": {"b": {"c": "deep"}}}, id="dotted-dictionaries"),
    pytest.param("{{items.1.name}} {{items.0.qty}}", ORDER, id="dotted-list-index"),
    pytest.param("{{name.0}}{{name.2}}", {"name": "Ana"}, id="dotted-text-index"),
    pytest.param(
        "{{tags.-1}} [{{tags.5}}]",
        {"tags": ["red", "blue"]},
        id="negative-and-missing-index",
    ),
    pytest.param("{{profile}}", _json_cell("profile", PROFILE), id="json-cell"),
    pytest.param(
        "{{profile.city}} {{profile.tags.1}} [{{profile.zip}}]",
        _json_cell("profile", PROFILE),
        id="json-cell-keys",
    ),
    pytest.param(
        "{{#profile}}{{city}}: {{#tags}}{{.}} {{/tags}}{{/profile}}",
        _json_cell("profile", PROFILE),
        id="json-cell-section",
    ),
    pytest.param(
        "{{=<% %>=}}Hi <% name %> {{name}}<%={{ }}=%> {{name}}",
        {"name": "Ana"},
        id="set-delimiter",
    ),
    pytest.param(
        "{{=| |=}}\nHi |name|\n|={{ }}=|\nBye {{name}}\n",
        {"name": "Ana"},
        id="standalone-set-delimiter",
    ),
    pytest.param(
        "Begin\n{{#flag}}\n  inside\n{{/flag}}\nEnd\n",
        {"flag": True},
        id="standalone-section",
    ),
    pytest.param("Begin\n  {{! note }}\nEnd", {}, id="standalone-comment"),
    pytest.param(
        "List:\n  {{#items}}\n  - {{name}}\n  {{/items}}\nDone",
        ORDER,
        id="standalone-indented-list",
    ),
    pytest.param(
        " {{name}} \n\t{{#flag}} x {{/flag}}\n",
        {"name": "Ana", "flag": True},
        id="inline-whitespace",
    ),
]


class TestMustacheResolvesKeysAndIndexesOnly:
    def test_a_dotted_name_on_text_resolves_by_index_only(self):
        assert mustache("[{{name.__class__}}]", {"name": "Ana"}) == "[]"

    def test_a_section_on_a_name_that_is_not_a_key_or_index_is_not_called(self):
        context = {"name": "Ana"}
        rendered = mustache("[{{#name.upper}}x{{/name.upper}}]", context)
        assert rendered == mustache("[{{#missing}}x{{/missing}}]", context)
        assert rendered == "[]"

    def test_a_section_on_a_callable_value_does_not_call_it(self):
        calls = []

        def greet(*args):
            calls.append(args)
            return "called"

        rendered = mustache(
            "[{{#greet}}hi {{name}}{{/greet}}]", {"greet": greet, "name": "Ana"}
        )
        assert calls == []
        assert rendered == "[hi Ana]"

    def test_a_partial_tag_does_not_read_a_file(self, tmp_path, monkeypatch):
        partial = tmp_path / "probe_partial.mustache"
        partial.write_text("text of the partial file", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        rendered = mustache("before {{> probe_partial}} after", {})
        assert "text of the partial file" not in rendered
        assert rendered == "before  after"

    def test_render_template_uses_the_strict_renderer(self):
        from model_hub.utils.prompt_rendering import render_mustache_template

        context = _json_cell("profile", PROFILE)
        template = "{{profile.city}} {{profile.tags.0}}"
        assert render_mustache_template(template, context) == "Paris red"
        assert mustache(template, context) == "Paris red"


class TestCorrectTemplatesRenderAsBefore:
    def test_the_table_covers_at_least_25_templates(self):
        assert len(CORRECT_TEMPLATES) >= 25

    @pytest.mark.parametrize("template, context", CORRECT_TEMPLATES)
    def test_same_text_as_before(self, template, context):
        assert mustache(template, context) == chevron.render(template, context)
