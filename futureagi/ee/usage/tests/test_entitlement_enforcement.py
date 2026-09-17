"""Phase 3.3: Entitlement enforcement tests for email alerts and automation rules.

Tests that resource creation is gated by plan limits via Entitlements.can_create().
"""

from unittest.mock import patch

import pytest
from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from agentcc.models.email_alert import AgentccEmailAlert
from conftest import WorkspaceAwareAPIClient
from django.utils import timezone
from ee.usage.schemas.events import CheckResult
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotatorRole,
    AutomationRule,
)
from rest_framework import status


@pytest.fixture
def organization(db):
    return Organization.objects.create(name="Test Org")


@pytest.fixture
def user(db, organization):
    return User.objects.create_user(
        email="test@futureagi.com",
        password="test123",
        name="Test User",
        organization=organization,
    )


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name="Default Workspace",
        organization=organization,
        is_default=True,
        created_by=user,
    )


@pytest.fixture
def auth_client(user, workspace):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    yield client
    client.stop_workspace_injection()


def _email_alert_payload():
    return {
        "name": "Test Alert",
        "recipients": ["test@example.com"],
        "events": ["error.occurred"],
    }


def _make_queue_manager(queue, user):
    # AnnotationQueue.save auto-creates a MANAGER annotator for created_by,
    # so use get_or_create to stay idempotent when queue.created_by == user.
    annotator, _ = AnnotationQueueAnnotator.objects.get_or_create(
        queue=queue,
        user=user,
        defaults={
            "role": AnnotatorRole.MANAGER.value,
            "roles": [
                AnnotatorRole.MANAGER.value,
                AnnotatorRole.ANNOTATOR.value,
                AnnotatorRole.REVIEWER.value,
            ],
        },
    )
    return annotator


class TestEmailAlertEnforcement:
    def test_create_allowed_under_limit(self, db, auth_client, organization):
        with patch("ee.usage.services.entitlements.Entitlements.can_create") as mock:
            mock.return_value = CheckResult(allowed=True)
            resp = auth_client.post(
                "/agentcc/email-alerts/",
                _email_alert_payload(),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK

    def test_create_blocked_at_limit(self, db, auth_client, organization):
        # Count limits are cloud-only (check_ee_can_create no-ops off-cloud),
        # so simulate cloud mode to exercise the enforcement path.
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch("ee.usage.services.entitlements.Entitlements.can_create") as mock,
        ):
            mock.return_value = CheckResult(
                allowed=False,
                reason="You've reached the 3 alerts limit",
                error_code="ENTITLEMENT_LIMIT",
            )
            resp = auth_client.post(
                "/agentcc/email-alerts/",
                _email_alert_payload(),
                format="json",
            )
            assert resp.status_code == status.HTTP_402_PAYMENT_REQUIRED
            assert "3 alerts limit" in str(resp.data)

    def test_create_works_without_entitlements_module(
        self, db, auth_client, organization
    ):
        with patch(
            "ee.usage.services.entitlements.Entitlements",
            side_effect=ImportError,
        ):
            resp = auth_client.post(
                "/agentcc/email-alerts/",
                _email_alert_payload(),
                format="json",
            )
            assert resp.status_code == status.HTTP_200_OK


class TestAutomationRuleEnforcement:
    def test_create_allowed_under_limit(self, db, auth_client, organization, user):
        queue = AnnotationQueue.objects.create(
            name="Test Queue", organization=organization, created_by=user
        )
        _make_queue_manager(queue, user)
        with patch("ee.usage.services.entitlements.Entitlements.can_create") as mock:
            mock.return_value = CheckResult(allowed=True)
            resp = auth_client.post(
                f"/model-hub/annotation-queues/{queue.id}/automation-rules/",
                {
                    "name": "Test Rule",
                    "source_type": "trace",
                    "conditions": {},
                    "enabled": True,
                },
                format="json",
            )
            assert resp.status_code == status.HTTP_201_CREATED

    def test_create_blocked_at_limit(self, db, auth_client, organization, user):
        queue = AnnotationQueue.objects.create(
            name="Test Queue", organization=organization, created_by=user
        )
        _make_queue_manager(queue, user)
        with patch("ee.usage.services.entitlements.Entitlements.can_create") as mock:
            mock.return_value = CheckResult(
                allowed=False,
                reason="You've reached the 1 automation_rules limit",
                error_code="ENTITLEMENT_LIMIT",
            )
            resp = auth_client.post(
                f"/model-hub/annotation-queues/{queue.id}/automation-rules/",
                {
                    "name": "Test Rule",
                    "source_type": "trace",
                    "conditions": {},
                    "enabled": True,
                },
                format="json",
            )
            assert resp.status_code == status.HTTP_403_FORBIDDEN
            assert "automation_rules limit" in str(resp.data)

    def test_create_works_without_entitlements_module(
        self, db, auth_client, organization, user
    ):
        queue = AnnotationQueue.objects.create(
            name="Test Queue", organization=organization, created_by=user
        )
        _make_queue_manager(queue, user)
        with patch(
            "ee.usage.services.entitlements.Entitlements",
            side_effect=ImportError,
        ):
            resp = auth_client.post(
                f"/model-hub/annotation-queues/{queue.id}/automation-rules/",
                {
                    "name": "Test Rule",
                    "source_type": "trace",
                    "conditions": {},
                    "enabled": True,
                },
                format="json",
            )
            assert resp.status_code == status.HTTP_201_CREATED
