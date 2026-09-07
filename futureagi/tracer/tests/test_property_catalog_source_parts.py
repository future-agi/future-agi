"""Committed-part repair notices do not trust import/version wall clocks."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tracer.services.clickhouse.v2.property_catalog import source_parts as subject
from tracer.services.clickhouse.v2.property_catalog.coordinator import (
    FileSupersessionJournal,
)
from tracer.services.clickhouse.v2.property_catalog.source_repair import (
    pending_source_repair,
)
from tracer.tests.test_property_catalog_source_repair import (
    EVENT,
    NOW,
    PROJECT,
    probe,
    scope,
)

OLD = (("20260906_1_1_0", "abc"),)
NEW = (*OLD, ("20260906_2_2_0", "def"))


def run_notice(
    tmp_path, parts=OLD, *, full=False, start=None, event=EVENT, reader=None
):
    calls = []
    reader = reader or SimpleNamespace(
        parts_snapshot=lambda: parts,
        history_in_parts=lambda **kw: calls.append(kw) or event,
    )
    result = subject.notice_part_changes(
        **scope(tmp_path),
        reader=reader,
        project_ids=(PROJECT,),
        since=EVENT - timedelta(days=1),
        until=NOW - timedelta(minutes=1),
        observed_at=NOW,
        full_replacement=full,
        started_parts=start,
    )
    return result, calls


def pending(tmp_path):
    return pending_source_repair(**scope(tmp_path))


@pytest.mark.parametrize("event", [EVENT, None], ids=["historical", "unrelated"])
def test_preplanning_probe_never_advances_inventory(tmp_path, event):
    run_notice(tmp_path, full=True, start=OLD)
    journal = FileSupersessionJournal(str(tmp_path))
    identity = scope(tmp_path)
    key = (
        f"source-parts:{identity['organization_id']}:"
        f"{identity['workspace_id']}:{identity['source_database']}"
    )
    baseline = journal.load_record(key)
    reader = SimpleNamespace(
        parts_snapshot=Mock(return_value=NEW),
        history_in_parts=Mock(return_value=event),
    )
    assert subject.notice_part_changes(
        **identity,
        reader=reader,
        project_ids=(PROJECT,),
        since=EVENT - timedelta(days=1),
        until=NOW - timedelta(minutes=1),
        observed_at=NOW,
        full_replacement=False,
        started_parts=None,
        positive_only=True,
    ) is (event is not None)
    assert journal.load_record(key) == baseline
    reader.history_in_parts.assert_called_once_with(
        project_ids=(PROJECT,),
        since=EVENT - timedelta(days=1),
        until=NOW - timedelta(minutes=1),
        part_names=(NEW[-1][0],),
    )
    notice = pending(tmp_path)
    assert (notice is not None) is (event is not None)
    if notice is not None:
        assert not notice.path.with_name(notice.path.name + ".ack").exists()
    # The ordinary post-scan watch must still see the unacknowledged new part.
    assert run_notice(tmp_path, NEW)[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"positive_only": 1},
        {"full_replacement": True},
        {"started_parts": OLD},
        {"scan_since": EVENT},
        {"scope_audit_check": lambda **_: True},
    ],
)
def test_preplanning_cannot_claim_completed_scan_coverage(tmp_path, changes):
    reader = SimpleNamespace(parts_snapshot=Mock(), history_in_parts=Mock())
    options = {
        **scope(tmp_path),
        "reader": reader,
        "project_ids": (PROJECT,),
        "since": EVENT - timedelta(days=1),
        "until": NOW,
        "observed_at": NOW,
        "full_replacement": False,
        "started_parts": None,
        "positive_only": True,
        **changes,
    }
    with pytest.raises(ValueError, match="positive-only observation"):
        subject.notice_part_changes(**options)
    reader.parts_snapshot.assert_not_called()
    reader.history_in_parts.assert_not_called()


def test_fresh_full_build_baselines_without_fabricated_repair(tmp_path):
    assert run_notice(tmp_path, full=True, start=OLD) == (False, [])
    assert pending(tmp_path) is None
    # A new Python object/restart reads the same durable inventory.
    assert run_notice(tmp_path) == (False, [])


def test_missing_inventory_on_existing_or_resumed_fenced_build_requests_repair(
    tmp_path,
):
    assert run_notice(tmp_path, full=True, start=None)[0]
    assert pending(tmp_path) is not None


def test_new_part_is_probed_without_reference_to_source_version(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)
    changed, calls = run_notice(tmp_path, NEW)
    assert changed
    assert calls == [
        {
            "project_ids": (PROJECT,),
            "since": EVENT - timedelta(days=1),
            "until": NOW - timedelta(minutes=1),
            "part_names": (NEW[-1][0],),
        }
    ]
    assert pending(tmp_path) is not None
    assert run_notice(tmp_path, NEW) == (False, [])


def test_unrelated_or_future_part_does_not_force_historical_repair(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)
    assert not run_notice(tmp_path, NEW, event=None)[0]
    assert pending(tmp_path) is None


def test_negative_probe_cannot_acknowledge_a_part_replaced_during_its_read(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)
    merged = (("20260906_1_2_1", "123"),)
    reader = SimpleNamespace(
        parts_snapshot=Mock(side_effect=[NEW, merged]),
        # The requested part no longer exists when the source query starts.
        history_in_parts=Mock(return_value=None),
    )
    assert run_notice(tmp_path, reader=reader)[0], (
        "an empty query of a disappeared part is not proof of unchanged history"
    )
    assert pending(tmp_path) is not None


def test_failed_post_probe_inventory_read_cannot_acknowledge_new_parts(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)
    reader = SimpleNamespace(
        parts_snapshot=Mock(side_effect=[NEW, TimeoutError("inventory recheck")]),
        history_in_parts=Mock(return_value=None),
    )
    with pytest.raises(TimeoutError, match="inventory recheck"):
        run_notice(tmp_path, reader=reader)
    assert run_notice(tmp_path, NEW)[0]


def test_unchanged_inventory_needs_no_extra_metadata_read(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)
    reader = SimpleNamespace(
        parts_snapshot=Mock(return_value=OLD), history_in_parts=Mock()
    )
    assert not run_notice(tmp_path, reader=reader)[0]
    reader.parts_snapshot.assert_called_once()
    reader.history_in_parts.assert_not_called()


@pytest.mark.parametrize(
    "parts", [(), ((OLD[0][0], "123"),), (("20260906_1_2_1", "abc"),)]
)
def test_removal_checksum_change_and_merge_are_conservatively_noticed(tmp_path, parts):
    run_notice(tmp_path, full=True, start=OLD)
    assert run_notice(tmp_path, parts)[0]
    assert pending(tmp_path) is not None


def test_full_build_compares_start_not_stale_prior_inventory(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)
    assert run_notice(tmp_path, NEW, full=True, start=NEW) == (False, [])
    assert pending(tmp_path) is None


def test_change_during_full_build_is_not_acknowledged_as_covered(tmp_path):
    assert run_notice(tmp_path, NEW, full=True, start=OLD)[0]
    notice = pending(tmp_path)
    assert not notice.acknowledge_replacement(
        since=EVENT, until=NOW - timedelta(seconds=1)
    )


def test_post_audit_commit_inside_increment_cannot_be_acknowledged_without_repair(
    tmp_path,
):
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        CheckedInPropertyCatalogDevRuntime,
    )
    from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
        LifecycleRunMode,
        ReservationStatus,
    )

    run_notice(tmp_path, full=True, start=OLD)
    window = SimpleNamespace(since=NOW - timedelta(minutes=1), until=NOW)
    committed_event = NOW - timedelta(seconds=30)
    calls = []

    def probe_changed_part(**kwargs):
        calls.append(kwargs)
        return (
            committed_event
            if kwargs["since"] <= committed_event < kwargs["until"]
            else None
        )

    runtime = SimpleNamespace(
        _source_capture=None,
        config=SimpleNamespace(span_since=EVENT - timedelta(days=1)),
        span_reader=SimpleNamespace(
            parts_snapshot=lambda: NEW, history_in_parts=probe_changed_part
        ),
        _source_repair_scope=lambda: scope(tmp_path),
        _source_parts_at_start=OLD,
        now=lambda: NOW,
    )
    execution = SimpleNamespace(
        prepared=SimpleNamespace(
            mode=LifecycleRunMode.INCREMENTAL,
            cutoffs=SimpleNamespace(span_window=window),
            scope=SimpleNamespace(project_ids=(PROJECT,)),
            reservation_status=ReservationStatus.OPEN,
        )
    )
    CheckedInPropertyCatalogDevRuntime._notice_source_part_changes(runtime, execution)
    assert pending(tmp_path) is not None, (
        "post-audit commit was silently acknowledged outside historical-only probe"
    )
    assert any(k["since"] <= committed_event < k["until"] for k in calls)


def test_capture_watch_detects_arrival_older_than_discovered_history(tmp_path):
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        CheckedInPropertyCatalogDevRuntime,
    )
    from tracer.services.clickhouse.v2.property_catalog.durable_lifecycle import (
        LifecycleRunMode,
        ReservationStatus,
    )

    old_event = EVENT - timedelta(days=800)
    calls = []

    def probe_changed_part(**kwargs):
        calls.append(kwargs)
        return old_event if kwargs["since"] <= old_event < kwargs["until"] else None

    runtime = SimpleNamespace(
        _source_capture=object(),
        config=SimpleNamespace(span_since=EVENT),
        span_reader=SimpleNamespace(
            parts_snapshot=lambda: NEW, history_in_parts=probe_changed_part
        ),
        _source_repair_scope=lambda: scope(tmp_path),
        _source_parts_at_start=OLD,
        now=lambda: NOW,
    )
    execution = SimpleNamespace(
        prepared=SimpleNamespace(
            mode=LifecycleRunMode.FULL_REPAIR,
            cutoffs=SimpleNamespace(
                span_window=SimpleNamespace(since=EVENT, until=NOW)
            ),
            scope=SimpleNamespace(project_ids=(PROJECT,)),
            reservation_status=ReservationStatus.OPEN,
        )
    )
    CheckedInPropertyCatalogDevRuntime._notice_source_part_changes(runtime, execution)
    assert pending(tmp_path) is not None
    assert calls[0]["since"] < old_event


def test_probe_failure_does_not_advance_inventory(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)

    def fail(**kwargs):
        raise TimeoutError("part probe")

    with pytest.raises(TimeoutError):
        run_notice(
            tmp_path,
            reader=SimpleNamespace(parts_snapshot=lambda: NEW, history_in_parts=fail),
        )
    assert run_notice(tmp_path, NEW)[0]


@pytest.mark.parametrize(
    "start,event,expected",
    [
        (NEW, NOW - timedelta(seconds=30), False),  # Already present before scan.
        (OLD, NOW - timedelta(seconds=30), True),  # Committed during/after audit.
        (NEW, EVENT, True),  # Prior history was not part of this increment.
        (None, NOW - timedelta(seconds=30), True),  # Resumed without scan proof.
        (OLD, NOW + timedelta(seconds=30), False),  # Future increment will scan it.
    ],
)
def test_increment_distinguishes_preexisting_current_rows_from_uncovered_commits(
    tmp_path, start, event, expected
):
    run_notice(tmp_path, full=True, start=OLD)
    reader = SimpleNamespace(
        parts_snapshot=lambda: NEW,
        history_in_parts=lambda **kw: (
            event if kw["since"] <= event < kw["until"] else None
        ),
    )
    result = subject.notice_part_changes(
        **scope(tmp_path),
        reader=reader,
        project_ids=(PROJECT,),
        since=EVENT - timedelta(days=1),
        until=NOW,
        scan_since=NOW - timedelta(minutes=1),
        observed_at=NOW,
        full_replacement=False,
        started_parts=start,
    )
    assert result is expected
    assert (pending(tmp_path) is not None) is expected


def test_current_interval_probe_failure_preserves_unacknowledged_part(tmp_path):
    run_notice(tmp_path, full=True, start=OLD)
    boundary = NOW - timedelta(minutes=1)

    def fails_only_current(**kw):
        if kw["since"] == boundary:
            raise TimeoutError("current interval unavailable")
        return None

    options = dict(
        **scope(tmp_path),
        project_ids=(PROJECT,),
        since=EVENT - timedelta(days=1),
        until=NOW,
        scan_since=boundary,
        observed_at=NOW,
        full_replacement=False,
        started_parts=OLD,
    )
    with pytest.raises(TimeoutError, match="current interval"):
        subject.notice_part_changes(
            **options,
            reader=SimpleNamespace(
                parts_snapshot=lambda: NEW, history_in_parts=fails_only_current
            ),
        )
    reader = SimpleNamespace(
        parts_snapshot=lambda: NEW,
        history_in_parts=lambda **kw: (
            NOW - timedelta(seconds=30) if kw["since"] == boundary else None
        ),
    )
    assert subject.notice_part_changes(**options, reader=reader)
    assert pending(tmp_path) is not None


def test_notice_fsync_failure_does_not_advance_inventory(tmp_path, monkeypatch):
    run_notice(tmp_path, full=True, start=OLD)
    original = subject.record_source_repair
    monkeypatch.setattr(
        subject,
        "record_source_repair",
        lambda **_: (_ for _ in ()).throw(OSError("fsync")),
    )
    with pytest.raises(OSError):
        run_notice(tmp_path, NEW)
    monkeypatch.setattr(subject, "record_source_repair", original)
    assert run_notice(tmp_path, NEW)[0]


def test_inventory_failure_leaves_repair_pending(tmp_path, monkeypatch):
    run_notice(tmp_path, full=True, start=OLD)
    monkeypatch.setattr(
        FileSupersessionJournal,
        "save_record",
        lambda *args: (_ for _ in ()).throw(OSError("inventory fsync")),
    )
    with pytest.raises(OSError):
        run_notice(tmp_path, NEW)
    assert pending(tmp_path) is not None


@pytest.mark.parametrize(
    "parts",
    [
        None,
        [("../part", "abc")],
        [("p", "not-a-checksum")],
        [OLD[0], OLD[0]],
        list(reversed(NEW)),
        [("p", "a", 3)],
    ],
)
def test_inventory_shape_is_strict(parts):
    with pytest.raises(ValueError):
        subject.checked_parts(parts)


def test_part_probe_has_no_timestamp_version_or_deleted_row_exclusion():
    reader, calls, _ = probe(
        [{"project_id_text": PROJECT, "seen_at_us": int(EVENT.timestamp() * 1e6)}]
    )
    assert (
        reader.history_in_parts(
            project_ids=(PROJECT,), since=EVENT, until=NOW, part_names=(OLD[0][0],)
        )
        == EVENT
    )
    sql, params, options = calls[0]
    assert "_part IN %(catalog_part_names)s" in sql
    assert "_version" not in sql and "is_deleted" not in sql and "FINAL" not in sql
    assert params["catalog_part_names"] == (OLD[0][0],)
    assert options["timeout_ms"] <= 5000
    assert options["settings"]["read_overflow_mode"] == "throw"


@pytest.mark.parametrize(
    "row",
    [
        {"project_id_text": "foreign", "seen_at_us": 1},
        {"project_id_text": PROJECT, "seen_at_us": 1},
    ],
)
def test_part_probe_rejects_cross_scope_rows(row):
    reader, _, _ = probe([row])
    with pytest.raises(RuntimeError):
        reader.history_in_parts(
            project_ids=(PROJECT,), since=EVENT, until=NOW, part_names=(OLD[0][0],)
        )


def test_native_metadata_method_is_exact_and_does_not_open_general_system_reads():
    from tracer.services.clickhouse.v2.property_catalog.dev_runtime import (
        NativeSourceClient,
    )

    calls = []

    def execute(sql, params, **kwargs):
        calls.append((sql, params, kwargs))
        return (
            [(OLD[0][0], OLD[0][1])],
            [("name", "String"), ("checksum", "String")],
            {},
        )

    driver = SimpleNamespace(
        database="default", server_enforced_readonly=True, execute_read=execute
    )
    client = NativeSourceClient(
        driver, source_database="default", catalog_database="property_catalog_dev"
    )
    assert client.source_parts(timeout_ms=1000) == (
        {"name": OLD[0][0], "checksum": OLD[0][1]},
    )
    sql, params, options = calls[0]
    assert "database=%(source_database)s AND table=%(source_table)s" in sql
    assert params["source_database"] == "default"
    assert params["source_table"] == "spans"
    assert options["settings"]["readonly"] == 2
    assert options["settings"]["result_overflow_mode"] == "throw"
    with pytest.raises(RuntimeError, match="only the exact bound source table"):
        client.query(
            "SELECT name FROM `system`.`parts`", {}, timeout_ms=1000, settings={}
        )
    assert len(calls) == 1
    driver.database = "other"
    with pytest.raises(RuntimeError, match="changed databases"):
        client.source_parts(timeout_ms=1000)
    assert len(calls) == 1
