"""Test-only receipt for the two owner checks in the million-field refresh.

system.query_log documents memory_usage as the query's memory consumption in
bytes. Copy the recorded terminal value, including for exceptions: it is not
process RSS, the refused allocation size, or a reconstructed failure peak.
ProfileEvents absent from the recorded map are unknown, not measured zeros.
"""

import json
import re
from contextlib import contextmanager, nullcontext
from time import monotonic, sleep
from uuid import uuid4

import pytest
from clickhouse_driver import Client

_COUNTERS = ("memory_usage", "read_rows", "read_bytes", "query_duration_ms")
_EVENTS = (
    "SelectedParts",
    "SelectedRanges",
    "SelectedMarks",
    "SelectedRows",
    "SelectedBytes",
    "ExternalSortWritePart",
    "ExternalSortMerge",
)
_TERMINAL = ("QueryFinish", "ExceptionWhileProcessing", "ExceptionBeforeStart")
_MAX_POLLS = 5
_DIAGNOSTIC_SECONDS = 5
_IO_SECONDS = 1
_POLL_INTERVAL = 0.1


def _can_start_io(deadline):
    # These driver timeouts cover individual waits, not a whole execute().
    return deadline - monotonic() >= _IO_SECONDS


def _uint(value):
    return value if type(value) is int and 0 <= value < 2**64 else None


def _rows(client, sql, params=None):
    try:
        return client.execute(sql, params)
    except Exception:  # noqa: BLE001 - optional diagnostics; never expose exception text
        return None


