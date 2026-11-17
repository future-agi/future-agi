"""Legacy diagnostics must not require decoding an excluded Microsoft secret."""
import json
from unittest.mock import Mock

import pytest
from structlog.testing import capture_logs

from agentcc.tests import test_microsoft_connect_gate as gate

pytestmark = pytest.mark.django_db
rig = gate.rig


@pytest.mark.parametrize("document,reason", [
    ('{"microsoft": {"client_id": "fixture-platform-client-not-real", '
     '"client_secret": "fixture-client-secret-not-real"}, "google": []}',
     "must hold exactly client_id and client_secret"),
    ("[]", "must hold one JSON object"),
    ("{", "not UTF-8 JSON"),
])
def test_non_microsoft_metadata_error_is_value_free_before_custody(
    rig, monkeypatch, tmp_path, document, reason,
):
    org, _, _, _ = rig
    path = tmp_path / "malformed-fixture.json"
    path.write_text(document)
    monkeypatch.setenv(gate.clients.CLIENTS_FILE_ENV, str(path))
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", raising=False)
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", raising=False)
    seal = Mock(side_effect=AssertionError("metadata error decoded a secret"))
    monkeypatch.setattr(gate.clients._Sealed, "__init__", seal)
    with capture_logs() as logs, pytest.raises(gate.clients.PlatformClientMisconfigured) as caught:
        gate.clients.resolve(org, "gmail", "google", token_url=gate.ISSUER + "/token",
                             revocation_url=None)
    assert reason in str(caught.value)
    assert gate.clients.CLIENTS_FILE_ENV in str(caught.value)
    assert str(path) not in str(caught.value) and gate.SECRET not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert [e["path"] for e in logs if e["event"] == "connector_oauth_client_misconfigured"] == [str(path)]
    seal.assert_not_called()


def test_valid_mixed_file_loads_only_nonexcluded_secret(rig, monkeypatch, tmp_path):
    path = tmp_path / "mixed-fixture.json"
    path.write_text(json.dumps({
        "microsoft": {"client_id": gate.PLATFORM_ID, "client_secret": gate.SECRET},
        "google": {"client_id": "fixture-google-id", "client_secret": "fixture-google-secret"},
    }))
    monkeypatch.setenv(gate.clients.CLIENTS_FILE_ENV, str(path))
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_ID", raising=False)
    monkeypatch.delenv("AGENTCC_CONNECTOR_CLIENT_GOOGLE_SECRET", raising=False)
    original = gate.clients._Sealed.__init__
    sealed_values = []

    def seal(self, value):
        sealed_values.append(value)
        return original(self, value)

    monkeypatch.setattr(gate.clients._Sealed, "__init__", seal)
    ref = gate.clients._platform_client("google")
    assert ref.client_id == "fixture-google-id"
    assert sealed_values == ["fixture-google-secret"]
    assert gate.clients._platform_client("microsoft") is None
