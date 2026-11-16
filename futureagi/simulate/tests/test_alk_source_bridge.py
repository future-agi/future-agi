from simulate.services import alk_source_bridge
from simulate.services.harness_sandbox import HarnessSandboxClient


def test_configured_local_sandbox_executes_the_source_contract(settings, monkeypatch):
    settings.ALK_HARNESS_SANDBOX_URL = "http://alk-sandbox:8788"
    settings.ALK_HARNESS_SANDBOX_TOKEN = "controller-token"
    payload = {"version": "environment-sources.request.v1", "operation": "prepare"}
    seen = {}

    def execute(client, request):
        seen.update(base_url=client.base_url, token=client.token, request=request)
        return {"ok": True, "result": {"draft": {}}}

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
