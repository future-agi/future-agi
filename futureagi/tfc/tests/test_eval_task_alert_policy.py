"""Exercise the provisioned SQL in PostgreSQL with isolated synthetic rows."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection

POLICY = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "deploy/grafana/alerts/eval-task-failures.json"
    ).read_text()
)


def test_stalled_reminders_are_daily_without_delaying_new_tasks():
    parent = POLICY["notification_policy_route"]
    route = next(
        r
        for r in parent.get("routes", [])
        if ["kind", "=", "stalled"] in r["object_matchers"]
    )
    assert route["repeat_interval"] == "24h"
    assert route["group_wait"] == "30s"
    assert route["group_interval"] == "5m"
    assert route["group_by"] == ["alertname", "task_id"]
    assert parent["repeat_interval"] == "6h"  # failed-task escalation is unchanged


@pytest.mark.django_db
def test_stall_query_retains_old_and_new_incidents_without_minute_label_churn():
    query = POLICY["rules"][0]["data"][0]["model"]["rawSql"]
    with connection.cursor() as cursor:
        # Temporary tables shadow application tables on this test connection only.
        cursor.execute(
            "CREATE TEMP TABLE accounts_organization (id uuid, name text) ON COMMIT DROP"
        )
        cursor.execute(
            "CREATE TEMP TABLE tracer_project (id uuid, name text, organization_id uuid) ON COMMIT DROP"
        )
        cursor.execute(
            "CREATE TEMP TABLE tracer_eval_task (id uuid, name text, project_id uuid, continuous_cursor timestamptz, start_time timestamptz, created_at timestamptz, run_type text, status text, deleted boolean) ON COMMIT DROP"
        )
        cursor.execute(
            "CREATE TEMP TABLE tracer_eval_logger (eval_task_id text, created_at timestamptz, updated_at timestamptz, status text, deleted boolean) ON COMMIT DROP"
        )
        org_id, project_id = uuid4(), uuid4()
        cursor.execute(
            "INSERT INTO accounts_organization VALUES (%s, 'Synthetic')", [org_id]
        )
        cursor.execute(
            "INSERT INTO tracer_project VALUES (%s, 'Test', %s)", [project_id, org_id]
        )
        ids = {}
        for name, age, status, deleted in [
            ("old", "30 days", "running", False),
            ("new", "61 minutes", "running", False),
            ("paused", "2 days", "paused", False),
            ("deleted", "2 days", "running", True),
            ("healthy", "5 minutes", "running", False),
            ("draining", "2 days", "running", False),
        ]:
            task_id = ids[name] = uuid4()
            cursor.execute(
                "INSERT INTO tracer_eval_task VALUES (%s, %s, %s, NULL, NULL, now() - %s::interval, 'continuous', %s, %s)",
                [task_id, name, project_id, age, status, deleted],
            )
        for name in ("old", "draining"):
            cursor.execute(
                "INSERT INTO tracer_eval_logger VALUES (%s, now() - interval '2 hours', now() - interval '2 hours', 'pending', false)",
                [str(ids[name])],
            )
        cursor.execute(
            "INSERT INTO tracer_eval_logger VALUES (%s, now() - interval '2 hours', now(), 'completed', false)",
            [str(ids["draining"])],
        )
        cursor.execute(query)
        columns = [c[0] for c in cursor.description]
        rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        reasons = {(row["task"], row["reason"]) for row in rows}
        assert reasons == {
            ("old", "coverage lag"),
            ("old", "backlog not draining"),
            ("new", "coverage lag"),
            ("draining", "coverage lag"),
        }
        assert all(isinstance(row["minutes"], float) for row in rows)

        def labels(values):
            return {
                tuple(
                    (key, value) for key, value in row.items() if isinstance(value, str)
                )
                for row in values
            }

        cursor.execute(
            "UPDATE tracer_eval_task SET created_at = created_at - interval '5 minutes' WHERE name != 'healthy'"
        )
        cursor.execute(query)
        later = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        assert labels(rows) == labels(later)
        cursor.execute(
            "UPDATE tracer_eval_task SET continuous_cursor = now() WHERE name = 'new'"
        )
        cursor.execute(query)
        assert str(ids["new"]) not in {row[0] for row in cursor.fetchall()}
