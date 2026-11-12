"""J2: the Hub grades native observations itself; a guest's text never decides a repair."""

from uuid import uuid4

import pytest

from model_hub.services.skills_hub.r3.native_runs import _digest, grade_observation

SKILL, OTHER = str(uuid4()), str(uuid4())
ROOT = "---\nname: refund-policy\n---\nSKILL-MARKER: refund\n"


def control(action="issue_refund"):
    return {
        "expectations": {"case": {"schema": 1, "skill_id": SKILL, "action": action}},
        "catalogs": {
            "candidate": {
                SKILL: {"name": "refund-policy", "mount": "skills/refund/"},
                OTHER: {"name": "chargeback", "mount": "skills/chargeback/"},
            }
        },
        "_roots": {"candidate": {"skills/refund/": ROOT, "skills/chargeback/": "x"}},
    }


def observed(
    selected=SKILL, operations=(), answer="Your receipt is on its way.", delivered=ROOT
):
    return {
        "application": {
            "status": "completed",
            "selected": [selected],
            "output": answer,
            "events": [
                {"kind": "child_selection", "body": {"skill_id": selected}},
                {
                    "kind": "file_delivery",
                    "body": {
                        "skill_id": selected,
                        "path": "SKILL.md",
                        "content_digest": _digest(delivered),
                    },
                },
            ],
        },
        "business": {"operations": list(operations)},
    }


CASE = {
    "id": "case",
    "checks": [
        {
            "id": "c1",
            "kind": "contains",
            "config": {"text": "receipt", "case_sensitive": False},
        }
    ],
}


def grade(body, ctl=None):
    result = grade_observation(
        control=ctl or control(), case=CASE, variant="candidate", body=body
    )
    return {check.family: check.verdict for check in result["checks"]}


APPLIED = {"action": "issue_refund", "status": "applied"}


def test_correct_application_run_passes_every_family():
    assert grade(observed(operations=[APPLIED])) == {
        "selection": "pass",
        "delivery": "pass",
        "tools": "pass",
        "answer": "pass",
    }


@pytest.mark.parametrize("status", ["declined", "unknown"])
def test_text_pass_with_failed_business_tool_is_graded_as_a_tool_failure(status):
    """One-sided: only the business receipt changes; the answer text still passes."""
    result = grade(observed(operations=[{"action": "issue_refund", "status": status}]))
    assert result["answer"] == "pass" and result["tools"] == "fail"


def test_routing_near_miss_fails_selection_even_when_the_answer_looks_right():
    result = grade(observed(selected=OTHER, operations=[APPLIED]))
    assert result["selection"] == "fail"


def test_a_second_business_action_is_not_a_repair():
    twice = [APPLIED, {"action": "open_chargeback", "status": "applied"}]
    assert grade(observed(operations=twice))["tools"] == "fail"


def test_delivery_requires_the_exact_bytes_of_the_frozen_arm():
    assert (
        grade(observed(operations=[APPLIED], delivered=ROOT + "tampered"))["delivery"]
        == "fail"
    )


def test_none_expectation_requires_no_business_operation():
    ctl = control("none")
    assert grade(observed(operations=[]), ctl)["tools"] == "pass"
    assert grade(observed(operations=[APPLIED]), ctl)["tools"] == "fail"


def test_an_incomplete_application_has_no_checks_to_pass():
    body = observed(operations=[APPLIED])
    body["application"]["status"] = "paused"
    result = grade_observation(
        control=control(), case=CASE, variant="candidate", body=body
    )
    assert result["status"] == "incomplete" and result["checks"] == []
