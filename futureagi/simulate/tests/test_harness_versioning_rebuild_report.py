import time
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


def _report(result, value):
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    with patch.object(nudges, "_agent_secrets", return_value={"K": {value}}):
        return rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None), nudge, result
        )


# ALK cuts a problem text at the platform's cap plus the longest credential.
ALK_CUT = rebuild.PROBLEM_TEXT_MAX + CREDENTIAL_VALUE_MAX_CHARS


@pytest.mark.parametrize(
    "secret_text",
    [
        # Masking the earlier copies shrinks the text, pulling the cut one forward.
        lambda value: "boom: " + value * 3,
        # A masked assignment shrinks it the same way.
        lambda value: "token=" + "x" * 65_600 + " " + value,
    ],
    ids=["repeated", "assignment"],
)
def test_a_value_cut_short_by_alk_is_masked_where_masking_pulls_it_in(secret_text):
    value = ("FAKE-CREDENTIAL-" + "0123456789abcdef" * 2_000)[:22_200]
    cut = secret_text(value)[:ALK_CUT]

    report = _report({"checks": {"problems": [{"message": cut}]}}, value)

    message = report["problems"][0]["message"]
    assert value[:8] not in message
    assert "[REDAC" in message


POSTGRES = "postgresql://svcadmin:Hx83jdPq20sLm@prod-db.internal:5432/app"


def _shrinking_pad(length):
    # Masked to "token=[REDACTED] ", pulling what follows into the cap.
    return "token=" + "x" * (length - 7) + " "


@pytest.mark.parametrize(
    ("values", "secret_text"),
    [
        (
            ["AKIAIOSFODNN7EXAMPLE secret=wJalrXUtnFEMIK7MDENGbPxRfiCY"],
            lambda: _shrinking_pad(ALK_CUT - 40)
            + "AKIAIOSFODNN7EXAMPLE secret=wJalrXUtnFEMIK7MDENGbPxRfiCY",
        ),
        (
            [POSTGRES + "?token=Q1w2E3r4T5y6U7i8"],
            lambda: _shrinking_pad(ALK_CUT - len(POSTGRES) - 24 + 6)
            + POSTGRES
            + "?token=Q1w2E3r4T5y6U7i8",
        ),
        (
            [POSTGRES, "Hx83jdPq20sLm"],
            lambda: _shrinking_pad(ALK_CUT - 40) + POSTGRES,
        ),
        (
            # The cut value starts inside a whole earlier one.
            [POSTGRES[:-3] + "appdb", "appdb_secret_KJH23k4j5h6g7f8d9s0a"],
            lambda: _shrinking_pad(ALK_CUT - 70)
            + POSTGRES[:-3]
            + "appdb_secret_KJH23k4j5h6g7f8d9s0a",
        ),
        (
            # A whole value ends with the cut one's start.
            ["prod-db-Zq9wX7vT3kLm5678", "5678XYZ-other-secret"],
            lambda: _shrinking_pad(ALK_CUT - 30) + "prod-db-Zq9wX7vT3kLm5678XYZ-oth",
        ),
    ],
    ids=[
        "assignment-inside",
        "query-token-inside",
        "nested-value",
        "inside-a-whole-value",
        "after-a-whole-value",
    ],
)
def test_a_cut_value_partly_masked_inside_is_still_masked(values, secret_text):
    cut = secret_text()[:ALK_CUT]
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )
    with patch.object(nudges, "_agent_secrets", return_value={"K": set(values)}):
        report = rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None),
            nudge,
            {"checks": {"problems": [{"message": cut}]}},
        )

    message = report["problems"][0]["message"]
    for leaked in ("AKIAIOSF", "svcadmin", "prod-db", "Hx83jdPq", "Zq9wX7vT"):
        assert leaked not in message


def test_a_cut_run_of_one_repeated_value_is_masked_quickly():
    values = ["aaaaaaaa", *(f"a{index:04d}" + "b" * 35 for index in range(500))]
    nudge = SimpleNamespace(
        trigger_agent_version_id="a1",
        base_agent_version_id=None,
        base_environment_version=None,
        draft_snapshot={},
    )

    started = time.monotonic()
    with patch.object(nudges, "_agent_secrets", return_value={"K": set(values)}):
        report = rebuild._report(
            SimpleNamespace(id="env", agent_version_id=None),
            nudge,
            {"checks": {"problems": [{"message": "a" * ALK_CUT}]}},
        )

    assert time.monotonic() - started < 5
    assert "aaaa" not in report["problems"][0]["message"]


def test_a_value_the_report_cuts_short_is_masked():
    # A stored value longer than accepted today, straddling the notes' own cut.
    value = ("FAKE-CREDENTIAL-" + "0123456789abcdef" * 6_000)[:90_000]
    notes = "n" * (rebuild.NOTES_MAX - 10) + value

    report = _report({"notes": notes}, value)

    assert value[:8] not in report["notes"]


def test_a_private_key_header_flood_is_masked_quickly():
    notes = "-----BEGIN PRIVATE KEY-----" * 15_000

    started = time.monotonic()
    _report({"notes": notes, "checks": {"problems": [{"message": notes}]}}, "x" * 8)

    assert time.monotonic() - started < 5


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
