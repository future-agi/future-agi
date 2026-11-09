"""r2c controlled overlapping Authenticate/callback schedules; value-free failures."""

import secrets
from unittest.mock import Mock, patch

import pytest
from django.db import connections, router
from rest_framework.test import APIRequestFactory

from ee.falcon_ai import oauth_client, views_connectors
from ee.falcon_ai.credentials import decrypt_falcon_token, encrypt_falcon_token
from ee.falcon_ai.models import MCPConnector
from ee.falcon_ai.tests import test_oauth_at_rest as support
from ee.falcon_ai.tests.test_oauth_at_rest import check, row
from ee.falcon_ai.tests.test_oauth_at_rest_preflight import (
    persistent_test_key,  # noqa: F401 -- pytest fixture
)
from ee.falcon_ai.tests.test_oauth_at_rest_recovery import request_for

pytestmark = pytest.mark.django_db
connector = support.connector
CALLBACK = "https://app.example.invalid/cb"
AUTH_REQUIRED = "CONNECTOR_AUTH_REQUIRED"


def seed_registration(connector):
    connector.oauth_client_id = "superseded-test-client"
    connector.oauth_client_secret = encrypt_falcon_token(secrets.token_urlsafe(32))
    connector.oauth_state = secrets.token_urlsafe(32)
    connector.oauth_code_verifier = secrets.token_urlsafe(48)
    connector.save()
    return row(connector)


def snapshot(connector):
    stored = row(connector)
    return tuple(
        getattr(stored, field.attname) for field in stored._meta.concrete_fields
    )


def callback_for(stale):
    request = APIRequestFactory().get(
        "/callback/", {"code": "test-code", "state": stale.oauth_state}
    )
    # Model a callback whose lookup completed before the replacement committed.
    with patch.object(MCPConnector.objects, "get", return_value=stale):
        return views_connectors.MCPConnectorOAuthCallbackView.as_view()(
            request, connector_id=stale.pk
        )


@pytest.mark.parametrize("replica_loaded", [False, True])
def test_overlapping_authenticate_registers_once_and_refreshes(
    connector, user, replica_loaded
):
    superseded = seed_registration(connector)
    connector.oauth_client_secret = "enc::invalid-test-only"
    connector.save()
    caller_a, caller_b = row(connector), row(connector)
    if replica_loaded:
        caller_a._state.db = caller_b._state.db = "r2c_forbidden_replica"
    secret, access, refresh = (secrets.token_urlsafe(32) for _ in range(3))
    original_preflight = oauth_client.require_persistent_key
    nested = []
    started = False

    def schedule_b_before_a_decides():
        nonlocal started
        original_preflight()
        if not started:
            started = True
            # A has entered Authenticate with its unreadable snapshot; B has the
            # same snapshot and finishes while A is paused before its decision.
            nested.append(
                views_connectors.MCPConnectorAuthenticateView.as_view()(
                    request_for(user, caller_b), connector_id=caller_b.pk
                )
            )

    alias = router.db_for_write(MCPConnector)
    locks = []

    def record_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql.upper():
            locks.append(context["connection"].in_atomic_block)
        return execute(sql, params, many, context)

    with (
        patch.object(
            views_connectors,
            "_get_connector_for_request",
            side_effect=[(caller_a, None), (caller_b, None)],
        ),
        patch.object(
            oauth_client, "require_persistent_key", schedule_b_before_a_decides
        ),
        patch.object(
            oauth_client,
            "register_client",
            return_value={
                "client_id": "replacement-test-client",
                "client_secret": secret,
            },
        ) as register,
        patch.object(router, "db_for_write", return_value=alias) as write_router,
        connections[alias].execute_wrapper(record_lock),
    ):
        response = views_connectors.MCPConnectorAuthenticateView.as_view()(
            request_for(user, caller_a), connector_id=caller_a.pk
        )
    check(response.status_code == 200, "outer Authenticate refused")
    check(
        len(nested) == 1 and nested[0].status_code == 200, "inner Authenticate refused"
    )
    check(register.call_count == 1, "concurrent replacement registered twice")
    check(len(locks) >= 2 and all(locks), "replacement lacks locked re-read")
    check(
        all(not call.kwargs.get("instance") for call in write_router.call_args_list),
        "replica instance biased write route",
    )
    check(caller_a._state.db == alias, "caller not synchronized to WRITE row")

    before = snapshot(connector)
    with patch.object(oauth_client.httpx, "post") as post:
        refused = callback_for(superseded)
    check(refused.status_code == 409, "superseded callback accepted")
    check(AUTH_REQUIRED.encode() in refused.content, "callback lost static code")
    check(not post.called, "superseded callback contacted provider")
    check(snapshot(connector) == before, "superseded callback saved a field")

    current = row(connector)
    token_response = Mock()
    token_response.json.return_value = {
        "access_token": access,
        "refresh_token": refresh,
    }
    with patch.object(oauth_client.httpx, "post", return_value=token_response) as post:
        oauth_client.exchange_code_for_tokens(current, "test-code", CALLBACK)
        oauth_client.refresh_access_token(row(connector))
    check(post.call_count == 2, "current callback or refresh failed")
    check(
        all(
            call.kwargs["data"]["client_id"] == "replacement-test-client"
            for call in post.call_args_list
        ),
        "mixed registration used",
    )
    check(
        all(
            call.kwargs["data"].get("client_secret") == secret
            for call in post.call_args_list
        ),
        "mixed client secret used",
    )
    check(
        post.call_args.kwargs["data"]["refresh_token"] == refresh,
        "mixed refresh token used",
    )
    check(
        decrypt_falcon_token(row(connector).oauth_access_token) == access,
        "token roundtrip failed",
    )


