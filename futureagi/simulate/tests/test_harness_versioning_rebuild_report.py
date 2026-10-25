from types import SimpleNamespace
from unittest.mock import patch

from simulate.services.harness_versioning import nudges, rebuild


def test_a_value_across_a_cap_is_masked_before_the_report_cuts_it():
    value = "FAKE-CREDENTIAL-1234567890abcdef"
    near = rebuild.PROBLEM_TEXT_MAX - 10
    result = {
        "notes": "n" * (rebuild.NOTES_MAX - 18) + value,
        "checks": {
            "problems": [
                {"item": "i" * near + value, "kind": "k" * 50 + value},
                {"message": "m" * near + value},
            ],
            "warnings": ["w" * near + value],
            "not_checked": ["c" * near + value],
        },
        "left_out": [{"key": "k", "why": "q" * near + value}],
    }
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    with patch.object(nudges, "_agent_secrets", return_value={"K": {value}}):
        report = rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None), nudge, result
        )

    problems = report["problems"]
    for text, cap in [
        (report["notes"], rebuild.NOTES_MAX),
        (problems[0]["item"], rebuild.PROBLEM_TEXT_MAX),
        (problems[0]["kind"], 64),
        (problems[1]["message"], rebuild.PROBLEM_TEXT_MAX),
        (report["warnings"][0], rebuild.PROBLEM_TEXT_MAX),
        (report["not_checked"][0], rebuild.PROBLEM_TEXT_MAX),
        (report["left_out"][0]["why"], rebuild.PROBLEM_TEXT_MAX),
    ]:
        assert value[:8] not in text
        assert "[REDAC" in text
        assert len(text) <= cap


def test_only_a_bounded_prefix_of_each_text_is_masked():
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    masked = []

    def redacted(_environment, _nudge, texts):
        masked.extend(texts)
        return texts

    with patch.object(rebuild, "_redacted", redacted):
        rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None),
            nudge,
            {"notes": "n" * rebuild.RESULT_FILE_MAX},
        )

    assert len(masked[0]) == rebuild.NOTES_MAX + rebuild.SECRET_VALUE_MAX


def test_control_characters_cannot_push_a_value_past_the_masked_prefix():
    value = "FAKE-CREDENTIAL-1234567890abcdef"
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    padding = "\x01" * (rebuild.NOTES_MAX + rebuild.SECRET_VALUE_MAX - 4)
    with patch.object(nudges, "_agent_secrets", return_value={"K": {value}}):
        report = rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None),
            nudge,
            {"notes": padding + value},
        )

    assert value[:4] not in report["notes"]
    assert "[REDAC" in report["notes"]
