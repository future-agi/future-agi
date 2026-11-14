"""Tests for Jinja2-aware variable extraction."""

import pytest

from model_hub.utils.jinja_variables import extract_jinja_variables


class TestExtractJinjaVariables:
    """extract_jinja_variables returns only top-level input variables."""

    def test_simple_variables(self):
        """Plain {{ var }} expressions are returned as-is."""
        template = "Hello {{ name }}, your score is {{ score }}."
        assert sorted(extract_jinja_variables(template)) == ["name", "score"]

    def test_for_loop_extracts_collection(self):
        """{% for item in items %} → reports 'items', not 'item' or 'item.x'."""
        template = (
            "{% for example in examples %}"
            "Input: {{ example.input }} Output: {{ example.output }}"
            "{% endfor %}"
            "Now answer: {{ query }}"
        )
        assert sorted(extract_jinja_variables(template)) == ["examples", "query"]

    def test_nested_for_loops(self):
        """Nested loops: both collection names reported, loop vars excluded."""
        template = (
            "{% for group in groups %}"
            "{% for item in group.items %}"
            "{{ item.name }}"
            "{% endfor %}"
            "{% endfor %}"
        )
        # group.items is an attribute of loop var 'group' → only 'groups' needed
        assert extract_jinja_variables(template) == ["groups"]

    def test_set_variable_excluded(self):
        """{% set x = ... %} defines a local, not an input variable."""
        template = "{% set greeting = 'hello' %}{{ greeting }} {{ name }}"
        assert extract_jinja_variables(template) == ["name"]

    def test_if_condition_variable_included(self):
        """Variables in {% if %} conditions are inputs."""
        template = (
            "{% if verbose %}Details: {{ details }}{% endif %}Summary: {{ summary }}"
        )
        assert sorted(extract_jinja_variables(template)) == [
            "details",
            "summary",
            "verbose",
        ]

    def test_dotted_access_on_non_loop_var(self):
        """{{ obj.field }} where obj is NOT a loop var → 'obj' is the input."""
        template = "Result: {{ response.text }} Score: {{ score }}"
        assert sorted(extract_jinja_variables(template)) == ["response", "score"]

    def test_filter_expressions_ignored(self):
        """{{ name | upper }} → 'name' is the variable, 'upper' is a filter."""
        template = "Hello {{ name | upper }}, your id is {{ id }}."
        assert sorted(extract_jinja_variables(template)) == ["id", "name"]

    def test_empty_string(self):
        assert extract_jinja_variables("") == []

    def test_none(self):
        assert extract_jinja_variables(None) == []

    def test_no_variables(self):
        assert extract_jinja_variables("plain text no braces") == []

    def test_loop_variable_used_standalone(self):
        """{{ item }} inside a for loop (no dot access) should not be reported."""
        template = "{% for item in items %}{{ item }}{% endfor %}"
        assert extract_jinja_variables(template) == ["items"]

    def test_for_loop_with_tuple_unpacking(self):
        """{% for k, v in mapping.items() %} → 'mapping' is the input."""
        template = (
            "{% for key, value in data.items() %}{{ key }}: {{ value }}{% endfor %}"
        )
        assert extract_jinja_variables(template) == ["data"]

    def test_auto_context_roots_not_filtered(self):
        """This function does NOT strip auto-context roots (row/span/etc).
        That's the caller's responsibility. It should return them as-is."""
        template = "{{ row.input }} {{ query }}"
        assert sorted(extract_jinja_variables(template)) == ["query", "row"]

    def test_fallback_on_malformed_template(self):
        """Malformed Jinja2 falls back to regex, extracting root names."""
        template = "{{ name }}{% for %}"
        result = extract_jinja_variables(template)
        assert "name" in result

    def test_set_with_variable_rhs(self):
        """{% set x = some_var %} excludes x but includes some_var."""
        template = "{% set greeting = name %}{{ greeting }} {{ title }}"
        result = extract_jinja_variables(template)
        assert sorted(result) == ["name", "title"]
