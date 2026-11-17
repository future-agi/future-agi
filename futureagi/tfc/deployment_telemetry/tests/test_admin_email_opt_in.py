"""Admin and owner emails leave a self-hosted install only on explicit opt-in.

FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS (off by default) decides whether the
registration carries ``users`` (emails and email domains). Nothing else that
deployment telemetry sends changes: the registration still carries the
instance id, version and deployment type and still waits for the first
eligible account; heartbeats and journey events are as before.

Runs offline, either under the app's test settings or standalone on SQLite
(``-c /dev/null --noconftest
--ds=tfc.deployment_telemetry.tests.offline_settings``). The account query is a
fake; the state row, payload encoding, HTTP retries and the heartbeat outbox
are real. The receiver is a fake ``requests.post`` that records every request
and fails the test on any request it does not expect. No test may open a
network connection, even one that application code would swallow.
"""

from __future__ import annotations

import ast
import importlib
import json
import socket
import sys
from datetime import timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from django.apps import apps
from django.db import close_old_connections
from django.utils import timezone

from tfc.deployment_telemetry import config, events, transport
from tfc.deployment_telemetry.models import DeploymentTelemetryState
from tfc.deployment_telemetry.schema import REGISTRATION_FIELDS
from tfc.deployment_telemetry.state import get_or_create_telemetry_state

BACKEND = Path(__file__).resolve().parents[3]
SHARE = "FUTURE_AGI_TELEMETRY_SHARE_ADMIN_EMAILS"
RECEIVER = "http://127.0.0.1:9"
REGISTER = "/telemetry/register/"
HEARTBEAT = "/telemetry/heartbeat/"
# The retry floor after a failed registration (config.REGISTRATION_RETRY_SECONDS).
RETRY_FLOOR_SECONDS = 60 * 60
OWNER = {"email": "owner@example.test", "domain": "example.test"}
FULL = DeploymentTelemetryState.RegistrationKind.FULL
TRUTHY = ["true", "1", "yes", "on", "TRUE", " On ", "\tyes\n", "oN"]
FALSY = ["", "false", "0", "no", "off", "FALSE", "garbage", "y", "enabled", "true1"]


class FakeAccounts:
    """Stands in for ``User``: every query returns the accounts in ``emails``.

    ``exists()`` answers whether an eligible account exists without reading an
    email; slicing or iterating the query counts as reading the emails.
    """

    def __init__(self) -> None:
        self.emails: list[str] = []
        self.emails_read = 0

    @property
    def objects(self) -> FakeAccounts:
        return self

    def filter(self, *args, **kwargs) -> FakeAccounts:
        return self

    def distinct(self) -> FakeAccounts:
        return self

    def order_by(self, *args) -> FakeAccounts:
        return self

    def exists(self) -> bool:
        return bool(self.emails)

    def _rows(self) -> list[SimpleNamespace]:
        self.emails_read += 1
        return [SimpleNamespace(email=email) for email in self.emails]

    def __getitem__(self, key):
        return self._rows()[key]

    def __iter__(self):
        return iter(self._rows())


