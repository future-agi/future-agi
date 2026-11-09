"""Console calls explain their availability before contacting the gateway."""

from unittest.mock import patch

import pytest


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize(
    "route,payload,issue",
    [
        ("test-playground", {"prompt": "hello", "model": "test/model"}, "KI-733-PG"),
        (
            "test-playground-stream",
            {"prompt": "hello", "model": "test/model"},
            "KI-733-PG",
        ),
        (
            "submit-batch",
            {
                "requests": [
                    {
                        "custom_id": "one",
                        "body": {
                            "model": "test/model",
                            "messages": [{"role": "user", "content": "hello"}],
                        },
                    }
                ]
            },
            "KI-733-BATCH",
        ),
    ],
)
def test_disabled_console_call_has_no_gateway_work(auth_client, route, payload, issue):
    with patch("agentcc.views.gateway.get_gateway_client") as gateway:
        response = auth_client.post(
            f"/agentcc/gateways/default/{route}/", payload, format="json"
        )
    assert response.status_code == 409
    assert response.json()["code"] == "feature_unavailable_733"
    assert response.json()["known_issue"] == issue
    assert "Unavailable in this release" in response.json()["message"]
    gateway.assert_not_called()
