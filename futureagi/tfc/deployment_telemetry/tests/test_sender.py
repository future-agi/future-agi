import os
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.utils import _fire_deployment_telemetry_registration
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.deployment_telemetry.buffer import prune_expired_windows, store_window
from tfc.deployment_telemetry.config import BUFFER_RETENTION_DAYS
from tfc.deployment_telemetry.models import DeploymentTelemetryState
from tfc.deployment_telemetry.payloads import (
    build_full_registration_payload,
    build_heartbeat_payload,
)
from tfc.deployment_telemetry.sender import (
    _flush_buffer,
    compute_previous_utc_window,
    ensure_registration,
    run_telemetry_cycle,
)
from tfc.deployment_telemetry.state import get_or_create_telemetry_state
from tfc.deployment_telemetry.transport import TelemetryClient, TelemetryResponse
from tfc.temporal.schedules.deployment_telemetry import (
    DEPLOYMENT_TELEMETRY_SCHEDULES,
)


@pytest.fixture(autouse=True)
def telemetry_buffer_dir(monkeypatch, tmp_path):
    buffer_dir = tmp_path / "deployment-telemetry"
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_BUFFER_DIR", str(buffer_dir))
    return buffer_dir


@pytest.mark.parametrize(
    ("now", "interval", "expected_start", "expected_end"),
    [
        (
            datetime(2026, 6, 7, 13, 45, tzinfo=UTC),
            6,
            datetime(2026, 6, 7, 6, tzinfo=UTC),
            datetime(2026, 6, 7, 12, tzinfo=UTC),
        ),
        (
            datetime(2026, 6, 7, 0, 1, tzinfo=UTC),
            24,
            datetime(2026, 6, 6, 0, tzinfo=UTC),
            datetime(2026, 6, 7, 0, tzinfo=UTC),
        ),
        (
            datetime(2026, 6, 7, 8, 0, tzinfo=UTC),
            8,
            datetime(2026, 6, 7, 0, tzinfo=UTC),
            datetime(2026, 6, 7, 8, tzinfo=UTC),
        ),
    ],
)
def test_previous_fixed_utc_window(
    now,
    interval,
    expected_start,
    expected_end,
):
    assert compute_previous_utc_window(now, interval) == (
        expected_start,
        expected_end,
    )



def test_schedule_disables_temporal_retries():
    schedule = DEPLOYMENT_TELEMETRY_SCHEDULES[0]
    assert schedule.schedule_id == "deployment-telemetry-heartbeat"
    assert schedule.activity_name == "send_deployment_telemetry_heartbeat"
    assert schedule.jitter_seconds == 1800

    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

    assert _ACTIVITY_REGISTRY[schedule.activity_name]["max_retries"] == 0


def test_http_sender_retries_three_times(monkeypatch):
    response = MagicMock(status_code=503)
    with (
        patch(
            "tfc.deployment_telemetry.transport.requests.post",
            return_value=response,
        ) as post,
        patch("tfc.deployment_telemetry.transport.time.sleep"),
    ):
        client = TelemetryClient(base_url="https://example.test")
        result = client.post("/telemetry/register/", {"instance_id": "id"})

    assert result.ok is False
    assert post.call_count == 3


def test_heartbeat_post_is_hmac_signed():
    """A client built with a secret signs the body so the receiver can
    authenticate it; an unsigned client sends no signature header."""
    from tfc.deployment_telemetry.transport import (
        SIGNATURE_HEADER,
        compute_signature,
    )

    captured = {}

    def fake_post(url, data, headers, timeout):
        captured["headers"] = headers
        captured["data"] = data
        return MagicMock(status_code=200, json=lambda: {"status": "ok"})

    with patch("tfc.deployment_telemetry.transport.requests.post", fake_post):
        TelemetryClient(secret="s3cret").post("/telemetry/heartbeat/", {"a": 1})

    assert captured["headers"][SIGNATURE_HEADER] == compute_signature(
        "s3cret", captured["data"]
    )

    captured.clear()
    with patch("tfc.deployment_telemetry.transport.requests.post", fake_post):
        TelemetryClient().post("/telemetry/heartbeat/", {"a": 1})
    assert SIGNATURE_HEADER not in captured["headers"]


