"""Exact native journal crashes and tampering are local-only deterministic tests."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog import (
    native_write_journal as subject,
)
from tracer.services.clickhouse.v2.property_catalog.installation_identity import (
    IDENTITY_FILENAME,
)
from tracer.services.clickhouse.v2.property_catalog.write_admission import (
    WRITE_ADMISSION_FILENAME,
    _canonical,
)
from tracer.tests.test_property_catalog_write_admission import Probe, admit

TABLE = "property_catalog_activation_control_events"
TOKEN = "property-catalog-control-v1:exact-token"


def rows(table=TABLE):
    result = dict.fromkeys(subject._COLUMNS[table])
    for column, value in {
        "organization_id": UUID(int=1),
        "workspace_id": UUID(int=2),
        "catalog_epoch": 1,
        "projection_version": 1,
        "catalog_revision": 2,
        "build_token": str(UUID(int=3)),
        "target_catalog_revision": 2,
        "target_build_token": str(UUID(int=3)),
    }.items():
        if column in result:
            result[column] = value
    result[subject._COLUMNS[table][-1]] = datetime(
        2026, 9, 6, 12, 34, 56, 123456, tzinfo=UTC
    )
    return [result]


@contextmanager
def managed_session(journal, table, token):
    subject._key(table, token)
    scope = subject.NativeWriteScope.from_rows(table, rows(table), journal.identity)
    with journal.scope(scope, create=True) as scoped:
        with scoped.session(table, token) as session:
            yield session


def options(admission, table=TABLE):
    return {
        "database": admission.database,
        "member": admission.members[0].name,
        "user": "catalog_control",
        "admission_sha256": hashlib.sha256(admission.encode()).hexdigest(),
        "columns": subject._COLUMNS[table],
        "rows": rows(table),
        "settings": {
            "async_insert": 0,
            "insert_deduplicate": 1,
            "insert_quorum": len(admission.members)
            if admission.family == "replicated"
            else 0,
            "insert_quorum_parallel": 1,
            "insert_quorum_timeout": 1000,
            "max_execution_time": 2,
            "insert_block_size": 16384,
        },
    }


def ack(attempt, kind="native_end_of_stream"):
    return {
        "kind": kind,
        "query_id": attempt.query_id,
        "parameters_sha256": attempt.parameters_sha256,
        "admission_sha256": attempt["admission_sha256"],
        "written_rows": attempt.row_count,
    }


def coverage(attempt, admission):
    return {
        "kind": "all_member_coverage",
        "query_id": attempt.query_id,
        "parameters_sha256": attempt.parameters_sha256,
        "admission_sha256": attempt["admission_sha256"],
        "members": [m.name for m in admission.members],
        "settlement": attempt.acknowledgement["kind"],
    }


def path(directory, table=TABLE, token=TOKEN, extension="json"):
    return (
        directory
        / subject.NATIVE_WRITE_ATTEMPT_DIRECTORY
        / (subject._key(table, token) + "." + extension)
    )


@pytest.mark.parametrize("table", sorted(subject._COLUMNS))
def test_exact_typed_payload_roundtrips_all_seven_pinned_tables(tmp_path, table):
    admission = admit(tmp_path)
    request = options(admission, table)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with managed_session(journal, table, TOKEN) as session:
            assert session.load() is None
            first = session.prepare(**request)
            assert first.state == "prepared" and UUID(first.query_id).version == 4
            assert first.columns == tuple(request["columns"])
            assert first.rows == tuple(request["rows"])
            assert first.parameters == tuple(
                tuple(row[column] for column in first.columns)
                for row in request["rows"]
            )
            assert first.sql == subject.native_insert_sql(
                admission.database,
                table,
                first.columns,
                quorum=request["settings"]["insert_quorum"],
            )
            assert first.user == request["user"] and first.row_count == 1
            assert first.parameters_sha256 == subject.native_parameters_sha256(
                first.columns, first.rows
            )
            assert first.settings["log_comment"] == first.parameters_sha256
            assert first.settings["insert_deduplication_token"] == TOKEN
            assert first["admission_sha256"] != admission.topology_sha256
            assert subject.NativeWriteAttempt(first.encode()).encode() == first.encode()
            assert session.load().encode() == first.encode()
    assert path(tmp_path, table).stat().st_mode & 0o777 == 0o600
    assert path(tmp_path, table).stat().st_nlink == 1
    assert path(tmp_path, table).parent.stat().st_mode & 0o777 == 0o700


def test_cells_preserve_exact_integer_extremes_uuid_strings_and_utc_microseconds(
    tmp_path,
):
    admission = admit(tmp_path)
    request = options(admission)
    value = (
        None,
        "",
        str(UUID(int=3)),
        UUID(int=3),
        -(1 << 63),
        -1,
        0,
        (1 << 64) - 1,
        datetime(1970, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        ("café<>&\u2028\u2029", "\x00"),
    )
    request["rows"][0]["action"] = list(value)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        attempt = session.prepare(**request)
        assert attempt.rows[0]["action"] == value
        assert type(attempt.rows[0]["action"][2]) is str
        assert type(attempt.rows[0]["action"][3]) is UUID
        assert attempt.rows[0]["action"][8].microsecond == 1
        assert b'"uint64","18446744073709551615"' in attempt.encode()
        assert b'"int64","-9223372036854775808"' in attempt.encode()
        assert b"1970-01-01T00:00:00.000001Z" in attempt.encode()


@pytest.mark.parametrize(
    "bad",
    [
        True,
        False,
        1.0,
        float("nan"),
        Decimal("1"),
        b"bytes",
        {},
        set(),
        1 << 64,
        -(1 << 63) - 1,
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1))),
    ],
)
def test_unsupported_or_ambiguous_cells_never_create_an_attempt(tmp_path, bad):
    admission = admit(tmp_path)
    request = options(admission)
    request["rows"][0]["action"] = bad
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        with pytest.raises(ValueError):
            session.prepare(**request)
        assert session.load() is None
    assert not path(tmp_path).exists()


@pytest.mark.parametrize("kind", ["native_end_of_stream", "query_log_finish"])
def test_forward_phases_store_full_bound_evidence_and_remain_proof_only(tmp_path, kind):
    admission = admit(tmp_path, Probe(2))
    request = options(admission)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        prepared = session.prepare(**request)
        sent = session.mark_sent(prepared)
        assert sent.state == "sent" and sent.query_id == prepared.query_id
        acknowledged = session.mark_acknowledged(sent, evidence=ack(sent, kind))
        complete = session.mark_complete(
            acknowledged, evidence=coverage(acknowledged, admission)
        )
        assert (
            complete.state == "complete" and complete.parameters == prepared.parameters
        )
        assert complete.acknowledgement["kind"] == kind
        assert complete["completion"]["members"] == ("replica1", "replica2")
        assert complete["completion"]["settlement"] == kind
        assert complete.settings == prepared.settings
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        recovered = session.prepare(**request)
        assert recovered.encode() == complete.encode()
        with pytest.raises(ValueError, match="no replay"):
            session.mark_sent(recovered)


@pytest.mark.parametrize("state", ["prepared", "sent", "acknowledged", "complete"])
def test_existing_exact_intent_keeps_original_query_id_and_frozen_settings(
    tmp_path, monkeypatch, state
):
    admission = admit(tmp_path)
    request = options(admission)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        first = session.prepare(**request)
        if state != "prepared":
            first = session.mark_sent(first)
        if state in {"acknowledged", "complete"}:
            first = session.mark_acknowledged(first, evidence=ack(first))
        if state == "complete":
            first = session.mark_complete(first, evidence=coverage(first, admission))
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        monkeypatch.setattr(
            subject, "uuid4", lambda: pytest.fail("existing attempt allocated a UUID")
        )
        assert session.prepare(**request).encode() == first.encode()
        assert session.load().state == state
        changed = deepcopy(request)
        changed["settings"]["max_execution_time"] += 1
        with pytest.raises(ValueError, match="intent/settings conflict"):
            session.prepare(**changed)
        assert session.load().encode() == first.encode()


@pytest.mark.parametrize(
    "field",
    [
        "database",
        "member",
        "user",
        "admission_sha256",
        "columns",
        "rows",
        "sql",
        "settings",
    ],
)
def test_same_table_token_refuses_any_frozen_intent_conflict(tmp_path, field):
    admission = admit(tmp_path)
    request = options(admission)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        original = session.prepare(**request)
        changed = deepcopy(request)
        if field == "columns":
            changed[field] = tuple(reversed(changed[field]))
        elif field == "rows":
            changed[field][0]["action"] = "other"
        elif field == "sql":
            changed[field] = original.sql + " SETTINGS async_insert=1"
        elif field == "settings":
            changed[field]["insert_quorum"] = 3
        else:
            changed[field] = "f" * 64 if field == "admission_sha256" else "foreign"
        with pytest.raises((ValueError, RuntimeError)):
            session.prepare(**changed)
        assert session.load().encode() == original.encode()


def test_attempt_and_input_mutations_cannot_change_persisted_payload(tmp_path):
    admission = admit(tmp_path)
    request = options(admission)
    request["rows"][0]["action"] = ["one", ["two"]]
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        attempt = session.prepare(**request)
        expected = attempt.encode()
        request["rows"][0]["action"][1].append("mutated")
        request["settings"]["max_execution_time"] = 900
        with pytest.raises(TypeError):
            attempt.settings["max_execution_time"] = 900
        with pytest.raises(TypeError):
            attempt.rows[0]["action"] = None
        detached = attempt.as_mapping()
        detached["rows"][0]["action"] = None
        assert attempt.rows[0]["action"] == ("one", ("two",))
        assert session.load().encode() == expected


@pytest.mark.parametrize(
    "setting,value",
    [
        ("async_insert", 1),
        ("async_insert", False),
        ("insert_quorum_parallel", 0),
        ("insert_quorum", "2"),
        ("insert_quorum", True),
        ("insert_quorum", None),
        ("insert_quorum", -1),
        ("max_execution_time", True),
        ("max_execution_time", 2.0),
        ("max_execution_time", 1 << 64),
        ("insert_deduplication_token", "foreign"),
        ("log_comment", "foreign"),
        ("log_queries", 1),
        ("password", "never-persist"),
    ],
)
def test_settings_are_exact_bounded_and_never_persist_credentials(
    tmp_path, setting, value
):
    admission = admit(tmp_path)
    request = options(admission)
    request["settings"][setting] = value
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        with pytest.raises(ValueError):
            session.prepare(**request)
        assert session.load() is None


def test_string_integer_settings_preserve_types_and_do_not_equal_integer_retry(
    tmp_path,
):
    admission = admit(tmp_path)
    request = options(admission)
    request["settings"] = {
        key: value if key == "insert_quorum" else str(value)
        for key, value in request["settings"].items()
    }
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        first = session.prepare(**request)
        assert first.settings["async_insert"] == "0"
        request["settings"]["async_insert"] = 0
        with pytest.raises(ValueError, match="intent/settings conflict"):
            session.prepare(**request)
        assert session.load().encode() == first.encode()


@pytest.mark.parametrize("quorum", [0, 2])
def test_sql_pins_exact_synchronous_parallel_quorum(quorum):
    columns = subject._COLUMNS[TABLE]
    expected = (
        f"INSERT INTO property_catalog.{TABLE} ({', '.join(columns)}) "
        f"SETTINGS async_insert=0, insert_quorum={quorum}, "
        "insert_quorum_parallel=1 VALUES"
    )
    assert (
        subject.native_insert_sql("property_catalog", TABLE, columns, quorum=quorum)
        == expected
    )
    if quorum == 0:
        assert subject.native_insert_sql("property_catalog", TABLE, columns) == expected


@pytest.mark.parametrize("quorum", [None, True, False, "2", 2.0, -1, 1 << 64])
def test_sql_quorum_requires_strict_uint64_without_coercion(quorum):
    with pytest.raises(ValueError, match="quorum"):
        subject.native_insert_sql(
            "property_catalog", TABLE, subject._COLUMNS[TABLE], quorum=quorum
        )


def test_prepared_and_decoded_sql_must_match_frozen_protocol_quorum(tmp_path):
    admission = admit(tmp_path, Probe(2))
    request = options(admission)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        wrong_sql = subject.native_insert_sql(
            admission.database, TABLE, request["columns"]
        )
        with pytest.raises(ValueError, match="SQL differs"):
            session.prepare(**request, sql=wrong_sql)
        assert session.load() is None
        pinned = subject.native_insert_sql(
            admission.database, TABLE, request["columns"], quorum=2
        )
        prepared = session.prepare(**request, sql=pinned)
        assert prepared.sql == pinned and prepared.settings["insert_quorum"] == 2
        variants = [
            {"sql": wrong_sql},
            {
                "sql": f"INSERT INTO {admission.database}.{TABLE} ({', '.join(request['columns'])}) VALUES"
            },
            {"settings": {**dict(prepared.settings), "insert_quorum": 0}},
            {"settings": {**dict(prepared.settings), "insert_quorum": "2"}},
            {
                "settings": {
                    key: value
                    for key, value in prepared.settings.items()
                    if key != "insert_quorum"
                }
            },
            {"settings": {**dict(prepared.settings), "insert_quorum_parallel": 0}},
        ]
        for change in variants:
            document = {**json.loads(prepared.encode()), **change}
            # Recomputing the checksum cannot legitimize a conflict between
            # exact statement pins and frozen protocol settings.
            with pytest.raises(ValueError):
                subject._record(document)
        assert session.load().encode() == prepared.encode()


def test_cas_phase_skips_rewinds_and_stale_expected_records_fail(tmp_path):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        prepared = session.prepare(**options(admission))
        with pytest.raises(ValueError, match="unsafe"):
            session.mark_acknowledged(prepared, evidence=ack(prepared))
        sent = session.mark_sent(prepared)
        with pytest.raises(ValueError, match="compare-and-swap"):
            session.mark_sent(prepared)
        with pytest.raises(ValueError, match="unsafe"):
            session.mark_sent(sent)
        with pytest.raises(ValueError, match="unsafe"):
            session.mark_complete(sent, evidence={})
        assert session.load().encode() == sent.encode()
    with pytest.raises(ValueError, match="live exclusive"):
        session.load()


@pytest.mark.parametrize(
    "bad",
    [
        "hash_only",
        "unknown",
        "bool_count",
        "count",
        "query",
        "payload",
        "admission",
        "kind",
        "extra",
    ],
)
def test_ack_needs_full_exact_native_or_positive_query_finish_evidence(tmp_path, bad):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        sent = session.mark_sent(session.prepare(**options(admission)))
        evidence = ack(sent)
        if bad == "hash_only":
            evidence = "a" * 64
        elif bad == "unknown":
            evidence = None
        else:
            key, value = {
                "bool_count": ("written_rows", True),
                "count": ("written_rows", 0),
                "query": ("query_id", str(UUID(int=20))),
                "payload": ("parameters_sha256", "f" * 64),
                "admission": ("admission_sha256", "f" * 64),
                "kind": ("kind", "rows_visible"),
                "extra": ("unreviewed", "claim"),
            }[bad]
            evidence[key] = value
        with pytest.raises(ValueError):
            session.mark_acknowledged(sent, evidence=evidence)
        assert session.load().encode() == sent.encode()


@pytest.mark.parametrize(
    "bad", ["subset", "foreign", "duplicate", "unsorted", "settlement", "row_hash"]
)
def test_complete_requires_exact_full_admitted_members_and_same_settlement(
    tmp_path, bad
):
    admission = admit(tmp_path, Probe(2))
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        sent = session.mark_sent(session.prepare(**options(admission)))
        acknowledged = session.mark_acknowledged(sent, evidence=ack(sent))
        evidence = coverage(acknowledged, admission)
        if bad == "subset":
            evidence["members"].pop()
        elif bad == "foreign":
            evidence["members"][-1] = "foreign"
        elif bad == "duplicate":
            evidence["members"].append(evidence["members"][-1])
        elif bad == "unsorted":
            evidence["members"].reverse()
        elif bad == "settlement":
            evidence["settlement"] = "query_log_finish"
        else:
            evidence["parameters_sha256"] = "f" * 64
        with pytest.raises(ValueError):
            session.mark_complete(acknowledged, evidence=evidence)
        assert session.load().encode() == acknowledged.encode()


def test_lock_filename_binds_bare_table_and_token_without_path_interpretation(tmp_path):
    admission = admit(tmp_path)
    token = "token/with:characters.and-unicode-é"
    with subject.NativeWriteJournal(tmp_path) as journal:
        for table in (TABLE, "property_catalog_activations"):
            with managed_session(journal, table, token) as session:
                session.prepare(**options(admission, table))
                assert path(tmp_path, table, token).exists()
    assert path(tmp_path, TABLE, token) != path(
        tmp_path, "property_catalog_activations", token
    )
    assert len(path(tmp_path, TABLE, token).stem) == 64
    with subject.NativeWriteJournal(tmp_path) as journal:
        for table, bad in (
            ("`" + TABLE + "`", token),
            ("default." + TABLE, token),
            (TABLE, ""),
            (TABLE, "x\n"),
            (TABLE, "x" * 1025),
        ):
            with pytest.raises(ValueError):
                with managed_session(journal, table, bad):
                    pytest.fail("invalid lock opened")


def test_exclusive_flock_is_nonblocking_in_another_process(tmp_path):
    admission = admit(tmp_path)
    code = """import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(23)
