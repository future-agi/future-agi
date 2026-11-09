"""Scans and readings that fail: a scan always ends, users read fixed messages,
two connects of one source make one agent, and a reading runs off the shared
thread."""

import asyncio
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from simulate.models import AgentScan
from simulate.services import agent_reading_sandbox
from simulate.services.agent_connect import connect_lock_name
from simulate.services.agent_reading_sandbox import (
    SCAN_TIMED_OUT,
    SCAN_UNAVAILABLE,
    _crashed,
    expire_stale_scan,
    fail_scan,
)
from simulate.tasks.agent_scans import SCAN_DEADLINE_SECONDS, scan_agent_source
from simulate.temporal.activities import agent_version as activities

pytestmark = pytest.mark.django_db

run_scan = scan_agent_source._original_func

SCAN = "/simulate/agents/scan/"


@pytest.fixture
def keep_connection():
    with patch("simulate.tasks.agent_scans.close_old_connections"):
        yield


@pytest.fixture
def scan(organization, workspace):
    return AgentScan.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        source={"kind": "github", "repository": "acme/agent"},
        status=AgentScan.Status.RUNNING,
    )


def _aged(scan, seconds):
    AgentScan.no_workspace_objects.filter(id=scan.id).update(
        created_at=timezone.now() - timedelta(seconds=seconds)
    )
    scan.refresh_from_db()
    return scan


class TestStuckScans:
    def test_a_scan_past_every_attempt_reads_as_failed(self, auth_client, scan):
        _aged(scan, SCAN_DEADLINE_SECONDS + 60)

        body = auth_client.get(f"{SCAN}{scan.id}/").json()

        assert body["status"] == "failed"
        assert body["failure"] == {
            "code": SCAN_TIMED_OUT,
            "message": "We couldn't read this source. Try again.",
            "retryable": True,
        }
        scan.refresh_from_db()
        assert scan.status == AgentScan.Status.FAILED
        assert scan.finished_at is not None

    def test_a_scan_still_in_time_keeps_running(self, scan):
        _aged(scan, 60)

        assert expire_stale_scan(scan).status == AgentScan.Status.RUNNING

    def test_a_finished_scan_is_never_failed(self, scan):
        AgentScan.no_workspace_objects.filter(id=scan.id).update(
            status=AgentScan.Status.FOUND
        )

        assert fail_scan(scan.id) is False
        scan.refresh_from_db()
        assert scan.status == AgentScan.Status.FOUND

    def test_the_last_attempt_fails_the_scan_and_still_raises(
        self, scan, keep_connection
    ):
        with patch.object(
            agent_reading_sandbox, "scan_source", side_effect=RuntimeError("down")
        ):
            with pytest.raises(RuntimeError):
                run_scan(str(scan.id))

        scan.refresh_from_db()
        assert scan.status == AgentScan.Status.FAILED
        assert scan.failure["code"] == SCAN_UNAVAILABLE

    def test_an_attempt_with_retries_left_leaves_the_scan_running(
        self, scan, keep_connection
    ):
        info = SimpleNamespace(attempt=1, retry_policy=None)
        with (
            patch.object(
                agent_reading_sandbox, "scan_source", side_effect=RuntimeError("down")
            ),
            patch("simulate.tasks.agent_scans.try_activity_info", return_value=info),
        ):
            with pytest.raises(RuntimeError):
                run_scan(str(scan.id))

        scan.refresh_from_db()
        assert scan.status == AgentScan.Status.RUNNING


class TestMessages:
    def test_a_crashed_reading_never_shows_its_output(self):
        run = SimpleNamespace(
            exit_code=1, output="Traceback ... AGENTCC_API_KEY=sk-live-123"
        )

        failure = _crashed(run)

        assert "sk-live-123" not in failure["message"]
        assert "Traceback" not in failure["message"]
        assert failure["details"] == {"exit_code": 1}


class TestConnectLock:
    def test_one_source_has_one_lock_whatever_its_case(self):
        names = {
            connect_lock_name("org", "ws", {"kind": "github", "repository": repo})
            for repo in ("Acme/Agent", "acme/agent")
        }

        assert len(names) == 1

    def test_each_folder_and_upload_has_its_own_lock(self):
        names = {
            connect_lock_name("org", "ws", source)
            for source in (
                {"kind": "github", "repository": "acme/agent"},
                {"kind": "github", "repository": "acme/agent", "subfolder": "a"},
                {"kind": "archive", "archive_artifact_id": "1"},
                {"kind": "archive", "archive_artifact_id": "2"},
            )
        }

        assert len(names) == 4

    def test_another_workspace_has_its_own_lock(self):
        source = {"kind": "github", "repository": "acme/agent"}

        assert connect_lock_name("org", "a", source) != connect_lock_name(
            "org", "b", source
        )


class TestReadingThread:
    def test_two_steps_run_side_by_side(self):
        started = threading.Barrier(2, timeout=5)

        def step():
            started.wait()
            return threading.get_ident()

        async def both():
            return await asyncio.gather(
                activities._run_db(step), activities._run_db(step)
            )

        first, second = asyncio.run(both())

        assert first != second

    def test_a_failed_version_is_recorded_with_its_code(self, monkeypatch):
        from simulate.services import agent_version_steps
        from simulate.temporal.types.agent_version import AgentVersionFailure

        calls = []
        monkeypatch.setattr(
            agent_version_steps,
            "fail",
            lambda version_id, **kwargs: calls.append((version_id, kwargs)),
        )

        asyncio.run(
            activities.fail_agent_version(
                AgentVersionFailure(
                    version_id="v1",
                    code="agent_reading_unavailable",
                    message="Reading the agent couldn't finish.",
                    retryable=True,
                )
            )
        )

        assert calls == [
            (
                "v1",
                {
                    "code": "agent_reading_unavailable",
                    "message": "Reading the agent couldn't finish.",
                    "retryable": True,
                },
            )
        ]
