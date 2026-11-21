"""Native CH25.3 oracle/protocol tests on a private disposable database.

Run with B20_CLICKHOUSE_PORT explicitly set in the owned cloud runtime. These
exercise the real pinned physical read SQL and R2 reducer, not PG publication or
B21 request wiring. Those integration gates remain independently required.
"""

import math
import os
import random
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from clickhouse_driver import Client

from tracer.services.derived_metrics import user_rules as ur
from tracer.tests.test_derived_metric_user_rules import START, WINDOW, WORKSPACE, oracle

ORG, PROJECT, FIELD = UUID(int=2), UUID(int=3), UUID(int=4)
CHECKPOINT, DELTA, UNCOMMITTED, ABORTED = [UUID(int=i) for i in range(10, 14)]


@pytest.fixture
def native():
    port = os.environ.get("B20_CLICKHOUSE_PORT")
    if not port:
        pytest.skip("requires explicit owned B20 native ClickHouse runtime")
    from conftest import _open_ch_test_native_client

    database = "test_b20_" + uuid4().hex
    # Keep the shared forwarded-port/sidecar/target guards on the real client.
    environ = {**os.environ, "CH25_NATIVE_PORT": port}
    admin = _open_ch_test_native_client(owned_database=database, environ=environ)
    admin.execute(f"CREATE DATABASE {database}")
    client = _open_ch_test_native_client(database=database, environ=environ)
    schema = """
        org_id UUID, workspace_id UUID, project_id UUID, field_id UUID,
        field_version UInt32, level LowCardinality(String), unit_id String,
        generation UInt64, _version UInt64, attempt_id UUID, user_id String,
        unit_start DateTime64(6, 'UTC'), status LowCardinality(String),
        value_bool Nullable(UInt8), value_number Nullable(Float64),
        sample_probability Nullable(Float64), invalid_probability UInt8,
        is_deleted UInt8, settled_at Nullable(DateTime64(6, 'UTC'))
    """
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        client.execute(
            f"CREATE TABLE {table} ({schema}) ENGINE=MergeTree ORDER BY (org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id)"
        )
    try:
        assert client.execute("SELECT version()")[0][0].startswith("25.3.")
        yield client
    finally:
        client.disconnect()
        admin.execute(f"DROP DATABASE {database} SYNC")
        admin.disconnect()


def record(unit, user, value, **changes):
    result = {
        "org_id": ORG,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "field_id": FIELD,
        "field_version": 1,
        "level": "conversation",
        "unit_id": unit,
        "generation": 1,
        "_version": 1,
        "attempt_id": CHECKPOINT,
        "user_id": user,
        "unit_start": START,
        "status": "ok",
        "value_bool": int(value) if value in (0, 1) else None,
        "value_number": value,
        "sample_probability": 1.0,
        "invalid_probability": 0,
        "is_deleted": 0,
        "settled_at": START,
    }
    result.update(changes)
    return result


def insert(client, rows, *, checkpoint=True):
    if not rows:
        return
    columns = tuple(rows[0])
    table = "metric_field_values_checkpoint" if checkpoint else "metric_field_values"
    client.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES",
        [tuple(row[k] for k in columns) for row in rows],
    )


def source(client, *, delta=False, limits=ur.Limits(), guard=lambda: None):
    # Exactly the pinned pair membership, not generation<=head or latest raw rows.
    columns = ", ".join(ur._COLUMNS)
    common = "org_id = %(org)s AND project_id = %(project)s AND field_id = %(field)s AND field_version = 1 AND level = 'conversation'"
    branches = [
        f"SELECT {columns} FROM metric_field_values_checkpoint PREWHERE {common} WHERE (generation, attempt_id) IN %(cp)s"
    ]
    if delta:
        branches.append(
            f"SELECT {columns} FROM metric_field_values PREWHERE {common} WHERE (generation, attempt_id) IN %(delta)s"
        )
    return ur.NativeSource(
        client,
        branches,
        {
            "org": ORG,
            "project": PROJECT,
            "field": FIELD,
            "cp": ((1, CHECKPOINT),),
            "delta": ((2, DELTA),),
        },
        workspace_id=WORKSPACE,
        guard=guard,
        limits=limits,
    )


