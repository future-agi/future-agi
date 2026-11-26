"""Why a version's check failed, as the version row tells it."""

from simulate.models import HarnessAgentVersionRuntime
from simulate.services.agent_version_steps import check_failure

Status = HarnessAgentVersionRuntime.PreflightStatus


def _failed(report):
    return HarnessAgentVersionRuntime(
        preflight_status=Status.FAILED, preflight_report=report
    )


def test_missing_credentials_are_named():
    failure = check_failure(
        _failed({"missing": ["LIVEKIT_URL", "LIVEKIT_API_KEY"], "checks": []})
    )

    assert failure == {
        "code": "credentials_missing",
        "message": "Missing credentials: LIVEKIT_API_KEY, LIVEKIT_URL",
        "retryable": False,
        "missing": ["LIVEKIT_API_KEY", "LIVEKIT_URL"],
    }


def test_another_failed_check_is_named_by_its_label():
    failure = check_failure(
        _failed(
            {
                "checks": [
                    {"id": "source", "label": "Source reachable", "status": "passed"},
                    {
                        "id": "provider_target",
                        "label": "Provider agent reachable",
                        "status": "failed",
                    },
                ]
            }
        )
    )

    assert failure["code"] == "provider_target_failed"
    assert failure["message"] == "Provider agent reachable"


def test_a_reading_failure_still_comes_first():
    failure = check_failure(
        _failed(
            {
                "missing": ["LIVEKIT_URL"],
                "reading": {"status": "failed", "code": "agent_reading_crashed"},
            }
        )
    )

    assert failure["code"] == "agent_reading_crashed"


def test_a_passed_check_has_no_failure():
    passed = HarnessAgentVersionRuntime(
        preflight_status=Status.PASSED, preflight_report={"missing": ["X"]}
    )

    assert check_failure(passed) is None
