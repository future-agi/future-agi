"""Shared plumbing for the live ClickHouse Users list modules.

``lane_client`` is the one gate the real-schema modules pass before they
write to (and stop merges on) ``spans``: the database ``CH25_DATABASE`` names
must be a provisioned lane database carrying the deployed schema.
``LiveExecutor`` runs the manager's statements exactly as they are built.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from conftest import _ch_test_native_client


def lane_database() -> str:
    database = (os.environ.get("CH25_DATABASE") or "").strip()
    if not database:
        pytest.skip("no lane database named: set CH25_DATABASE")
    # CI gives every job its own throwaway ClickHouse, whose test_tfc carries
    # the deployed schema and runs one test at a time. Locally test_tfc is shared
    # with other runs, and these modules write to and stop merges on spans, so a
    # local run needs its own database provisioned with provision-lane-ch-db.sh.
    if database == "test_tfc" and os.environ.get("GITHUB_ACTIONS") == "true":
        return database
    if database == "test_tfc" or not database.startswith("test_"):
        pytest.skip(
            f"not writing to {database!r}: point CH25_DATABASE at a database "
            "provisioned with provision-lane-ch-db.sh"
        )
    return database


@contextmanager
def lane_client() -> Iterator[Any]:
    """A native client on the lane database, which must carry the deployed schema."""

    database = lane_database()
    with _ch_test_native_client(database=database) as client:
        kind = client.execute(
            "SELECT default_kind FROM system.columns WHERE database = "
            "currentDatabase() AND table = 'spans' AND name = 'trace_name'"
        )
        if kind != [("MATERIALIZED",)]:
            pytest.fail(
                f"{database} does not carry the deployed spans schema; "
                "provision it with provision-lane-ch-db.sh"
            )
        client.database_name = database
        yield client


class LiveExecutor:
    """Runs the manager's statements as they are, recording each one."""

    def __init__(self, client):
        self.client = client
        self.statements: list[str] = []

    def execute_ch_query(
        self,
        query,
        params=None,
        timeout_ms=None,
        settings=None,
        *,
        server_execution_cap_ms=None,
    ):
        self.statements.append(query)
        rows, columns = self.client.execute(
            query, params or {}, with_column_types=True, settings=settings or {}
        )
        names = [name for name, _type in columns]
        return SimpleNamespace(
            data=[dict(zip(names, row, strict=True)) for row in rows],
            columns=names,
            query_time_ms=1.0,
        )
