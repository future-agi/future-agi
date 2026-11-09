"""Zero-egress regression tests, including switches rendered without services.

Use offline_settings with --noconftest for a standalone SQLite run. Only the
admin-user query is substituted; state, payload encoding, HTTP retries, and
both disk outboxes are real. No test may open a network connection, even when
application code catches the exception. Helm is local `template`, never a
cluster command; the installer test executes only its parser and opt-out block.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps
from django.db import close_old_connections
from django.utils import timezone

from tfc.deployment_telemetry import config, events, transport
from tfc.deployment_telemetry.buffer import pending_windows, store_window
from tfc.deployment_telemetry.event_buffer import pending_events, store_event
from tfc.deployment_telemetry.state import get_or_create_telemetry_state

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "futureagi"
NOW = datetime(2026, 10, 31, 0, tzinfo=UTC)
SWITCHES = ["true", "1", "yes", "on", " TRUE ", "installer", "helm", "airgap"]
ENTRY_POINTS = [
    "registration",
    "signup_registration",
    "cycle",
    "buffer_drain",
    "event_flush",
    "event_drain",
    "background_events",
    "scheduled_heartbeat",
    "scheduled_events",
    "signup_hook",
    "boot_event",
]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append((args, kwargs))
        raise AssertionError("real network attempt forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket.socket, "sendto", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(transport.requests.sessions.Session, "send", forbidden)
    yield
    assert attempts == [], "network attempt was swallowed by application code"


@pytest.fixture
def telemetry(monkeypatch, tmp_path, db):
    # Avoid importing the entire accounts app in the standalone SQLite run.
    # In the normal app test environment no import substitution is necessary.
    if not apps.is_installed("accounts"):
        module = ModuleType("accounts.models.user")
        module.User = MagicMock()
        monkeypatch.setitem(sys.modules, "accounts.models.user", module)
    sender = importlib.import_module("tfc.deployment_telemetry.sender")
    assert Path(sender.__file__).resolve().is_relative_to(BACKEND)
    from tfc.deployment_telemetry import payloads

    user = MagicMock()
    user.objects.filter.return_value.filter.return_value.distinct.return_value.order_by.return_value.__getitem__.return_value = [
        SimpleNamespace(email="Admin@Example.test")
    ]
    monkeypatch.setattr(payloads, "User", user)
    monkeypatch.setattr(sender, "is_self_hosted_deployment", lambda: True)
    monkeypatch.setattr(events, "is_self_hosted_deployment", lambda: True)
    monkeypatch.setattr(events, "_schedule_flush", lambda: None)
    monkeypatch.setattr(sender, "_disclosure_logged", False)
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    monkeypatch.delenv("FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS", raising=False)
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_BUFFER_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("FUTURE_AGI_VERSION", "1.2.3")
    monkeypatch.setenv("FUTURE_AGI_DEPLOYMENT_TYPE", "docker")
    monkeypatch.setattr(sender, "collect_counts", MagicMock(return_value={}))
    monkeypatch.setattr(transport.time, "sleep", MagicMock())
    return sender


@pytest.fixture(scope="module")
def switch_values(tmp_path_factory):
    # Execute the shipped flag parser and flag-to-.env write, not the installer
    # (which also checks Docker and pulls images). Never read an operator .env.
    install = (ROOT / "bin/install").read_text()
    parser = install[
        install.index("NO_TELEMETRY=0") : install.index(
            "\ndone", install.index("NO_TELEMETRY=0")
        )
        + 5
    ]
    write = re.search(
        r"if \(\( NO_TELEMETRY == 1 \)\); then\n.*?\nfi", install, re.S
    ).group()
    directory = tmp_path_factory.mktemp("installer")
    (directory / ".env").write_text("")
    script = (
        'source "$1"; shift; ok() { :; };\n'
        + parser
        + "\n"
        + write
        + "\nenv_value FUTURE_AGI_TELEMETRY_DISABLED"
    )
    result = subprocess.run(
        [
            "bash",
            "-eu",
            "-c",
            script,
            "test",
            str(ROOT / "bin/lib/env.sh"),
            "--no-telemetry",
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        check=True,
    )
    values = {"installer": result.stdout}
    helm = shutil.which("helm")
    if not helm:
        return {**values, "helm": None, "airgap": None}
    chart = ROOT / "deploy/helm/futureagi"
    for switch, flag in [
        ("helm", "config.telemetry=false"),
        ("airgap", "global.airgap=true"),
    ]:
        result = subprocess.run(
            [
                helm,
                "template",
                "optout",
                str(chart),
                "-f",
                str(chart / "examples/local.yaml"),
                "--set",
                flag,
                "--set",
                "serving.enabled=false",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        # Pod env values are quoted strings; no secrets are printed.
        matches = re.findall(
            r'name: FUTURE_AGI_TELEMETRY_DISABLED\s+value: "([^"]+)"', result.stdout
        )
        assert matches and set(matches) == {"true"}
        values[switch] = matches[0]
    return values


def disable(monkeypatch, switch, switch_values):
    value = switch_values.get(switch, switch)
    if value is None:
        pytest.skip("helm is required for rendered chart switch coverage")
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", value)
    assert config.telemetry_is_disabled()


def seed_outboxes(state):
    from tfc.deployment_telemetry.payloads import build_heartbeat_payload

    end = timezone.now().replace(microsecond=0)
    start = end - timedelta(hours=6)
    store_window(start, end, build_heartbeat_payload(state.instance_id, start, end, {}))
    store_event(
        events.build_event(
            "user_created",
            instance_id=state.instance_id,
            actor_type="human_user",
            actor_id="fixture",
        )
    )


def schedule_module():
    # Import the real activity decorator and this schedule without eagerly
    # importing all other schedules and their unrelated application models.
    schedules = ModuleType("tfc.temporal.schedules")
    schedules.__path__ = [str(BACKEND / "tfc/temporal/schedules")]
    drop_in = ModuleType("tfc.temporal.drop_in")
    drop_in.__path__ = [str(BACKEND / "tfc/temporal/drop_in")]
    with patch.dict(
        sys.modules, {schedules.__name__: schedules, drop_in.__name__: drop_in}
    ):
        drop_in.temporal_activity = importlib.import_module(
            "tfc.temporal.drop_in.decorator"
        ).temporal_activity
        return importlib.import_module("tfc.temporal.schedules.deployment_telemetry")


def invoke(name, sender):
    if name == "scheduled_heartbeat":
        return schedule_module().send_deployment_telemetry_heartbeat()
    if name == "scheduled_events":
        return schedule_module().flush_deployment_telemetry_events()
    if name == "signup_hook":
        # Execute the actual hook in isolation from accounts.utils' other imports.
        source = ast.parse((BACKEND / "accounts/utils.py").read_text())
        node = next(
            node
            for node in source.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_fire_deployment_telemetry_registration"
        )
        namespace = {
            "close_old_connections": close_old_connections,
            "logger": MagicMock(),
        }
        exec(
            compile(
                ast.Module(body=[node], type_ignores=[]), "accounts/utils.py", "exec"
            ),
            namespace,
        )
        with (
            patch("threading.Thread") as thread,
            patch.object(config, "is_self_hosted_deployment", return_value=True),
        ):
            thread.side_effect = lambda **kwargs: SimpleNamespace(
                start=kwargs["target"]
            )
            return namespace[node.name](user_id="fixture")
    return {
        "registration": sender.ensure_registration,
        "signup_registration": sender.attempt_registration,
        "cycle": sender.run_telemetry_cycle,
        "buffer_drain": sender._flush_buffer,
        "event_flush": events.flush_events,
        "event_drain": events._flush_events,
        "background_events": events._background_flush,
        "boot_event": events.queue_boot_event,
    }[name]()


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize(
    "registered",
    ["", "full", "minimal_disabled", "missing_secret"],
    ids=["new", "full", "legacy", "reregistration"],
)
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_disabled_entry_points_never_post(
    telemetry, monkeypatch, switch_values, switch, registered, entry_point
):
    state = get_or_create_telemetry_state()
    # A secret and actual buffered data prevent vacuous passes at flush gates.
    state.instance_secret = "fixture-secret"
    if registered:
        state.registration_kind = (
            "full" if registered == "missing_secret" else registered
        )
        state.registered_at = NOW
    if registered == "missing_secret":
        state.instance_secret = ""
    state.save()
    seed_outboxes(state)
    disable(monkeypatch, switch, switch_values)
    with patch.object(
        transport.TelemetryClient,
        "post",
        return_value=transport.TelemetryResponse(
            ok=True, data={"instance_secret": "fixture-secret"}
        ),
    ) as post:
        invoke(entry_point, telemetry)
        invoke(entry_point, telemetry)  # repeated scheduler ticks/retries
    assert post.call_count == 0
    telemetry.collect_counts.assert_not_called()


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize(
    "stored_users",
    [None, [{"email": "admin@example.test", "domain": "example.test"}]],
    ids=["registered-without-emails", "registered-with-emails"],
)
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_the_admin_email_opt_in_never_overrides_the_opt_out(
    telemetry, monkeypatch, switch_values, switch, stored_users, entry_point
):
    """FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS=true with the opt-out set sends
    nothing either, including no re-registration of a FULL instance whose
    stored registration disagrees with the setting."""
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS", "true")
    state = get_or_create_telemetry_state()
    state.instance_secret = "fixture-secret"
    state.registration_kind = "full"
    state.registered_at = NOW
    state.registration_metadata = (
        {"users": stored_users} if stored_users is not None else {}
    )
    state.save()
    seed_outboxes(state)
    disable(monkeypatch, switch, switch_values)
    with patch.object(
        transport.TelemetryClient,
        "post",
        return_value=transport.TelemetryResponse(
            ok=True, data={"instance_secret": "fixture-secret"}
        ),
    ) as post:
        invoke(entry_point, telemetry)
        invoke(entry_point, telemetry)
    assert post.call_count == 0
    telemetry.collect_counts.assert_not_called()
    state.refresh_from_db()
    assert state.registration_metadata == (
        {"users": stored_users} if stored_users is not None else {}
    )


@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("endpoint", ["register", "heartbeat", "events"])
def test_client_itself_never_attempts_http(
    telemetry, monkeypatch, switch_values, switch, endpoint
):
    client = transport.TelemetryClient(
        secret="fixture-secret"
    )  # created before opt-out
    disable(monkeypatch, switch, switch_values)
    with patch.object(
        transport.requests, "post", return_value=MagicMock(status_code=503)
    ) as post:
        assert (
            client.post(f"/telemetry/{endpoint}/", {"instance_id": "fixture"}).ok
            is False
        )
    assert post.call_count == 0
    transport.time.sleep.assert_not_called()


@pytest.mark.parametrize("status", ["idle", "in_progress"])
def test_disabled_claim_stays_idle_without_repeated_writes(
    telemetry, monkeypatch, status
):
    state = get_or_create_telemetry_state()
    state.registration_status = status
    state.registration_claimed_at = NOW if status == "in_progress" else None
    state.save()
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    first, claimed, full = telemetry._claim_registration()
    assert (claimed, full) == (False, False)
    assert first.telemetry_disabled
    assert first.registration_status == "idle"
    assert first.registration_claimed_at is None
    assert first.last_registration_attempt_at is None
    assert first.registered_at is None
    second, claimed, full = telemetry._claim_registration()
    assert second.updated_at == first.updated_at
    assert (claimed, full) == (False, False)


@pytest.mark.parametrize(
    ("share", "users"),
    [
        (None, ""),
        ("true", ',"users":[{"email":"admin@example.test","domain":"example.test"}]'),
    ],
    ids=["default", "admin-emails-opted-in"],
)
def test_enabled_registration_keeps_exact_wire_bytes(
    telemetry, monkeypatch, share, users
):
    from tfc.deployment_telemetry import payloads

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(payloads, "datetime", FixedDatetime)
    if share is None:
        monkeypatch.delenv("FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS", raising=False)
    else:
        monkeypatch.setenv("FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS", share)
    state = get_or_create_telemetry_state()
    # Emails go on the wire only with FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS.
    expected = (
        '{"schema_version":1,"instance_id":"' + str(state.instance_id) + '",'
        '"version":"1.2.3","deployment_type":"docker",'
        '"timestamp":"2026-10-31T00:00:00Z","telemetry_disabled":false' + users + "}"
    ).encode()
    with patch.object(
        transport.requests,
        "post",
        return_value=MagicMock(
            status_code=201, json=lambda: {"instance_secret": "fixture-secret"}
        ),
    ) as post:
        assert telemetry.ensure_registration() == (True, state.instance_id)
        assert telemetry.ensure_registration() == (True, state.instance_id)
    post.assert_called_once_with(
        "http://127.0.0.1:9/telemetry/register/",
        data=expected,
        headers={"Content-Type": "application/json"},
        timeout=5.0,
    )
    state.refresh_from_db()
    assert state.registration_metadata == json.loads(expected)
    assert state.instance_secret == "fixture-secret"
    assert state.registration_kind == "full"


def test_opt_out_during_retry_stops_remaining_attempts(telemetry, monkeypatch):
    def fail_and_disable(*args, **kwargs):
        monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
        raise transport.requests.ConnectionError("fixture outage")

    with patch.object(transport.requests, "post", side_effect=fail_and_disable) as post:
        assert not transport.TelemetryClient().post("/telemetry/register/", {}).ok
    assert post.call_count == 1


def test_disabled_cycle_clears_only_own_outboxes(telemetry, monkeypatch):
    from uuid import uuid4

    state = get_or_create_telemetry_state()
    seed_outboxes(state)
    other = SimpleNamespace(instance_id=uuid4())
    seed_outboxes(other)
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    with patch.object(transport.TelemetryClient, "post") as post:
        assert telemetry.run_telemetry_cycle() == {
            "skipped": True,
            "reason": "disabled",
        }
    post.assert_not_called()
    assert not pending_windows(instance_id=str(state.instance_id))
    assert not pending_events(instance_id=str(state.instance_id))
    assert pending_windows(instance_id=str(other.instance_id))
    assert pending_events(instance_id=str(other.instance_id))


def test_opt_out_then_back_in_preserves_registration(telemetry, monkeypatch):
    state = get_or_create_telemetry_state()
    with patch.object(
        transport.requests,
        "post",
        return_value=MagicMock(
            status_code=201, json=lambda: {"instance_secret": "fixture-secret"}
        ),
    ) as post:
        assert telemetry.ensure_registration()[0]
        state.refresh_from_db()
        metadata = state.registration_metadata
        registered_at = state.registered_at
        post.reset_mock()
        monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
        assert telemetry.ensure_registration() == (False, state.instance_id)
        assert telemetry.attempt_registration() is False
        post.assert_not_called()
        state.refresh_from_db()
        assert state.telemetry_disabled
        assert state.registration_metadata == metadata
        assert state.registered_at == registered_at
        monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
        assert telemetry.ensure_registration() == (True, state.instance_id)
        post.assert_not_called()
        state.refresh_from_db()
        assert not state.telemetry_disabled


def test_disabled_disclosure_once_without_warnings(telemetry, monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    with (
        patch.object(telemetry, "logger") as log,
        patch.object(transport.requests, "post") as post,
    ):
        assert telemetry.attempt_registration() is False
        assert telemetry.attempt_registration() is False
    post.assert_not_called()
    log.warning.assert_not_called()
    log.info.assert_called_once()
    assert log.info.call_args.kwargs["sends"] == "nothing to Future AGI"


def test_enabled_http_retry_bytes_and_signature_are_unchanged(telemetry):
    body = b'{"schema_version":1,"instance_id":"fixture","events":[]}'
    with patch.object(
        transport.requests, "post", return_value=MagicMock(status_code=503)
    ) as post:
        assert (
            not transport.TelemetryClient(secret="fixture-secret")
            .post("/telemetry/events/", json.loads(body))
            .ok
        )
    assert post.call_count == 3
    for call in post.call_args_list:
        assert call.kwargs["data"] == body
        assert call.kwargs["headers"] == {
            "Content-Type": "application/json",
            transport.SIGNATURE_HEADER: transport.compute_signature(
                "fixture-secret", body
            ),
        }
