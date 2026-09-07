"""Initial reservation intent survives lost replies and pending-index cleanup."""

import json
from dataclasses import replace
from uuid import UUID

import pytest

from tracer.services.clickhouse.v2.property_catalog.coordinator import _row_lease
from tracer.services.clickhouse.v2.property_catalog.native_write_journal import (
    NativeWriteJournalError,
    NativeWriteScope,
    NativeWriteScopeSession,
    native_parameters_sha256,
)
from tracer.services.clickhouse.v2.property_catalog.native_write_proof import _COLUMNS
from tracer.services.clickhouse.v2.property_catalog.write_admission import _canonical
from tracer.tests.test_property_catalog_native_supersession_receipt import (
    TABLE,
    fixture_setup,
)


def load_reservation_seed(writer, scope):
    with writer._journal() as journal, journal.scope(scope) as scoped:
        row, token = scoped.reservation_seed()
    return row, _row_lease(row), token


@pytest.mark.parametrize("lost_reply", [False, True])
@pytest.mark.parametrize("native_uuid", [False, True])
def test_seed_exists_before_dispatch_and_survives_restart(
    tmp_path, monkeypatch, lost_reply, native_uuid
):
    writer, proof, transport, h, options = fixture_setup(tmp_path, monkeypatch)
    row = dict(options["rows"][0])
    if native_uuid:
        for field in (
            "organization_id",
            "workspace_id",
            "build_token",
            "producer_stream_id",
        ):
            row[field] = UUID(str(row[field]))
    options = {**options, "rows": (row,)}
    scope = NativeWriteScope.from_rows(TABLE, (row,), writer.proof.identity)
    saved = []
    retain = NativeWriteScopeSession._retain_reservation_seed

    def capture(self, attempt):
        retain(self, attempt)
        saved.append(attempt.parameters_sha256)

    monkeypatch.setattr(NativeWriteScopeSession, "_retain_reservation_seed", capture)

    def send(*args, **kwargs):
        assert saved == [native_parameters_sha256(_COLUMNS[TABLE], (row,))]
        kwargs["before_send"]()
        if lost_reply:
            raise TimeoutError("original reply lost")
        return len(kwargs["values"])

    transport.side_effect = send
    if lost_reply:
        with pytest.raises(TimeoutError, match="original reply lost"):
            writer.insert(**options)
    else:
        writer.insert(**options)
        with writer._journal() as journal, journal.scope(scope) as scoped:
            assert not scoped.pending()
    h.restart()
    restored, lease, token = load_reservation_seed(writer, scope)
    assert native_parameters_sha256(
        _COLUMNS[TABLE], (restored,)
    ) == native_parameters_sha256(_COLUMNS[TABLE], (row,))
    assert lease == h.build.lease
    assert token == options["deduplication_token"]
    assert transport.call_count == 1


def test_changed_initial_seed_fails_before_second_dispatch(tmp_path, monkeypatch):
    writer, _, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    row = {**options["rows"][0], "gap_count": 1, "gap_reasons": ["changed"]}
    with pytest.raises(NativeWriteJournalError):
        writer.insert(**{**options, "rows": (row,)})
    assert transport.call_count == 1


def test_seed_cannot_cross_workspace_or_admission(tmp_path, monkeypatch):
    writer, _, transport, _, options = fixture_setup(tmp_path, monkeypatch)
    writer.insert(**options)
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], writer.proof.identity)
    with pytest.raises(NativeWriteJournalError, match="missing initialized scope"):
        load_reservation_seed(writer, replace(scope, workspace_id=str(UUID(int=98))))
    with writer._journal() as journal, journal.scope(scope) as scoped:
        path = tmp_path / "native-write-attempts" / (scoped._key + ".reservation")
    document = json.loads(path.read_bytes())
    document["admission_sha256"] = "e" * 64
    path.write_bytes(_canonical(document) + b"\n")
    with pytest.raises(NativeWriteJournalError, match="seed binding differs"):
        load_reservation_seed(writer, scope)
    assert transport.call_count == 1


def test_seed_archive_failure_prevents_prepare_or_send(tmp_path, monkeypatch):
    writer, _, transport, _, options = fixture_setup(tmp_path, monkeypatch)

    def fail(*args, **kwargs):
        raise OSError("archive unavailable")

    monkeypatch.setattr(NativeWriteScopeSession, "_retain_reservation_seed", fail)
    with pytest.raises(OSError, match="archive unavailable"):
        writer.insert(**options)
    transport.assert_not_called()
    scope = NativeWriteScope.from_rows(TABLE, options["rows"], writer.proof.identity)
    with writer._journal() as journal, journal.scope(scope) as scoped:
        assert not scoped.pending()
        assert scoped.generation == 0
