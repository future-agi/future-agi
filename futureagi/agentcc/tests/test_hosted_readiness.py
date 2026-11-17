import json
from unittest.mock import patch

import pytest
from django.test import RequestFactory
from django.urls import resolve

from agentcc.readiness import readiness


@pytest.mark.parametrize(
    "database,shared,expected,reason",
    [
        (False, True, 503, "database_or_schema"),
        (True, False, 503, "shared_state"),
        (True, True, 200, None),
    ],
)
def test_hosted_readiness_dependencies(database, shared, expected, reason):
    with (
        patch("agentcc.readiness.deployment_profile", return_value="hosted"),
        patch("agentcc.readiness.validate_hosted_configuration"),
        patch("agentcc.readiness.database_ready", return_value=database),
        patch("agentcc.readiness.shared_state_ready", return_value=shared),
    ):
        response = readiness(RequestFactory().get("/readyz/"))
    assert response.status_code == expected
    assert json.loads(response.content).get("reason") == reason
    assert response["Cache-Control"] == "no-store"


def test_readiness_does_not_expose_dependency_exception():
    with (
        patch("agentcc.readiness.deployment_profile", return_value="hosted"),
        patch("agentcc.readiness.validate_hosted_configuration"),
        patch(
            "agentcc.readiness.database_ready",
            side_effect=RuntimeError("sensitive-dsn"),
        ),
    ):
        response = readiness(RequestFactory().get("/readyz/"))
    assert response.status_code == 503
    assert b"sensitive-dsn" not in response.content


def test_readiness_is_routed_separately_from_liveness():
    assert resolve("/readyz/").func is readiness
    assert resolve("/health/").func is not readiness


def test_readiness_uses_isolated_bounded_database_connection():
    from agentcc.readiness import database_ready

    with (
        patch("agentcc.readiness.connections") as connections,
        patch(
            "agentcc.readiness.required_migrations",
            return_value=({("prism", "0051")}, {}),
        ),
    ):
        clone = connections.__getitem__.return_value.copy.return_value
        clone.vendor = "postgresql"
        clone.settings_dict = {"OPTIONS": {"connect_timeout": 60}}
        cursor = clone.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = []
        assert not database_ready()
        cursor.fetchall.return_value = [("prism", "0051")]
        assert database_ready()
        assert clone.settings_dict["OPTIONS"]["connect_timeout"] == 2
        assert "statement_timeout=1000" in clone.settings_dict["OPTIONS"]["options"]
        assert clone.close.call_count == 2


def test_readiness_deadline_does_not_queue_or_reuse_late_success():
    import threading
    import time

    import agentcc.readiness as probe

    release = threading.Event()

    def stalled():
        release.wait(2)
        return None

    try:
        with (
            patch.object(probe, "PROBE_DEADLINE_SECONDS", 0.02),
            patch.object(probe, "_check_dependencies", side_effect=stalled) as checks,
        ):
            started = time.monotonic()
            first = readiness(RequestFactory().get("/readyz/"))
            second = readiness(RequestFactory().get("/readyz/"))
            assert first.status_code == second.status_code == 503
            assert time.monotonic() - started < 0.5
            assert checks.call_count == 1
            release.set()
            probe._pending.result(timeout=1)
        with patch.object(probe, "_check_dependencies", return_value="shared_state"):
            # A late success from the old probe must not make this one green.
            assert readiness(RequestFactory().get("/readyz/")).status_code == 503
    finally:
        release.set()