@pytest.mark.django_db
def test_enabled_registration_sends_users_and_persists_full_state(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    monkeypatch.setenv("FUTURE_AGI_VERSION", "1.2.3")
    User.objects.create_user(
        email="admin@example.com",
        name="Admin",
        password="password",
    )

    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.TelemetryClient.post",
            return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"}),
        ) as post,
    ):
        assert ensure_registration()[0] is True

    state = DeploymentTelemetryState.objects.get(singleton_key=1)
    assert state.registration_kind == DeploymentTelemetryState.RegistrationKind.FULL
    assert state.registration_status == DeploymentTelemetryState.RegistrationStatus.IDLE
    assert state.registration_metadata["users"] == [
        {"email": "admin@example.com", "domain": "example.com"}
    ]
    assert post.call_args.args[0] == "/telemetry/register/"


@pytest.mark.django_db
def test_disabled_registration_is_minimal_and_sends_no_heartbeat(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.TelemetryClient.post",
            return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"}),
        ) as post,
    ):
        result = run_telemetry_cycle()

    state = DeploymentTelemetryState.objects.get(singleton_key=1)
    assert state.registration_kind == (
        DeploymentTelemetryState.RegistrationKind.MINIMAL_DISABLED
    )
    assert "users" not in state.registration_metadata
    assert result == {"skipped": True, "reason": "disabled"}
    assert post.call_count == 1
    assert post.call_args.args[0] == "/telemetry/register/"


@pytest.mark.django_db
def test_enabled_without_users_releases_registration_claim(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("tfc.deployment_telemetry.sender.TelemetryClient.post") as post,
    ):
        assert ensure_registration()[0] is False

    state = DeploymentTelemetryState.objects.get(singleton_key=1)
    assert state.registered_at is None
    assert state.registration_status == DeploymentTelemetryState.RegistrationStatus.IDLE
    post.assert_not_called()


@pytest.mark.django_db
def test_registration_transitions_from_full_to_disabled(monkeypatch):
    User.objects.create_user(
        email="admin@example.com",
        name="Admin",
        password="password",
    )
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("tfc.deployment_telemetry.sender.TelemetryClient.post", return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"})),
    ):
        assert ensure_registration()[0] is True

    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("tfc.deployment_telemetry.sender.TelemetryClient.post", return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"})),
    ):
        assert ensure_registration()[0] is False

    state = DeploymentTelemetryState.objects.get(singleton_key=1)
    assert state.telemetry_disabled is True
    assert state.registration_kind == (
        DeploymentTelemetryState.RegistrationKind.MINIMAL_DISABLED
    )
    assert "users" not in state.registration_metadata


@pytest.mark.django_db
def test_registration_transitions_from_disabled_to_full(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("tfc.deployment_telemetry.sender.TelemetryClient.post", return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"})),
    ):
        assert ensure_registration()[0] is False

    User.objects.create_user(
        email="admin@example.com",
        name="Admin",
        password="password",
    )
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("tfc.deployment_telemetry.sender.TelemetryClient.post", return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"})),
    ):
        assert ensure_registration()[0] is True

    state = DeploymentTelemetryState.objects.get(singleton_key=1)
    assert state.telemetry_disabled is False
    assert state.registration_kind == DeploymentTelemetryState.RegistrationKind.FULL
    assert state.registration_metadata["users"][0]["email"] == "admin@example.com"


@pytest.mark.django_db
def test_fresh_registration_claim_prevents_duplicate_send(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    state = get_or_create_telemetry_state()
    state.registration_status = DeploymentTelemetryState.RegistrationStatus.IN_PROGRESS
    state.registration_claimed_at = timezone.now()
    state.save()

    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("tfc.deployment_telemetry.sender.TelemetryClient.post") as post,
    ):
        assert ensure_registration()[0] is False

    post.assert_not_called()


@pytest.mark.django_db
def test_stale_registration_claim_is_recovered(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    state = get_or_create_telemetry_state()
    state.registration_status = DeploymentTelemetryState.RegistrationStatus.IN_PROGRESS
    state.registration_claimed_at = timezone.now() - timedelta(minutes=11)
    state.save()

    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.TelemetryClient.post",
            return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"}),
        ) as post,
    ):
        assert ensure_registration()[0] is False

    post.assert_called_once()
    state.refresh_from_db()
    assert state.registration_status == DeploymentTelemetryState.RegistrationStatus.IDLE
    assert state.registered_at is not None


