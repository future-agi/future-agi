"""Actual HTTP auth/DRF error handling without importing or starting the app.

Run this file directly with unittest, not the backend pytest bootstrap. The
application definitions are compiled unchanged from their AST; ORM/cache and
the unrelated EE feature exception are doubles. DRF dispatch, exception handling,
set_rollback and the public error-envelope implementation are real.
"""

import ast
import base64
import hashlib
import json
import re
import runpy
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.apps import apps
from django.conf import settings

if not settings.configured:
    settings.configure(
        USE_I18N=False,
        SECRET_KEY="offline-auth-fixture",
        REST_FRAMEWORK={"UNAUTHENTICATED_USER": None},
    )

from django.db import (
    DatabaseError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    PermissionDenied,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

ROOT = Path(__file__).resolve().parents[2] / "futureagi"
SOURCE = ROOT / "accounts/authentication.py"
NOW = datetime(2026, 9, 9, tzinfo=UTC)


def load_definitions():
    names = {
        "APIKeyAuthentication",
        "decode_token",
        "decrypt_message",
        "custom_exception_handler",
        "DatabaseUnavailable",
        "_database_unavailable_metadata",
    }
    nodes = [
        node
        for node in ast.parse(SOURCE.read_text(), filename=str(SOURCE)).body
        if getattr(node, "name", None) in names
    ]
    # New definitions stay optional so retained old source reaches behavioral
    # assertions instead of failing to load during red-first replay.
    assert {node.name for node in nodes} >= names - {
        "DatabaseUnavailable",
        "_database_unavailable_metadata",
    }
    ns = {
        "BaseAuthentication": BaseAuthentication,
        "APIException": APIException,
        "AuthenticationFailed": AuthenticationFailed,
        "PermissionDenied": PermissionDenied,
        "DatabaseError": DatabaseError,
        "InterfaceError": InterfaceError,
        "OperationalError": OperationalError,
        "IntegrityError": IntegrityError,
        "Response": Response,
        "status": status,
        "settings": settings,
        "Fernet": Fernet,
        "base64": base64,
        "hashlib": hashlib,
        "json": json,
        "re": re,
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": SimpleNamespace(now=lambda: NOW),
        "AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES": 60,
        "logger": MagicMock(),
        "structlog": MagicMock(),
        "traceback": MagicMock(),
    }
    ns.update(runpy.run_path(str(ROOT / "tfc/utils/api_errors.py")))
    ns["__name__"] = "accounts.authentication"
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), ns)
    return ns


class AuthenticationDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.assertFalse(apps.ready, "This suite must not bootstrap Django apps")
        self.ns = load_definitions()
        self.real_decrypt = self.ns["decrypt_message"]
        ee = ModuleType("tfc.ee_gating")
        ee.FeatureUnavailable = type("FeatureUnavailable", (APIException,), {})
        self.modules = patch.dict("sys.modules", {"tfc.ee_gating": ee})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.atomic = MagicMock(
            settings_dict={"ATOMIC_REQUESTS": True}, in_atomic_block=True
        )
        self.nonatomic = MagicMock(
            settings_dict={"ATOMIC_REQUESTS": False}, in_atomic_block=False
        )
        self.connections = patch("rest_framework.views.connections")
        self.connections.start().all.return_value = [self.atomic, self.nonatomic]
        self.addCleanup(self.connections.stop)
        self.prepare_auth()

    def prepare_auth(self):
        self.ns["logger"].reset_mock()
        self.atomic.reset_mock()
        self.nonatomic.reset_mock()
        self.user = SimpleNamespace(
            id="fixture-user",
            pk="fixture-user",
            is_active=True,
            is_authenticated=True,
            _state=SimpleNamespace(fields_cache={"organization": None}),
        )
        self.users, self.tokens, self.keys, self.cache = (MagicMock() for _ in range(4))
        self.users.select_related.return_value.get.return_value = self.user
        self.token = SimpleNamespace(
            is_active=True, last_used_at=NOW, id="fixture-token", save=MagicMock()
        )
        self.tokens.get.return_value = self.token
        self.cache.get.return_value = {"user": self.user, "token": "fixture-access"}
        self.missing = type("DoesNotExist", (Exception,), {})
        self.ns.update(
            User=SimpleNamespace(objects=self.users, DoesNotExist=self.missing),
            AuthToken=SimpleNamespace(objects=self.tokens, DoesNotExist=self.missing),
            AuthTokenType=SimpleNamespace(ACCESS=SimpleNamespace(value="access")),
            OrgApiKey=SimpleNamespace(objects=self.keys, DoesNotExist=self.missing),
            cache=self.cache,
        )
        self.ns["decrypt_message"] = MagicMock(
            return_value={"user_id": "fixture-user", "id": "fixture-token"}
        )
        self.auth = self.ns["APIKeyAuthentication"]()
        self.auth._bind_user_context = MagicMock()
        self.auth._set_workspace_context = MagicMock()
        self.get = MagicMock(return_value=Response({"ok": True}))
        auth, handler, get = self.auth, self.ns["custom_exception_handler"], self.get

        class Probe(APIView):
            permission_classes = [IsAuthenticated]

            def get_authenticators(self):
                return [auth]

            def get_exception_handler(self):
                return handler

            def get(self, request):
                return get(request)

        self.view = Probe.as_view()

    def request(self, api_key=False):
        headers = (
            {"HTTP_X_API_KEY": "fixture-key", "HTTP_X_SECRET_KEY": "fixture-secret"}
            if api_key
            else {"HTTP_AUTHORIZATION": "Bearer fixture-access"}
        )
        return self.view(APIRequestFactory().get("/offline-auth-probe/", **headers))

    def assert_unavailable(self, response, secret, *, view_called=False):
        self.assertIsNotNone(response, "Database unavailability must be handled")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "service_unavailable")
        self.assertEqual(response.data["type"], "service_unavailable")
        self.assertIs(response.data["status"], False)
        self.assertEqual(response.data["detail"], "Database temporarily unavailable.")
        self.assertNotIn(secret, json.dumps(response.data))
        self.assertNotIn("WWW-Authenticate", response)
        self.assertNotIn("Retry-After", response)
        if view_called:
            self.get.assert_called_once()
        else:
            self.get.assert_not_called()
        self.atomic.set_rollback.assert_called_once_with(True)
        self.nonatomic.set_rollback.assert_not_called()
        self.assertFalse(apps.ready)

    def assert_database_event(
        self,
        error_class,
        *,
        cause_class=None,
        sqlstate=None,
        module=None,
        function=None,
        line=None,
    ):
        self.ns["logger"].warning.assert_called_once_with(
            "api_database_unavailable",
            error_class=error_class,
            cause_class=cause_class,
            sqlstate=sqlstate,
            source_module=module,
            source_function=function,
            source_line=line,
        )
        self.assertEqual(len(self.ns["logger"].mock_calls), 1)

    def test_decoder_database_errors_escape_unchanged_without_retry(self):
        for error_type in (
            OperationalError,
            InterfaceError,
            ProgrammingError,
            IntegrityError,
        ):
            with self.subTest(error_type=error_type.__name__):
                self.prepare_auth()
                error = error_type("synthetic database failure")
                update = self.tokens.filter.return_value.update
                update.side_effect = error
                with self.assertRaises(error_type) as raised:
                    self.ns["decode_token"]("fixture-access")
                self.assertIs(raised.exception, error)
                update.assert_called_once()
                self.cache.delete.assert_not_called()
                self.assertEqual(self.ns["logger"].mock_calls, [])

    def test_operational_failures_at_auth_database_boundaries_are_503(self):
        for stage in (
            "cached-user",
            "cached-touch",
            "cold-user",
            "cold-token",
            "cold-save",
            "workspace",
            "api-key",
        ):
            for error_type in (OperationalError, InterfaceError):
                with self.subTest(stage=stage, error_type=error_type.__name__):
                    self.prepare_auth()
                    secret = "synthetic-private-dsn-password"
                    error = error_type(secret)
                    if stage.startswith("cold-"):
                        self.cache.get.return_value = None
                    if stage == "cached-user":
                        self.user._state.fields_cache = {}
                    boundary = {
                        "cached-user": self.users.select_related.return_value.get,
                        "cached-touch": self.tokens.filter.return_value.update,
                        "cold-user": self.users.select_related.return_value.get,
                        "cold-token": self.tokens.get,
                        "cold-save": self.token.save,
                        "workspace": self.auth._set_workspace_context,
                        "api-key": self.keys.select_related.return_value.get,
                    }[stage]

                    def fail_at_boundary(*args, error=error, **kwargs):
                        raise error

                    boundary.side_effect = fail_at_boundary
                    self.assert_unavailable(
                        self.request(api_key=stage == "api-key"), secret
                    )
                    self.assert_database_event(
                        error_type.__name__,
                        module=__name__,
                        function="fail_at_boundary",
                        line=fail_at_boundary.__code__.co_firstlineno + 1,
                    )
                    boundary.assert_called_once()
                    self.cache.delete.assert_not_called()
                    self.tokens.create.assert_not_called()
                    self.assertTrue(self.token.is_active)
                    if stage != "workspace":
                        self.auth._set_workspace_context.assert_not_called()

    def test_nonavailability_database_errors_remain_unhandled_server_errors(self):
        for error_type in (DatabaseError, ProgrammingError, IntegrityError):
            for stage in ("decode", "workspace", "api-key"):
                with self.subTest(error_type=error_type.__name__, stage=stage):
                    self.prepare_auth()
                    error = error_type("synthetic internal error")
                    boundary = {
                        "decode": self.tokens.filter.return_value.update,
                        "workspace": self.auth._set_workspace_context,
                        "api-key": self.keys.select_related.return_value.get,
                    }[stage]
                    boundary.side_effect = error
                    self.assertIsNone(self.ns["custom_exception_handler"](error, {}))
                    # DRF re-raises; Django's outer server-error handling owns 500.
                    with self.assertRaises(error_type) as raised:
                        self.request(api_key=stage == "api-key")
                    self.assertIs(raised.exception, error)
                    boundary.assert_called_once()
                    self.get.assert_not_called()
                    self.assertEqual(self.ns["logger"].mock_calls, [])

    def test_view_origin_uses_actual_innermost_traceback_and_driver_cause(self):
        class DriverFailure(Exception):
            sqlstate = "57P01"

        for error_type in (OperationalError, InterfaceError):
            with self.subTest(error_type=error_type.__name__):
                self.prepare_auth()
                secret = "synthetic-private-dsn-password"
                error = error_type(secret)

                def view_failure(request, secret=secret, error=error):
                    try:
                        raise DriverFailure(secret)
                    except DriverFailure as cause:
                        raise error from cause

                self.get.side_effect = view_failure
                self.assert_unavailable(self.request(), secret, view_called=True)
                self.assert_database_event(
                    error_type.__name__,
                    cause_class="DriverFailure",
                    sqlstate="57P01",
                    module=__name__,
                    function="view_failure",
                    line=view_failure.__code__.co_firstlineno + 4,
                )
                self.tokens.filter.return_value.update.assert_called_once()
                self.tokens.create.assert_not_called()
                self.cache.delete.assert_not_called()

    def test_missing_traceback_does_not_inspect_request_or_exception_messages(self):
        class PrivateFailure(OperationalError):
            def __str__(self):
                raise AssertionError("Exception text must never be read")

            __repr__ = __str__

        class UnreadableRequest:
            def __getattribute__(self, name):
                raise AssertionError("Request data must never be read")

        error = PrivateFailure("synthetic-private-dsn-password")
        response = self.ns["custom_exception_handler"](
            error, {"request": UnreadableRequest()}
        )
        self.assert_unavailable(response, "synthetic-private-dsn-password")
        self.assert_database_event("PrivateFailure")

    def test_sqlstate_accepts_only_five_uppercase_ascii_letters_or_digits(self):
        for location in ("error", "cause"):
            for attribute in ("sqlstate", "pgcode"):
                for value, expected in (
                    ("08006", "08006"),
                    ("57P01", "57P01"),
                    ("57p01", None),
                    ("0800", None),
                    ("080006", None),
                    (" 08006", None),
                    ("08006\n", None),
                    ("５７P01", None),
                    ("ABCDE;SELECT secret", None),
                    ("synthetic-private-dsn-password", None),
                    (8006, None),
                    (b"08006", None),
                    (None, None),
                ):
                    with self.subTest(
                        location=location, attribute=attribute, value=value
                    ):
                        self.prepare_auth()
                        error = OperationalError("synthetic-private-dsn-password")
                        target = error
                        if location == "cause":
                            target = RuntimeError("synthetic-private-driver-password")
                            error.__cause__ = target
                        setattr(target, attribute, value)
                        response = self.ns["custom_exception_handler"](error, {})
                        self.assert_unavailable(
                            response, "synthetic-private-dsn-password"
                        )
                        self.assert_database_event(
                            "OperationalError",
                            cause_class="RuntimeError" if location == "cause" else None,
                            sqlstate=expected,
                        )

    def test_immediate_cause_sqlstate_precedes_wrapper_and_implicit_context_is_bounded(
        self,
    ):
        for explicit, suppressed in ((True, False), (False, False), (False, True)):
            with self.subTest(explicit=explicit, suppressed=suppressed):
                self.prepare_auth()
                error = InterfaceError("private-wrapper")
                error.sqlstate = "08006"
                cause = RuntimeError("private-cause")
                cause.pgcode = "57P01"
                cause.__context__ = cause  # Never traverse an unbounded cause chain.
                if explicit:
                    error.__cause__ = cause
                else:
                    error.__context__ = cause
                    error.__suppress_context__ = suppressed
                self.assert_unavailable(
                    self.ns["custom_exception_handler"](error, {}), "private"
                )
                self.assert_database_event(
                    "InterfaceError",
                    cause_class=None if suppressed else "RuntimeError",
                    sqlstate="08006" if suppressed else "57P01",
                )

    def test_metadata_names_are_bounded_and_never_use_filename_or_locals(self):
        for module, expected_module in (
            ("fixture." + "m" * 160, ("fixture." + "m" * 160)[:128]),
            ("/private/secret.py", None),
            ("private\npassword", None),
            (None, None),
        ):
            with self.subTest(module=module):
                self.prepare_auth()
                error = type("E" * 160, (OperationalError,), {})("private-message")
                error.__cause__ = type("C" * 160, (Exception,), {})("private-cause")
                try:
                    exec(
                        compile("raise error", "/private/secret.py", "exec"),
                        {
                            "__name__": module,
                            "error": error,
                            "password": "private-local",
                        },
                    )
                except OperationalError as caught:
                    response = self.ns["custom_exception_handler"](caught, {})
                self.assert_unavailable(response, "private")
                self.assert_database_event(
                    "E" * 128,
                    cause_class="C" * 128,
                    module=expected_module,
                    function="<module>",
                    line=1,
                )

    def test_non_database_view_failure_is_not_logged_as_database_unavailable(self):
        error = ValueError("synthetic-private-value")
        self.get.side_effect = error
        with self.assertRaises(ValueError) as raised:
            self.request()
        self.assertIs(raised.exception, error)
        self.get.assert_called_once()
        self.assertEqual(self.ns["logger"].mock_calls, [])
        self.atomic.set_rollback.assert_not_called()

    def test_invalid_token_cases_remain_401(self):
        for case in (
            "encryption",
            "missing-user",
            "missing-token",
            "inactive-user",
            "inactive-token",
            "expired-token",
        ):
            with self.subTest(case=case):
                self.prepare_auth()
                self.cache.get.return_value = None
                if case == "encryption":
                    self.ns["decrypt_message"] = self.real_decrypt
                elif case == "missing-user":
                    self.users.select_related.return_value.get.side_effect = (
                        self.missing()
                    )
                elif case == "missing-token":
                    self.tokens.get.side_effect = self.missing()
                elif case == "inactive-user":
                    self.user.is_active = False
                elif case == "inactive-token":
                    self.token.is_active = False
                else:
                    self.token.last_used_at = NOW - timedelta(hours=2)
                response = self.request()
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.data["code"], "authentication_failed")
                self.assertEqual(response["WWW-Authenticate"], "ApiKey")
                self.get.assert_not_called()
                self.assertEqual(self.ns["logger"].mock_calls, [])

    def test_invalid_api_key_remains_401(self):
        self.keys.select_related.return_value.get.side_effect = self.missing()
        self.assertEqual(self.request(api_key=True).status_code, 401)
        self.assertEqual(self.ns["logger"].mock_calls, [])

    def test_permission_denied_remains_403(self):
        self.auth._set_workspace_context.side_effect = PermissionDenied(
            "Access denied to this workspace"
        )
        response = self.request()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "permission_denied")
        self.assertNotIn("WWW-Authenticate", response)
        self.get.assert_not_called()
        self.assertEqual(self.ns["logger"].mock_calls, [])

    def test_success_keeps_existing_token_and_touch(self):
        self.assertEqual(self.request().status_code, 200)
        self.tokens.filter.return_value.update.assert_called_once_with(last_used_at=NOW)
        self.auth._set_workspace_context.assert_called_once()
        self.tokens.create.assert_not_called()
        self.assertEqual(self.cache.get.return_value["token"], "fixture-access")
        self.assertEqual(self.ns["logger"].mock_calls, [])

    def test_drf_rollback_is_once_per_503_and_only_for_active_atomic_request(self):
        response = self.ns["custom_exception_handler"](OperationalError("private"), {})
        self.assert_unavailable(response, "private")
        self.atomic.set_rollback.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