class FakeReceiver:
    """``requests.post`` for the telemetry receiver at ``RECEIVER``.

    ``outcomes[path]`` lists what the next requests to ``path`` get, in order:
    an HTTP status or ``"unreachable"``. Once it is empty a registration gets
    201 (with ``instance_secret`` unless ``issue_secret`` is None) and a
    heartbeat 202. Any other URL is recorded in ``unexpected``; the fixture
    fails the test on it, even when the sender swallows the error.
    """

    def __init__(self) -> None:
        # (path, decoded body, headers, raw body) of every expected request.
        self.requests: list[tuple[str, dict, dict, bytes]] = []
        self.unexpected: list[str] = []
        self.outcomes: dict[str, list] = {REGISTER: [], HEARTBEAT: []}
        self.issue_secret: str | None = "issued-secret"

    def __call__(self, url, data, headers, timeout):
        path = url.removeprefix(RECEIVER)
        if not url.startswith(RECEIVER + "/") or path not in self.outcomes:
            self.unexpected.append(url)
            raise AssertionError(f"unexpected telemetry request to {url}")
        self.requests.append((path, json.loads(data), dict(headers), data))
        queued = self.outcomes[path]
        outcome = queued.pop(0) if queued else (201 if path == REGISTER else 202)
        if outcome == "unreachable":
            raise transport.requests.ConnectionError("fixture receiver is down")
        body = {"status": "ok"}
        if path == REGISTER and self.issue_secret:
            body["instance_secret"] = self.issue_secret
        return MagicMock(status_code=outcome, json=lambda: body)

    def bodies(self, path: str) -> list[dict]:
        return [body for sent, body, _, _ in self.requests if sent == path]

    def paths(self) -> list[str]:
        return [sent for sent, *_ in self.requests]


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
    # The standalone SQLite run does not install the accounts app; the account
    # query is faked below either way.
    if not apps.is_installed("accounts"):
        module = ModuleType("accounts.models.user")
        module.User = MagicMock()
        monkeypatch.setitem(sys.modules, "accounts.models.user", module)
    sender = importlib.import_module("tfc.deployment_telemetry.sender")
    payloads = importlib.import_module("tfc.deployment_telemetry.payloads")
    assert Path(sender.__file__).resolve().is_relative_to(BACKEND)

    accounts = FakeAccounts()
    receiver = FakeReceiver()
    sleep = MagicMock()
    monkeypatch.setattr(payloads, "User", accounts)
    monkeypatch.setattr(transport.requests, "post", receiver)
    monkeypatch.setattr(transport.time, "sleep", sleep)
    monkeypatch.setattr(sender, "is_self_hosted_deployment", lambda: True)
    monkeypatch.setattr(events, "is_self_hosted_deployment", lambda: True)
    monkeypatch.setattr(events, "_schedule_flush", lambda: None)
    monkeypatch.setattr(sender, "_disclosure_logged", False)
    monkeypatch.setattr(
        sender, "collect_counts", MagicMock(return_value={"active_users_count": 1})
    )
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "false")
    monkeypatch.delenv(SHARE, raising=False)
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_BUFFER_DIR", str(tmp_path / "outbox"))
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_URL", RECEIVER)
    monkeypatch.setenv("FUTURE_AGI_VERSION", "1.2.3")
    monkeypatch.setenv("FUTURE_AGI_DEPLOYMENT_TYPE", "docker")
    yield SimpleNamespace(
        sender=sender,
        payloads=payloads,
        accounts=accounts,
        receiver=receiver,
        sleep=sleep,
    )
    assert receiver.unexpected == [], receiver.unexpected


def registered_full(users: list[dict] | None) -> DeploymentTelemetryState:
    """A FULL registration with a stored secret, as an earlier release left it."""
    state = get_or_create_telemetry_state()
    metadata = {
        "schema_version": 1,
        "instance_id": str(state.instance_id),
        "version": "1.0.0",
        "deployment_type": "docker",
        "timestamp": "2026-09-01T00:00:00Z",
        "telemetry_disabled": False,
    }
    if users is not None:
        metadata["users"] = users
    state.registered_at = timezone.now() - timedelta(days=30)
    state.registration_kind = FULL
    state.instance_secret = "stored-secret"
    state.registration_metadata = metadata
    state.save()
    return state


def age_last_attempt(state: DeploymentTelemetryState, seconds: int) -> None:
    DeploymentTelemetryState.objects.filter(pk=state.pk).update(
        last_registration_attempt_at=timezone.now() - timedelta(seconds=seconds)
    )


def fire_signup_hook(user_id: str = "fixture-user") -> None:
    """Run accounts.utils._fire_deployment_telemetry_registration inline,
    isolated from accounts.utils' other imports."""
    source = ast.parse((BACKEND / "accounts/utils.py").read_text())
    node = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_fire_deployment_telemetry_registration"
    )
    namespace = {"close_old_connections": close_old_connections, "logger": MagicMock()}
    exec(
        compile(ast.Module(body=[node], type_ignores=[]), "accounts/utils.py", "exec"),
        namespace,
    )
    with (
        patch("threading.Thread") as thread,
        patch.object(config, "is_self_hosted_deployment", return_value=True),
    ):
        thread.side_effect = lambda **kwargs: SimpleNamespace(start=kwargs["target"])
        namespace[node.name](user_id=user_id)


