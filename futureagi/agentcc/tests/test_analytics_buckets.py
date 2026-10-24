"""Analytics ranges are bucketed at the granularity the console sends.

The console picks 5m (<= 6 h), 15m (<= 24 h), 1h (<= 7 d), 6h (<= 30 d) and 1d
(longer) from the range (analytics/UsageCharts.jsx, LatencyAnalytics.jsx,
ErrorAnalytics.jsx); KeyDetailDrawer.jsx sends 1h. Each range is bucketed at that
size. An unknown granularity answers 400.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.http import QueryDict
from rest_framework import status

from agentcc.models import AgentccRequestLog

USAGE = "/agentcc/analytics/usage-timeseries/"
LATENCY = "/agentcc/analytics/latency-stats/"
ERRORS = "/agentcc/analytics/error-breakdown/"
GUARDRAIL_TRENDS = "/agentcc/analytics/guardrail-trends/"

START = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)


def _iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def _log(organization, workspace, request_id, at, **fields):
    return AgentccRequestLog.objects.create(
        organization=organization,
        workspace=workspace,
        request_id=request_id,
        model="gpt-4o",
        provider="openai",
        status_code=fields.pop("status_code", 200),
        latency_ms=fields.pop("latency_ms", 100),
        cost=Decimal(fields.pop("cost", "0.001")),
        total_tokens=10,
        started_at=at,
        **fields,
    )


def _get(auth_client, path, start, end, granularity):
    return auth_client.get(
        path,
        {"start": _iso(start), "end": _iso(end), "granularity": granularity},
    )


def _buckets(series, key="bucket"):
    return [
        datetime.fromisoformat(str(row[key]).replace("Z", "+00:00")) for row in series
    ]


def _steps(buckets):
    return {b - a for a, b in zip(buckets, buckets[1:], strict=False)}


@pytest.mark.integration
@pytest.mark.django_db
class TestGranularityBuckets:
    @pytest.mark.parametrize(
        ("granularity", "hours", "step", "points"),
        [
            # The console's ranges (TimeRangeSelector 1H/6H/24H/7D/30D/90D).
            ("5m", 1, timedelta(minutes=5), 13),
            ("5m", 6, timedelta(minutes=5), 73),
            ("15m", 24, timedelta(minutes=15), 97),
            ("1h", 24 * 7, timedelta(hours=1), 169),
            ("6h", 24 * 30, timedelta(hours=6), 121),
            ("1d", 24 * 90, timedelta(days=1), 91),
        ],
    )
    def test_console_granularity_sets_the_bucket_size(
        self, auth_client, organization, workspace, granularity, hours, step, points
    ):
        end = START + timedelta(hours=hours)
        response = _get(auth_client, USAGE, START, end, granularity)

        assert response.status_code == status.HTTP_200_OK, response.content
        result = response.json()["result"]
        assert result["granularity"] == granularity
        buckets = _buckets(result["series"])
        assert len(buckets) == points
        assert _steps(buckets) == {step}
        assert buckets[0] == START

    def test_five_minute_buckets_count_each_request_in_its_own_bucket(
        self, auth_client, organization, workspace
    ):
        # 00:01 and 00:04 share the 00:00 bucket; 00:07 is 00:05's; 00:58 is 00:55's.
        for i, minute in enumerate((1, 4, 7, 58)):
            _log(
                organization,
                workspace,
                f"5m-{i}",
                START + timedelta(minutes=minute),
                status_code=500 if minute == 7 else 200,
                is_error=minute == 7,
            )

        result = _get(
            auth_client, USAGE, START, START + timedelta(hours=1), "5m"
        ).json()["result"]

        counts = {
            row["bucket"]: (row["request_count"], row["error_count"])
            for row in result["series"]
            if row["request_count"]
        }
        assert counts == {
            _iso_bucket(START): (2, 0),
            _iso_bucket(START + timedelta(minutes=5)): (1, 1),
            _iso_bucket(START + timedelta(minutes=55)): (1, 0),
        }
        assert sum(row["request_count"] for row in result["series"]) == 4

    def test_six_hour_buckets_align_to_utc_quarter_days(
        self, auth_client, organization, workspace
    ):
        _log(organization, workspace, "6h-a", START + timedelta(hours=5))
        _log(organization, workspace, "6h-b", START + timedelta(hours=6, minutes=1))
        _log(organization, workspace, "6h-c", START + timedelta(hours=23))

        result = _get(
            auth_client, USAGE, START, START + timedelta(days=2), "6h"
        ).json()["result"]

        counts = {
            row["bucket"]: row["request_count"]
            for row in result["series"]
            if row["request_count"]
        }
        assert counts == {
            _iso_bucket(START): 1,
            _iso_bucket(START + timedelta(hours=6)): 1,
            _iso_bucket(START + timedelta(hours=18)): 1,
        }

    def test_grouped_usage_uses_the_same_buckets(
        self, auth_client, organization, workspace
    ):
        _log(organization, workspace, "grp-a", START + timedelta(minutes=16))

        result = auth_client.get(
            USAGE,
            {
                "start": _iso(START),
                "end": _iso(START + timedelta(hours=1)),
                "granularity": "15m",
                "group_by": "model",
            },
        ).json()["result"]

        series = result["groups"]["gpt-4o"]
        assert _steps(_buckets(series)) == {timedelta(minutes=15)}
        hit = [row for row in series if row["request_count"]]
        assert [row["bucket"] for row in hit] == [
            _iso_bucket(START + timedelta(minutes=15))
        ]

    def test_latency_and_error_timeseries_use_the_granularity(
        self, auth_client, organization, workspace
    ):
        _log(
            organization,
            workspace,
            "lat-a",
            START + timedelta(minutes=20),
            latency_ms=900,
            status_code=503,
            is_error=True,
        )
        end = START + timedelta(hours=1)

        latency = _get(auth_client, LATENCY, START, end, "5m").json()["result"]
        assert latency["granularity"] == "5m"
        assert _steps(_buckets(latency["timeseries"])) == {timedelta(minutes=5)}
        busy = [row for row in latency["timeseries"] if row.get("request_count")]
        assert [row["bucket"] for row in busy] == [
            _iso_bucket(START + timedelta(minutes=20))
        ]

        errors = _get(auth_client, ERRORS, START, end, "15m").json()["result"]
        error_buckets = _buckets(errors["error_timeseries"])
        assert _steps(error_buckets) == {timedelta(minutes=15)}
        failing = [row for row in errors["error_timeseries"] if row["error_count"]]
        assert [row["bucket"] for row in failing] == [
            _iso_bucket(START + timedelta(minutes=15))
        ]

    def test_guardrail_trends_use_the_granularity(
        self, auth_client, organization, workspace
    ):
        _log(
            organization,
            workspace,
            "gr-a",
            START + timedelta(minutes=31),
            guardrail_triggered=True,
        )

        result = _get(
            auth_client, GUARDRAIL_TRENDS, START, START + timedelta(hours=1), "15m"
        ).json()["result"]

        assert result["granularity"] == "15m"
        stamps = _buckets(result["series"], key="timestamp")
        assert _steps(stamps) == {timedelta(minutes=15)}
        hit = [row for row in result["series"] if row["trigger_count"]]
        assert len(hit) == 1
        assert _buckets(hit, key="timestamp") == [START + timedelta(minutes=30)]

    @pytest.mark.parametrize("path", [USAGE, LATENCY, ERRORS, GUARDRAIL_TRENDS])
    @pytest.mark.parametrize("granularity", ["7m", "fortnight", "0m", "hourly"])
    def test_unknown_granularity_answers_400(self, auth_client, path, granularity):
        response = _get(
            auth_client, path, START, START + timedelta(hours=1), granularity
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
        assert "granularity" in str(response.json()).lower()

    @pytest.mark.parametrize(
        ("granularity", "step"),
        [
            ("minute", timedelta(minutes=1)),
            ("hour", timedelta(hours=1)),
            ("day", timedelta(days=1)),
        ],
    )
    def test_named_granularities_work(self, auth_client, granularity, step):
        end = (
            START + timedelta(hours=2)
            if granularity != "day"
            else START + timedelta(days=3)
        )
        result = _get(auth_client, USAGE, START, end, granularity).json()["result"]

        assert result["granularity"] == granularity
        assert _steps(_buckets(result["series"])) == {step}

    def test_no_granularity_is_hourly(self, auth_client):
        result = auth_client.get(
            USAGE,
            {"start": _iso(START), "end": _iso(START + timedelta(hours=3))},
        ).json()["result"]

        assert result["granularity"] == "hour"
        assert _steps(_buckets(result["series"])) == {timedelta(hours=1)}


def test_parse_time_range_reads_the_console_granularities():
    """parse_time_range reads every granularity the console sends."""
    from agentcc.services.analytics import parse_time_range

    for granularity in ("5m", "15m", "1h", "6h", "1d", "hour"):
        query = QueryDict(mutable=True)
        query["start"] = "2026-10-19T00:00:00Z"
        query["end"] = "2026-10-19T01:00:00Z"
        query["granularity"] = granularity
        assert parse_time_range(query)[2] == granularity


def _iso_bucket(dt):
    """A bucket as the API renders it (DRF's datetime format)."""
    return _iso(dt)


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/agentcc/analytics/overview/",
        "/agentcc/analytics/cost-breakdown/",
        "/agentcc/analytics/model-comparison/",
        "/agentcc/analytics/guardrail-overview/",
        "/agentcc/analytics/guardrail-rules/",
    ],
)
def test_endpoints_that_do_not_bucket_ignore_granularity(auth_client, path):
    """Only the time-bucketed endpoints read granularity; the others ignore it and do
    not refuse a value."""
    response = _get(auth_client, path, START, START + timedelta(hours=1), "fortnight")

    assert response.status_code == status.HTTP_200_OK, response.content
