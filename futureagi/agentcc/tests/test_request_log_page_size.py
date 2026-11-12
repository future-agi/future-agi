"""Gateway Logs reads keep the shared paginator's typed page-size refusal.

Every request-log handler that pages its rows wraps the read in a broad
``except``; the paginator's ``PageSizeTooLarge`` must pass through it as the
typed 400 (code ``page_size_too_large``) instead of a generic bad request.
"""

import pytest
from django.utils import timezone
from rest_framework import status

from agentcc.models import AgentccRequestLog
from tfc.utils.pagination import MAX_PAGE_SIZE

SESSION_ID = "sess-page-size"

PAGED_ROUTES = [
    pytest.param("/agentcc/request-logs/", {}, id="list"),
    pytest.param("/agentcc/request-logs/search/", {"q": "gpt"}, id="search"),
    pytest.param("/agentcc/request-logs/sessions/", {}, id="sessions"),
    pytest.param(
        f"/agentcc/request-logs/sessions/{SESSION_ID}/", {}, id="session-detail"
    ),
]


@pytest.fixture
def session_log(organization, workspace):
    # session_detail answers 404 before paging when the session has no rows.
    return AgentccRequestLog.objects.create(
        organization=organization,
        workspace=workspace,
        request_id="req-page-size-1",
        model="gpt-4o",
        provider="openai",
        status_code=200,
        latency_ms=120,
        is_error=False,
        session_id=SESSION_ID,
        started_at=timezone.now(),
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url,params", PAGED_ROUTES)
def test_request_log_route_keeps_page_size_refusal(
    auth_client, session_log, url, params
):
    response = auth_client.get(url, {**params, "limit": MAX_PAGE_SIZE + 1})

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
    assert response.data["code"] == "page_size_too_large"
    assert response.data["type"] == "validation_error"
    assert response.data["message"] == (
        f"The maximum page size is {MAX_PAGE_SIZE}. "
        "Fetch more pages to retrieve additional results."
    )


@pytest.mark.django_db
@pytest.mark.parametrize("url,params", PAGED_ROUTES)
def test_request_log_route_accepts_the_cap(auth_client, session_log, url, params):
    response = auth_client.get(url, {**params, "limit": MAX_PAGE_SIZE})

    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["count"] == 1
    assert response.data["current_page"] == 1