@pytest.mark.parametrize("rule", ["any", "every", "average", "total"])
def test_native_randomized_oracle_all_windows_and_bounded_splits(native, rule):
    rng = random.Random(2105)
    values = (
        [None, 0, 1]
        if rule in {"any", "every"}
        else [None, 0, 3, -5, 1e308, -1e308, 5e-324, math.nan, math.inf, -math.inf]
    )
    rows = [
        record(
            f"c{i}",
            f"u{rng.randrange(45)}",
            rng.choice(values),
            unit_start=START + timedelta(days=rng.randrange(90)),
        )
        for i in range(360)
    ]
    insert(native, rows)
    for days in (1, 7, 30, 90):
        window = ur.Window(START, START + timedelta(days=days))
        grouped = {}
        for item in rows:
            if item["unit_start"] < window.end:
                grouped.setdefault(item["user_id"].encode(), []).append(
                    item["value_bool"]
                    if rule in {"any", "every"}
                    else item["value_number"]
                )
        users = {}
        actual_source = source(native, limits=ur.Limits(rows=48, states=12))
        (point,) = ur.reduce_users(
            actual_source,
            rule,
            [window],
            retained_from=START,
            consumer=lambda p, u, v, users=users: users.__setitem__(u, v),
        )
        expected = {user: oracle(items, rule) for user, items in grouped.items()}
        assert {key: result.value for key, result in users.items()} == expected
        finite = [value for value in expected.values() if value is not None]
        assert point.eligible_users == len(finite)
        assert point.no_value_users == len(expected) - len(finite)
        expected_mean = oracle([float(x) for x in finite], "average")
        assert point.value == expected_mean
        assert actual_source.splits > 0


@pytest.mark.parametrize("rule", ["any", "every", "average", "total"])
def test_latest_remaps_reopens_moves_tombstones_and_exact_allowlist(native, rule):
    base = [record(f"c{i}", "old", 1) for i in range(8)]
    base += [record("cp-only", "unchanged", 0), record("missing", "unknown", None)]
    insert(native, base)
    updates = [
        record("c0", "merged", 1),
        record("c1", "", 1),
        record("c2", "moved", 1, unit_start=START - timedelta(days=1)),
        record("c3", "deleted", 1, is_deleted=1),
        record("c4", "open", 1, status="open", settled_at=None),
        record("c5", "foreign", 1, workspace_id=UUID(int=900)),
    ]
    updates = [
        dict(item, generation=2, _version=2, attempt_id=DELTA) for item in updates
    ]
    insert(native, updates, checkpoint=False)
    # Duplicate retries, and poison attempts with a newer numeric generation.
    insert(native, updates, checkpoint=False)
    insert(
        native,
        [
            record(
                "cp-only", "poison", 1, generation=999, _version=999, attempt_id=attempt
            )
            for attempt in (UNCOMMITTED, ABORTED)
        ],
        checkpoint=False,
    )
    insert(
        native,
        [
            record("alien", "alien", 1, org_id=UUID(int=99)),
            record("other-project", "alien", 1, project_id=UUID(int=99)),
            record("other-field", "alien", 1, field_id=UUID(int=99)),
            record("other-version", "alien", 1, field_version=2),
        ],
    )
    users = {}
    (point,) = ur.reduce_users(
        source(native, delta=True, limits=ur.Limits(rows=4, states=2)),
        rule,
        [WINDOW],
        retained_from=START,
        consumer=lambda p, u, v, users=users: users.__setitem__(u, v),
    )
    assert set(users) == {b"merged", b"old", b"unchanged", b"unknown"}
    assert point.conversations_without_user == 1
    assert (point.eligible_users, point.no_value_users) == (3, 1)
    assert users[b"old"].conversations == 2
    assert users[b"unchanged"].value == (False if rule in {"any", "every"} else 0)


