"""Application update locking on a real PostgreSQL row; no fake database."""

import json
import re
import threading
from unittest.mock import patch

import pytest
from django.db import connections, router
from django.db.models import QuerySet

from agentcc.models.webhook import AgentccWebhook
from agentcc.serializers.webhook import AgentccWebhookSerializer
from agentcc.services import webhook_credential_storage as codec
from agentcc.services.secret_mask import MASKED_SECRET
from agentcc.tests.test_webhook_encrypted_fixtures import (
    make_webhook,
    stored_value,
)
from agentcc.tests.test_webhook_encrypted_fixtures import (
    webhook_encryption_key as webhook_encryption_key,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]


def _sqlstate(exc):
    for name in ("sqlstate", "pgcode"):
        value = getattr(exc, name, None)
        if isinstance(value, str) and re.fullmatch(r"[0-9A-Z]{5}", value):
            return value
    return None


def _exception_metadata(exc):
    """Only class identities and SQLSTATEs; never exception text, args or SQL."""
    result = {}
    for label, error in (
        ("caught", exc),
        ("cause", exc.__cause__),
        ("context", exc.__context__),
    ):
        if error is None:
            result[label] = None
            continue
        cls = type(error)
        name = f"{cls.__module__}.{cls.__qualname__}"
        result[label] = {
            "class": name
            if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9.]{0,159}", name)
            else "other",
            "sqlstate": _sqlstate(error),
        }
    return result


def test_update_holds_row_lock_through_encryption_and_preserves_unrelated_state(
    organization,
    record_property,
):
    alias = router.db_for_write(AgentccWebhook)
    if connections[alias].vendor != "postgresql":
        pytest.skip("PostgreSQL independent sessions required")
    hook = make_webhook(
        organization=organization,
        name="concurrent-hook",
        url="https://fixture.invalid/",
        headers={"X": "first"},
        secret="signing",
    )
    stale = AgentccWebhook.no_workspace_objects.get(pk=hook.pk)
    outcomes = []
    original = codec.encrypt_for_storage
    witnessed = False
    diagnostic = {
        "writer_alias": alias if alias in {"default", "default_direct"} else "other",
        "instance_write_route_matches": router.db_for_write(
            AgentccWebhook, instance=stale
        )
        == alias,
        "lock_query_completed_in_atomic": False,
        "writer_atomic_before": False,
        "writer_atomic_after": False,
        "independent_backend": False,
        "same_database": False,
        "competitor_finished_inside_callback": False,
        "stage": "not_started",
        "exception": None,
        "cleanup_exception": None,
    }

    def compete(writer_session):
        connection = None
        try:
            diagnostic["stage"] = "connection_copy"
            thread_connection = connections[alias]
            connection = thread_connection.copy()
            # Registration is thread-local; the writer retains its own connection.
            connections[alias] = connection
            diagnostic["stage"] = "cursor_open"
            with connection.cursor() as cursor:
                diagnostic["stage"] = "session_identity"
                cursor.execute("SELECT pg_backend_pid(), current_database()")
                competitor_session = cursor.fetchone()
                diagnostic["independent_backend"] = (
                    competitor_session[0] != writer_session[0]
                )
                diagnostic["same_database"] = competitor_session[1] == writer_session[1]
                if not (
                    diagnostic["independent_backend"] and diagnostic["same_database"]
                ):
                    return
                diagnostic["stage"] = "lock_timeout_setup"
                cursor.execute("SET lock_timeout = '200ms'")
                diagnostic["stage"] = "update"
                QuerySet(model=AgentccWebhook, using=alias).filter(pk=hook.pk).update(
                    description="competing"
                )
                outcomes.append("unexpected-write")
        except Exception as exc:
            diagnostic["exception"] = _exception_metadata(exc)
            if diagnostic["stage"] == "update":
                # Django preserves the driver exception as the cause.
                outcomes.append(_sqlstate(exc.__cause__))
        finally:
            if connection is not None:
                try:
                    try:
                        connection.close()
                    finally:
                        connections[alias] = thread_connection
                except Exception as exc:
                    diagnostic["cleanup_exception"] = _exception_metadata(exc)

    def observe_lock(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        if (
            sql.lstrip().upper().startswith("SELECT")
            and '"agentcc_webhook"' in sql
            and "FOR UPDATE" in sql.upper()
        ):
            diagnostic["lock_query_completed_in_atomic"] = (
                context["connection"] is connections[alias]
                and connections[alias].in_atomic_block
            )
        return result

    def witness(context, value):
        nonlocal witnessed
        if not witnessed:
            witnessed = True
            diagnostic["writer_atomic_before"] = connections[alias].in_atomic_block
            assert diagnostic["instance_write_route_matches"], diagnostic
            assert diagnostic["lock_query_completed_in_atomic"], diagnostic
            assert diagnostic["writer_atomic_before"], diagnostic
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid(), current_database()")
                writer_session = cursor.fetchone()
            thread = threading.Thread(target=compete, args=(writer_session,))
            thread.start()
            thread.join(timeout=5)
            diagnostic["competitor_finished_inside_callback"] = not thread.is_alive()
            diagnostic["writer_atomic_after"] = connections[alias].in_atomic_block
            assert diagnostic["competitor_finished_inside_callback"], diagnostic
            assert diagnostic["writer_atomic_after"], diagnostic
            assert diagnostic["independent_backend"], diagnostic
            assert diagnostic["same_database"], diagnostic
            assert outcomes == ["55P03"], diagnostic
            assert diagnostic["cleanup_exception"] is None, diagnostic
        return original(context, value)

    # Independent concurrent budget deduplication state must not be overwritten
    # by the stale serializer's full model snapshot.
    AgentccWebhook.no_workspace_objects.filter(pk=hook.pk).update(
        budget_event_keys={"period": 123}
    )
    serializer = AgentccWebhookSerializer(
        stale, data={"headers": {"x": MASKED_SECRET}}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    try:
        with (
            connections[alias].execute_wrapper(observe_lock),
            patch.object(codec, "encrypt_for_storage", side_effect=witness),
        ):
            serializer.save()
    finally:
        record_property(
            "webhook_lock_diagnostic", json.dumps(diagnostic, sort_keys=True)
        )
    assert witnessed
    hook.refresh_from_db()
    assert stored_value(hook, "headers") == {"x": "first"}
    assert stored_value(hook, "secret") == "signing"
    assert hook.budget_event_keys == {"period": 123}
