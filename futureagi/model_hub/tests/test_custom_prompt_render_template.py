"""Unit tests for CustomPromptEvaluator._render_template.

The helper is called at three sites (System via self.system_prompt, every
User / Assistant turn from the multi-message editor, and it mirrors the
edge-case handling rule_prompt already has inline). These tests pin the
edge cases so a future change to the helper can't silently regress the
multi-turn variable-substitution feature.
"""

import pytest
from jinja2.sandbox import SandboxedEnvironment

from agentic_eval.core_evals.fi_evals.llm.custom_prompt_evaluator.evaluator import (
    CustomPromptEvaluator,
)
from agentic_eval.core_evals.fi_utils.utils import PreserveUndefined


@pytest.fixture
def stub_evaluator():
    """Build the minimum evaluator surface _render_template reads: just
    self.env. Mirrors production exactly - SandboxedEnvironment, same
    delimiters, same undefined policy - so the SSTI regression tests
    below actually exercise what production would see.
    """
    inst = CustomPromptEvaluator.__new__(CustomPromptEvaluator)
    inst.env = SandboxedEnvironment(
        variable_start_string="{{",
        variable_end_string="}}",
        undefined=PreserveUndefined,
    )
    return inst


class TestRenderTemplatePlainSubstitution:
    def test_single_variable(self, stub_evaluator):
        out = stub_evaluator._render_template("Hello {{name}}", {"name": "Karthik"})
        assert out == "Hello Karthik"

    def test_multiple_variables(self, stub_evaluator):
        out = stub_evaluator._render_template(
            "Input: {{input}}, Expected: {{expected}}",
            {"input": "yes", "expected": "no"},
        )
        assert out == "Input: yes, Expected: no"

    def test_whitespace_inside_braces(self, stub_evaluator):
        out = stub_evaluator._render_template("Hi {{ name }}", {"name": "Karthik"})
        assert out == "Hi Karthik"

    def test_empty_template_returns_empty(self, stub_evaluator):
        assert stub_evaluator._render_template("", {"x": 1}) == ""

    def test_none_template_returns_none(self, stub_evaluator):
        assert stub_evaluator._render_template(None, {"x": 1}) is None


class TestRenderTemplateJinjaBlockConstructs:
    """Same Environment as rule_prompt so {% if %} / {% for %} / filters
    all work at the helper sites too."""

    def test_if_then_branch(self, stub_evaluator):
        out = stub_evaluator._render_template(
            '{% if x == "yes" %}Y{% else %}N{% endif %}',
            {"x": "yes"},
        )
        assert out == "Y"

    def test_if_else_branch(self, stub_evaluator):
        out = stub_evaluator._render_template(
            '{% if x == "yes" %}Y{% else %}N{% endif %}',
            {"x": "no"},
        )
        assert out == "N"

    def test_for_loop_over_list(self, stub_evaluator):
        out = stub_evaluator._render_template(
            "{% for item in items %}{{item}},{% endfor %}",
            {"items": ["a", "b", "c"]},
        )
        assert out == "a,b,c,"

    def test_filter_pipe(self, stub_evaluator):
        out = stub_evaluator._render_template(
            "{{ name | upper }}", {"name": "karthik"}
        )
        assert out == "KARTHIK"


class TestRenderTemplateEdgeCases:
    def test_variable_name_with_spaces_substitutes_from_context(self, stub_evaluator):
        """{{TTS Testing}} would raise TemplateSyntaxError in Jinja
        (spaces not allowed in variable names). The helper preprocesses
        it into a string replacement from safe_context."""
        out = stub_evaluator._render_template(
            "Result: {{TTS Testing}}", {"TTS Testing": "ok"}
        )
        assert out == "Result: ok"

    def test_variable_name_with_spaces_missing_from_context_stays_literal(
        self, stub_evaluator
    ):
        """When the spaced-name key is missing from context AND from the
        fallback_kwargs, the raw {{name}} form is preserved."""
        out = stub_evaluator._render_template(
            "Result: {{TTS Testing}}", {}, {}
        )
        assert out == "Result: {{TTS Testing}}"

    def test_variable_name_with_spaces_fallback_kwargs(self, stub_evaluator):
        """The fallback_kwargs is consulted for spaced-name keys the
        safe_context lost during path 1's preprocessing."""
        out = stub_evaluator._render_template(
            "Result: {{TTS Testing}}", {}, {"TTS Testing": "from-kwargs"}
        )
        assert out == "Result: from-kwargs"

    def test_dotted_flat_key_is_flattened(self, stub_evaluator):
        """A top-level flat key with a dot ('obj.field') gets flattened
        into nested access so Jinja resolves {{obj.field}} via attribute
        lookup rather than emitting a PreserveUndefined literal."""
        out = stub_evaluator._render_template(
            "Hello {{obj.field}}", {"obj.field": "world"}
        )
        assert out == "Hello world"

    def test_dotted_real_dict_works_natively(self, stub_evaluator):
        """When context holds a real dict, Jinja's normal attribute
        access resolves it - the helper's flattening path is a no-op."""
        out = stub_evaluator._render_template(
            "Hello {{obj.field}}", {"obj": {"field": "world"}}
        )
        assert out == "Hello world"

    def test_unmapped_variable_preserved_literally(self, stub_evaluator):
        """PreserveUndefined keeps unmapped {{name}} tokens as literal
        text so the LLM can still see them, matching rule_prompt."""
        out = stub_evaluator._render_template("Hi {{missing}}", {})
        assert "{{ missing }}" in out or "{{missing}}" in out

    def test_syntax_error_falls_back_to_str_replace(self, stub_evaluator):
        """{{var}} inside {% %} is invalid Jinja. The helper catches the
        TemplateSyntaxError and falls back to plain str.replace so the
        variables still get substituted (only the {% %} tags remain
        literal), matching rule_prompt's fallback."""
        out = stub_evaluator._render_template(
            '{% if {{input}} == "yes" %}Y{% endif %}', {"input": "yes"}
        )
        assert "yes" in out  # {{input}} got replaced
        assert "{% if" in out  # tag structure preserved (fallback path)

    def test_syntax_error_fallback_leaves_unknown_vars_alone(self, stub_evaluator):
        """The str.replace fallback only replaces known safe_context keys.
        A totally unknown reference stays literal."""
        out = stub_evaluator._render_template(
            '{% if {{missing}} == "yes" %}Y{% endif %}', {}
        )
        assert "{{missing}}" in out
        assert "{% if" in out