def test_singleton_retry_fallback_and_full_byte_identity(native):
    unit = b"identity\0\xff'"
    base = record(unit, b"user\0\xff'", 3)
    insert(native, [base] * 5)
    pinned = source(native, limits=ur.Limits(rows=2))
    result = pinned._page(ur._Range(depth=64, single=unit), lambda: 10)
    assert list(result) == [unit]
    assert result[unit].user == b"user\0\xff'"
    # Physical retries are one logical row even beyond the source-page cap.
    (point,) = ur.reduce_users(pinned, "total", [WINDOW], retained_from=START)
    assert point.value == 3
    assert 1 <= pinned.splits < 64


def test_indexed_source_continuation_does_not_replay_completed_prefix(native):
    insert(native, [record(f"c{i:04}", "one-user", 1) for i in reversed(range(257))])
    src = source(native, limits=ur.Limits(rows=8))
    (point,) = ur.reduce_users(src, "total", [WINDOW], retained_from=START)
    assert point.value == 257 and point.eligible_users == 1
    # Each ordered prefix proves eight full keys and one singleton boundary;
    # no discarded-prefix rereads or root restart. Include the final EOF page.
    assert src.statements <= 2 * math.ceil(257 / 9) + 1


@pytest.mark.parametrize(
    "change",
    ["number", "signed_zero", "nan_payload", "user", "probability", "nullable_boolean"],
)
def test_singleton_rejects_conflicting_equal_version_retry(native, change):
    import struct

    first = record("same", "u", 0)
    second = dict(first)
    if change == "number":
        second["value_number"] = 1
    elif change == "signed_zero":
        second["value_number"] = -0.0
    elif change == "nan_payload":
        first["value_number"] = struct.unpack(">d", bytes.fromhex("7ff8000000000001"))[
            0
        ]
        second["value_number"] = struct.unpack(">d", bytes.fromhex("7ff8000000000002"))[
            0
        ]
    elif change == "user":
        second["user_id"] = "other"
    elif change == "probability":
        second["sample_probability"] = None
    else:
        second["value_bool"] = None
    insert(native, [first, second] * 3)
    with pytest.raises(ur.UserRulesUnavailable, match="conflicting_retry"):
        source(native)._page(ur._SourceRange(single=b"same"), lambda: 10)


def test_singleton_identical_nan_retries_preserve_missing_at_max_version(native):
    insert(native, [record("same", "u", math.nan, _version=ur.MAX_COUNT)] * 8)
    (point,) = ur.reduce_users(
        source(native, limits=ur.Limits(rows=2)),
        "average",
        [WINDOW],
        retained_from=START,
    )
    assert point.value is None
    assert point.no_value_users == 1 and point.eligible_users == 0


def test_ordered_prefix_delta_boundary_does_not_resurrect_mutable_values(native):
    insert(native, [record(f"c{i:02}", "old", 1) for i in range(30)])
    insert(native, [record("c03", "old", 1)] * 20)
    insert(
        native,
        [
            record(
                f"c{i:02}",
                "new",
                0,
                generation=2,
                _version=2,
                attempt_id=DELTA,
                is_deleted=int(i % 3 == 0),
                unit_start=START - timedelta(days=int(i % 3 == 1)),
            )
            for i in range(30)
        ],
        checkpoint=False,
    )
    users = {}
    (point,) = ur.reduce_users(
        source(native, delta=True, limits=ur.Limits(rows=3)),
        "any",
        [WINDOW],
        retained_from=START,
        consumer=lambda p, u, v: users.__setitem__(u, v),
    )
    assert set(users) == {b"new"}
    assert users[b"new"].conversations == 10
    assert point.value == 0 and point.counted_users == 0


def test_wide_identity_is_not_silently_truncated(native):
    insert(native, [record("wide-ok", "x" * 1024, 1)])
    (point,) = ur.reduce_users(source(native), "any", [WINDOW], retained_from=START)
    assert point.value == 1
    insert(native, [record("wide-bad", "x" * 1025, 1)])
    with pytest.raises(ur.UserRulesUnavailable, match="identity_or_payload_limit"):
        ur.reduce_users(source(native), "any", [WINDOW], retained_from=START)


