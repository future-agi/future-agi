from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.serializers.harness_job import CREDENTIAL_VALUE_MAX_CHARS
from simulate.services.harness_versioning import nudges, rebuild
from simulate.services.hosted_harness_diagnostics import _normalize_log_text


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


@pytest.mark.parametrize("length", [24_000, CREDENTIAL_VALUE_MAX_CHARS])
def test_a_credential_as_long_as_accepted_is_masked_across_the_cap(length):
    value = ("FAKE-CREDENTIAL-" + "0123456789abcdef" * length)[:length]
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    with patch.object(nudges, "_agent_secrets", return_value={"K": {value}}):
        report = rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None),
            nudge,
            {
                "notes": "n" * (rebuild.NOTES_MAX - 18) + value,
                "checks": {
                    "problems": [
                        {"message": "m" * (rebuild.PROBLEM_TEXT_MAX - 10) + value}
                    ]
                },
            },
        )

    for text in (report["notes"], report["problems"][0]["message"]):
        assert value[:8] not in text
        assert "[REDAC" in text


def test_control_characters_cannot_push_a_value_past_the_masked_prefix():
    value = "FAKE-CREDENTIAL-1234567890abcdef"
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    padding = "\x01" * (rebuild.NOTES_MAX + CREDENTIAL_VALUE_MAX_CHARS)
    with patch.object(nudges, "_agent_secrets", return_value={"K": {value}}):
        report = rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None),
            nudge,
            {"notes": padding + value},
        )

    assert value[:4] not in report["notes"]
    assert "[REDAC" in report["notes"]


# ALK normalises its result texts the same way before it cuts them
# (``fi.alk.harness.nudge``); the same table is pinned there.
@pytest.mark.parametrize(
    ("raw", "normalised"),
    [
        ("plain text", "plain text"),
        ("tab\tnew\nline\rreturn", "tab\tnew\nline\rreturn"),
        ("\x00\x01\x08\x0b\x0c\x0e\x1f\x7fkept", "kept"),
        ("\x1b[31mred\x1b[0m", "red"),
        ("\x1b[1;2;3mx\x1bMy\x1b_z", "xyz"),
        ("\x1b]0;title\x07after", "0;titleafter"),
        ("\x80\x9f\u00e9", "\x80\x9f\u00e9"),
    ],
)
def test_result_text_normalisation_is_the_one_alk_applies(raw, normalised):
    assert _normalize_log_text(raw) == normalised