# ---------------------------------------------------------------------------
# (a), (b) The payload
# ---------------------------------------------------------------------------


def test_default_registration_carries_no_email_or_domain(telemetry):
    telemetry.accounts.emails = ["Owner@Example.test"]
    state = get_or_create_telemetry_state()

    payload = telemetry.payloads.build_full_registration_payload(state.instance_id)

    assert payload is not None
    assert "users" not in payload
    encoded = json.dumps(payload)
    assert "@" not in encoded
    assert "example.test" not in encoded
    assert set(payload) == REGISTRATION_FIELDS - {"users"}
    assert payload["schema_version"] == 1
    assert payload["instance_id"] == str(state.instance_id)
    assert payload["version"] == "1.2.3"
    assert payload["deployment_type"] == "docker"
    assert payload["telemetry_disabled"] is False
    assert payload["timestamp"].endswith("Z")
    # Only whether an eligible account exists is read, never an email.
    assert telemetry.accounts.emails_read == 0


@pytest.mark.parametrize("value", TRUTHY)
def test_opting_in_sends_the_emails_and_domains(telemetry, monkeypatch, value):
    monkeypatch.setenv(SHARE, value)
    telemetry.accounts.emails = ["Owner@Example.test", "not-an-email"]
    state = get_or_create_telemetry_state()

    payload = telemetry.payloads.build_full_registration_payload(state.instance_id)

    assert config.telemetry_shares_admin_emails() is True
    assert payload is not None
    assert payload["users"] == [OWNER]
    assert set(payload) == REGISTRATION_FIELDS


@pytest.mark.parametrize("value", FALSY)
def test_any_other_value_sends_no_emails(telemetry, monkeypatch, value):
    monkeypatch.setenv(SHARE, value)
    telemetry.accounts.emails = ["owner@example.test"]
    state = get_or_create_telemetry_state()

    payload = telemetry.payloads.build_full_registration_payload(state.instance_id)

    assert payload is not None
    assert "users" not in payload
    assert "@" not in json.dumps(payload)
    assert config.telemetry_shares_admin_emails() is False


def test_opted_in_without_a_usable_email_defers_as_before(telemetry, monkeypatch):
    monkeypatch.setenv(SHARE, "true")
    telemetry.accounts.emails = ["no-at-sign"]
    state = get_or_create_telemetry_state()

    assert telemetry.payloads.build_full_registration_payload(state.instance_id) is None


# ---------------------------------------------------------------------------
# (c), (d) First registration: timing and the heartbeat trap
# ---------------------------------------------------------------------------


def test_registration_still_waits_for_the_first_eligible_account(telemetry):
    sender = telemetry.sender

    # A fresh install: no account yet, so nothing is sent.
    assert sender.ensure_registration()[0] is False
    assert sender.attempt_registration() is False
    assert telemetry.receiver.requests == []
    state = get_or_create_telemetry_state()
    assert state.registered_at is None
    assert state.last_registration_error == ""

    # The first account is created: its sign-up registers right away.
    telemetry.accounts.emails = ["owner@example.test"]
    fire_signup_hook()

    (body,) = telemetry.receiver.bodies(REGISTER)
    assert set(body) == REGISTRATION_FIELDS - {"users"}
    assert "@" not in json.dumps(body)
    assert body["instance_id"] == str(state.instance_id)
    assert telemetry.accounts.emails_read == 0