def test_sampled_missing_and_ambiguous_rows_poison_only_their_point(native):
    rows = [
        record("valid", "u", 1),
        record("ambiguous", "", None, sample_probability=math.nan),
        record("tomorrow", "u", 0, unit_start=START + timedelta(days=1)),
        record("prior", "u", 1, unit_start=START - timedelta(days=1)),
        record(
            "open", "", None, sample_probability=0.2, status="open", settled_at=None
        ),
    ]
    insert(native, rows)
    windows = [
        ur.Window(START + timedelta(days=i), START + timedelta(days=i + 1))
        for i in range(7)
    ]
    points = ur.reduce_users(source(native), "any", windows, retained_from=START)
    assert points[0].point_status == "sampled_inputs_not_supported"
    assert points[0].conversations_without_user == 1
    assert points[0].sampled_conversations == 1
    assert points[1].value == 0
    assert all(point.point_status == "no_value" for point in points[2:])
    (previous,) = ur.reduce_users(
        source(native),
        "any",
        [WINDOW.previous()],
        retained_from=WINDOW.previous().start,
    )
    assert previous.value == 1


def test_native_exact_numeric_outer_boundaries(native):
    insert(
        native,
        [
            record("a1", "a", 1e308),
            record("a2", "a", 1e308),
            record("b", "b", 10),
            record("zero", "z", 0),
            record("missing", "m", None),
        ],
    )
    (average,) = ur.reduce_users(
        source(native), "average", [WINDOW], retained_from=START
    )
    (total,) = ur.reduce_users(source(native), "total", [WINDOW], retained_from=START)
    assert average.value == oracle([1e308, 10, 0], "average")
    assert (average.eligible_users, average.no_value_users, average.counted_users) == (
        3,
        1,
        3,
    )
    assert total.value == 5
    assert (total.eligible_users, total.no_value_users, total.non_finite_users) == (
        2,
        2,
        1,
    )


def test_native_final_pin_revocation_fails_closed(native):
    insert(native, [record("one", "u", 1)])
    guarded = source(native)
    original = guarded.guard

    def guard():
        original()
        if guarded.statements:
            raise ur.UserRulesUnavailable("expired_pin")

    guarded.guard = guard
    with pytest.raises(ur.UserRulesUnavailable, match="expired_pin"):
        ur.reduce_users(guarded, "any", [WINDOW], retained_from=START)


@pytest.mark.parametrize("offset", [330, 345])
def test_native_half_open_snapped_offset_boundaries(native, offset):
    from datetime import datetime, timezone

    zone = timezone(timedelta(minutes=offset))
    window = ur.Window.snap(
        datetime(2026, 1, 2, 0, 1, tzinfo=zone),
        datetime(2026, 1, 3, 0, 14, tzinfo=zone),
    )
    insert(
        native,
        [
            record(
                "before",
                "before",
                1,
                unit_start=window.start - timedelta(microseconds=1),
            ),
            record("start", "start", 1, unit_start=window.start),
            record(
                "last", "last", 1, unit_start=window.end - timedelta(microseconds=1)
            ),
            record("end", "end", 1, unit_start=window.end),
        ],
    )
    users = {}
    (point,) = ur.reduce_users(
        source(native),
        "any",
        [window],
        retained_from=window.previous().start,
        consumer=lambda p, u, v: users.__setitem__(u, v),
    )
    assert set(users) == {b"start", b"last"}
    assert point.eligible_users == 2
    (previous,) = ur.reduce_users(
        source(native),
        "any",
        [window.previous()],
        retained_from=window.previous().start,
    )
    assert previous.eligible_users == 1


def _elapsed_until_error(src):
    import time

    started = time.monotonic()
    error = None
    try:
        ur.reduce_users(src, "any", [WINDOW], retained_from=START)
    except Exception as exc:
        error = type(exc).__name__
    return time.monotonic() - started, error


def test_subsecond_reducer_deadline_reaches_native_sql(native):
    insert(native, [record("a", "u", 1)])
    src = source(native, limits=ur.Limits(seconds=0.1))
    src.branches = tuple(branch + " AND sleep(1.5) = 0" for branch in src.branches)
    elapsed, error = _elapsed_until_error(src)
    assert error is not None
    assert elapsed < 0.3, f"100ms budget took {elapsed:.6f}s; error={error}"
    assert not native.connection.connected