finally:
    os.close(fd)
"""
    with subject.NativeWriteJournal(tmp_path) as journal:
        with managed_session(journal, TABLE, TOKEN) as session:
            session.prepare(**options(admission))
            with pytest.raises(subject.NativeWriteJournalBusy):
                with managed_session(journal, TABLE, TOKEN):
                    pytest.fail("duplicate session admitted")
            result = subprocess.run(
                [sys.executable, "-c", code, str(path(tmp_path, extension="lock"))],
                timeout=5,
            )
            assert result.returncode == 23
        result = subprocess.run(
            [sys.executable, "-c", code, str(path(tmp_path, extension="lock"))],
            timeout=5,
        )
        assert result.returncode == 0
    with pytest.raises(ValueError, match="closed"):
        with managed_session(journal, TABLE, TOKEN):
            pass


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "directory", "public", "wrong_owner"]
)
@pytest.mark.parametrize("extension", ["json", "lock"])
def test_unsafe_records_and_locks_never_replaced_or_followed(
    tmp_path, monkeypatch, kind, extension
):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        target = path(tmp_path, extension=extension)
        if kind in {"symlink", "hardlink"}:
            other = tmp_path / "preserve"
            other.write_bytes(b"untouched")
            if kind == "symlink":
                target.symlink_to(other)
            else:
                os.link(other, target)
        elif kind == "fifo":
            os.mkfifo(target, 0o600)
        elif kind == "directory":
            target.mkdir(mode=0o700)
        else:
            target.write_bytes(b"untouched")
            target.chmod(0o644 if kind == "public" else 0o600)
        if kind == "wrong_owner":
            real = subject.os.fstat

            def fstat(fd):
                value = real(fd)
                if (value.st_dev, value.st_ino) == (
                    target.stat().st_dev,
                    target.stat().st_ino,
                ):
                    fields = list(value)
                    fields[4] = value.st_uid + 1
                    return os.stat_result(fields)
                return value

            monkeypatch.setattr(subject.os, "fstat", fstat)
        with pytest.raises((ValueError, OSError)):
            with managed_session(journal, TABLE, TOKEN) as session:
                session.prepare(**options(admission))
        if kind in {"symlink", "hardlink"}:
            assert other.read_bytes() == b"untouched"


@pytest.mark.parametrize("where", ["installation", "journal", "identity", "admission"])
def test_symlinked_directories_or_installed_descriptors_fail_before_attempt(
    tmp_path, where
):
    admit(tmp_path)
    if where == "installation":
        alias = tmp_path / "alias"
        alias.symlink_to(tmp_path, target_is_directory=True)
        target = alias
    else:
        target = tmp_path
        name = {
            "journal": subject.NATIVE_WRITE_ATTEMPT_DIRECTORY,
            "identity": IDENTITY_FILENAME,
            "admission": WRITE_ADMISSION_FILENAME,
        }[where]
        item = tmp_path / name
        if where == "journal":
            real = tmp_path / "other"
            real.mkdir(mode=0o700)
        else:
            real = tmp_path / "original"
            item.rename(real)
        item.symlink_to(real, target_is_directory=where == "journal")
    with pytest.raises((ValueError, OSError)):
        subject.NativeWriteJournal(target)
    assert not path(tmp_path).exists()


def test_missing_or_public_installation_is_not_created_or_repaired(tmp_path):
    with pytest.raises(FileNotFoundError):
        subject.NativeWriteJournal(tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        subject.NativeWriteJournal(tmp_path)
    assert not (tmp_path / subject.NATIVE_WRITE_ATTEMPT_DIRECTORY).exists()
    admit(tmp_path)
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        subject.NativeWriteJournal(tmp_path)
    assert not (tmp_path / subject.NATIVE_WRITE_ATTEMPT_DIRECTORY).exists()


@pytest.mark.parametrize("change", ["replace", "unlink", "public", "hardlink"])
def test_live_lock_tampering_prevents_sent_transition(tmp_path, change):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        prepared = session.prepare(**options(admission))
        lock = path(tmp_path, extension="lock")
        if change == "replace":
            lock.rename(tmp_path / "preserved-lock")
            lock.write_bytes(b"")
            lock.chmod(0o600)
        elif change == "unlink":
            lock.unlink()
        elif change == "public":
            lock.chmod(0o644)
        else:
            os.link(lock, tmp_path / "extra-lock-link")
        with pytest.raises((ValueError, OSError)):
            session.mark_sent(prepared)
        assert path(tmp_path).read_bytes() == prepared.encode()


def test_journal_close_keeps_live_session_descriptors_until_context_exit(tmp_path):
    admission = admit(tmp_path)
    journal = subject.NativeWriteJournal(tmp_path)
    with managed_session(journal, TABLE, TOKEN) as session:
        prepared = session.prepare(**options(admission))
        journal.close()
        assert session.mark_sent(prepared).state == "sent"
    with pytest.raises(ValueError, match="live exclusive"):
        session.load()


@pytest.mark.parametrize(
    "mutation",
    [
        "record_hash",
        "payload",
        "parameter_hash",
        "row_count",
        "typed_int",
        "typed_uuid",
        "typed_time",
        "unknown",
        "duplicate_key",
        "whitespace",
        "no_newline",
    ],
)
def test_tamper_or_noncanonical_payload_fails_without_rewriting(tmp_path, mutation):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        attempt = session.prepare(**options(admission))
        document = json.loads(attempt.encode())
        if mutation == "record_hash":
            document["record_sha256"] = "f" * 64
        elif mutation == "payload":
            document["parameters"][0][0] = ["uuid", str(UUID(int=999))]
        elif mutation == "parameter_hash":
            document["parameters_sha256"] = "f" * 64
        elif mutation == "row_count":
            document["row_count"] = True
        elif mutation.startswith("typed_"):
            document["parameters"][0][0] = {
                "typed_int": ["uint64", "01"],
                "typed_uuid": ["uuid", "not-a-uuid"],
                "typed_time": ["datetime", "2026-09-06T00:00:00Z"],
            }[mutation]
            document["parameters_sha256"] = subject._hash(
                {"columns": document["columns"], "parameters": document["parameters"]}
            )
            document["settings"]["log_comment"] = document["parameters_sha256"]
        elif mutation == "unknown":
            document["extra"] = "forged"
        if mutation != "record_hash":
            document["record_sha256"] = subject._hash(
                {k: v for k, v in document.items() if k != "record_sha256"}
            )
        raw = _canonical(document) + b"\n"
        if mutation == "duplicate_key":
            raw = raw.replace(
                b'{"acknowledgement":', b'{"format":"duplicate","acknowledgement":', 1
            )
        elif mutation == "whitespace":
            raw += b" "
        elif mutation == "no_newline":
            raw = raw[:-1]
        path(tmp_path).write_bytes(raw)
        with pytest.raises(ValueError):
            session.load()
        assert path(tmp_path).read_bytes() == raw


@pytest.mark.parametrize(
    "fault", ["file_fsync", "rename_before", "rename_after", "directory_fsync"]
)
def test_sent_transition_crash_never_dispatches_and_retains_exact_uncertainty(
    tmp_path, monkeypatch, fault
):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        prepared = session.prepare(**options(admission))
        real_sync, real_rename = os.fsync, os.rename
        dispatched = []

        def sync(fd):
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            if (fault == "directory_fsync" and directory) or (
                fault == "file_fsync" and not directory
            ):
                raise OSError("crash at fsync")
            real_sync(fd)

        def rename(*args, **kwargs):
            if fault == "rename_before":
                raise OSError("crash before rename")
            real_rename(*args, **kwargs)
            if fault == "rename_after":
                raise OSError("lost rename ACK")

        with monkeypatch.context() as patch:
            patch.setattr(subject.os, "fsync", sync)
            patch.setattr(subject.os, "rename", rename)
            with pytest.raises(OSError):
                session.mark_sent(prepared)
                dispatched.append("INSERT")
        assert not dispatched
        observed = session.load()
        assert observed.state == (
            "prepared" if fault in {"file_fsync", "rename_before"} else "sent"
        )
        assert (
            observed.query_id == prepared.query_id
            and observed.parameters == prepared.parameters
        )
        assert not list(path(tmp_path).parent.glob("*.tmp"))
        if observed.state == "sent":
            with pytest.raises(ValueError, match="no replay"):
                session.mark_sent(observed)


def test_sent_is_file_and_directory_fsynced_before_caller_can_send(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        managed_session(journal, TABLE, TOKEN) as session,
    ):
        prepared = session.prepare(**options(admission))
        events = []
        real_sync, real_rename = os.fsync, os.rename

        def sync(fd):
            events.append(
                "dir_fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file_fsync"
            )
            real_sync(fd)

        def rename(*args, **kwargs):
            events.append("rename")
            real_rename(*args, **kwargs)

        monkeypatch.setattr(subject.os, "fsync", sync)
        monkeypatch.setattr(subject.os, "rename", rename)
        sent = session.mark_sent(prepared)
        events.append("INSERT")
        assert events == ["file_fsync", "rename", "dir_fsync", "INSERT"]
        assert session.load().encode() == sent.encode()


def test_real_row_and_serialized_byte_bounds(tmp_path):
    admission = admit(tmp_path)
    request = options(admission)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with managed_session(journal, TABLE, TOKEN) as session:
            row = rows()[0]
            request["rows"] = [row] * (subject.MAX_ATTEMPT_ROWS + 1)
            with pytest.raises(ValueError, match="row/column"):
                session.prepare(**request)
            request["rows"] = [row] * subject.MAX_ATTEMPT_ROWS
            maximum = session.prepare(**request)
            assert maximum.row_count == 16384 and len(maximum.encode()) < 24 << 20
        with managed_session(journal, TABLE, "oversize") as session:
            request["rows"] = rows()
            request["rows"][0]["action"] = "x" * (24 << 20)
            with pytest.raises(ValueError, match="exceed"):
                session.prepare(**request)
            assert session.load() is None


def test_pending_unknown_files_are_never_pruned(tmp_path):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        unknown = (
            tmp_path
            / subject.NATIVE_WRITE_ATTEMPT_DIRECTORY
            / ".native-attempt-unknown.tmp"
        )
        unknown.write_bytes(b"preserve unresolved evidence")
        with managed_session(journal, TABLE, TOKEN) as session:
            session.prepare(**options(admission))
        assert unknown.read_bytes() == b"preserve unresolved evidence"


def scope_for(journal, table=TABLE):
    return subject.NativeWriteScope.from_rows(table, rows(table), journal.identity)


def finish(session, attempt, admission):
    if attempt.state == "prepared":
        attempt = session.mark_sent(attempt)
    if attempt.state == "sent":
        attempt = session.mark_acknowledged(attempt, evidence=ack(attempt))
    return session.mark_complete(attempt, evidence=coverage(attempt, admission))


def publication(admission):
    request = options(admission, subject._ACTIVE_TABLE)
    request["rows"][0].update(status="active", revision_fence_sha256="a" * 64)
    binding = subject.NativePublicationBinding(
        deduplication_token="exact-active",
        parameters_sha256=subject.native_parameters_sha256(
            request["columns"], request["rows"]
        ),
        publication_sha256="b" * 64,
        fence_sha256="a" * 64,
        build_lease_sha256="c" * 64,
        checkpoint_state_sha256s=("d" * 64, "e" * 64),
    )
    return request, binding


def test_root_session_is_read_only_even_for_an_existing_managed_attempt(tmp_path):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with managed_session(journal, TABLE, TOKEN) as managed:
            prepared = managed.prepare(**options(admission))
        with journal.session(TABLE, TOKEN) as diagnostic:
            assert diagnostic.load().encode() == prepared.encode()
            for mutation in (
                lambda: diagnostic.prepare(**options(admission)),
                lambda: diagnostic.mark_sent(prepared),
                lambda: diagnostic.mark_acknowledged(prepared, evidence=ack(prepared)),
                lambda: diagnostic.mark_complete(prepared, evidence={}),
            ):
                with pytest.raises(ValueError, match="scoped session"):
                    mutation()
            assert diagnostic.load().encode() == prepared.encode()


@pytest.mark.parametrize(
    "change", ["missing", "corrupt", "symlink", "public", "hardlink"]
)
def test_create_true_never_repairs_existing_scope_index(tmp_path, change):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with pytest.raises(ValueError, match="missing"):
            with journal.scope(scope):
                pass
        with journal.scope(scope, create=True) as scoped:
            index = path(tmp_path).parent / (scoped._key + ".json")
        preserved = index.read_bytes()
        if change == "missing":
            index.unlink()
        elif change == "corrupt":
            index.write_bytes(b"corrupt")
        elif change == "symlink":
            other = index.with_suffix(".preserved")
            index.rename(other)
            index.symlink_to(other)
        elif change == "public":
            index.chmod(0o644)
        else:
            os.link(index, index.with_suffix(".extra"))
        with pytest.raises((ValueError, OSError)):
            with journal.scope(scope, create=True):
                pytest.fail("existing scope reset")
        if change == "missing":
            assert not index.exists()
        else:
            assert index.read_bytes() == (
                b"corrupt" if change == "corrupt" else preserved
            )


def test_existing_scope_create_does_not_rewrite_and_empty_is_positive(
    tmp_path, monkeypatch
):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            assert scoped.pending() == () and scoped.generation == 0
        monkeypatch.setattr(
            subject, "_atomic", lambda *a: pytest.fail("rewrote existing scope")
        )
        with journal.scope(scope, create=True) as scoped:
            assert scoped.pending() == () and scoped.generation == 0


@pytest.mark.parametrize(
    "fault",
    ["registering", "before_attempt", "after_attempt", "before_ready", "after_ready"],
)
def test_full_registering_intent_recovers_exact_prepared_after_crash(
    tmp_path, monkeypatch, fault
):
    admission = admit(tmp_path)
    original_atomic = subject._atomic
    saved = []
    fired = False
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:

            def atomic(directory, name, raw):
                nonlocal fired
                document = json.loads(raw)
                entry = next(iter(document.get("pending", {}).values()), None)
                if entry and entry["stage"] == "registering":
                    saved.append(
                        subject.NativeWriteAttempt(
                            _canonical(entry["prepared"]) + b"\n"
                        )
                    )
                phase = (
                    "attempt"
                    if document.get("format") == subject._FORMAT
                    else entry["stage"]
                    if entry
                    else "other"
                )
                chosen = {
                    "registering": ("registering", "after"),
                    "before_attempt": ("attempt", "before"),
                    "after_attempt": ("attempt", "after"),
                    "before_ready": ("ready", "before"),
                    "after_ready": ("ready", "after"),
                }[fault]
                fail = not fired and phase == chosen[0]
                if fail and chosen[1] == "before":
                    fired = True
                    raise OSError("injected registration crash")
                original_atomic(directory, name, raw)
                if fail:
                    fired = True
                    raise OSError("lost registration ACK")

            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", atomic)
                with pytest.raises(OSError):
                    with scoped.session(TABLE, TOKEN) as session:
                        session.prepare(**options(admission))
        assert fired and saved
        with journal.scope(scope) as scoped:
            pending = scoped.pending()
            assert len(pending) == 1 and pending[0].query_id == saved[0].query_id
            assert scoped.generation == 1
            with scoped.session(TABLE, TOKEN) as session:
                recovered = session.load()
                assert recovered.encode() == saved[0].encode()
                assert recovered.state == "prepared"
                sent = session.mark_sent(recovered)
                with pytest.raises(ValueError, match="no replay"):
                    session.mark_sent(sent)


@pytest.mark.parametrize("change", ["missing", "payload", "scope"])
def test_ready_reference_refuses_missing_or_conflicting_receipt(tmp_path, change):
    admission = admit(tmp_path)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        with scoped.session(TABLE, TOKEN) as session:
            prepared = session.prepare(**options(admission))
        if change == "missing":
            path(tmp_path).unlink()
        else:
            document = json.loads(prepared.encode())
            if change == "payload":
                position = document["columns"].index("action")
                document["parameters"][0][position] = ["str", "tampered"]
            else:
                position = document["columns"].index("workspace_id")
                document["parameters"][0][position] = ["uuid", str(UUID(int=999))]
                document["scope"]["workspace_id"] = str(UUID(int=999))
            document["parameters_sha256"] = subject._hash(
                {"columns": document["columns"], "parameters": document["parameters"]}
            )
            document["settings"]["log_comment"] = document["parameters_sha256"]
            path(tmp_path).write_bytes(subject._record(document).encode())
        before = path(tmp_path).read_bytes() if path(tmp_path).exists() else None
        with pytest.raises(ValueError):
            scoped.pending()
        assert (
            path(tmp_path).read_bytes() if path(tmp_path).exists() else None
        ) == before


def test_complete_receipt_precedes_pending_reference_removal_and_recovers(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    original = subject._atomic
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                sent = session.mark_sent(session.prepare(**options(admission)))
                acknowledged = session.mark_acknowledged(sent, evidence=ack(sent))

                def atomic(directory, name, raw):
                    if name.endswith(".scope.json"):
                        assert (
                            subject.NativeWriteAttempt(
                                path(tmp_path).read_bytes()
                            ).state
                            == "complete"
                        )
                        raise OSError("crash before reference removal")
                    original(directory, name, raw)

                with monkeypatch.context() as patch:
                    patch.setattr(subject, "_atomic", atomic)
                    with pytest.raises(OSError):
                        session.mark_complete(
                            acknowledged, evidence=coverage(acknowledged, admission)
                        )
        complete = path(tmp_path).read_bytes()
        with journal.scope(scope) as scoped:
            assert [entry.deduplication_token for entry in scoped.pending()] == [TOKEN]
            assert scoped._read_index()["version"] == 4
            assert scoped.generation == 1
        assert path(tmp_path).read_bytes() == complete


def test_pending_bound_is_real_complete_and_scope_local(tmp_path, monkeypatch):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            for i in range(128):
                with scoped.session(TABLE, f"token-{i}") as session:
                    session.prepare(**options(admission))
            with scoped.session(TABLE, "overflow") as session:
                with pytest.raises(ValueError, match="bound"):
                    session.prepare(**options(admission))
                assert session.load() is None
            with pytest.raises(ValueError, match="bound"):
                scoped.pending(limit=127)
            monkeypatch.setattr(
                subject.os, "listdir", lambda *a: pytest.fail("global scan")
            )
            monkeypatch.setattr(
                subject.os, "scandir", lambda *a: pytest.fail("global scan")
            )
            assert len(scoped.pending(limit=128)) == 128 and scoped.generation == 128
        other = replace(scope, workspace_id=str(UUID(int=44)))
        with journal.scope(other, create=True) as scoped:
            assert scoped.pending() == ()


def test_closure_holds_scope_excludes_new_dependency_and_invalidates_after_new_generation(
    tmp_path,
):
    admission = admit(tmp_path)
    request, binding = publication(admission)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            closing = scoped.begin_closure(binding)
            assert closing.generation == 0
            with pytest.raises(subject.NativeWriteJournalBusy):
                with journal.scope(scope):
                    pass
            with scoped.session(TABLE, "racing-dependency") as session:
                with pytest.raises(ValueError, match="closing"):
                    session.prepare(**options(admission))
                with pytest.raises(ValueError, match="nested"):
                    scoped.pending()
            with scoped.session(
                subject._ACTIVE_TABLE, binding.deduplication_token
            ) as session:
                prepared = session.prepare(**request)
                active = finish(session, prepared, admission)
            closed = scoped.finish_closure(closing, active)
            assert closed.completed_generation == scoped.generation == 1
            scoped.require_closed(closed)
        with journal.scope(scope) as scoped:
            assert scoped.begin_closure(binding) == closed
            scoped.require_closed(closed)
            with scoped.session(TABLE, "finalization") as session:
                finalization = session.prepare(**options(admission))
                finish(session, finalization, admission)
            assert scoped.generation == 2
            with pytest.raises(ValueError, match="stale"):
                scoped.require_closed(closed)
            renewed = scoped.begin_closure(binding)
            renewed = scoped.finish_closure(renewed, active)
            scoped.require_closed(renewed)
            assert renewed.completed_generation == 2


def test_closure_restarts_exactly_and_sent_active_is_not_completion(tmp_path):
    admission = admit(tmp_path)
    request, binding = publication(admission)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                dependency = session.prepare(**options(admission))
            with pytest.raises(ValueError, match="unresolved"):
                scoped.begin_closure(binding)
            with scoped.session(TABLE, TOKEN) as session:
                finish(session, dependency, admission)
            closing = scoped.begin_closure(binding)
            with scoped.session(
                subject._ACTIVE_TABLE, binding.deduplication_token
            ) as session:
                sent = session.mark_sent(session.prepare(**request))
        with journal.scope(scope) as scoped:
            assert scoped.begin_closure(binding) == closing
            with pytest.raises(ValueError, match="binding"):
                scoped.begin_closure(replace(binding, fence_sha256="f" * 64))
            with pytest.raises(ValueError, match="pending"):
                scoped.finish_closure(closing, sent)
            with scoped.session(
                subject._ACTIVE_TABLE, binding.deduplication_token
            ) as session:
                assert session.load().encode() == sent.encode()
                with pytest.raises(ValueError, match="no replay"):
                    session.mark_sent(sent)
                acknowledged = session.mark_acknowledged(
                    sent, evidence=ack(sent, "query_log_finish")
                )
                active = session.mark_complete(
                    acknowledged, evidence=coverage(acknowledged, admission)
                )
            closed = scoped.finish_closure(closing, active)
            scoped.require_closed(closed)


@pytest.mark.parametrize(
    "field,value",
    [
        ("workspace_id", str(UUID(int=8))),
        ("catalog_epoch", True),
        ("catalog_epoch", 2),
        ("projection_version", 2),
        ("target_build_token", "not-uuid"),
        ("target_catalog_revision", 0),
    ],
)
def test_scope_validates_every_row_and_installed_identity(tmp_path, field, value):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        values = [rows()[0], {**rows()[0], field: value}]
        with pytest.raises(ValueError):
            subject.NativeWriteScope.from_rows(TABLE, values, journal.identity)


def test_targetless_disable_remains_control_scope_not_fake_build(tmp_path):
    admission = admit(tmp_path)
    request = options(admission)
    request["rows"][0].update(
        action="disable", target_catalog_revision=0, target_build_token=str(UUID(int=0))
    )
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = subject.NativeWriteScope.from_rows(
            TABLE, request["rows"], journal.identity
        )
        assert (
            scope.kind == "control"
            and scope.build_token is None
            and scope.catalog_revision is None
        )
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                assert session.prepare(**request).state == "prepared"
            with pytest.raises(ValueError, match="build publication"):
                scoped.begin_closure(publication(admission)[1])
        request["rows"][0]["action"] = "follow"
        with pytest.raises(ValueError, match="DISABLE"):
            subject.NativeWriteScope.from_rows(TABLE, request["rows"], journal.identity)


def test_registering_key_cannot_be_claimed_by_a_foreign_scope(tmp_path, monkeypatch):
    admission = admit(tmp_path)
    original = subject._atomic
    with subject.NativeWriteJournal(tmp_path) as journal:
        first_scope = scope_for(journal)
        with journal.scope(first_scope, create=True) as scoped:

            def crash_after_registration(directory, name, raw):
                original(directory, name, raw)
                document = json.loads(raw)
                if any(
                    entry["stage"] == "registering"
                    for entry in document.get("pending", {}).values()
                ):
                    raise OSError("REGISTERING durable; no attempt yet")

            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", crash_after_registration)
                with pytest.raises(OSError):
                    with scoped.session(TABLE, TOKEN) as session:
                        session.prepare(**options(admission))
        assert not path(tmp_path).exists()
        other = replace(first_scope, workspace_id=str(UUID(int=123)))
        with journal.scope(other, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                with pytest.raises(ValueError, match="different scope"):
                    session.load()
                request = options(admission)
                request["rows"][0]["workspace_id"] = other.workspace_id
                with pytest.raises(ValueError, match="different scope"):
                    session.prepare(**request)
            assert scoped.pending() == () and scoped.generation == 0
        with journal.scope(first_scope) as scoped:
            assert len(scoped.pending()) == 1
            with scoped.session(TABLE, TOKEN) as session:
                assert session.load().state == "prepared"


def test_registering_rejects_unexpected_sent_instead_of_rewinding(
    tmp_path, monkeypatch
):
    admission = admit(tmp_path)
    original = subject._atomic
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:

            def fail_ready(directory, name, raw):
                document = json.loads(raw)
                if any(
                    entry["stage"] == "ready"
                    for entry in document.get("pending", {}).values()
                ):
                    raise OSError("READY not written")
                original(directory, name, raw)

            with monkeypatch.context() as patch:
                patch.setattr(subject, "_atomic", fail_ready)
                with pytest.raises(OSError):
                    with scoped.session(TABLE, TOKEN) as session:
                        session.prepare(**options(admission))
        document = json.loads(path(tmp_path).read_bytes())
        document["state"] = "sent"
        sent = subject._record(document)
        path(tmp_path).write_bytes(sent.encode())
        with journal.scope(scope) as scoped:
            with pytest.raises(ValueError, match="never Sent"):
                scoped.pending()
        assert path(tmp_path).read_bytes() == sent.encode()


@pytest.mark.parametrize(
    "change", ["scope_type", "version", "generation", "unknown", "checksum"]
)
def test_scope_index_tampering_fails_even_with_recomputed_checksum(tmp_path, change):
    admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            index = path(tmp_path).parent / (scoped._key + ".json")
        document = json.loads(index.read_bytes())
        if change == "scope_type":
            document["scope"]["catalog_epoch"] = True
        elif change == "version":
            document["version"] = True
        elif change == "generation":
            document["generation"] = True
        elif change == "unknown":
            document["unexpected"] = "claim"
        document["sha256"] = (
            "f" * 64
            if change == "checksum"
            else subject._hash({k: v for k, v in document.items() if k != "sha256"})
        )
        raw = _canonical(document) + b"\n"
        index.write_bytes(raw)
        with pytest.raises(ValueError):
            with journal.scope(scope, create=True):
                pass
        assert index.read_bytes() == raw


@pytest.mark.parametrize("change", ["missing", "foreign"])
def test_scope_binding_is_required_after_ready(tmp_path, change):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            with scoped.session(TABLE, TOKEN) as session:
                session.prepare(**options(admission))
        binding = path(tmp_path, extension="scope-binding")
        if change == "missing":
            binding.unlink()
        else:
            binding.write_bytes(b"foreign")
        with journal.scope(scope) as scoped:
            with pytest.raises(ValueError, match="scope binding|different scope"):
                scoped.pending()


@pytest.mark.parametrize("change", ["wrong_fence", "building", "two_rows"])
def test_closure_reservation_cannot_dispatch_non_active_or_foreign_fence(
    tmp_path, change
):
    admission = admit(tmp_path)
    request, binding = publication(admission)
    if change == "wrong_fence":
        request["rows"][0]["revision_fence_sha256"] = "f" * 64
    elif change == "building":
        request["rows"][0]["status"] = "building"
    else:
        request["rows"] *= 2
    binding = replace(
        binding,
        parameters_sha256=subject.native_parameters_sha256(
            request["columns"], request["rows"]
        ),
    )
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        scoped.begin_closure(binding)
        with scoped.session(
            subject._ACTIVE_TABLE, binding.deduplication_token
        ) as session:
            with pytest.raises(ValueError, match="one exact ACTIVE"):
                session.prepare(**request)
            assert session.load() is None
        assert scoped.generation == 0


def test_scope_lock_is_nonblocking_across_processes_and_other_scopes_progress(tmp_path):
    admit(tmp_path)
    code = """import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(23)