def test_default_install_registers_stores_the_secret_and_flushes(telemetry):
    sender = telemetry.sender
    telemetry.accounts.emails = ["owner@example.test"]

    first = sender.run_telemetry_cycle()

    assert first["sent"] is True
    assert first["flush_complete"] is True
    assert telemetry.receiver.paths() == [REGISTER, HEARTBEAT]
    (registration,) = telemetry.receiver.bodies(REGISTER)
    assert "users" not in registration
    assert "@" not in json.dumps(registration)
    state = get_or_create_telemetry_state()
    assert state.registration_kind == FULL
    assert state.instance_secret == "issued-secret"
    assert "users" not in state.registration_metadata
    assert state.last_heartbeat_at is not None
    # The heartbeat is signed with the issued secret.
    _, _, headers, raw = telemetry.receiver.requests[1]
    assert headers[transport.SIGNATURE_HEADER] == transport.compute_signature(
        "issued-secret", raw
    )
    assert sender.ensure_registration() == (True, state.instance_id)

    telemetry.receiver.requests.clear()
    second = sender.run_telemetry_cycle()
    assert second["sent"] is True
    assert telemetry.receiver.paths() == [HEARTBEAT]


# ---------------------------------------------------------------------------
# (e), (f) Re-registration when the setting and the stored registration differ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("issued", [None, "reissued-secret"])
def test_upgraded_install_re_registers_once_without_emails(telemetry, issued):
    sender = telemetry.sender
    telemetry.accounts.emails = ["owner@example.test"]
    telemetry.receiver.issue_secret = issued
    state = registered_full(users=[OWNER])

    first = sender.run_telemetry_cycle()

    assert first["sent"] is True
    assert telemetry.receiver.paths() == [REGISTER, HEARTBEAT]
    (registration,) = telemetry.receiver.bodies(REGISTER)
    assert set(registration) == REGISTRATION_FIELDS - {"users"}
    assert "@" not in json.dumps(registration)
    state.refresh_from_db()
    assert "users" not in state.registration_metadata
    assert state.registration_kind == FULL
    assert state.instance_secret == "stored-secret"
    assert state.last_registration_error == ""
    _, _, headers, raw = telemetry.receiver.requests[1]
    assert headers[transport.SIGNATURE_HEADER] == transport.compute_signature(
        "stored-secret", raw
    )

    # Once is enough: later cycles and sign-ups do not register again.
    telemetry.receiver.requests.clear()
    assert sender.run_telemetry_cycle()["sent"] is True
    assert sender.attempt_registration() is True
    fire_signup_hook()
    assert telemetry.receiver.paths() == [HEARTBEAT]


def test_opting_in_re_registers_once_with_emails(telemetry, monkeypatch):
    sender = telemetry.sender
    telemetry.accounts.emails = ["Owner@Example.test", "admin@corp.example"]
    state = registered_full(users=None)
    monkeypatch.setenv(SHARE, "true")

    first = sender.run_telemetry_cycle()

    assert first["sent"] is True
    assert telemetry.receiver.paths() == [REGISTER, HEARTBEAT]
    (registration,) = telemetry.receiver.bodies(REGISTER)
    assert registration["users"] == [
        OWNER,
        {"email": "admin@corp.example", "domain": "corp.example"},
    ]
    state.refresh_from_db()
    assert state.registration_metadata["users"] == registration["users"]
    assert state.instance_secret == "stored-secret"

    telemetry.receiver.requests.clear()
    assert sender.run_telemetry_cycle()["sent"] is True
    assert telemetry.receiver.paths() == [HEARTBEAT]


# A 2xx registration response without instance_secret: a success when a secret
# is already stored, a failure when none is.


