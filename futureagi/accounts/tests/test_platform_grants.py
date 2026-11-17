"""Platform OTLP export uses an unregistered, short-lived service grant."""

from unittest.mock import patch

import pytest

from accounts.models import OrgApiKey
from accounts.services.envelope_keys import pair_keys
from tfc.service_envelope import verify_grant
from tracer.services.collector_ingest import (
    emit_spans_to_collector,
    get_or_create_export_credentials,
)

pytestmark = pytest.mark.django_db


def test_platform_export_sends_grant_and_mints_no_api_key(user, workspace, settings):
    settings.COLLECTOR_SERVICE_GRANTS_ENABLED = True
    with patch(
        "simulate.services.sim_collector_emit.export_sim_spans", return_value=1
    ) as export:
        assert (
            emit_spans_to_collector(
                [{"name": "test-only"}],
                project_name="test",
                project_type="traces",
                organization_id=str(user.organization_id),
                workspace_id=str(workspace.pk),
            )
            == 1
        )
    export.assert_called_once()
    assert "grant" not in export.call_args.kwargs
    claims = verify_grant(
        export.call_args.kwargs["secret_key"],
        keys=pair_keys("fa", "collector-emit"),
        audience="collector-emit",
        public_id=export.call_args.kwargs["api_key"],
    )
    assert export.call_args.kwargs["api_key"] == "fit_" + claims["id"]
    assert (
        claims["kind"] == "platform-emit"
        and claims["via"] == "svc"
        and claims["user"] == "-"
    )
    assert int(claims["exp"]) - int(claims["iat"]) == 60
    assert OrgApiKey._base_manager.count() == 0


def test_default_off_has_no_export_or_scope_inference(user, workspace, settings):
    settings.COLLECTOR_SERVICE_GRANTS_ENABLED = False
    with patch("simulate.services.sim_collector_emit.export_sim_spans") as export:
        assert (
            emit_spans_to_collector(
                [{}],
                project_name="test",
                project_type="traces",
                organization_id=str(user.organization_id),
                workspace_id=str(workspace.pk),
            )
            == 0
        )
    export.assert_not_called()
    credential = get_or_create_export_credentials(
        organization_id=str(user.organization_id),
        workspace_id=str(workspace.pk),
        agent_definition_id=None,
    )
    assert credential.enabled is False
    assert not credential.api_key and not credential.secret_key
    assert OrgApiKey._base_manager.count() == 0
