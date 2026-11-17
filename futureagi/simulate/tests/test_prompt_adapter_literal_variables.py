"""Prompt simulation variables must be inserted as literal dataset content."""

from types import SimpleNamespace
from uuid import UUID

import pytest

from simulate.services.prompt_based_agent_adapter import PromptBasedAgentAdapter


@pytest.fixture
def adapter():
    prompt_version = SimpleNamespace(
        id=UUID(int=1),
        prompt_config_snapshot={
            "configuration": {"model": "gpt-4o-mini"},
            "messages": [
                {
                    "role": "system",
                    "content": "Help with {{situation}}; again {{ situation }}.",
                },
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Scenario: {{situation}}"}],
                },
            ],
        },
    )
    return PromptBasedAgentAdapter(prompt_version, organization_id=UUID(int=2))


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(r"Cannot open C:\Users\demo", id="windows-path"),
        pytest.param(r"Match ^\d+\s+\w+$", id="regular-expression"),
        pytest.param(r"Keep \n, \r, and \t literal", id="literal-escapes"),
        pytest.param(r"Keep \1 literal", id="numbered-backreference"),
        pytest.param(r"Keep \g<name> literal", id="named-backreference"),
        pytest.param(r"Keep \g<0> literal", id="whole-match-backreference"),
        pytest.param("Ends with \\", id="trailing-backslash"),
        pytest.param(r"\\server\share", id="unc-path"),
        pytest.param("Cannot open /home/demo", id="posix-control"),
        pytest.param("Actual\nnewline", id="actual-newline-control"),
        pytest.param("", id="empty-control"),
    ],
)
def test_prepare_messages_preserves_literal_variable_values(adapter, value):
    adapter.variable_values = {"situation": value}
    history = [{"role": "user", "content": r"History keeps {{situation}} and \n"}]

    assert adapter._prepare_messages(history) == [
        {"role": "system", "content": f"Help with {value}; again {value}."},
        {
            "role": "user",
            "content": [{"type": "text", "text": f"Scenario: {value}"}],
        },
        *history,
    ]
    assert adapter.variable_values == {"situation": value}
    assert adapter.base_messages[0]["content"] == (
        "Help with {{situation}}; again {{ situation }}."
    )


def test_literal_replacement_preserves_other_variable_behavior(adapter):
    adapter.variable_values = {"situation": r"C:\Users\demo", "count": 3}

    assert adapter._inject_variables_in_string(
        "{{ situation }} / {{count}} / {{missing}} / {{situation}}"
    ) == r"C:\Users\demo / 3 / {{missing}} / C:\Users\demo"

    adapter.variable_values = {}
    assert adapter._inject_variables_in_string("{{situation}}") == "{{situation}}"