class CleanupProbe:
    def __init__(self, store):
        self.client = store.client
        self.native = isinstance(self.client, Client)
        # Save the fixture database before a failed query discards its connection.
        self.database = self.client.connection.database if self.native else None
        self.ids = {role: str(uuid4()) for role in ("dependencies", "fields")}
        self.statements = {role: {"status": "not_executed"} for role in self.ids}
        self.unclassified = 0

    def tag(self, sql, params, kwargs):
        # Only this writer, within this refresh (caller), and the private binding
        # unique to _span_owner_cleanup_required. No scanning/text capture for
        # other queries, including transient-table restoration inside execute().
        if not params or "span_owner_cutoff" not in params:
            return
        if "AND canonical_session_id NOT IN (SELECT unit_id FROM (" in sql:
            role = "dependencies"
        elif (
            " AS span_owner_revisions " in sql
            and "AND (notEmpty(session_id) OR notEmpty(user_id)) AND session_id NOT IN ("
            in sql
        ):
            role = "fields"
        else:
            self.unclassified = min(255, self.unclassified + 1)
            return
        record = self.statements[role]
        if record["status"] != "not_executed":
            record["additional_calls"] = min(255, record.get("additional_calls", 0) + 1)
        elif self.native and "query_id" not in kwargs:
            record["status"] = "query_log_unavailable"
            record["query_id"] = kwargs["query_id"] = self.ids[role]
        else:
            # Never replace or print a caller ID, or pass query_id to chDB.
            record["status"] = "id_unavailable"

    def _diagnostic_client(self):
        if not self.native:
            # Embedded tests have no native server/log IDs. Only read metadata.
            return nullcontext(self.client)
        from conftest import _ch_test_native_client

        # A failed diagnostic must not reset the writer's connection/temporary
        # tables. Reuse the fixture's validated sidecar and owned database.
        return _ch_test_native_client(
            database=self.database,
            connect_timeout=_IO_SECONDS,
            send_receive_timeout=_IO_SECONDS,
            sync_request_timeout=_IO_SECONDS,
            disable_reconnect=True,
        )

    def _collect(self, client, report, deadline):
        if not _can_start_io(deadline):
            return
        info = _rows(
            client,
            "SELECT version(), revision(), toUInt8(getSetting('log_queries')), "
            "toUInt8(getSetting('log_profile_events'))",
        )
        if info:
            version, revision, logs, events = info[0]
            if isinstance(version, str) and re.fullmatch(
                r"[0-9]{1,5}(?:\.[0-9]{1,10}){2,3}", version
            ):
                report["version"] = version
            report["revision"] = _uint(revision)
            report["log_queries"] = _uint(logs)
            report["log_profile_events"] = _uint(events)
        if not _can_start_io(deadline):
            return
        build = _rows(
            client,
            "SELECT left(value, 41) FROM system.build_options "
            "WHERE name = 'VERSION_GITHASH' LIMIT 1",
        )
        if build:
            value = build[0][0]
            if isinstance(value, str) and re.fullmatch("[0-9a-fA-F]{40}", value):
                report["build_git_hash"] = value

        pending = {
            r["query_id"]: r for r in self.statements.values() if "query_id" in r
        }
        # A terminal event can arrive after a flush. Recheck only successful misses.
        for attempt in range(_MAX_POLLS):
            if not pending:
                return
            if attempt:
                if not _can_start_io(deadline - _POLL_INTERVAL):
                    return
                sleep(_POLL_INTERVAL)
            if not _can_start_io(deadline):
                return
            report["poll_count"] = (
                attempt + 1
            )  # Flushes started, even if the read cannot start.
            flushed = _rows(client, "SYSTEM FLUSH LOGS") is not None
            report["flush"] = "ok" if flushed else "unavailable"
            if not _can_start_io(deadline):
                return
            rows = _rows(
                client,
                "SELECT query_id, toString(type), memory_usage, read_rows, read_bytes, query_duration_ms, "
                "mapFilter((k, v) -> k IN %(events)s, ProfileEvents) "
                "FROM system.query_log WHERE event_date >= yesterday() AND is_initial_query = 1 "
                "AND query_id IN %(ids)s AND type IN %(terminal)s LIMIT 2 BY query_id",
                {"ids": tuple(pending), "events": _EVENTS, "terminal": _TERMINAL},
            )
            if rows is None:
                for record in pending.values():
                    record["status"] = "query_log_unavailable"
                return
            self._record_rows(pending, rows)
            if not flushed:
                return

    @staticmethod
    def _record_rows(pending, rows):
        for query_id in tuple(pending):
            matches = [
                row for row in rows if row[0] == query_id and row[1] in _TERMINAL
            ]
            if not matches:
                pending[query_id]["status"] = "no_terminal_row"
                continue
            record = pending.pop(query_id)
            if len(matches) != 1:
                record["status"] = "ambiguous"
                continue
            _, terminal, *counters, events = matches[0]
            record.update(
                {k: _uint(v) for k, v in zip(_COUNTERS, counters, strict=True)}
            )
            record["ProfileEvents"] = (
                {k: _uint(events[k]) for k in _EVENTS if k in events}
                if isinstance(events, dict)
                else None
            )
            record["status"] = terminal

    @contextmanager
    def after_refresh(self, mode, capsys):
        outcome = "raised"
        try:
            yield
            outcome = "returned"  # Not a verdict: the existing assertions still run.
        finally:
            report = {
                "mode": mode,
                "refresh": outcome,
                "version": None,
                "revision": None,
                "build_git_hash": None,
                "log_queries": None,
                "log_profile_events": None,
                "flush": "not_attempted",
                "poll_count": 0,
                "statements": self.statements,
                "unclassified": self.unclassified,
            }
            try:
                # Charge setup and metadata to the same admission deadline as the polls.
                deadline = monotonic() + _DIAGNOSTIC_SECONDS
                with self._diagnostic_client() as client:
                    self._collect(client, report, deadline)
            except (Exception, pytest.skip.Exception):  # noqa: BLE001 - observation cannot skip/fail the product test
                report["diagnostics"] = "unavailable"
            if self.unclassified:
                for record in self.statements.values():
                    if record["status"] == "not_executed":
                        record["status"] = "unknown"
            # Backend CI splits tests between jobs, not xdist workers. Suspend
            # capture for this bounded line only, including on passing runs.
            try:
                with capsys.disabled():
                    print(
                        "\nM241_PROBE " + json.dumps(report, separators=(",", ":")),
                        flush=True,
                    )
            except Exception:  # noqa: BLE001 - a diagnostic sink cannot replace a product failure
                # Best effort only if pytest's output sink itself is broken.
                pass
