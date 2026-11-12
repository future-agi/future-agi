"""r2d: real committed PostgreSQL connections, value-free deterministic schedules."""

import secrets
import threading
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.db import connections, router, transaction
from django.test import RequestFactory, TransactionTestCase
from django.utils import timezone

from ee.falcon_ai import oauth_client, views_connectors
from ee.falcon_ai.admin import MCPConnectorAdmin
from ee.falcon_ai.credentials import FalconCredentialError, encrypt_falcon_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_oauth_at_rest as support
from ee.falcon_ai.tests.test_oauth_at_rest import check, row
from ee.falcon_ai.tests.test_oauth_at_rest_preflight import (
    persistent_test_key,  # noqa: F401
)
from ee.falcon_ai.tests.test_oauth_at_rest_recovery import request_for

pytestmark = pytest.mark.django_db(transaction=True)
connector = support.connector
CALLBACK = "https://app.example.invalid/cb"
WAIT = 8


def seed(connector):
    connector.oauth_client_id = "contention-test-client"
    connector.oauth_client_secret = encrypt_falcon_token(secrets.token_urlsafe(32))
    connector.oauth_access_token = encrypt_falcon_token(secrets.token_urlsafe(32))
    connector.oauth_refresh_token = encrypt_falcon_token(secrets.token_urlsafe(32))
    connector.oauth_state = secrets.token_urlsafe(32)
    connector.oauth_code_verifier = secrets.token_urlsafe(48)
    connector.oauth_token_expires_at = timezone.now() + timedelta(hours=1)
    connector.is_verified = True
    connector.save()
    return row(connector)


def snapshot(connector):
    stored = row(connector)
    return tuple(getattr(stored, f.attname) for f in stored._meta.concrete_fields)


def pid():
    alias = router.db_for_write(MCPConnector)
    check(connections[alias].vendor == "postgresql", "PostgreSQL required")
    with connections[alias].cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        return cursor.fetchone()[0]


@contextmanager
def db_budget():
    """Bound both connections, including regressions that accidentally keep locks."""
    alias = router.db_for_write(MCPConnector)
    connection = connections[alias]
    check(connection.vendor == "postgresql", "PostgreSQL required")
    check(connection.get_autocommit(), "schedule needs committed fixture rows")
    with connection.cursor() as cursor:
        cursor.execute("SHOW lock_timeout")
        lock_timeout = cursor.fetchone()[0]
        cursor.execute("SHOW statement_timeout")
        statement_timeout = cursor.fetchone()[0]
        cursor.execute("SET lock_timeout = '2s'")
        cursor.execute("SET statement_timeout = '3s'")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('lock_timeout', %s, false)", [lock_timeout]
            )
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, false)", [statement_timeout]
            )


class Concurrent:
    def __init__(self, operation):
        self.operation = operation
        self.ready = threading.Barrier(2, timeout=WAIT)
        self.release = threading.Event()
        self.errors = []
        self.connection_pid = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            with db_budget():
                self.connection_pid = pid()
                self.operation()
        except Exception as error:
            # Never return exception text, traceback locals, SQL parameters or tokens.
            self.errors.append(type(error).__name__)
        finally:
            connections.close_all()

    def pause(self):
        self.ready.wait()
        check(self.release.wait(WAIT), "schedule release deadline exceeded")

    def __enter__(self):
        self.thread.start()
        try:
            self.ready.wait()
            check(self.connection_pid != pid(), "schedule reused one DB connection")
        except Exception:
            self.release.set()
            self.thread.join(WAIT + 2)
            raise AssertionError("worker did not reach contention barrier") from None
        return self

    def __exit__(self, *args):
        self.release.set()
        self.thread.join(WAIT + 2)
        check(not self.thread.is_alive(), "worker did not terminate")


def response():
    result = Mock()
    result.json.return_value = {
        "access_token": secrets.token_urlsafe(32),
        "refresh_token": secrets.token_urlsafe(32),
    }
    return result


