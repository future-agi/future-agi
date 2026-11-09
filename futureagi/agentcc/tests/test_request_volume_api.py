"""The Logs page's requests-over-time strip follows the table's filters.

The strip read the analytics usage series, which filters by time and API key
only, so with a model, status, provider or search filter set it counted
requests the table did not list, and said so in a note. GET
/agentcc/request-logs/volume/ takes the list's own filters (the same
``_apply_filters``) plus the window and bucket size, and answers OK and error
counts per bucket.
"""

from datetime import UTC, datetime, timedelta

import pytest
from rest_framework import status

from accounts.models import Organization
from agentcc.models.request_log import AgentccRequestLog

URL = "/agentcc/request-logs/volume/"
T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
WINDOW = {
    "start": (T0 - timedelta(hours=1)).isoformat(),
    "end": (T0 + timedelta(hours=2, minutes=59)).isoformat(),
    "granularity": "hour",
}


def _log(organization, request_id, at, **fields):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=organization,
        request_id=request_id,
        started_at=at,
        model=fields.pop("model", "openai/gpt-4o"),
        provider=fields.pop("provider", "openai"),
        status_code=fields.pop("status_code", 200),
        is_error=fields.pop("is_error", False),
        **fields,
    )


@pytest.fixture
def logs(user):
    org = user.organization
    _log(org, "req-a1", T0 + timedelta(minutes=5))
    _log(org, "req-a2", T0 + timedelta(minutes=50), status_code=502, is_error=True)
    _log(org, "req-b1", T0 + timedelta(hours=1, minutes=10), model="mistral/small")
    _log(org, "req-b2", T0 + timedelta(hours=2, minutes=1), model="mistral/small")
    _log(
        org,
        "req-c1",
        T0 + timedelta(hours=2, minutes=30),
        model="anthropic/haiku",
        provider="anthropic",
        status_code=429,
        is_error=True,
    )
    other = Organization.objects.create(name="Volume Other Org")
    _log(other, "req-other", T0 + timedelta(minutes=6))


def _series(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    result = response.json()["result"]
    assert result["granularity"] == "hour"
    return {
        datetime.fromisoformat(point["bucket"]): (
            point["request_count"],
            point["error_count"],
        )
        for point in result["series"]
    }


def _totals(series):
    return (
        sum(count for count, _ in series.values()),
        sum(errors for _, errors in series.values()),
    )


@pytest.mark.integration
@pytest.mark.api
class TestRequestVolume:
    def test_counts_ok_and_errors_per_bucket_for_the_org(self, auth_client, logs):
        series = _series(auth_client.get(URL, WINDOW))

        assert series[T0 - timedelta(hours=1)] == (0, 0)
        assert series[T0] == (2, 1)
        assert series[T0 + timedelta(hours=1)] == (1, 0)
        assert series[T0 + timedelta(hours=2)] == (2, 1)
        assert _totals(series) == (5, 2)  # the other org's request is not counted

    def test_follows_the_model_filter(self, auth_client, logs):
        series = _series(auth_client.get(URL, {**WINDOW, "model": "mistral/small"}))
        assert _totals(series) == (2, 0)

    def test_follows_status_provider_and_error_filters(self, auth_client, logs):
        assert (
            _totals(_series(auth_client.get(URL, {**WINDOW, "status_code": "429"})))[0]
            == 1
        )
        assert _totals(
            _series(auth_client.get(URL, {**WINDOW, "provider": "anthropic"}))
        ) == (1, 1)
        assert _totals(
            _series(auth_client.get(URL, {**WINDOW, "is_error": "true"}))
        ) == (2, 2)

    def test_follows_the_search(self, auth_client, logs):
        assert _totals(_series(auth_client.get(URL, {**WINDOW, "q": "req-b"}))) == (
            2,
            0,
        )

    def test_follows_the_tables_own_time_filter(self, auth_client, logs):
        series = _series(
            auth_client.get(
                URL,
                {
                    **WINDOW,
                    "started_after": (T0 + timedelta(hours=1)).isoformat(),
                },
            )
        )
        assert _totals(series) == (3, 1)

    def test_refuses_an_unknown_bucket_size(self, auth_client, logs):
        response = auth_client.get(URL, {**WINDOW, "granularity": "fortnight"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
