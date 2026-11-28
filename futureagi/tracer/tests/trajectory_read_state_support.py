"""Test doubles for the trajectory read-state schema probe.

``trajectory_read_state`` probes ClickHouse for the six 040 ``spans`` columns
and ``trajectory_items``. Unit tests that build SQL or hydrate rows against
fake clients replace the default probe with one of these, so the state
follows ``FI_TRAJECTORY_STORE_READ_ENABLED`` exactly as before the probe
existed (true: READY, false: NOT_READY while no items exist) and no probe
query reaches a fake client.
"""

from __future__ import annotations

from typing import Any

from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse.v2 import trajectory_hydration


def _column_row(name: str) -> tuple[str, str, str, str]:
    """``system.columns`` (name, type, default_kind, default_expression) for
    one pointer column exactly as ClickHouse reports the 040 declaration."""
    declared = core._TRAJECTORY_SPAN_COLUMNS[name]
    typ, _, expr = declared.partition(" DEFAULT ")
    return name, typ, "DEFAULT", expr


class SchemaProbe:
    """A scripted probe: the columns, the ``trajectory_items`` row count, and
    optionally an exception (``error`` for every statement, ``system_error``
    for the system-table statements only), with every statement it was
    asked recorded."""

    def __init__(
        self,
        *,
        columns: tuple[str, ...] | None = trajectory_hydration.TRAJECTORY_SPAN_COLUMNS,
        table: bool = True,
        total_rows: int | None = 0,
        error: BaseException | None = None,
        system_error: BaseException | None = None,
        endpoint: str = "fake-probe",
    ):
        self.columns = tuple(columns or ())
        self.table = table
        self.total_rows = total_rows
        self.error = error
        self.system_error = system_error
        self.endpoint = endpoint
        self.statements: list[str] = []

    def run(
        self, sql: str, params: dict[str, Any], names: tuple[str, ...]
    ) -> list[tuple[Any, ...]]:
        del params, names
        self.statements.append(sql)
        if self.error is not None:
            raise self.error
        if "system." in sql and self.system_error is not None:
            raise self.system_error
        if sql.strip() == trajectory_hydration._PROBE_DATABASE_SQL:
            return [("fake_db",)]
        if "hasColumnInTable" in sql:
            return [
                tuple(
                    int(c in self.columns)
                    for c in trajectory_hydration.TRAJECTORY_SPAN_COLUMNS
                )
            ]
        if "system.columns" in sql:
            return [_column_row(name) for name in self.columns]
        if "system.tables" in sql:
            return [(self.total_rows,)] if self.table else []
        raise AssertionError(f"unexpected probe statement: {sql}")

    @property
    def probe(self) -> trajectory_hydration.ReadStateProbe:
        return trajectory_hydration.ReadStateProbe(self.endpoint, self.run)


def install_default_schema_probe(monkeypatch, **script: Any) -> SchemaProbe:
    """Replace the default (CH25 endpoint) probe with a scripted one (by
    default: complete 040, empty ``trajectory_items``) and empty the state
    cache; ``monkeypatch`` restores the probe, call
    ``trajectory_hydration.reset_read_state_cache()`` on teardown."""
    fake = SchemaProbe(endpoint=trajectory_hydration.DEFAULT_PROBE_ENDPOINT, **script)
    trajectory_hydration.reset_read_state_cache()
    monkeypatch.setattr(trajectory_hydration, "_default_probe", lambda: fake.probe)
    return fake