@pytest.mark.django_db
def test_successful_heartbeat_updates_window_state(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    state = get_or_create_telemetry_state()
    state.registered_at = timezone.now()
    state.registration_kind = DeploymentTelemetryState.RegistrationKind.FULL
    # A fully-registered instance holds the HMAC secret issued at
    # registration; without it the sender re-registers before heartbeating.
    state.instance_secret = "test-secret"
    state.save()
    window_start = datetime(2026, 6, 7, 0, tzinfo=UTC)
    window_end = datetime(2026, 6, 7, 6, tzinfo=UTC)
    counts = {
        "traces_count": 1,
        "spans_count": 2,
        "projects_count": 1,
        "eval_logger_count": 1,
        "model_hub_evaluations_count": 1,
        "dataset_eval_runs_count": 0,
        "total_evaluations_count": 2,
        "simulation_runs_count": 1,
        "simulation_calls_count": 3,
        "experiments_count": 1,
        "gateway_requests_count": 4,
        "datasets_count": 1,
        "active_users_count": 1,
    }

    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.compute_previous_utc_window",
            return_value=(window_start, window_end),
        ),
        patch(
            "tfc.deployment_telemetry.sender.collect_counts",
            return_value=counts,
        ),
        patch(
            "tfc.deployment_telemetry.sender.TelemetryClient.post",
            return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"}),
        ) as post,
    ):
        result = run_telemetry_cycle()

    assert result["sent"] is True
    assert post.call_args.args[0] == "/telemetry/heartbeat/"
    state.refresh_from_db()
    assert state.last_heartbeat_window_start == window_start
    assert state.last_heartbeat_window_end == window_end


@pytest.mark.django_db
def test_failed_heartbeat_is_buffered_and_flushed_later(
    monkeypatch,
    telemetry_buffer_dir,
):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    state = get_or_create_telemetry_state()
    state.registered_at = timezone.now()
    state.registration_kind = DeploymentTelemetryState.RegistrationKind.FULL
    # A fully-registered instance holds the HMAC secret issued at
    # registration; without it the sender re-registers before heartbeating.
    state.instance_secret = "test-secret"
    state.save()
    window_start = datetime(2026, 6, 7, 0, tzinfo=UTC)
    window_end = datetime(2026, 6, 7, 6, tzinfo=UTC)
    counts = {
        "active_users_count": 1,
        "traces_count": 1,
        "spans_count": 1,
        "projects_count": 1,
        "eval_logger_count": 0,
        "model_hub_evaluations_count": 0,
        "dataset_eval_runs_count": 0,
        "total_evaluations_count": 0,
        "simulation_runs_count": 0,
        "simulation_calls_count": 0,
        "experiments_count": 0,
        "gateway_requests_count": 0,
        "datasets_count": 0,
    }

    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.compute_previous_utc_window",
            return_value=(window_start, window_end),
        ),
        patch(
            "tfc.deployment_telemetry.sender.collect_counts",
            return_value=counts,
        ),
        patch(
            "tfc.deployment_telemetry.sender.TelemetryClient.post",
            return_value=TelemetryResponse(ok=False),
        ),
    ):
        first = run_telemetry_cycle()

    assert first["sent"] is False
    assert first["flush_complete"] is False
    assert len(list(telemetry_buffer_dir.glob("*.json"))) == 1

    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.compute_previous_utc_window",
            return_value=(window_start, window_end),
        ),
        patch(
            "tfc.deployment_telemetry.sender.collect_counts",
            return_value=counts,
        ),
        patch(
            "tfc.deployment_telemetry.sender.TelemetryClient.post",
            return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"}),
        ) as post,
    ):
        second = run_telemetry_cycle()

    assert second["sent_count"] == 1
    assert len(list(telemetry_buffer_dir.glob("*.json"))) == 0
    post.assert_called_once()


