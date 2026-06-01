"""Integration tests for `user_id` resolution in the ClickHouse session list.

Regression: ``_list_sessions_clickhouse`` never selected the end-user, so
the response had no ``user_id`` key and the frontend "User ID" column was
blank (only the Postgres fallback populated it). These tests pin that the
CH path now resolves ``end_user_id`` -> EndUser fields and returns
``user_id``/``user_id_type``/``user_id_hash`` — without leaking the
internal ``end_user_id`` UUID.

Run with: bin/test -k "session_list_user_id_ch" integration
"""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.observation_span import EndUser, ObservationSpan
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession
from tracer.services.clickhouse.query_builders.base import NIL_UUID
from tracer.services.clickhouse.query_service import AnalyticsQueryService

pytestmark = [pytest.mark.integration, pytest.mark.api]

LIST_SESSIONS_URL = "/tracer/trace-session/list_sessions/"


class _FakeCHResult:
    """Minimal stand-in for the ClickHouse result object (only `.data`)."""

    def __init__(self, data):
        self.data = data


def _result(response):
    data = response.json()
    return data.get("result", data)


def _date_filters():
    start = timezone.now() - timedelta(days=1)
    end = timezone.now() + timedelta(days=1)
    return [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [start.isoformat(), end.isoformat()],
            },
        }
    ]


def _phase1_row(session_id, end_user_id):
    """A Phase-1 build() result row, in the builder's SELECT order."""
    now = timezone.now()
    return {
        "session_id": str(session_id),
        "session_start": now - timedelta(seconds=10),
        "session_end": now,
        "duration": 10,
        "total_cost": 1.5,
        "total_tokens": 42,
        "traces_count": 2,
        "end_user_id": end_user_id,
    }


def _ch_side_effect(phase1_rows):
    """Route mocked CH calls by query content; only Phase 1 returns rows."""

    def _side_effect(query, params=None, timeout_ms=None):
        if "AS end_user_id" in query and "session_start" in query:
            return _FakeCHResult(list(phase1_rows))
        # content query / span-attributes query / count query -> empty
        return _FakeCHResult([])

    return _side_effect


def _get_sessions(auth_client, project, phase1_rows):
    with (
        patch.object(AnalyticsQueryService, "should_use_clickhouse", return_value=True),
        patch.object(
            AnalyticsQueryService,
            "execute_ch_query",
            side_effect=_ch_side_effect(phase1_rows),
        ),
    ):
        return auth_client.get(
            LIST_SESSIONS_URL,
            {
                "project_id": str(project.id),
                "page_number": 0,
                "page_size": 30,
                "sort_params": json.dumps([]),
                "filters": json.dumps(_date_filters()),
            },
        )


class TestSessionListUserIdClickHouse:
    def test_user_id_resolved_from_end_user(
        self, auth_client, organization, workspace, observe_project
    ):
        end_user = EndUser.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            user_id="alice@example.com",
            user_id_type="email",
            user_id_hash="alice-hash",
        )
        session = TraceSession.objects.create(
            project=observe_project, name="session-alice"
        )
        rows = [_phase1_row(session.id, str(end_user.id))]

        response = _get_sessions(auth_client, observe_project, rows)

        assert response.status_code == status.HTTP_200_OK
        table = _result(response)["table"]
        row = next(r for r in table if r["session_id"] == str(session.id))
        assert row["user_id"] == "alice@example.com"
        assert row["user_id_type"] == "email"
        assert row["user_id_hash"] == "alice-hash"
        # Internal UUID must not leak into the response.
        assert "end_user_id" not in row

    def test_nil_or_missing_end_user_id_yields_none(
        self, auth_client, organization, workspace, observe_project
    ):
        s_nil = TraceSession.objects.create(project=observe_project, name="s-nil")
        s_none = TraceSession.objects.create(project=observe_project, name="s-none")
        s_unknown = TraceSession.objects.create(
            project=observe_project, name="s-unknown"
        )
        rows = [
            _phase1_row(s_nil.id, NIL_UUID),
            _phase1_row(s_none.id, None),
            # A well-formed UUID that resolves to no EndUser row.
            _phase1_row(s_unknown.id, str(uuid.uuid4())),
        ]

        response = _get_sessions(auth_client, observe_project, rows)

        assert response.status_code == status.HTTP_200_OK
        table = _result(response)["table"]
        for r in table:
            assert r["user_id"] is None
            assert "user_id" in r
            assert "user_id_type" in r
            assert "user_id_hash" in r
            assert "end_user_id" not in r


class TestSessionListFirstLinkedUser:
    """When a session has spans linked to multiple end-users, the list must
    return the FIRST linked one (earliest span by start_time). Exercised via
    the Postgres path (CH disabled) against real spans."""

    def test_first_linked_user_returned_via_pg(
        self, auth_client, organization, workspace, observe_project
    ):
        first_user = EndUser.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            user_id="first@example.com",
            user_id_type="email",
            user_id_hash="first-hash",
        )
        second_user = EndUser.objects.create(
            organization=organization,
            workspace=workspace,
            project=observe_project,
            user_id="second@example.com",
            user_id_type="email",
            user_id_hash="second-hash",
        )
        session = TraceSession.objects.create(
            project=observe_project, name="multi-user-session"
        )
        trace = Trace.objects.create(
            project=observe_project, session=session, name="multi-user-trace"
        )
        base = timezone.now() - timedelta(hours=1)

        # Earlier span -> first_user; later span -> second_user.
        ObservationSpan.objects.create(
            id=f"first_span_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=trace,
            end_user=first_user,
            name="early span",
            observation_type="llm",
            start_time=base,
            end_time=base + timedelta(seconds=2),
            total_tokens=5,
            cost=0.01,
            status="OK",
        )
        ObservationSpan.objects.create(
            id=f"second_span_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=trace,
            end_user=second_user,
            name="late span",
            observation_type="llm",
            start_time=base + timedelta(minutes=5),
            end_time=base + timedelta(minutes=5, seconds=2),
            total_tokens=7,
            cost=0.02,
            status="OK",
        )

        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        (base - timedelta(hours=1)).isoformat(),
                        (base + timedelta(hours=1)).isoformat(),
                    ],
                },
            }
        ]

        # Force the Postgres path so we exercise _fetch_end_user_info ordering.
        with patch.object(
            AnalyticsQueryService, "should_use_clickhouse", return_value=False
        ):
            response = auth_client.get(
                LIST_SESSIONS_URL,
                {
                    "project_id": str(observe_project.id),
                    "page_number": 0,
                    "page_size": 30,
                    "sort_params": json.dumps([]),
                    "filters": json.dumps(filters),
                },
            )

        assert response.status_code == status.HTTP_200_OK
        table = _result(response)["table"]
        row = next(r for r in table if r["session_id"] == str(session.id))
        # First linked (earliest span) wins, not the later one.
        assert row["user_id"] == "first@example.com"
        assert row["user_id_type"] == "email"
        assert row["user_id_hash"] == "first-hash"