def test_reducer_deadline_bounds_native_handshake_wait():
    import socket
    import threading
    import time

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]

    def stalled_peer():
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(1)
                conn.recv(4096)
                time.sleep(0.6)
        finally:
            listener.close()

    thread = threading.Thread(target=stalled_peer, daemon=True)
    thread.start()
    client = Client("127.0.0.1", port=port, connect_timeout=1, send_receive_timeout=1)
    try:
        elapsed, error = _elapsed_until_error(
            source(client, limits=ur.Limits(seconds=0.1))
        )
        assert not client.connection.connected
    finally:
        client.disconnect()
        thread.join(timeout=3)
    assert error is not None
    assert elapsed < 0.3, f"100ms budget took {elapsed:.6f}s; error={error}"


def pooled(native, *, readonly=False, preserve=True):
    from tracer.services.clickhouse.client import ClickHouseClient

    return ClickHouseClient(
        host="127.0.0.1",
        port=int(os.environ["B20_CLICKHOUSE_PORT"]),
        user="default",
        password="",
        database=native.connection.database,
        server_enforced_readonly=readonly,
        allow_query_settings_with_server_readonly=preserve,
        pool_size=1,
    )


@pytest.mark.parametrize("readonly", [False, True])
@pytest.mark.parametrize(
    "setting, expected",
    [
        ("max_result_rows", ur.MAX_ROWS + 1),
        ("max_result_bytes", ur.MAX_PAGE_BYTES),
        ("max_memory_usage", ur.SOURCE_SETTINGS["max_memory_usage"]),
        ("max_threads", 1),
        ("max_bytes_before_external_group_by", 0),
        ("max_bytes_before_external_sort", 0),
    ],
)
def test_application_wrapper_preserves_native_b20_settings(
    native, readonly, setting, expected
):
    from tracer.services.clickhouse.application_read_policy import (
        application_read_context,
    )

    insert(native, [record("one", "u", 1)])
    wrapper = pooled(native, readonly=readonly)
    try:
        src = source(wrapper)
        src.branches = tuple(
            branch.replace(
                "value_number", f"toFloat64(getSetting('{setting}')) AS value_number"
            )
            for branch in src.branches
        )
        with application_read_context(True):
            (point,) = ur.reduce_users(src, "total", [WINDOW], retained_from=START)
        assert point.value == expected
        assert wrapper._pool.qsize() == 1
    finally:
        wrapper.close()


@pytest.mark.parametrize("readonly", [False, True])
def test_application_wrapper_subsecond_sql_and_connection_retirement(native, readonly):
    from tracer.services.clickhouse.application_read_policy import (
        application_read_context,
    )

    insert(native, [record("one", "u", 1)])
    wrapper = pooled(native, readonly=readonly)
    try:
        src = source(wrapper, limits=ur.Limits(seconds=0.1))
        src.branches = tuple(branch + " AND sleep(1.5) = 0" for branch in src.branches)
        with application_read_context(True):
            elapsed, error = _elapsed_until_error(src)
        assert error is not None
        assert elapsed < 0.3, f"100ms wrapper budget took {elapsed:.6f}s; error={error}"
        assert wrapper._pool.empty()
        assert wrapper._read_admission.acquire(blocking=False)
        wrapper._read_admission.release()
    finally:
        wrapper.close()


def test_readonly_wrapper_without_bounded_settings_fails_closed(native):
    wrapper = pooled(native, readonly=True, preserve=False)
    try:
        with pytest.raises(
            ur.UserRulesUnavailable, match="bounded_stream_settings_required"
        ):
            ur.reduce_users(source(wrapper), "any", [WINDOW], retained_from=START)
        assert wrapper._pool.empty()
    finally:
        wrapper.close()