@pytest.mark.django_db
def test_flushes_three_buffered_windows_oldest_first(telemetry_buffer_dir):
    state = get_or_create_telemetry_state()
    # The flush path now refuses to run without a signing secret (the receiver
    # would reject every unsigned heartbeat with 401). Mint one before
    # exercising the multi-window flush.
    state.instance_secret = "test-secret"
    state.save(update_fields=["instance_secret"])
    base = datetime(2026, 6, 7, 0, tzinfo=UTC)
    counts = {
        "active_users_count": 0,
        "traces_count": 0,
        "spans_count": 0,
        "projects_count": 0,
        "eval_logger_count": 0,
        "model_hub_evaluations_count": 0,
        "dataset_eval_runs_count": 0,
        "total_evaluations_count": 0,
        "simulation_runs_count": 0,
        "simulation_calls_count": 0,
        "experiments_count": 0,
        "gateway_requests_count": 0,
        "datasets_count": 0,
    }
    expected_starts = []
    for offset in range(3):
        window_start = base + timedelta(hours=6 * offset)
        window_end = window_start + timedelta(hours=6)
        expected_starts.append(window_start.isoformat().replace("+00:00", "Z"))
        store_window(
            window_start,
            window_end,
            build_heartbeat_payload(
                state.instance_id,
                window_start,
                window_end,
                counts,
            ),
        )

    with patch(
        "tfc.deployment_telemetry.sender.TelemetryClient.post",
        return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"}),
    ) as post:
        sent_count, flush_complete = _flush_buffer()

    assert sent_count == 3
    assert flush_complete is True
    assert [call.args[1]["window_start"] for call in post.call_args_list] == (
        expected_starts
    )
    assert list(telemetry_buffer_dir.glob("*.json")) == []


@pytest.mark.django_db
def test_disabling_telemetry_clears_buffer(monkeypatch, telemetry_buffer_dir):
    telemetry_buffer_dir.mkdir()
    (telemetry_buffer_dir / "window.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")

    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.TelemetryClient.post",
            return_value=TelemetryResponse(ok=True, data={"instance_secret": "test-secret"}),
        ),
    ):
        result = run_telemetry_cycle()

    assert result == {"skipped": True, "reason": "disabled"}
    assert list(telemetry_buffer_dir.glob("*.json")) == []


@pytest.mark.django_db
def test_registration_caps_users_at_500():
    state = get_or_create_telemetry_state()
    org = Organization.objects.create(name="Example Org")
    users = [
        User(
            email=f"user-{index}@example.com",
            name=f"User {index}",
            password="",
            organization=org,
            organization_role=OrganizationRoles.ADMIN,
        )
        for index in range(501)
    ]
    User.objects.bulk_create(users)
    OrganizationMembership.objects.bulk_create(
        [
            OrganizationMembership(
                user=user,
                organization=org,
                role=OrganizationRoles.ADMIN,
                level=Level.ADMIN,
                is_active=True,
            )
            for user in User.objects.filter(organization=org)
        ]
    )

    payload = build_full_registration_payload(state.instance_id)

    assert payload is not None
    assert len(payload["users"]) == 500


@pytest.mark.django_db
def test_user_selection_prefers_recent_login():
    state = get_or_create_telemetry_state()
    org = Organization.objects.create(name="Example Org")
    older = User.objects.create_user(
        email="older@example.com",
        name="Older",
        password="password",
        organization=org,
        organization_role=OrganizationRoles.ADMIN,
    )
    newer = User.objects.create_user(
        email="newer@example.com",
        name="Newer",
        password="password",
        organization=org,
        organization_role=OrganizationRoles.ADMIN,
    )
    OrganizationMembership.objects.bulk_create(
        [
            OrganizationMembership(
                user=older,
                organization=org,
                role=OrganizationRoles.ADMIN,
                level=Level.ADMIN,
                is_active=True,
            ),
            OrganizationMembership(
                user=newer,
                organization=org,
                role=OrganizationRoles.ADMIN,
                level=Level.ADMIN,
                is_active=True,
            ),
        ]
    )
    older.last_login = timezone.now()
    older.save(update_fields=["last_login"])
    newer.last_login = timezone.now() - timedelta(days=1)
    newer.save(update_fields=["last_login"])

    payload = build_full_registration_payload(state.instance_id)

    assert payload is not None
    assert [user["email"] for user in payload["users"]] == [
        "older@example.com",
        "newer@example.com",
    ]


