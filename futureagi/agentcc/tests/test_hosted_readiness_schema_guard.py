"""Hosted readiness between the hosted migration phases, next to the startup guard.

Two features meet here. The self-hosted chart turns on the startup schema guard
(STARTUP_SCHEMA_GUARD_ENABLED, tfc/schema_readiness.py); the hosted profile does
not (deploy/helm/futureagi/hack/test_chart_union_preservation.py pins both renders).
A hosted API pod started after the schema phase and before backfill therefore
answers /readyz/ by agentcc/readiness.py, never by the self-hosted guard.
"""

import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from django.test import Client, override_settings

from tfc.schema_readiness import SchemaState
from tfc.settings.settings import MIDDLEWARE as DEPLOYED_MIDDLEWARE

GUARD_CHECK = "tfc.middleware.schema_readiness.schema_readiness.check"


def between_phases(stack, database_ready):
    """Hosted configuration, gateway address and shared state are in order;
    whether this image's migrations are all applied is `database_ready`."""
    for target, kwargs in (
        ("agentcc.readiness.deployment_profile", {"return_value": "hosted"}),
        ("agentcc.readiness.validate_hosted_configuration", {}),
        ("agentcc.readiness.hosted_gateway_public_url_missing", {"return_value": False}),
        ("agentcc.readiness.database_ready", {"return_value": database_ready}),
        ("agentcc.readiness.shared_state_ready", {"return_value": True}),
    ):
        stack.enter_context(patch(target, **kwargs))


@pytest.mark.django_db
def test_hosted_pod_between_phases_answers_readyz_by_hosted_readiness():
    # The hosted render sets no STARTUP_SCHEMA_GUARD_ENABLED, so the setting is off.
    with ExitStack() as stack:
        stack.enter_context(
            override_settings(MIDDLEWARE=DEPLOYED_MIDDLEWARE, STARTUP_SCHEMA_GUARD_ENABLED=False)
        )
        between_phases(stack, database_ready=False)
        guard = stack.enter_context(patch(GUARD_CHECK, return_value=SchemaState.PENDING))
        response = Client().get("/readyz/")

    assert response.status_code == 503
    assert json.loads(response.content) == {"status": "not_ready", "reason": "database_or_schema"}
    assert response["Cache-Control"] == "no-store"
    assert "Retry-After" not in response
    guard.assert_not_called()


@pytest.mark.django_db
def test_hosted_pod_after_backfill_is_ready_whatever_the_startup_guard_would_say():
    with ExitStack() as stack:
        stack.enter_context(
            override_settings(MIDDLEWARE=DEPLOYED_MIDDLEWARE, STARTUP_SCHEMA_GUARD_ENABLED=False)
        )
        between_phases(stack, database_ready=True)
        guard = stack.enter_context(patch(GUARD_CHECK, return_value=SchemaState.PENDING))
        response = Client().get("/readyz/")

    assert response.status_code == 200
    assert json.loads(response.content) == {"status": "ready"}
    guard.assert_not_called()


@pytest.mark.django_db
def test_with_the_self_hosted_guard_on_readyz_would_get_the_guard_answer_instead():
    # Why the hosted profile must not switch the guard on: with it on, the guard
    # answers first while the schema is pending, before hosted readiness runs.
    with ExitStack() as stack:
        stack.enter_context(
            override_settings(MIDDLEWARE=DEPLOYED_MIDDLEWARE, STARTUP_SCHEMA_GUARD_ENABLED=True)
        )
        between_phases(stack, database_ready=False)
        stack.enter_context(patch(GUARD_CHECK, return_value=SchemaState.PENDING))
        hosted = stack.enter_context(patch("agentcc.readiness._check_dependencies"))
        response = Client().get("/readyz/")

    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert json.loads(response.content)["code"] == "initializing"
    hosted.assert_not_called()