finally:
    os.close(fd)
"""
    with subject.NativeWriteJournal(tmp_path) as journal:
        scope = scope_for(journal)
        with journal.scope(scope, create=True) as scoped:
            target = path(tmp_path).parent / (scoped._key + ".lock")
            result = subprocess.run(
                [sys.executable, "-c", code, str(target)], timeout=5
            )
            assert result.returncode == 23
            with journal.scope(
                replace(scope, workspace_id=str(UUID(int=444))), create=True
            ) as other:
                assert other.pending() == ()
        result = subprocess.run([sys.executable, "-c", code, str(target)], timeout=5)
        assert result.returncode == 0


def test_preexisting_prepared_active_is_exact_reserved_exception_not_other_pending(
    tmp_path,
):
    admission = admit(tmp_path)
    request, binding = publication(admission)
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        with scoped.session(
            subject._ACTIVE_TABLE, binding.deduplication_token
        ) as session:
            prepared = session.prepare(**request)
        closing = scoped.begin_closure(binding)
        assert closing.generation == 1 and len(scoped.pending()) == 1
        with scoped.session(
            subject._ACTIVE_TABLE, binding.deduplication_token
        ) as session:
            assert session.load().encode() == prepared.encode()
            complete = finish(session, prepared, admission)
        closed = scoped.finish_closure(closing, complete)
        scoped.require_closed(closed)
        assert closed.completed_generation == 1


def test_preexisting_nonactive_receipt_cannot_be_reserved_by_payload_hash(tmp_path):
    admission = admit(tmp_path)
    request, binding = publication(admission)
    request["rows"][0]["status"] = "building"
    binding = replace(
        binding,
        parameters_sha256=subject.native_parameters_sha256(
            request["columns"], request["rows"]
        ),
    )
    with (
        subject.NativeWriteJournal(tmp_path) as journal,
        journal.scope(scope_for(journal), create=True) as scoped,
    ):
        with scoped.session(
            subject._ACTIVE_TABLE, binding.deduplication_token
        ) as session:
            prepared = session.prepare(**request)
        with pytest.raises(ValueError, match="one exact ACTIVE"):
            scoped.begin_closure(binding)
        with scoped.session(
            subject._ACTIVE_TABLE, binding.deduplication_token
        ) as session:
            assert session.load().encode() == prepared.encode()


def test_unreleased_v1_receipt_is_rejected_not_upgraded_or_adopted(tmp_path):
    admission = admit(tmp_path)
    with subject.NativeWriteJournal(tmp_path) as journal:
        with managed_session(journal, TABLE, TOKEN) as session:
            prepared = session.prepare(**options(admission))
        document = json.loads(prepared.encode())
        document["version"] = 1
        del document["scope"]
        document["record_sha256"] = subject._hash(
            {key: value for key, value in document.items() if key != "record_sha256"}
        )
        raw = _canonical(document) + b"\n"
        path(tmp_path).write_bytes(raw)
        with journal.session(TABLE, TOKEN) as diagnostic:
            with pytest.raises(ValueError):
                diagnostic.load()
        with managed_session(journal, TABLE, TOKEN) as session:
            with pytest.raises(ValueError):
                session.prepare(**options(admission))
        assert path(tmp_path).read_bytes() == raw