def scenario_refresh_cannot_overwrite_committed_winner(
    connector, winner, exchange=False
):
    seed(connector)

    def operation():
        if exchange:
            return oauth_client.exchange_code_for_tokens(
                row(connector), "test-code", CALLBACK
            )
        return oauth_client.refresh_access_token(row(connector))

    runner = Concurrent(operation)

    def provider(*args, **kwargs):
        runner.pause()
        return response()

    with patch.object(oauth_client.httpx, "post", side_effect=provider), runner:
        if winner == "disconnect":
            row(connector).delete()
        else:
            with patch.object(
                oauth_client,
                "register_client",
                return_value={"client_id": "replacement-test-client"},
            ):
                # Force an actual replacement of the now-unreadable registration.
                fresh = row(connector)
                fresh.oauth_client_secret = "enc::invalid-test-only"
                fresh.save(update_fields=["oauth_client_secret"])
                oauth_client.build_authorization_url(fresh, CALLBACK)
        committed = snapshot(connector)
    check(snapshot(connector) == committed, "stale refresh changed winner row")
    check(
        runner.errors == ["FalconCredentialError"], "refresh did not refuse stale grant"
    )


def scenario_replacement_clears_previous_grant_immediately(connector):
    seed(connector)
    connector.oauth_client_secret = "enc::invalid-test-only"
    connector.save()
    with patch.object(
        oauth_client,
        "register_client",
        return_value={"client_id": "replacement-test-client"},
    ):
        oauth_client.build_authorization_url(connector, CALLBACK)
    current = row(connector)
    check(not current.oauth_access_token, "replacement retained old access")
    check(not current.oauth_refresh_token, "replacement retained old refresh")
    check(current.oauth_token_expires_at is None, "replacement retained old expiry")
    check(not current.is_verified, "replacement retained verification")


def scenario_admin_metadata_save_cannot_restore_pre_callback_grant(connector):
    seed(connector)
    admin = MCPConnectorAdmin(MCPConnector, AdminSite())
    request = RequestFactory().post("/")

    def admin_save():
        stale = row(connector)
        stale.name = "metadata-edited"
        runner.pause()
        admin.save_model(request, stale, Mock(changed_data=["name"]), True)

    runner = Concurrent(admin_save)
    with runner:
        with patch.object(oauth_client.httpx, "post", return_value=response()):
            oauth_client.exchange_code_for_tokens(row(connector), "test-code", CALLBACK)
        committed = row(connector)
    current = row(connector)
    check(not runner.errors, "admin metadata save failed")
    check(current.name == "metadata-edited", "admin metadata update lost")
    for field in (
        "oauth_access_token",
        "oauth_refresh_token",
        "oauth_state",
        "oauth_code_verifier",
    ):
        check(
            getattr(current, field) == getattr(committed, field),
            "admin restored stale grant",
        )


def scenario_admin_oauth_identity_and_verification_are_readonly(connector):
    admin = MCPConnectorAdmin(MCPConnector, AdminSite())
    expected = {
        "auth_header_value",
        "oauth_client_id",
        "oauth_client_secret",
        "oauth_server_metadata",
        "oauth_access_token",
        "oauth_refresh_token",
        "oauth_token_expires_at",
        "oauth_code_verifier",
        "oauth_state",
        "is_verified",
    }
    check(
        expected.issubset(set(admin.readonly_fields)), "admin exposes credential edits"
    )


def scenario_patch_cannot_repopulate_disconnected_row(connector, user):
    seed(connector)
    original_encrypt = views_connectors.encrypt_falcon_token
    statuses = []

    def patch_secret():
        result = views_connectors.MCPConnectorDetailView.as_view()(
            request_for(
                user,
                connector,
                "patch",
                {"auth_header_value": secrets.token_urlsafe(32)},
            ),
            connector_id=connector.pk,
        )
        statuses.append(result.status_code)

    runner = Concurrent(patch_secret)

    def encryption(value):
        runner.pause()
        return original_encrypt(value)

    with (
        patch.object(views_connectors, "encrypt_falcon_token", side_effect=encryption),
        runner,
    ):
        row(connector).delete()
        committed = snapshot(connector)
    check(not runner.errors, "PATCH boundary raised")
    check(snapshot(connector) == committed, "PATCH repopulated disconnected row")
    check(statuses == [409], "PATCH did not refuse stale row")


