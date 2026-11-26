"""Bounded control-plane syntax checks, explicitly not a full RE2 validator.

Go's regexp compiler remains authoritative for repetitions, ranges, Unicode class
names and other syntax outside this scanner. No Python or JavaScript compilation
is used as evidence of Go acceptance.
"""

import pytest

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer


@pytest.mark.parametrize(
    "pattern",
    [
        "[",
        "abc[",
        "[]",
        "[^]",
        "(",
        "abc)",
        "abc\\",
        "(?=abc)",
        "(?!abc)",
        "(?<=abc)",
        "(?<!abc)",
        r"(a)\1",
        r"(a)\8",
        r"(?P<word>abc)(?P=word)",
        r"(?<word>abc)\k<word>",
    ],
)
def test_bounded_pattern_errors_are_rejected_without_raw_pattern(pattern):
    private_pattern = "private-pattern-value" + pattern
    policy = AgentccGuardrailPolicy(scope="key", applied_keys=["existing-key"])
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={
            "checks": [
                {
                    "name": "input-validation",
                    "config": {"deny_patterns": [private_pattern]},
                }
            ]
        },
        partial=True,
    )
    assert not serializer.is_valid()
    assert "private-pattern-value" not in str(serializer.errors)


@pytest.mark.parametrize(
    "pattern",
    [
        r"\p{Greek}+",
        "(?U)a.*b",
        "(?P<word>abc)",
        "(?<word>abc)",
        "(?im-s:abc)",
        r"\Q[lookaround(?=text)\E",
        r"\Qunterminated quote is literal[",
        r"\[",
        r"\(",
        r"\)",
        r"\\1",
        r"\123",
        r"\12",
        r"\0",
        r"\x{03B1}",
        "[[:alpha:]]",
        "[]]",
        "[^]]",
        "[[]",
        r"[\[\]]",
        "[()]",
        "",
        "abc]",
    ],
)
def test_go_specific_and_escaped_syntax_is_not_rejected_by_foreign_regex_engine(
    pattern,
):
    policy = AgentccGuardrailPolicy(scope="key", applied_keys=["existing-key"])
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={
            "checks": [
                {"name": "input-validation", "config": {"require_patterns": [pattern]}}
            ]
        },
        partial=True,
    )
    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    "config",
    [
        {"min_message_length": -1},
        {"max_message_length": "12"},
        {"max_total_length": True},
        {"max_message_length": 1.5},
        {"max_message_length": 2**63},
        {"max_message_length": 2**63 - 1},
        {"min_message_length": 10, "max_message_length": 5},
        {"min_message_length": 10, "max_total_length": 5},
    ],
)
def test_invalid_length_limits_rejected(config):
    policy = AgentccGuardrailPolicy(scope="key", applied_keys=["existing-key"])
    serializer = AgentccGuardrailPolicySerializer(
        policy,
        data={"checks": [{"name": "input-validation", "config": config}]},
        partial=True,
    )
    assert not serializer.is_valid()