class TestRenderTemplateContextMutation:
    """Path 1 (rule_prompt) mutates safe_context before paths 2/3 run.
    Verify the helper's mutation behaviour is consistent with that."""

    def test_spaced_name_key_popped_from_context(self, stub_evaluator):
        """When a spaced-name variable is substituted, its key is popped
        from safe_context. A second call with the same context won't
        find it - which is the whole reason the helper accepts a
        fallback_kwargs argument."""
        ctx = {"TTS Testing": "ok"}
        stub_evaluator._render_template("Result: {{TTS Testing}}", ctx)
        assert "TTS Testing" not in ctx

    def test_dotted_flat_key_replaced_by_nested_form(self, stub_evaluator):
        """After flattening, the flat 'obj.field' key is removed from
        safe_context and 'obj' now holds a nested dict."""
        ctx = {"obj.field": "world"}
        stub_evaluator._render_template("Hello {{obj.field}}", ctx)
        assert "obj.field" not in ctx
        assert isinstance(ctx.get("obj"), dict)
        assert ctx["obj"].get("field") == "world"


class TestRenderTemplateSSTIProtection:
    """SSTI / RCE regression: eval templates are user-authored, so the
    Jinja environment MUST reject attribute-walking payloads that reach
    Python internals (subprocess, os.environ, ...). Sandbox catches
    these; a plain Environment does not. Pin every well-known escape
    route so a future switch back to Environment turns these red."""

    def test_class_mro_subclasses_walk_is_blocked(self, stub_evaluator):
        """The classic SSTI payload: `''.__class__.__mro__[1].__subclasses__()`
        walks up to `object` and lists every subclass, from which the
        attacker can pick `subprocess.Popen` or similar. Sandbox raises
        SecurityError on `.__class__`, the helper catches it as a
        TemplateSyntaxError-shaped fallback, and the raw payload stays
        as literal text - no Python internals leaked."""
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        out = stub_evaluator._render_template(payload, {})
        # No subclass name should have been rendered into the output.
        assert "subprocess" not in out.lower()
        assert "popen" not in out.lower()
        assert "<class " not in out

    def test_globals_walk_is_blocked(self, stub_evaluator):
        """A different escape route: `x.__globals__` on any function
        reaches module-level bindings including `os`, `__builtins__`,
        etc. Sandbox rejects `.__globals__` access."""
        payload = "{{ ''.__class__.__init__.__globals__ }}"
        out = stub_evaluator._render_template(payload, {})
        assert "__builtins__" not in out
        assert "'os'" not in out
        assert "posix" not in out.lower()

    def test_getattr_via_pipe_is_blocked(self, stub_evaluator):
        """`{{ obj|attr('__class__') }}` is the filter-syntax variant
        of the same escape. Sandbox's default `is_safe_attribute`
        rejects dunders here too."""
        payload = "{{ '' | attr('__class__') | attr('__mro__') }}"
        out = stub_evaluator._render_template(payload, {})
        assert "<class " not in out
        assert "type '" not in out

    def test_dict_class_walk_is_blocked(self, stub_evaluator):
        """Dict variant: `{}.__class__.__base__.__subclasses__()`.
        Same intent, different starting object."""
        payload = "{{ {}.__class__.__base__.__subclasses__() }}"
        out = stub_evaluator._render_template(payload, {})
        assert "subprocess" not in out.lower()
        assert "popen" not in out.lower()

    def test_config_object_walk_is_blocked(self, stub_evaluator):
        """Even when a user-supplied context value is a Python object,
        Jinja shouldn't hand out its `__class__`. This regression pin
        assumes future changes to the sandbox class don't accidentally
        loosen `is_safe_attribute` for context objects."""

        class Marker:
            secret = "should-not-leak"

        payload = "{{ obj.__class__.__name__ }}"
        out = stub_evaluator._render_template(payload, {"obj": Marker()})
        assert "Marker" not in out

    def test_plain_variable_still_works_under_sandbox(self, stub_evaluator):
        """Sanity: locking down the environment mustn't break the ordinary
        substitution the entire feature is built on."""
        out = stub_evaluator._render_template(
            "Hello {{ name }}", {"name": "Karthik"}
        )
        assert out == "Hello Karthik"
