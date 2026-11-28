"""SSTI regression for JsonValidation LLM Similarity message rendering.

`_get_messages` renders caller-supplied system_message and user_message.
A plain jinja2 Environment lets a template walk to Python internals before
the strings are sent to the model. These payloads are the same six used by
TestRenderTemplateSSTIProtection. They must not produce class listings,
builtins, or the caller's object class name.
"""

from agentic_eval.core_evals.fi_evals.function.functions import _get_messages


def _rendered(payload, actual: object = "hello", expected: object = "world"):
    validation = {
        "system_message": payload,
        "user_message": "actual={{ actual }} expected={{ expected }}",
    }
    messages = _get_messages(validation, actual, expected)
    return messages[0]["content"], messages[1]["content"]


class TestJsonValidationGetMessagesSSTI:
    def test_class_mro_subclasses_walk_is_blocked(self):
        payload = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
        system, _user = _rendered(payload)
        assert "subprocess" not in system.lower()
        assert "popen" not in system.lower()
        assert "<class " not in system

    def test_globals_walk_is_blocked(self):
        payload = "{{ ''.__class__.__init__.__globals__ }}"
        system, _user = _rendered(payload)
        assert "__builtins__" not in system
        assert "'os'" not in system
        assert "posix" not in system.lower()

    def test_getattr_via_pipe_is_blocked(self):
        payload = "{{ '' | attr('__class__') | attr('__mro__') }}"
        system, _user = _rendered(payload)
        assert "<class " not in system
        assert "type '" not in system

    def test_dict_class_walk_is_blocked(self):
        payload = "{{ {}.__class__.__base__.__subclasses__() }}"
        system, _user = _rendered(payload)
        assert "subprocess" not in system.lower()
        assert "popen" not in system.lower()

    def test_context_object_class_access_is_blocked(self):
        class Marker:
            secret = "should-not-leak"

        payload = "{{ actual.__class__.__name__ }}"
        system, _user = _rendered(payload, actual=Marker(), expected="world")
        assert "Marker" not in system
        assert "should-not-leak" not in system

    def test_plain_variable_still_substitutes(self):
        _system, user = _rendered("safe", actual="hello", expected="world")
        assert user == "actual=hello expected=world"
