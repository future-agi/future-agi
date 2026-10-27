"""SSTI regression tests for AgentEvaluator._jinja_render.

Templates are user-authored and rendered server-side; a plain jinja2
Environment lets `{{ ''.__class__.__mro__[1].__subclasses__() }}` reach
subprocess/os on the eval worker (RCE + secret-read). Pin every escape
route so a switch back to plain Environment turns these red.

Mirrors futureagi/model_hub/tests/test_custom_prompt_render_template.py
::TestRenderTemplateSSTIProtection - same six payloads, adjusted to the
AgentEvaluator static-method call shape.
"""

from ee.evals.llm.agent_evaluator.evaluator import AgentEvaluator


class TestJinjaRenderSSTIProtection:
    def test_class_mro_subclasses_walk_is_blocked(self):
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        out = AgentEvaluator._jinja_render(payload, {})
        assert "subprocess" not in out.lower()
        assert "popen" not in out.lower()
        assert "<class " not in out

    def test_globals_walk_is_blocked(self):
        payload = "{{ ''.__class__.__init__.__globals__ }}"
        out = AgentEvaluator._jinja_render(payload, {})
        assert "__builtins__" not in out
        assert "'os'" not in out
        assert "posix" not in out.lower()

    def test_getattr_via_pipe_is_blocked(self):
        payload = "{{ '' | attr('__class__') | attr('__mro__') }}"
        out = AgentEvaluator._jinja_render(payload, {})
        assert "<class " not in out
        assert "type '" not in out

    def test_dict_class_walk_is_blocked(self):
        payload = "{{ {}.__class__.__base__.__subclasses__() }}"
        out = AgentEvaluator._jinja_render(payload, {})
        assert "subprocess" not in out.lower()
        assert "popen" not in out.lower()

    def test_user_object_class_access_is_blocked(self):
        class Marker:
            secret = "should-not-leak"

        payload = "{{ obj.__class__.__name__ }}"
        out = AgentEvaluator._jinja_render(payload, {"obj": Marker()})
        assert "Marker" not in out

    def test_plain_variable_still_works_under_sandbox(self):
        out = AgentEvaluator._jinja_render("Hello {{ name }}", {"name": "Karthik"})
        assert out == "Hello Karthik"


class TestJinjaRenderPreExistingBehaviour:
    """Non-regression pins on the two behaviours the fix must preserve:
    unmapped {{key}} stays literal, and TemplateSyntaxError falls back to
    plain str.replace."""

    def test_unmapped_variable_preserved_literally(self):
        out = AgentEvaluator._jinja_render("Hi {{missing}}", {})
        assert "{{ missing }}" in out or "{{missing}}" in out

    def test_syntax_error_falls_back_to_str_replace(self):
        out = AgentEvaluator._jinja_render(
            '{% if {{input}} == "yes" %}Y{% endif %}', {"input": "yes"}
        )
        assert "yes" in out
        assert "{% if" in out