@pytest.mark.parametrize("changed", ["client_id", "client_secret", "state"])
def test_stale_callback_refuses_without_any_write(connector, changed):
    stale = seed_registration(connector)
    if changed == "client_id":
        connector.oauth_client_id = "replacement-test-client"
    elif changed == "client_secret":
        connector.oauth_client_secret = encrypt_falcon_token(secrets.token_urlsafe(32))
    else:
        connector.oauth_state = secrets.token_urlsafe(32)
    connector.save()
    stale._state.db = "r2c_forbidden_replica"
    before = snapshot(connector)
    with (
        patch.object(oauth_client.httpx, "post", return_value=Mock()) as post,
        patch.object(oauth_client, "logger") as logger,
    ):
        response = callback_for(stale)
    check(response.status_code == 409, "stale registration callback accepted")
    check(AUTH_REQUIRED.encode() in response.content, "static code missing")
    check(not post.called, "stale callback sent provider request")
    check(snapshot(connector) == before, "refused callback changed row")
    check(not logger.mock_calls, "refused callback emitted values")


def test_callback_holds_write_lock_through_token_persistence(connector):
    current = seed_registration(connector)
    alias = router.db_for_write(MCPConnector)
    current._state.db = "r2c_forbidden_replica"
    locked = []
    response = Mock()
    response.json.return_value = {"access_token": secrets.token_urlsafe(32)}

    def record_lock(execute, sql, params, many, context):
        if "FOR UPDATE" in sql.upper():
            locked.append(context["connection"].in_atomic_block)
        return execute(sql, params, many, context)

    def provider(*args, **kwargs):
        check(bool(locked) and all(locked), "exchange started before registration lock")
        check(
            connections[alias].in_atomic_block, "callback released lock before exchange"
        )
        return response

    with (
        connections[alias].execute_wrapper(record_lock),
        patch.object(oauth_client.httpx, "post", side_effect=provider),
    ):
        oauth_client.exchange_code_for_tokens(current, "test-code", CALLBACK)
    check(current._state.db == alias, "callback retained replica provenance")
    check(row(connector).is_verified, "current callback did not persist")