@pytest.mark.django_db
def test_registration_excludes_active_non_admin_users():
    state = get_or_create_telemetry_state()
    org = Organization.objects.create(name="Example Org")
    admin = User.objects.create_user(
        email="admin@example.com",
        name="Admin",
        password="password",
        organization=org,
        organization_role=OrganizationRoles.ADMIN,
    )
    member = User.objects.create_user(
        email="member@example.com",
        name="Member",
        password="password",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    OrganizationMembership.objects.bulk_create(
        [
            OrganizationMembership(
                user=admin,
                organization=org,
                role=OrganizationRoles.ADMIN,
                level=Level.ADMIN,
                is_active=True,
            ),
            OrganizationMembership(
                user=member,
                organization=org,
                role=OrganizationRoles.MEMBER,
                level=Level.MEMBER,
                is_active=True,
            ),
        ]
    )

    payload = build_full_registration_payload(state.instance_id)

    assert payload is not None
    assert [user["email"] for user in payload["users"]] == ["admin@example.com"]


def test_cycle_failures_are_silent():
    with patch(
        "tfc.deployment_telemetry.sender._run_telemetry_cycle",
        side_effect=RuntimeError("boom"),
    ):
        assert run_telemetry_cycle() == {
            "sent": False,
            "error": "internal_error",
        }


def test_signup_hook_starts_non_daemon_registration_thread_without_joining():
    with (
        patch(
            "tfc.deployment_telemetry.config.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("threading.Thread") as thread,
    ):
        _fire_deployment_telemetry_registration()

    assert "daemon" not in thread.call_args.kwargs
    thread.return_value.start.assert_called_once()
    thread.return_value.join.assert_not_called()


def test_prune_expired_windows_respects_retention_cap(telemetry_buffer_dir):
    """A window older than BUFFER_RETENTION_DAYS is dropped; a fresh one
    survives. Pins the 30-day cap so a regression that drops the constant
    or inverts the comparison is caught."""
    telemetry_buffer_dir.mkdir(parents=True, exist_ok=True)
    old_window = telemetry_buffer_dir / "old.json"
    fresh_window = telemetry_buffer_dir / "fresh.json"
    old_window.write_text("{}", encoding="utf-8")
    fresh_window.write_text("{}", encoding="utf-8")

    now = datetime.now(UTC)
    old_mtime = (now - timedelta(days=BUFFER_RETENTION_DAYS + 1)).timestamp()
    fresh_mtime = (now - timedelta(days=BUFFER_RETENTION_DAYS - 1)).timestamp()
    os.utime(old_window, (old_mtime, old_mtime))
    os.utime(fresh_window, (fresh_mtime, fresh_mtime))

    removed = prune_expired_windows(now=now)

    assert removed == 1
    assert not old_window.exists()
    assert fresh_window.exists()


def test_ch_counts_queries_use_final_and_is_deleted_predicate():
    """The CH ``spans`` table is a ReplacingMergeTree; correctness depends
    on ``FINAL`` and ``is_deleted = 0`` on every read. Pin the predicates
    against the source so a future SQL edit can't quietly drop one."""
    import inspect

    from tfc.deployment_telemetry import ch_counts

    source = inspect.getsource(ch_counts)
    for fragment in ("FROM spans FINAL", "is_deleted = 0"):
        # 3 = count_spans + count_traces + ingesting_project_ids
        assert source.count(fragment) >= 3, fragment


@pytest.mark.django_db
def test_corrupt_buffer_window_logs_before_delete(telemetry_buffer_dir):
    """A buffered window with a malformed ``instance_id`` is unsendable;
    the flush path drops it but must log first so a later "missing window"
    investigation has a trail."""
    state = get_or_create_telemetry_state()
    state.instance_secret = "test-secret"
    state.save(update_fields=["instance_secret"])

    telemetry_buffer_dir.mkdir(parents=True, exist_ok=True)
    corrupt_window = telemetry_buffer_dir / "corrupt.json"
    corrupt_window.write_text('{"instance_id": "not-a-uuid"}', encoding="utf-8")

    with patch("tfc.deployment_telemetry.sender.logger.warning") as warning:
        sent_count, flush_complete = _flush_buffer()

    assert sent_count == 0
    assert flush_complete is True
    assert not corrupt_window.exists()
    assert any(
        call.args and call.args[0] == "deployment_telemetry_buffer_corrupt"
        for call in warning.call_args_list
    )
