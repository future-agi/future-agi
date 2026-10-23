"""manage.py agentcc_check_log_org reports request-log rows stored under an org
other than their API key's, and changes nothing."""

from io import StringIO

import pytest
from django.core.management import call_command

from accounts.models import Organization
from agentcc.models import AgentccAPIKey, AgentccRequestLog, AgentccSession
from agentcc.models.webhook import AgentccWebhook, AgentccWebhookEvent


@pytest.fixture
def seeded(organization):
    org_a = organization
    org_b = Organization.objects.create(name="Check Log Org B")
    AgentccAPIKey.objects.create(gateway_key_id="gk-a", name="a", organization=org_a)
    AgentccAPIKey.objects.create(gateway_key_id="gk-b", name="b", organization=org_b)
    rows = [
        ("r-a-ok", org_a, "gk-a", ""),
        # Rows stored under an org other than their key's.
        ("r-b-in-a", org_a, "gk-b", "session-b"),
        ("r-b-in-a-cached", org_a, "gk-b", ""),
        ("r-unknown-key", org_a, "gk-gone", ""),
        ("r-no-key", org_a, "", ""),
        ("r-b-ok", org_b, "gk-b", ""),
    ]
    for request_id, org, key_id, session_id in rows:
        AgentccRequestLog.no_workspace_objects.create(
            organization=org,
            request_id=request_id,
            api_key_id=key_id,
            session_id=session_id,
            cache_hit=request_id.endswith("cached"),
        )
    AgentccSession.no_workspace_objects.create(
        organization=org_a, session_id="session-b", name="session-b"
    )
    hook = AgentccWebhook.objects.create(
        organization=org_a,
        name="org-a-hook",
        url="https://example.com/hook",
        events=["request.completed"],
    )
    AgentccWebhookEvent.no_workspace_objects.create(
        organization=org_a,
        webhook=hook,
        event_type="request.completed",
        payload={"request_id": "r-b-in-a"},
    )
    return org_a, org_b


def run(*args):
    out = StringIO()
    call_command("agentcc_check_log_org", *args, stdout=out)
    return out.getvalue()


def snapshot():
    return sorted(
        AgentccRequestLog._base_manager.values_list(
            "request_id", "organization_id", "api_key_id"
        )
    )


@pytest.mark.integration
class TestCheckLogOrgCommand:
    def test_reports_rows_stored_under_another_org_and_changes_nothing(self, seeded):
        org_a, org_b = seeded
        before = snapshot()

        output = run()

        assert snapshot() == before
        assert (
            f"stored_org={org_a.id} key_org={org_b.id} rows=2 cache_hits=1 "
            "webhook_events=1 sessions=1" in output
        )
        assert "r-b-in-a" in output and "r-b-in-a-cached" in output
        assert "r-a-ok" not in output and "r-b-ok" not in output
        assert "Rows to review: 2" in output
        assert "Rows with no API key Django can place: 2 (no_key=1, unknown_key=1)" in (
            output
        )
        assert (
            "webhook_events and sessions count matches by request and session" in output
        )
        assert "Nothing was changed" in output

    def test_org_limits_the_report_to_that_org(self, seeded):
        org_a, org_b = seeded
        other = Organization.objects.create(name="Check Log Org C")

        assert f"stored_org={org_a.id}" in run("--org", str(org_b.id))
        output = run("--org", str(other.id))
        assert "Rows to review: 0" in output
        assert "Rows with no API key Django can place: 0" in output

    def test_a_clean_table_reports_nothing(self, organization):
        AgentccAPIKey.objects.create(
            gateway_key_id="gk-clean", name="clean", organization=organization
        )
        AgentccRequestLog.no_workspace_objects.create(
            organization=organization, request_id="r-clean", api_key_id="gk-clean"
        )

        output = run()

        assert "Rows to review: 0" in output
        assert "stored_org=" not in output
