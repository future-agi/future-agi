import pytest

from simulate.services import alk_source_bridge
from simulate.services.harness_sandbox import HarnessSandboxClient
from simulate.services.hosted_harness import HostedHarnessError


def test_configured_local_sandbox_executes_the_source_contract(settings, monkeypatch):
    settings.ALK_HARNESS_SANDBOX_URL = "http://alk-sandbox:8788"
    settings.ALK_HARNESS_SANDBOX_TOKEN = "controller-token"
    payload = {"version": "environment-sources.request.v1", "operation": "prepare"}
    seen = {}

    def execute(client, request):
        seen.update(base_url=client.base_url, token=client.token, request=request)
        return {
            "version": "environment-sources.response.v1",
            "ok": True,
            "operation": "prepare",
            "result": {"draft": {}},
        }

    monkeypatch.setattr(HarnessSandboxClient, "environment_sources", execute)
    monkeypatch.setattr(
        alk_source_bridge,
        "get_sandbox_provider",
        lambda: (_ for _ in ()).throw(AssertionError("hosted provider was used")),
    )

    response = alk_source_bridge.execute_source_request(payload)

    assert response["ok"] is True
    assert seen == {
        "base_url": "http://alk-sandbox:8788",
        "token": "controller-token",
        "request": payload,
    }


@pytest.mark.parametrize(
    "response",
    [
        {
            "version": "environment-sources.response.v0",
            "ok": True,
            "operation": "prepare",
            "result": {},
        },
        {
            "version": "environment-sources.response.v1",
            "ok": True,
            "operation": "accept",
            "result": {},
        },
    ],
)
def test_source_contract_rejects_the_wrong_response_envelope(
    settings, monkeypatch, response
):
    settings.ALK_HARNESS_SANDBOX_URL = "http://alk-sandbox:8788"
    monkeypatch.setattr(
        HarnessSandboxClient,
        "environment_sources",
        lambda client, request: response,
    )

    with pytest.raises(HostedHarnessError) as caught:
        alk_source_bridge.execute_source_request(
            {"version": "environment-sources.request.v1", "operation": "prepare"}
        )

    assert caught.value.code == "source_processor_invalid"
