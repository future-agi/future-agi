"""Launch pin: no implicit keys, sandbox attribution, or export authority."""

import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def no_export_network(monkeypatch):
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("export OFF test attempted network")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    yield attempts
    assert attempts == []


@pytest.mark.django_db
@pytest.mark.parametrize("grant_switch", [False, True])
def test_sandbox_off_never_mints_or_injects_keys(
    settings, monkeypatch, organization, workspace, no_export_network, grant_switch
):
    from accounts.models import OrgApiKey
    from accounts.models.service_grant import ServiceGrant
    from simulate.services import hosted_harness_gateway as harness
    from tracer.services import collector_ingest as ingest

    settings.COLLECTOR_SERVICE_GRANTS_ENABLED = grant_switch
    keys_before = OrgApiKey._base_manager.count()
    grants_before = ServiceGrant.objects.count()
    save_key = Mock(side_effect=AssertionError("automatic API key mint"))
    issue_run = Mock(side_effect=AssertionError("unattributed run grant mint"))
    decide = Mock(side_effect=AssertionError("dormant sandbox planner reached"))
    monkeypatch.setattr(OrgApiKey, "save", save_key)
    monkeypatch.setattr(ingest, "issue_sandbox_export_credentials", issue_run)
    monkeypatch.setattr(harness, "_decide_observability_export", decide)
    assert ingest.SANDBOX_EXPORT_STATE == "OFF"
    credentials = ingest.get_or_create_export_credentials(
        organization_id=str(organization.pk),
        workspace_id=str(workspace.pk),
        agent_definition_id="33333333-3333-4333-8333-333333333333",
    )
    assert (credentials.enabled, credentials.api_key, credentials.secret_key) == (
        False,
        "",
        "",
    )
    plan = harness._plan_observability_export(
        SimpleNamespace(id="synthetic-job"),
        {"observability": {"enabled": True}},
        selected_snapshot="synthetic-snapshot",
        allowed_domains=(),
        max_egress_domains=0,
    )
    assert plan.state == "OFF"
    assert plan.reason == ingest.DISABLED_REASON == "run_token_unavailable"
    assert plan.secrets == ()
    assert plan.collector_url is None
    assert plan.project_name is None
    assert plan.export is None
    assert plan.instrumentation is None
    assert plan.egress_domains is None
    assert plan.redaction_values == ()
    save_key.assert_not_called()
    issue_run.assert_not_called()
    decide.assert_not_called()
    assert OrgApiKey._base_manager.count() == keys_before
    assert ServiceGrant.objects.count() == grants_before


@pytest.mark.django_db
@pytest.mark.parametrize("grant_switch", [False, True])
def test_platform_emits_nothing_without_export_credentials(
    settings, monkeypatch, organization, workspace, no_export_network, grant_switch
):
    from accounts.models import OrgApiKey
    from accounts.models.service_grant import ServiceGrant
    from simulate.services import sim_collector_emit
    from tfc.service_envelope import EnvelopeRejected
    from tracer.services import collector_ingest as ingest

    settings.COLLECTOR_SERVICE_GRANTS_ENABLED = grant_switch
    keys_before = OrgApiKey._base_manager.count()
    grants_before = ServiceGrant.objects.count()
    save_key = Mock(side_effect=AssertionError("automatic API key mint"))
    issue = Mock(side_effect=EnvelopeRejected())
    export = Mock(side_effect=AssertionError("export without per-export authority"))
    monkeypatch.setattr(OrgApiKey, "save", save_key)
    monkeypatch.setattr(ingest, "issue_platform_grant", issue)
    monkeypatch.setattr(sim_collector_emit, "export_sim_spans", export)
    assert ingest.PLATFORM_EMISSION_STATE == "OFF"
    assert (
        ingest.emit_spans_to_collector(
            [{"name": "synthetic-no-authority"}],
            project_name="no-authority",
            project_type="observe",
            organization_id=str(organization.pk),
            workspace_id=str(workspace.pk),
        )
        == 0
    )
    if grant_switch:
        issue.assert_called_once()
    else:
        issue.assert_not_called()
        assert (
            ingest.resolve_system_emit_credentials(
                str(organization.pk), str(workspace.pk)
            )
            is None
        )
    export.assert_not_called()
    save_key.assert_not_called()
    assert OrgApiKey._base_manager.count() == keys_before
    assert ServiceGrant.objects.count() == grants_before
