"""POST /agentcc/console-run/authorizations/: a run authorization for one
Console chat (fi-security 1d8b0ce0; owner: the switch is on by default and
inert until an operator registers an agent service key).

A person's own browser session only; models from their Console list; the one
registered deployment named in the token; refused, never minted, when no key is
registered or the switch is off. Every key here is generated for the test.
"""

import base64
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.test import override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from structlog.testing import capture_logs

from accounts.models.service_grant import ServiceGrant
from agentcc.services import console_run_auth
from agentcc.services.console_models import ConsoleModelsUnavailable
from agentcc.services.console_run_signer_keys import add_key
from agentcc.services.delegated_availability import (
    CONSOLE_RUN_SWITCH,
    console_run_enabled,
)

pytestmark = pytest.mark.django_db

LISTED = {
    "models": [{"model": "gpt-4o-mini"}, {"model": "anthropic/claude-sonnet-5.5"}]
}


def register_key(deployment="ee-prod-1", kid="ee-k1"):
    raw = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    add_key(
        deployment=deployment,
        kid=kid,
        public_key=base64.urlsafe_b64encode(raw).rstrip(b"=").decode(),
        operator="ops@example.test",
    )


def call(user, workspace, models, *, session=True, list_error=None):
    request = APIRequestFactory().post(
        "/agentcc/console-run/authorizations/", {"models": models}, format="json"
    )
    force_authenticate(request, user=user)
    request.organization = user.organization
    request.workspace = workspace
    if session:
        request.credential_provenance = "interactive_session"
    answer = {"side_effect": list_error} if list_error else {"return_value": LISTED}
    with patch.object(console_run_auth, "list_console_models", **answer) as listing:
        from agentcc.views.console_run import ConsoleRunAuthorizationsView

        response = ConsoleRunAuthorizationsView.as_view()(request)
    response.listing = listing
    return response


def test_the_switch_is_on_unless_set_off(monkeypatch):
    monkeypatch.delenv(CONSOLE_RUN_SWITCH, raising=False)
    assert console_run_enabled() is True
    for value, expected in (("false", False), ("0", False), ("FALSE", False)):
        monkeypatch.setenv(CONSOLE_RUN_SWITCH, value)
        assert console_run_enabled() is expected
    for value in ("true", "1", "yes"):
        monkeypatch.setenv(CONSOLE_RUN_SWITCH, value)
        assert console_run_enabled() is True
    with override_settings(AGENTCC_DELEGATED_CONSOLE_RUN_ENABLED=False):
        assert console_run_enabled() is False


def test_a_session_gets_a_run_authorization(user, workspace, monkeypatch):
    monkeypatch.delenv(CONSOLE_RUN_SWITCH, raising=False)
    register_key()
    with capture_logs() as records:
        response = call(user, workspace, ["gpt-4o-mini"])
    assert response.status_code == 200, response.data
    assert response["Cache-Control"] == "no-store"
    result = response.data["result"]
    assert result["run_authorization"].startswith("fe1r.")
    row = ServiceGrant.objects.get(kind="console-run-auth")
    assert (row.model_ids, row.ee, row.sub) == (
        "gpt-4o-mini",
        "ee-prod-1",
        "chat:" + result["run_id"],
    )
    assert row.exp - row.iat == 8 * 3600
    assert result["exp"] == row.exp
    assert any(r.get("event") == "console_run_authorized" for r in records)
    assert result["run_authorization"] not in repr(records)


def test_no_registered_key_is_refused_and_nothing_is_minted(user, workspace):
    response = call(user, workspace, ["gpt-4o-mini"])
    assert response.status_code == 409
    assert response.data["code"] == "console_run_not_configured"
    assert not ServiceGrant.objects.filter(kind="console-run-auth").exists()
    response.listing.assert_not_called()


def test_the_switch_off_refuses(user, workspace, monkeypatch):
    register_key()
    monkeypatch.setenv(CONSOLE_RUN_SWITCH, "false")
    response = call(user, workspace, ["gpt-4o-mini"])
    assert response.status_code == 403
    assert response.data["code"] == "console_run_disabled"
    assert not ServiceGrant.objects.filter(kind="console-run-auth").exists()


def test_only_a_browser_session_can_authorize(user, workspace):
    register_key()
    response = call(user, workspace, ["gpt-4o-mini"], session=False)
    assert response.status_code == 403
    assert response.data["code"] == "console_run_session_required"


def test_a_model_outside_the_list_is_refused(user, workspace):
    register_key()
    response = call(user, workspace, ["gpt-4o-mini", "o3"])
    assert response.status_code == 403
    assert response.data["code"] == "model_not_in_list"
    assert not ServiceGrant.objects.filter(kind="console-run-auth").exists()


@pytest.mark.parametrize(
    "models",
    [[], ["gpt-4o-mini", "gpt-4o-mini"], [1], [f"m{i}" for i in range(9)], "gpt"],
)
def test_invalid_model_lists_are_refused(user, workspace, models):
    register_key()
    response = call(user, workspace, models)
    assert response.status_code == 400
    assert response.data["code"] == "invalid_models"


def test_an_unreadable_list_is_a_typed_503(user, workspace):
    register_key()
    response = call(
        user, workspace, ["gpt-4o-mini"], list_error=ConsoleModelsUnavailable(503)
    )
    assert response.status_code == 503
    assert response.data["code"] == "console_models_unavailable"


def test_a_workspace_is_required(user):
    register_key()
    response = call(user, None, ["gpt-4o-mini"])
    assert response.status_code == 400
    assert response.data["code"] == "workspace_required"
