"""
One gateway log batch carries the requests of every org the gateway served.
Each entry must be stored, alerted on and metered under its own org.
"""

from unittest.mock import patch

import pytest

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccRequestLog
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent
from agentcc.services.log_ingestion import ingest_request_logs


def _entry(request_id, key_id=None, **fields):
    entry = {
        "request_id": request_id,
        "model": "gpt-4o-mini",
        "provider": "openai",
        "status_code": 200,
        **fields,
    }
    if key_id:
        entry["auth_key_id"] = key_id
    return entry


def _stored_orgs():
    return dict(
        AgentccRequestLog.no_workspace_objects.values_list(
            "request_id", "organization_id"
        )
    )


@pytest.fixture
def other_org(db):
    return Organization.objects.create(name="Other Organization")


@pytest.fixture
def org_keys(organization, other_org):
    AgentccAPIKey.objects.create(
        gateway_key_id="key-owner", name="owner", organization=organization
    )
    AgentccAPIKey.objects.create(
        gateway_key_id="key-other", name="other", organization=other_org
    )


@pytest.mark.integration
@pytest.mark.usefixtures("org_keys")
class TestMixedOrgBatch:
    def test_each_entry_is_stored_under_its_own_org(self, organization, other_org):
        count = ingest_request_logs(
            [
                _entry("owner-1", "key-owner"),
                _entry("other-1", "key-other"),
                _entry("owner-2", "key-owner"),
            ]
        )

        assert count == 3
        assert _stored_orgs() == {
            "owner-1": organization.id,
            "other-1": other_org.id,
            "owner-2": organization.id,
        }

    @patch("agentcc.services.webhook_delivery.deliver_webhook_events")
    def test_webhook_events_go_only_to_the_entrys_org(
        self, mock_deliver_webhook_events, organization, other_org
    ):
        webhooks = {
            org.id: AgentccWebhook.objects.create(
                organization=org,
                name="req-complete",
                url="https://example.com/webhook",
                events=["request.completed"],
                is_active=True,
            )
            for org in (organization, other_org)
        }

        ingest_request_logs(
            [_entry("owner-1", "key-owner"), _entry("other-1", "key-other")]
        )

        for org, request_id in ((organization, "owner-1"), (other_org, "other-1")):
            events = AgentccWebhookEvent.no_workspace_objects.filter(
                webhook=webhooks[org.id]
            )
            assert [e.payload["request_id"] for e in events] == [request_id]
            assert {e.organization_id for e in events} == {org.id}

    @patch("ee.usage.services.emitter.emit")
    @patch("ee.usage.deployment.DeploymentMode.is_oss", return_value=False)
    def test_usage_is_metered_per_org(
        self, _mock_is_oss, mock_emit, organization, other_org
    ):
        ingest_request_logs(
            [
                _entry("owner-1", "key-owner"),
                _entry("owner-2", "key-owner"),
                _entry("other-1", "key-other"),
            ]
        )

        metered = {
            event.org_id: event.amount
            for event in (call.args[0] for call in mock_emit.call_args_list)
        }
        assert metered == {str(organization.id): 2, str(other_org.id): 1}

    def test_an_entry_with_no_known_org_is_skipped(self, organization):
        count = ingest_request_logs(
            [_entry("unknown-1", "key-unknown"), _entry("owner-1", "key-owner")]
        )

        assert count == 1
        assert _stored_orgs() == {"owner-1": organization.id}

    def test_metadata_org_is_used_per_entry_when_the_key_is_unknown(
        self, organization, other_org
    ):
        count = ingest_request_logs(
            [
                _entry("other-1", metadata={"org_id": str(other_org.id)}),
                _entry("owner-1", "key-owner"),
            ]
        )

        assert count == 2
        assert _stored_orgs() == {
            "other-1": other_org.id,
            "owner-1": organization.id,
        }

    def test_a_malformed_metadata_org_does_not_drop_the_batch(self, organization):
        count = ingest_request_logs(
            [
                _entry("bad-1", metadata={"org_id": "not-an-org"}),
                _entry("owner-1", "key-owner"),
            ]
        )

        assert count == 1
        assert _stored_orgs() == {"owner-1": organization.id}

    def test_a_single_org_batch_is_unchanged(self, organization):
        count = ingest_request_logs(
            [_entry("owner-1", "key-owner"), _entry("owner-2", "key-owner")]
        )

        assert count == 2
        assert _stored_orgs() == {
            "owner-1": organization.id,
            "owner-2": organization.id,
        }