@pytest.mark.parametrize(
    ("share", "stored_users", "sent_users"),
    [(None, [OWNER], None), ("true", None, [OWNER])],
    ids=["opt-in-turned-off", "opt-in-turned-on"],
)
def test_a_re_registration_answered_without_a_secret_keeps_the_stored_one(
    telemetry, monkeypatch, share, stored_users, sent_users
):
    sender = telemetry.sender
    telemetry.accounts.emails = ["owner@example.test"]
    telemetry.receiver.issue_secret = None
    state = registered_full(users=stored_users)
    if share:
        monkeypatch.setenv(SHARE, share)

    assert sender.run_telemetry_cycle()["sent"] is True

    assert telemetry.receiver.paths() == [REGISTER, HEARTBEAT]
    (registration,) = telemetry.receiver.bodies(REGISTER)
    assert registration.get("users") == sent_users
    state.refresh_from_db()
    # Complete: the new registration is recorded and the stored secret kept.
    assert state.registration_metadata == registration
    assert state.registration_kind == FULL
    assert state.instance_secret == "stored-secret"
    assert state.last_registration_error == ""
    _, _, headers, raw = telemetry.receiver.requests[1]
    assert headers[transport.SIGNATURE_HEADER] == transport.compute_signature(
        "stored-secret", raw
    )

    # Not a failure: nothing is retried, even after the retry floor.
    telemetry.receiver.requests.clear()
    age_last_attempt(state, RETRY_FLOOR_SECONDS + 1)
    assert sender.run_telemetry_cycle()["sent"] is True
    assert sender.ensure_registration() == (True, state.instance_id)
    assert telemetry.receiver.paths() == [HEARTBEAT]


@pytest.mark.parametrize("share", [None, "true"], ids=["default", "opted-in"])
def test_a_first_registration_answered_without_a_secret_fails_until_the_floor(
    telemetry, monkeypatch, share
):
    sender = telemetry.sender
    telemetry.accounts.emails = ["owner@example.test"]
    telemetry.receiver.issue_secret = None
    if share:
        monkeypatch.setenv(SHARE, share)

    assert sender.run_telemetry_cycle()["sent"] is False

    assert telemetry.receiver.paths() == [REGISTER]
    state = get_or_create_telemetry_state()
    assert state.registered_at is None
    assert state.instance_secret == ""
    assert state.registration_metadata == {}
    assert state.last_registration_error == "missing_secret"

    # A failure: inside the retry floor nothing registers again.
    for _ in range(3):
        assert sender.attempt_registration() is False
        fire_signup_hook()
    age_last_attempt(state, RETRY_FLOOR_SECONDS - 60)
    assert sender.run_telemetry_cycle()["sent"] is False
    assert telemetry.receiver.paths() == [REGISTER]

    # After the floor it is retried, and a response with the secret completes it.
    telemetry.receiver.issue_secret = "issued-secret"
    age_last_attempt(state, RETRY_FLOOR_SECONDS + 1)
    assert sender.run_telemetry_cycle()["sent"] is True
    paths = telemetry.receiver.paths()
    assert paths[:2] == [REGISTER, REGISTER]
    assert paths[2:] and set(paths[2:]) == {HEARTBEAT}
    state.refresh_from_db()
    assert state.registration_kind == FULL
    assert state.instance_secret == "issued-secret"
    assert state.last_registration_error == ""


@pytest.mark.parametrize(
    ("share", "stored_users", "emails"),
    [
        ("true", None, ["no-at-sign"]),
        ("true", None, []),
        ("false", [OWNER], []),
    ],
    ids=["opt-in-no-usable-email", "opt-in-no-account", "opt-out-no-account"],
)
def test_re_registration_without_a_payload_keeps_heartbeats(
    telemetry, monkeypatch, share, stored_users, emails
):
    sender = telemetry.sender
    telemetry.accounts.emails = emails
    state = registered_full(users=stored_users)
    before = dict(state.registration_metadata)
    monkeypatch.setenv(SHARE, share)

    assert sender.ensure_registration() == (True, state.instance_id)
    result = sender.run_telemetry_cycle()

    assert result["sent"] is True
    assert telemetry.receiver.paths() == [HEARTBEAT]
    state.refresh_from_db()
    assert state.registration_kind == FULL
    assert state.instance_secret == "stored-secret"
    assert state.registration_metadata == before


# ---------------------------------------------------------------------------
# (g), (h) Failures: no demotion, no retry storm
# ---------------------------------------------------------------------------


def test_the_retry_floor_is_one_hour():
    assert config.REGISTRATION_RETRY_SECONDS == RETRY_FLOOR_SECONDS


