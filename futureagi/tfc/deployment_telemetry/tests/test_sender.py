from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from accounts.models.user import User
from accounts.utils import _fire_deployment_telemetry_registration
from tfc.deployment_telemetry.buffer import store_window
from tfc.deployment_telemetry.models import DeploymentTelemetryState
from tfc.deployment_telemetry.payloads import (
    build_full_registration_payload,
    build_heartbeat_payload,
)
from tfc.deployment_telemetry.sender import (
    _flush_buffer,
    _post_payload,
    compute_previous_utc_window,
    ensure_registration,
    run_telemetry_cycle,
)
from tfc.deployment_telemetry.state import get_or_create_telemetry_state
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


def test_previous_fixed_utc_window_with_seconds_override(monkeypatch):
    monkeypatch.delenv("FUTURE_AGI_TELEMETRY_INTERVAL_SECONDS", raising=False)
    assert compute_previous_utc_window(
        datetime(2026, 6, 7, 8, 5, 59, tzinfo=UTC),
        interval_seconds=120,
    ) == (
        datetime(2026, 6, 7, 8, 2, tzinfo=UTC),
        datetime(2026, 6, 7, 8, 4, tzinfo=UTC),
    )


def test_schedule_disables_temporal_retries():
    schedule = DEPLOYMENT_TELEMETRY_SCHEDULES[0]
    assert schedule.schedule_id == "deployment-telemetry-heartbeat"
    assert schedule.activity_name == "send_deployment_telemetry_heartbeat"
    assert schedule.jitter_seconds == 1800

    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY

    assert _ACTIVITY_REGISTRY[schedule.activity_name]["max_retries"] == 0


def test_http_sender_retries_three_times(monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_URL", "https://example.test")
    response = MagicMock(status_code=503)
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch(
            "tfc.deployment_telemetry.sender.requests.post",
            return_value=response,
        ) as post,
        patch("tfc.deployment_telemetry.sender.time.sleep"),
    ):
        assert _post_payload("/telemetry/register/", {"instance_id": "id"}) is False

    assert post.call_count == 3


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
            "tfc.deployment_telemetry.sender._post_payload",
            return_value=True,
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
            "tfc.deployment_telemetry.sender._post_payload",
            return_value=True,
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
        patch("tfc.deployment_telemetry.sender._post_payload") as post,
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
        patch("tfc.deployment_telemetry.sender._post_payload", return_value=True),
    ):
        assert ensure_registration()[0] is True

    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    with (
        patch(
            "tfc.deployment_telemetry.sender.is_self_hosted_deployment",
            return_value=True,
        ),
        patch("tfc.deployment_telemetry.sender._post_payload", return_value=True),
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
        patch("tfc.deployment_telemetry.sender._post_payload", return_value=True),
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
        patch("tfc.deployment_telemetry.sender._post_payload", return_value=True),
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
        patch("tfc.deployment_telemetry.sender._post_payload") as post,
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
            "tfc.deployment_telemetry.sender._post_payload",
            return_value=True,
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
            "tfc.deployment_telemetry.sender._post_payload",
            return_value=True,
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
            "tfc.deployment_telemetry.sender._post_payload",
            return_value=False,
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
            "tfc.deployment_telemetry.sender._post_payload",
            return_value=True,
        ) as post,
    ):
        second = run_telemetry_cycle()

    assert second["sent_count"] == 1
    assert len(list(telemetry_buffer_dir.glob("*.json"))) == 0
    post.assert_called_once()


@pytest.mark.django_db
def test_flushes_three_buffered_windows_oldest_first(telemetry_buffer_dir):
    state = get_or_create_telemetry_state()
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
        "tfc.deployment_telemetry.sender._post_payload",
        return_value=True,
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
            "tfc.deployment_telemetry.sender._post_payload",
            return_value=True,
        ),
    ):
        result = run_telemetry_cycle()

    assert result == {"skipped": True, "reason": "disabled"}
    assert list(telemetry_buffer_dir.glob("*.json")) == []


@pytest.mark.django_db
def test_registration_caps_users_at_500(monkeypatch):
    state = get_or_create_telemetry_state()
    users = [
        User(
            email=f"user-{index}@example.com",
            name=f"User {index}",
            password="",
        )
        for index in range(501)
    ]
    User.objects.bulk_create(users)

    payload = build_full_registration_payload(state.instance_id)

    assert payload is not None
    assert len(payload["users"]) == 500


@pytest.mark.django_db
def test_user_selection_prefers_recent_login(monkeypatch):
    state = get_or_create_telemetry_state()
    older = User.objects.create_user(
        email="older@example.com",
        name="Older",
        password="password",
    )
    newer = User.objects.create_user(
        email="newer@example.com",
        name="Newer",
        password="password",
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
