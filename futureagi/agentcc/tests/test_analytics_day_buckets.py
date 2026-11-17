"""Day buckets are the viewer's days, and a chart window has a bucket limit.

The analytics series and the Logs volume strip cut day buckets at UTC
midnight, while the console labels them in the viewer's zone, so a viewer in
Los Angeles saw each daily bar under the day before. Every time series now
takes ``tz`` (an IANA zone name), cuts day buckets at the viewer's midnight,
and names the zone it used in ``time_zone`` (UTC when ``tz`` is missing or
unknown). Minute and hour buckets stay UTC: they are labelled by instant.

Each series also refuses a window of more than ``MAX_CHART_BUCKETS`` buckets
with a 400, instead of building one row per empty bucket in Python.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from rest_framework import status

from agentcc.models.request_log import AgentccRequestLog

# The documented limit (agentcc.services.analytics.MAX_CHART_BUCKETS).
MAX_CHART_BUCKETS = 2000

LA = ZoneInfo("America/Los_Angeles")
LA_NAME = "America/Los_Angeles"

# Oct 20 20:00 in Los Angeles (Oct 21 03:00 UTC) and Oct 21 08:00 there.
EVENING = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
MORNING = datetime(2026, 10, 21, 15, 0, tzinfo=UTC)
WINDOW = {
    "start": datetime(2026, 10, 19, 12, 0, tzinfo=UTC).isoformat(),
    "end": datetime(2026, 10, 22, 12, 0, tzinfo=UTC).isoformat(),
    "granularity": "day",
}

# (url, the result's series key, the bucket key, the count key)
SERIES = {
    "usage": (
        "/agentcc/analytics/usage-timeseries/",
        "series",
        "bucket",
        "request_count",
    ),
    "latency": (
        "/agentcc/analytics/latency-stats/",
        "timeseries",
        "bucket",
        "request_count",
    ),
    "errors": (
        "/agentcc/analytics/error-breakdown/",
        "error_timeseries",
        "bucket",
        "total_count",
    ),
    "guardrails": (
        "/agentcc/analytics/guardrail-trends/",
        "series",
        "timestamp",
        "trigger_count",
    ),
    "volume": (
        "/agentcc/request-logs/volume/",
        "series",
        "bucket",
        "request_count",
    ),
}


def _log(organization, request_id, at):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=organization,
        request_id=request_id,
        started_at=at,
        model="openai/gpt-4o",
        provider="openai",
        status_code=200,
        is_error=False,
        latency_ms=120,
        guardrail_triggered=True,
    )


@pytest.fixture
def two_requests(user):
    _log(user.organization, "req-evening", EVENING)
    _log(user.organization, "req-morning", MORNING)


def _result(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()["result"]


def _counts(result, name):
    """{bucket instant: count} for the buckets with requests."""
    _, series_key, bucket_key, count_key = SERIES[name]
    counts = {}
    for point in result[series_key]:
        if point[count_key]:
            at = datetime.fromisoformat(point[bucket_key].replace("Z", "+00:00"))
            counts[at.astimezone(UTC)] = point[count_key]
    return counts


def _la_midnight(day):
    return datetime(2026, 10, day, tzinfo=LA).astimezone(UTC)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("name", list(SERIES))
class TestDayBucketsInTheViewersZone:
    def test_cuts_days_at_the_viewers_midnight(self, auth_client, two_requests, name):
        url = SERIES[name][0]
        result = _result(auth_client.get(url, {**WINDOW, "tz": LA_NAME}))

        assert result["time_zone"] == LA_NAME
        # The evening request is Oct 20 in Los Angeles, the morning one Oct 21.
        assert _counts(result, name) == {_la_midnight(20): 1, _la_midnight(21): 1}

    def test_every_bucket_is_a_local_midnight(self, auth_client, two_requests, name):
        _, series_key, bucket_key, _ = SERIES[name]
        result = _result(auth_client.get(SERIES[name][0], {**WINDOW, "tz": LA_NAME}))
        for point in result[series_key]:
            at = datetime.fromisoformat(point[bucket_key].replace("Z", "+00:00"))
            local = at.astimezone(LA)
            assert (local.hour, local.minute) == (0, 0), point

    def test_without_a_zone_days_are_utc_and_say_so(
        self, auth_client, two_requests, name
    ):
        result = _result(auth_client.get(SERIES[name][0], WINDOW))

        assert result["time_zone"] == "UTC"
        assert _counts(result, name) == {datetime(2026, 10, 21, tzinfo=UTC): 2}

    def test_an_unknown_zone_falls_back_to_utc(self, auth_client, two_requests, name):
        result = _result(
            auth_client.get(SERIES[name][0], {**WINDOW, "tz": "Mars/Olympus_Mons"})
        )

        assert result["time_zone"] == "UTC"
        assert _counts(result, name) == {datetime(2026, 10, 21, tzinfo=UTC): 2}

    def test_hour_buckets_stay_utc_hours(self, auth_client, two_requests, name):
        result = _result(
            auth_client.get(
                SERIES[name][0], {**WINDOW, "granularity": "hour", "tz": LA_NAME}
            )
        )
        assert _counts(result, name) == {EVENING: 1, MORNING: 1}

    def test_refuses_a_window_over_the_bucket_limit(self, auth_client, name):
        response = auth_client.get(
            SERIES[name][0],
            {
                "start": "2000-01-01T00:00:00Z",
                "end": "2026-10-22T00:00:00Z",
                "granularity": "minute",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"{MAX_CHART_BUCKETS:,}" in str(response.json()["result"])

    def test_charts_a_window_at_the_limit(self, auth_client, name):
        end = datetime(2026, 10, 22, tzinfo=UTC)
        start = end - timedelta(minutes=MAX_CHART_BUCKETS - 1)
        response = auth_client.get(
            SERIES[name][0],
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "granularity": "minute",
            },
        )
        assert response.status_code == status.HTTP_200_OK, response.content


@pytest.mark.integration
@pytest.mark.api
def test_day_buckets_cross_a_dst_change_one_local_midnight_each(auth_client, user):
    # Los Angeles leaves daylight time on Nov 1 2026: that day is 25 hours.
    _log(user.organization, "req-dst", datetime(2026, 11, 2, 7, 30, tzinfo=UTC))
    result = _result(
        auth_client.get(
            "/agentcc/analytics/usage-timeseries/",
            {
                "start": datetime(2026, 10, 31, 12, tzinfo=UTC).isoformat(),
                "end": datetime(2026, 11, 2, 12, tzinfo=UTC).isoformat(),
                "granularity": "day",
                "tz": LA_NAME,
            },
        )
    )
    local_days = [
        datetime.fromisoformat(p["bucket"].replace("Z", "+00:00")).astimezone(LA)
        for p in result["series"]
    ]
    assert [(d.month, d.day, d.hour) for d in local_days] == [
        (10, 31, 0),
        (11, 1, 0),
        (11, 2, 0),
    ]
    # 07:30 UTC on Nov 2 is 23:30 PST on Nov 1.
    counts = [p["request_count"] for p in result["series"]]
    assert counts == [0, 1, 0]


# The analytics series also take the fixed sizes 5m, 15m, 1h, 6h and 1d (the
# Logs volume strip takes minute, hour and day only). Those stay floored to the
# Unix epoch, so in UTC whatever tz asks, and the series says UTC; the bucket
# limit applies to them too, and an unknown granularity is still refused as one.
FIXED_SERIES = [name for name in SERIES if name != "volume"]


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("name", FIXED_SERIES)
class TestFixedSizeBuckets:
    def test_a_fixed_day_is_a_utc_epoch_day_whatever_the_zone(
        self, auth_client, two_requests, name
    ):
        result = _result(
            auth_client.get(
                SERIES[name][0], {**WINDOW, "granularity": "1d", "tz": LA_NAME}
            )
        )

        assert result["time_zone"] == "UTC"
        assert _counts(result, name) == {datetime(2026, 10, 21, tzinfo=UTC): 2}

    def test_six_hour_buckets_ignore_the_zone(self, auth_client, two_requests, name):
        result = _result(
            auth_client.get(
                SERIES[name][0], {**WINDOW, "granularity": "6h", "tz": LA_NAME}
            )
        )

        assert result["time_zone"] == "UTC"
        assert _counts(result, name) == {
            datetime(2026, 10, 21, 0, tzinfo=UTC): 1,
            datetime(2026, 10, 21, 12, tzinfo=UTC): 1,
        }

    def test_refuses_a_fixed_size_window_over_the_bucket_limit(self, auth_client, name):
        response = auth_client.get(
            SERIES[name][0],
            {
                "start": "2026-09-01T00:00:00Z",
                "end": "2026-10-22T00:00:00Z",
                "granularity": "5m",
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert f"{MAX_CHART_BUCKETS:,}" in str(response.json()["result"])

    def test_an_unknown_granularity_is_refused_as_one_before_the_limit(
        self, auth_client, name
    ):
        response = auth_client.get(
            SERIES[name][0],
            {
                "start": "2000-01-01T00:00:00Z",
                "end": "2026-10-22T00:00:00Z",
                "granularity": "7m",
                "tz": LA_NAME,
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "granularity" in str(response.json()).lower()
        assert f"{MAX_CHART_BUCKETS:,}" not in str(response.json())