def scenario_slow_provider_does_not_hold_row_lock_or_commit(connector):
    seed(connector)
    connector.oauth_client_secret = "enc::invalid-test-only"
    connector.save()
    before = snapshot(connector)
    runner = Concurrent(
        lambda: oauth_client.build_authorization_url(row(connector), CALLBACK)
    )
    unlocked = False

    def slow_provider(*args, **kwargs):
        runner.pause()
        raise RuntimeError("test provider deadline")

    with (
        patch.object(oauth_client, "register_client", side_effect=slow_provider),
        runner,
    ):
        alias = router.db_for_write(MCPConnector)
        try:
            with transaction.atomic(using=alias):
                MCPConnector._base_manager.using(alias).select_for_update(
                    nowait=True
                ).get(pk=connector.pk)
            unlocked = True
        except Exception:
            unlocked = False
    check(unlocked, "provider I/O held row lock")
    check(snapshot(connector) == before, "provider timeout changed credentials")
    check(runner.errors == ["RuntimeError"], "unexpected provider outcome")


def scenario_stale_snapshot_refused_before_provider(connector, operation):
    stale = seed(connector)
    row(connector).delete()
    before = snapshot(connector)
    refused = False
    with patch.object(oauth_client.httpx, "post", return_value=response()) as post:
        try:
            if operation == "refresh":
                oauth_client.refresh_access_token(stale)
            else:
                oauth_client.exchange_code_for_tokens(stale, "test-code", CALLBACK)
        except FalconCredentialError:
            refused = True
    check(refused, "deleted snapshot accepted")
    check(not post.called, "deleted snapshot reached provider")
    check(snapshot(connector) == before, "deleted snapshot wrote a column")


class WriterContentionTests(TransactionTestCase):
    # Mirror accounts/tests/test_hard_erase_db.py:IsolatedEraseTestCase.
    # Authority exists ONLY for framework teardown in an isolated test_ DB,
    # never during a scenario, credential operation or concurrency barrier.
    @pytest.fixture(autouse=True)
    def fixture_rows(self, connector, user):
        self.connector = connector
        self.user = user

    def setUp(self):
        # unittest cleanups run before Django flush; pytest fixture finalizers
        # run after it. Keep the 2s/3s scenario limits out of database teardown.
        self.enterContext(db_budget())

    def _fixture_teardown(self):
        from accounts.services.hard_erase_sql import sql_authority

        alias = router.db_for_write(MCPConnector)
        connection = connections[alias]
        check(
            str(connection.settings_dict["NAME"]).split("/")[-1].startswith("test_"),
            "contention teardown requires isolated test database",
        )
        with transaction.atomic(using=alias), sql_authority(alias, "erase"):
            super()._fixture_teardown()

    def test_refresh_vs_disconnect(self):
        scenario_refresh_cannot_overwrite_committed_winner(self.connector, "disconnect")

    def test_refresh_vs_replacement(self):
        scenario_refresh_cannot_overwrite_committed_winner(
            self.connector, "replacement"
        )

    def test_replacement_clears_previous_grant(self):
        scenario_replacement_clears_previous_grant_immediately(self.connector)

    def test_admin_vs_callback(self):
        scenario_admin_metadata_save_cannot_restore_pre_callback_grant(self.connector)

    def test_admin_readonly(self):
        scenario_admin_oauth_identity_and_verification_are_readonly(self.connector)

    def test_patch_vs_disconnect(self):
        scenario_patch_cannot_repopulate_disconnected_row(self.connector, self.user)

    def test_slow_provider_releases_lock(self):
        scenario_slow_provider_does_not_hold_row_lock_or_commit(self.connector)

    def test_deleted_refresh_before_io(self):
        scenario_stale_snapshot_refused_before_provider(self.connector, "refresh")

    def test_deleted_exchange_before_io(self):
        scenario_stale_snapshot_refused_before_provider(self.connector, "exchange")

    def test_callback_vs_disconnect(self):
        scenario_refresh_cannot_overwrite_committed_winner(
            self.connector, "disconnect", exchange=True
        )

    def test_callback_vs_replacement(self):
        scenario_refresh_cannot_overwrite_committed_winner(
            self.connector, "replacement", exchange=True
        )
