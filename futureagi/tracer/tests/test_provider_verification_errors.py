from unittest.mock import patch

import pytest
from structlog.testing import capture_logs


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("path", "message"),
    [
        (
            "/tracer/observability-provider/verify_api_key/",
            "Could not verify the API key. Check the key and try again.",
        ),
        (
            "/tracer/observability-provider/verify_assistant_id/",
            "Could not verify the assistant ID. Check the ID and API key and try again.",
        ),
        (
            "/simulate/api/agent-definition-operations/fetch_assistant_from_provider/",
            "Please recheck your API key and assistant ID",
        ),
    ],
)
def test_provider_exception_returns_actionable_error(auth_client, path, message):
    payload = {"provider": "bland", "api_key": "test-key"}
    if not path.endswith("/verify_api_key/"):
        payload["assistant_id"] = "test-pathway"
    marker = "test-provider-exception-detail"
    with (
        patch("requests.get", side_effect=RuntimeError(marker)) as upstream,
        capture_logs() as events,
    ):
        response = auth_client.post(path, payload, format="json")

    upstream.assert_called_once()
    assert response.status_code == 400
    assert response.json()["message"] == message
    assert marker not in response.content.decode()
    assert marker not in repr(events)
    failures = [event for event in events if event.get("error_type") == "RuntimeError"]
    assert len(failures) == 1
    assert not failures[0].get("exc_info")