def test_admitted_wrapper_queue_wait_uses_remaining_deadline(native):
    wrapper = pooled(native)
    assert wrapper._read_admission.acquire(blocking=False)
    try:
        elapsed, error = _elapsed_until_error(
            source(wrapper, limits=ur.Limits(seconds=0.1))
        )
        assert error == "TimeoutError"
        assert elapsed < 0.3
        assert wrapper._pool.empty()
    finally:
        wrapper._read_admission.release()
        wrapper.close()


@pytest.mark.parametrize("mode", ["borrowed", "pooled", "pooled_readonly_flag"])
def test_warm_native_ping_respects_remaining_deadline(native, mode):
    """Pause only an explicitly owned local test server, never a shared target."""
    import pathlib
    import signal
    import threading

    pid_file = os.environ.get("B20_OWNED_CH_SERVER_PID_FILE")
    binary = os.environ.get("B20_OWNED_CH_SERVER_BINARY")
    if not pid_file or not binary:
        pytest.skip("requires explicit owned disposable server process")
    pid = int(pathlib.Path(pid_file).read_text())
    assert pathlib.Path(f"/proc/{pid}/exe").resolve() == pathlib.Path(binary).resolve()
    assert b"server" in pathlib.Path(f"/proc/{pid}/cmdline").read_bytes()
    insert(native, [record("one", "u", 1)])
    wrapper = (
        None
        if mode == "borrowed"
        else pooled(native, readonly=mode == "pooled_readonly_flag")
    )
    client = native if wrapper is None else wrapper
    assert (
        ur.reduce_users(source(client), "any", [WINDOW], retained_from=START)[0].value
        == 1
    )
    raw = native if wrapper is None else wrapper._pool.queue[0]
    connection = raw.connection
    assert connection.connected
    original = (connection.sync_request_timeout, connection.disable_reconnect)
    resume_now = threading.Event()

    def resume():
        resume_now.wait(0.6)
        os.kill(pid, signal.SIGCONT)

    thread = threading.Thread(target=resume, daemon=True)
    thread.start()
    try:
        os.kill(pid, signal.SIGSTOP)
        elapsed, error = _elapsed_until_error(
            source(client, limits=ur.Limits(seconds=0.1))
        )
        assert error is not None
        assert elapsed < 0.3, (
            f"100ms warm native deadline took {elapsed:.6f}s; error={error}"
        )
        assert not connection.connected
        assert (
            connection.sync_request_timeout,
            connection.disable_reconnect,
        ) == original
        if wrapper is not None:
            assert wrapper._pool.empty()
            assert wrapper._read_admission.acquire(blocking=False)
            wrapper._read_admission.release()
    finally:
        resume_now.set()
        os.kill(pid, signal.SIGCONT)
        thread.join(timeout=2)
        if wrapper is not None:
            wrapper.close()


@pytest.mark.parametrize("mode", ["borrowed", "pooled", "pooled_readonly_flag"])
def test_warm_native_timeouts_restored_after_success(native, mode):
    insert(native, [record("one", "u", 1)])
    wrapper = (
        None
        if mode == "borrowed"
        else pooled(native, readonly=mode == "pooled_readonly_flag")
    )
    client = native if wrapper is None else wrapper
    try:
        ur.reduce_users(source(client), "any", [WINDOW], retained_from=START)
        raw = native if wrapper is None else wrapper._pool.queue[0]
        names = (
            "connect_timeout",
            "send_receive_timeout",
            "sync_request_timeout",
            "disable_reconnect",
        )
        original = tuple(getattr(raw.connection, name) for name in names)
        socket_timeout = raw.connection.socket.gettimeout()
        assert (
            ur.reduce_users(
                source(client, limits=ur.Limits(seconds=0.5)),
                "any",
                [WINDOW],
                retained_from=START,
            )[0].value
            == 1
        )
        assert raw.connection.connected
        assert tuple(getattr(raw.connection, name) for name in names) == original
        assert raw.connection.socket.gettimeout() == socket_timeout
        if wrapper is not None:
            assert wrapper._pool.qsize() == 1
            assert wrapper._read_admission.acquire(blocking=False)
            wrapper._read_admission.release()
    finally:
        if wrapper is not None:
            wrapper.close()
