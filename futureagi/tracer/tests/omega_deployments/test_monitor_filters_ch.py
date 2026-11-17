"""The default monitors ignore check runs and preview revisions, measured in ClickHouse.

The monitors come from a real ensure call. Spans are seeded into the test ClickHouse through
``_ch_seed`` (the production ``adapt()`` shape), and the numbers come from the evaluator's own
functions (``tracer.utils.monitor``), so the stored filters go through the same translation and
query a firing alert would. Mirrors ``test_monitor_metrics_execution_smoke.py``: the v2 builder,
a synchronous delete on teardown, a skip only when ClickHouse is not reachable.
"""

import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.test import override_settings
from django.utils import timezone

from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.tests.omega_deployments.conftest import ensure_url, new_deployment_id

pytestmark = [pytest.mark.integration, pytest.mark.django_db]

V2_MONITORS = {**settings.CLICKHOUSE_V2, "QUERY_TYPES_V2_ONLY": "monitor_metrics"}

# (environment, run source, status, total tokens). The rows the defaults count are
# production traffic: one error and 1,500,000 tokens in all.
SEEDED = [
    ("production", "api", "ERROR", 1_000_000),
    ("production", "api", "OK", 500_000),
    ("production", "studio", "OK", 0),
    ("production", "check", "ERROR", 2_000_000),
    ("production", "check", "ERROR", 2_000_000),
    ("preview", "api", "ERROR", 3_000_000),
]
EXPECTED_ERRORS = 1
EXPECTED_TOKENS = 1_500_000
ALL_ERRORS = 4
ALL_TOKENS = 8_500_000


def _client():
    import clickhouse_connect

    from tracer.services.clickhouse.v2 import get_v2_config

    cfg = get_v2_config()
    try:
        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
        )
        client.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"test ClickHouse not reachable ({exc!r})")
    return client


def _span(project_id, org_id, start, environment, source, status, tokens):
    return {
        "id": f"obslink-{uuid.uuid4().hex[:16]}",
        "trace_id": str(uuid.uuid4()),
        "project_id": project_id,
        "org_id": org_id,
        "parent_span_id": "",
        "name": "agent run",
        "observation_type": "agent",
        "status": status,
        "start_time": start,
        "end_time": start,
        "created_at": start,
        "latency_ms": 10,
        "total_tokens": tokens,
        "provider": "",
        "trace_session_id": None,
        "span_attributes": {
            "omega.deployment.id": "dep-0123456789abcdef",
            "omega.deployment.environment": environment,
            "omega.run.source": source,
            "omega.deployment.revision": 3,
        },
    }


@pytest.fixture
def seeded_monitors(auth_client, organization):
    client = _client()
    result = auth_client.post(
        ensure_url(new_deployment_id()), {"name": "metered-agent"}, format="json"
    ).data["result"]
    project_id = result["projectId"]
    now = timezone.now().replace(microsecond=0)
    from tracer.tests._ch_seed import seed_ch_spans

    seed_ch_spans(
        [
            _span(
                project_id,
                str(organization.id),
                now - timedelta(minutes=5 + index),
                *row,
            )
            for index, row in enumerate(SEEDED)
        ],
        client=client,
    )
    monitors = {
        alert["kind"]: UserAlertMonitor.no_workspace_objects.get(id=alert["id"])
        for alert in result["alerts"]
    }
    try:
        yield monitors, now
    finally:
        client.command(
            "ALTER TABLE spans DELETE WHERE project_id = %(p)s "
            "SETTINGS mutations_sync=2",
            parameters={"p": project_id},
        )
        client.close()


def _value(monitor, start, end):
    from tracer.utils.monitor import _get_metric_value

    with override_settings(CLICKHOUSE_V2=V2_MONITORS):
        return _get_metric_value(monitor, start, end)


def test_errors_spike_counts_production_errors_only(seeded_monitors):
    monitors, now = seeded_monitors
    errors = monitors["errors_spike"]
    start = now - timedelta(minutes=errors.alert_frequency)

    assert _value(errors, start, now) == EXPECTED_ERRORS

    errors.filters = {}
    assert _value(errors, start, now) == ALL_ERRORS


def test_daily_tokens_counts_production_tokens_only(seeded_monitors):
    monitors, now = seeded_monitors
    tokens = monitors["daily_tokens"]

    assert _value(tokens, now - timedelta(hours=1), now) == EXPECTED_TOKENS

    tokens.filters = {}
    assert _value(tokens, now - timedelta(hours=1), now) == ALL_TOKENS


def test_check_runs_alone_do_not_fire_daily_tokens_but_traffic_does(
    seeded_monitors, monkeypatch
):
    from tracer.utils import monitor as evaluator

    sent = []
    monkeypatch.setattr(evaluator, "email_helper", lambda **kwargs: sent.append(kwargs))
    monitors, now = seeded_monitors
    tokens = monitors["daily_tokens"]

    # 4,000,000 check tokens and 3,000,000 preview tokens are over the limit;
    # production traffic (1,500,000) is too, so raise the bar between them.
    tokens.critical_threshold_value = 2_000_000
    with override_settings(CLICKHOUSE_V2=V2_MONITORS):
        evaluator._process_monitor(tokens, now)
    assert not UserAlertMonitorLog.objects.filter(alert=tokens).exists()
    assert sent == []

    tokens.critical_threshold_value = 1_000_000
    with override_settings(CLICKHOUSE_V2=V2_MONITORS):
        evaluator._process_monitor(tokens, now)
    fired = UserAlertMonitorLog.objects.get(alert=tokens)
    assert fired.type == "critical"
    assert "1500000.00" in fired.message
    assert [mail["to_email_list"] for mail in sent] == [tokens.notification_emails]
