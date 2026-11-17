"""Public routes must preserve the default paginator's typed refusal.

These cloud rows also pin catch-all wrappers in neighboring lanes: raising a
400 in the paginator alone is insufficient if the caller turns it into a 500
or drops its stable code. No queryset should need evaluating for this refusal.
"""

import pytest


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url",
    [
        "/simulate/agent-definitions/",
        "/tracer/replay-session/",
        "/agentcc/request-logs/",
    ],
    ids=["agents", "replay", "gateway"],
)
def test_public_list_preserves_page_size_refusal(auth_client, url):
    response = auth_client.get(url, {"limit": 5001})
    assert response.status_code == 400, response.data
    assert response.data["code"] == "page_size_too_large"
    assert response.data["type"] == "validation_error"
    assert response.data["message"] == (
        "The maximum page size is 5000. "
        "Fetch more pages to retrieve additional results."
    )
