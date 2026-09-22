from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory

from simulate.services.hosted_sandbox.ingress_relay import (
    mint_ingress_url,
    relay_ingress,
)


def test_e2b_relay_uses_signed_sandbox_port_and_server_side_traffic_token(settings):
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example.com"
    url = mint_ingress_url(
        sandbox_id="sandbox-1", port=8080, expires_in_seconds=300
    )
    token = url.split("/harness-ingress/", 1)[1].strip("/")
    private = SimpleNamespace(
        traffic_access_token="secret-traffic-token",
        get_host=lambda port: f"{port}-sandbox-1.e2b.app",
    )
    provider = SimpleNamespace(
        name="e2b", get=lambda sandbox_id: SimpleNamespace(_sandbox=private)
    )
    class Upstream:
        status_code = 200
        headers = {"content-type": "application/json"}

        def iter_content(self, chunk_size):
            return iter([b'{"ok":true}'])

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    request = RequestFactory().post(
        "/simulate/api/harness-ingress/ignored/tool?x=1",
        data=b'{"name":"test"}',
        content_type="application/json",
        HTTP_HOST="attacker.example.com",
        HTTP_AUTHORIZATION="Bearer must-not-forward",
    )
    with (
        patch(
            "simulate.services.hosted_sandbox.get_sandbox_provider",
            return_value=provider,
        ),
        patch(
            "simulate.services.hosted_sandbox.ingress_relay.requests.request",
            return_value=Upstream(),
        ) as send,
    ):
        response = relay_ingress(request, token, "tool")
    assert response.status_code == 200
    assert response.content == b'{"ok":true}'
    args, kwargs = send.call_args
    assert args == ("POST", "https://8080-sandbox-1.e2b.app/tool?x=1")
    assert kwargs["headers"]["e2b-traffic-access-token"] == "secret-traffic-token"
    assert "authorization" not in kwargs["headers"]
    assert "host" not in kwargs["headers"]


def test_e2b_relay_rejects_invalid_or_expired_capability(settings, monkeypatch):
    settings.HARNESS_PUBLIC_BASE_URL = "https://platform.example.com"
    request = RequestFactory().get("/simulate/api/harness-ingress/bad/")
    assert relay_ingress(request, "bad").status_code == 403
    monkeypatch.setattr(
        "simulate.services.hosted_sandbox.ingress_relay.time.time", lambda: 1000
    )
    url = mint_ingress_url(sandbox_id="sandbox-1", port=8080, expires_in_seconds=1)
    token = url.split("/harness-ingress/", 1)[1].strip("/")
    monkeypatch.setattr(
        "simulate.services.hosted_sandbox.ingress_relay.time.time", lambda: 1002
    )
    assert relay_ingress(request, token).status_code == 410
