import pytest
from jinja2.sandbox import SecurityError

from agentic_eval.core_evals.fi_evals.function.functions import _get_messages


def test_get_messages_valid_template():
    validation = {
        "system_message": "System: {{ actual }}",
        "user_message": "Expected: {{ expected }}",
    }
    messages = _get_messages(validation, "val_a", "val_b")
    assert messages[0] == {"role": "system", "content": "System: val_a"}
    assert messages[1] == {"role": "user", "content": "Expected: val_b"}


@pytest.mark.parametrize("field", ["system_message", "user_message"])
def test_get_messages_sandboxed_blocks_ssti(field):
    malicious = {
        "system_message": "test",
        "user_message": "test",
    }
    malicious[field] = "{{ ''.__class__.__mro__[1].__subclasses__() }}"
    with pytest.raises(SecurityError):
        _get_messages(malicious, "a", "b")