@pytest.mark.parametrize(
    ("failure", "attempts"),
    [(400, 1), (422, 1), (503, 3), ("unreachable", 3)],
)
def test_failed_re_registration_keeps_heartbeats_and_waits_for_the_floor(
    telemetry, failure, attempts
):
    sender = telemetry.sender
    telemetry.accounts.emails = ["owner@example.test"]
    state = registered_full(users=[OWNER])
    before = dict(state.registration_metadata)
    telemetry.receiver.outcomes[REGISTER] = [failure] * 3

    first = sender.run_telemetry_cycle()

    assert first["sent"] is True
    assert first["flush_complete"] is True
    assert telemetry.receiver.paths() == [REGISTER] * attempts + [HEARTBEAT]
    state.refresh_from_db()
    assert state.registration_kind == FULL
    assert state.instance_secret == "stored-secret"
    assert state.registration_metadata == before
    assert state.last_registration_error

    # Inside the floor nothing registers, from the schedule or from sign-ups,
    # and heartbeats keep flowing.
    telemetry.receiver.requests.clear()
    assert sender.run_telemetry_cycle()["sent"] is True
    for _ in range(5):
        assert sender.attempt_registration() is True
        fire_signup_hook()
    age_last_attempt(state, RETRY_FLOOR_SECONDS - 60)
    assert sender.ensure_registration() == (True, state.instance_id)
    assert telemetry.receiver.paths() == [HEARTBEAT]

    # After the floor, the re-registration is tried once more and succeeds.
    telemetry.receiver.requests.clear()
    telemetry.receiver.outcomes[REGISTER] = []
    age_last_attempt(state, RETRY_FLOOR_SECONDS + 1)
    assert sender.run_telemetry_cycle()["sent"] is True
    assert telemetry.receiver.paths() == [REGISTER, HEARTBEAT]
    state.refresh_from_db()
    assert "users" not in state.registration_metadata
    assert state.last_registration_error == ""


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422])
@pytest.mark.parametrize("path", [REGISTER, HEARTBEAT])
def test_a_4xx_response_is_a_single_attempt(telemetry, status, path):
    telemetry.receiver.outcomes[path] = [status] * 3

    response = transport.TelemetryClient().post(path, {"instance_id": "fixture"})

    assert response.ok is False
    assert telemetry.receiver.paths() == [path]
    telemetry.sleep.assert_not_called()


@pytest.mark.parametrize("failure", [408, 429, 500, 502, 503, "unreachable"])
def test_timeouts_rate_limits_and_outages_keep_their_retries(telemetry, failure):
    telemetry.receiver.outcomes[REGISTER] = [failure] * 3

    response = transport.TelemetryClient().post(REGISTER, {"instance_id": "fixture"})

    assert response.ok is False
    assert telemetry.receiver.paths() == [REGISTER] * 3
    assert telemetry.sleep.call_args_list == [call(0.2), call(0.5)]


def test_a_retry_that_succeeds_still_succeeds(telemetry):
    telemetry.receiver.outcomes[REGISTER] = [429, 503]

    response = transport.TelemetryClient().post(REGISTER, {"instance_id": "fixture"})

    assert response.ok is True
    assert telemetry.receiver.paths() == [REGISTER] * 3


def test_a_sign_up_burst_after_a_failed_registration_sends_no_burst(telemetry):
    sender = telemetry.sender
    telemetry.accounts.emails = ["owner@example.test"]
    telemetry.receiver.outcomes[REGISTER] = [400]

    # The first attempt is not held back; it fails.
    fire_signup_hook()
    assert telemetry.receiver.paths() == [REGISTER]
    state = get_or_create_telemetry_state()
    assert state.registered_at is None
    assert state.last_registration_error

    for index in range(20):
        fire_signup_hook(user_id=f"user-{index}")
        assert sender.attempt_registration() is False
    assert sender.run_telemetry_cycle()["sent"] is False
    assert telemetry.receiver.paths() == [REGISTER]

    # After the floor the next sign-up registers, and the buffered heartbeat
    # goes out with the next cycle.
    age_last_attempt(state, RETRY_FLOOR_SECONDS + 1)
    fire_signup_hook(user_id="after-the-floor")
    assert telemetry.receiver.paths() == [REGISTER, REGISTER]
    assert sender.run_telemetry_cycle()["sent"] is True
    assert telemetry.receiver.paths()[-1] == HEARTBEAT
    state.refresh_from_db()
    assert state.registration_kind == FULL
    assert state.instance_secret == "issued-secret"


