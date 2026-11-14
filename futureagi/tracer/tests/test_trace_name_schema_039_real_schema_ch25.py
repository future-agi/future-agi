"""Verify that the 039 row-local expression works on a real CH25 schema."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from tracer.tests._users_live_ch import lane_client

pytestmark = pytest.mark.integration


def test_new_root_and_child_are_row_local_without_mutations():
    with lane_client() as ch:
        before = ch.execute(
            "SELECT count() FROM system.mutations WHERE database = currentDatabase() "
            "AND table = 'spans'"
        )[0][0]
        project, trace = uuid.uuid4(), uuid.uuid4()
        root, child = uuid.uuid4().hex, uuid.uuid4().hex
        now = datetime(2026, 8, 15, tzinfo=UTC)
        columns = (
            "project_id, observation_type, service_name, start_time, "
            "trace_id, id, parent_span_id, name"
        )
        ch.execute(
            f"INSERT INTO spans ({columns}) VALUES",
            [
                (project, "span", "test", now, str(trace), root, "", "Root Name"),
                (project, "span", "test", now, str(trace), child, root, "Child Name"),
            ],
        )
        names = dict(
            ch.execute(
                "SELECT id, trace_name FROM spans WHERE project_id = %(project)s "
                "AND trace_id = %(trace)s",
                {"project": project, "trace": str(trace)},
            )
        )
        assert names == {root: "Root Name", child: ""}
        assert ch.execute(
            "SELECT default_expression FROM system.columns "
            "WHERE database = currentDatabase() AND table = 'spans' "
            "AND name = 'trace_name'"
        ) == [("if(empty(parent_span_id), name, '')",)]
        assert (
            ch.execute(
                "SELECT count() FROM system.mutations WHERE database = currentDatabase() "
                "AND table = 'spans'"
            )[0][0]
            == before
        )