def test_a_successful_registration_is_not_held_back(telemetry, monkeypatch):
    """The floor follows a failure only: a deferral for want of an account is
    not one, and an earlier success does not delay a needed re-registration."""
    sender = telemetry.sender

    assert sender.attempt_registration() is False  # no account yet: deferred
    telemetry.accounts.emails = ["owner@example.test"]
    assert sender.attempt_registration() is True
    assert telemetry.receiver.paths() == [REGISTER]

    # Registered moments ago without emails; opting in re-registers at once.
    monkeypatch.setenv(SHARE, "true")
    assert sender.attempt_registration() is True
    assert telemetry.receiver.paths() == [REGISTER, REGISTER]
    assert telemetry.receiver.bodies(REGISTER)[1]["users"] == [OWNER]


# ---------------------------------------------------------------------------
# (j) The disclosure log line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "shared"),
    [
        (None, False),
        ("false", False),
        ("garbage", False),
        ("true", True),
        (" ON ", True),
    ],
)
def test_the_disclosure_says_whether_emails_are_sent(
    telemetry, monkeypatch, value, shared
):
    from structlog.testing import capture_logs

    if value is not None:
        monkeypatch.setenv(SHARE, value)
    with capture_logs() as records:
        telemetry.sender._log_disclosure()

    (record,) = [r for r in records if r["event"] == "deployment_telemetry_disclosure"]
    assert record["mode"] == "enabled"
    assert record["share_admin_emails"] is shared
    assert record["opt_out_env"] == "FUTURE_AGI_TELEMETRY_DISABLED=true"
    sends = record["sends"]
    assert "instance id, version and deployment type" in sends
    assert "anonymous" not in sends
    assert "nothing" not in sends
    if shared:
        assert "active admin user emails + domains" in sends
        assert SHARE in sends
    else:
        assert "no user emails" in sends
        assert "emails + domains" not in sends


@pytest.mark.parametrize("value", [None, "true"])
def test_the_disclosure_says_why_when_and_which_fields(telemetry, monkeypatch, value):
    """Worded as site gate DH-016-A: why telemetry runs, when the registration
    goes out, and the exact fields of the default registration."""
    from structlog.testing import capture_logs

    if value is not None:
        monkeypatch.setenv(SHARE, value)
    with capture_logs() as records:
        telemetry.sender._log_disclosure()

    (record,) = [r for r in records if r["event"] == "deployment_telemetry_disclosure"]
    assert record["purpose"] == "count installs and plan release testing"
    sends = record["sends"]
    assert sends.startswith(
        "registration once an owner/admin account exists (instance id, version "
        "and deployment type: schema_version, instance_id, version, "
        "deployment_type, timestamp, telemetry_disabled=false; "
    )
    assert "content-free actor journey events" in sends


def test_the_opt_out_disclosure_is_unchanged(telemetry, monkeypatch):
    from structlog.testing import capture_logs

    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    monkeypatch.setenv(SHARE, "true")
    with capture_logs() as records:
        telemetry.sender._log_disclosure()

    (record,) = [r for r in records if r["event"] == "deployment_telemetry_disclosure"]
    assert {key: value for key, value in record.items() if key != "log_level"} == {
        "event": "deployment_telemetry_disclosure",
        "mode": "opt_out",
        "sends": "nothing to Future AGI",
        "opt_out_env": "FUTURE_AGI_TELEMETRY_DISABLED=true (already set)",
    }
    assert telemetry.receiver.requests == []
