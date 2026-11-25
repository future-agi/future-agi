"""Independent B20 arithmetic/oracle tests; no database or global flag bypass."""

import itertools
import math
import random
import struct
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from tracer.services.derived_metrics import user_rules as ur

WORKSPACE = UUID(int=1)
START = datetime(2026, 1, 1, tzinfo=UTC)
WINDOW = ur.Window(START, START + timedelta(days=7))


def oracle(values, rule):
    """Direct three-valued logic / independent 2600-digit Decimal arithmetic."""
    if rule in {"any", "every"}:
        known = [x for x in values if type(x) in (bool, int) and x in (0, 1)]
        if rule == "any" and any(known):
            return True
        if rule == "every" and any(not x for x in known):
            return False
        if values and len(known) == len(values):
            return rule == "every"
        return None
    known = [float(x) for x in values if type(x) in (int, float) and math.isfinite(x)]
    if not known:
        return None
    with localcontext() as ctx:
        ctx.prec = 2600
        value = sum(map(Decimal.from_float, known), Decimal(0))
        if rule == "average":
            value /= len(known)
        value = float(value)
    return value if math.isfinite(value) else None


def reduce_values(values, rule, partitions=1):
    states = [ur.RuleState(rule) for _ in range(partitions)]
    for index, value in enumerate(values):
        states[index % partitions].add(value)
    state = ur.RuleState(rule)
    for partial in states:
        state.merge(partial)
    return state.finish()


@pytest.mark.parametrize("rule", ["any", "every"])
@pytest.mark.parametrize("length", range(1, 7))
def test_exhaustive_three_valued_truth_and_partition_merges(rule, length):
    for values in itertools.product((True, False, None), repeat=length):
        for partitions in (1, 2, 4):
            result = reduce_values(values, rule, partitions)
            assert result.value is oracle(values, rule)
            assert result.conversations == length
            assert result.with_value == sum(x is not None for x in values)
            assert result.missing == values.count(None)


NUMERIC_CASES = [
    [1e308, 1e308],
    [-1e308, -1e308],
    [1e308, 1e308, -1e308, -1e308, 3],
    [float.fromhex("0x1.fffffffffffffp+1023")] * 2,
    [
        float.fromhex("0x1.fffffffffffffp+1023"),
        -float.fromhex("0x1.fffffffffffffp+1023"),
        5e-324,
    ],
    [1e308, -1e308, 1e-100],
    [5, -5],
    [None, math.nan, math.inf, -math.inf],
    [0, None, math.nan],
    [1e308] * 200,
    [1e308, -1e308, 3, 1e-308],
    [],
]


@pytest.mark.parametrize("rule", ["average", "total"])
@pytest.mark.parametrize("values", NUMERIC_CASES)
def test_extreme_numbers_preserve_finite_means_cancellation_and_zero(rule, values):
    for partitions in (1, 2, 4, 8):
        result = reduce_values(values, rule, partitions)
        assert result.value == oracle(values, rule)
        if result.value is not None:
            assert math.isfinite(result.value)
        if result.with_value and result.value is None:
            assert rule == "total"
            assert result.state == "non_finite_result"


@pytest.mark.parametrize("rule", ["average", "total"])
def test_random_binary64_against_independent_decimal(rule):
    randomizer = random.Random(201105)
    for _ in range(240):
        values = [
            struct.unpack(">d", randomizer.getrandbits(64).to_bytes(8, "big"))[0]
            for _ in range(randomizer.randint(1, 32))
        ]
        expected = oracle(values, rule)
        for partitions in (1, 2, 4, 8):
            assert reduce_values(values, rule, partitions).value == expected


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), True, "3"])
def test_internal_accumulator_corruption_is_not_missing(bad):
    state = ur.ExactSum(2, bad)
    with pytest.raises(ur.UserRulesUnavailable, match="corrupt_state"):
        state.value(average=True)
    with pytest.raises(ur.UserRulesUnavailable, match="corrupt_state"):
        ur.ExactSum().merge(state)


def test_checked_count_overflow_and_impossible_state():
    state = ur.ExactSum(ur.MAX_COUNT, 0)
    with pytest.raises(ur.UserRulesUnavailable, match="count_limit"):
        state.add(0)
    assert state.count == ur.MAX_COUNT
    with pytest.raises(ur.UserRulesUnavailable):
        ur.ExactSum(0, 1).value(average=True)
    with pytest.raises(ur.UserRulesUnavailable):
        ur.RuleState("any", conversations=1, with_value=2).finish()
    with pytest.raises(ur.UserRulesUnavailable):
        ur.ExactSum().add(math.inf)


def test_outer_mean_and_effect_use_finalized_equal_user_weights():
    state = ur.ExactSum()
    for value in [1e308, 1e308]:
        state.add(value)
    assert state.value(average=True) == 1e308
    assert state.leave_one_out(1e308) == 0
    assert ur.ExactSum(1, ur.coefficient(10)).leave_one_out(10) is None
    ordinary = ur.ExactSum()
    for value in [0, 10, 0]:
        ordinary.add(value)
    assert ordinary.value(average=True) == float(Decimal(10) / 3)
    assert ordinary.leave_one_out(10) == float(Decimal(10) / 3)


@pytest.mark.parametrize("offset", [330, 345, -210])
def test_outward_snapping_and_previous_at_offset_timezones(offset):
    zone = timezone(timedelta(minutes=offset))
    start = datetime(2026, 1, 1, 0, 1, tzinfo=zone)
    end = datetime(2026, 1, 2, 0, 14, tzinfo=zone)
    window = ur.Window.snap(start, end)
    assert window.start == start.replace(minute=0).astimezone(UTC)
    assert window.end == end.replace(minute=15).astimezone(UTC)
    assert window.previous().end == window.start
    assert window.previous().end - window.previous().start == window.end - window.start
    assert ur.Window.snap(window.start, window.end) == window
    with pytest.raises(ValueError):
        ur.Window.snap(start.replace(tzinfo=None), end)


def row(user=b"u", value=1, *, unit=b"c", at=START, **kwargs):
    values = {
        "unit": unit,
        "generation": 1,
        "version": 1,
        "user": user,
        "user_hash": int.from_bytes(user[:8].ljust(8, b"\0"), "big"),
        "start_us": ur._micros(at),
        "status": b"ok",
        "boolean": value,
        "number": value,
        "probability": 1.0,
        "invalid_probability": 0,
        "deleted": 0,
        "settled": 1,
        "workspace": WORKSPACE,
    }
    values.update(kwargs)
    return ur.Conversation(**values)


class MemorySource:
    def __init__(self, rows, *, limits=ur.Limits(), guard=lambda: None):
        self.values = rows
        self.limits = limits
        self.guard = guard
        self.workspace = WORKSPACE
        self.scans = 0

    def rows(self, check):
        self.scans += 1
        for value in self.values:
            check()
            yield value


def test_missing_ambiguous_users_open_and_sampled_inputs_global_evidence():
    rows = [
        row(b"a", True),
        row(b"a", None),
        row(b"b", False),
        row(b"b", None),
        row(b"", None, probability=0.25),
        row(b"", True),
        row(b"open", True, status=b"open"),
        row(b"unsettled", True, settled=0),
        row(b"deleted", True, deleted=1),
        row(b"foreign", True, workspace=UUID(int=2)),
    ]
    users = {}
    (point,) = ur.reduce_users(
        MemorySource(rows),
        "any",
        [WINDOW],
        retained_from=START,
        consumer=lambda p, u, v: users.__setitem__(u, v),
    )
    assert set(users) == {b"a", b"b"}
    assert users[b"a"].value is True
    assert users[b"b"].value is None
    assert (point.counted_users, point.eligible_users, point.no_value_users) == (
        1,
        1,
        1,
    )
    assert point.conversations_without_user == 2
    assert point.point_status == "sampled_inputs_not_supported"
    assert point.value is point.count_value is None


@pytest.mark.parametrize(
    "probability", [None, 0, -1, 0.5, 2, math.nan, math.inf, -math.inf]
)
def test_every_invalid_sampling_probability_is_unsupported(probability):
    (point,) = ur.reduce_users(
        MemorySource([row(probability=probability)]),
        "total",
        [WINDOW],
        retained_from=START,
    )
    assert point.point_status == "sampled_inputs_not_supported"
    assert point.eligible_users == 1
    assert point.sampled_conversations == 1
    assert point.value is point.count_value is None


@pytest.mark.parametrize(
    "values,eligible,missing,count",
    [([], 0, 0, None), ([None], 0, 1, None), ([False], 1, 0, 0), ([True], 1, 0, 1)],
)
def test_count_never_displays_a_missing_point_as_zero(values, eligible, missing, count):
    (point,) = ur.reduce_users(
        MemorySource([row(value=x) for x in values]),
        "any",
        [WINDOW],
        retained_from=START,
    )
    assert (point.eligible_users, point.no_value_users, point.count_value) == (
        eligible,
        missing,
        count,
    )


@pytest.mark.parametrize("rule", ["any", "every", "average", "total"])
def test_user_state_splits_discard_tentative_counts_and_full_hash_collision(rule):
    rows = [row(f"u{i}".encode(), i % 2, user_hash=0) for i in range(12)]
    rows.extend([row(b"", None, user_hash=0), row(b"", None, user_hash=0)])
    source = MemorySource(rows, limits=ur.Limits(states=2))
    users = {}
    actual = ur.reduce_users(
        source,
        rule,
        [WINDOW],
        retained_from=START,
        consumer=lambda p, u, v: users.__setitem__(u, v),
    )
    expected = ur.reduce_users(MemorySource(rows), rule, [WINDOW], retained_from=START)
    assert actual == expected
    assert len(users) == 12
    assert actual[0].conversations_without_user == 2
    assert 1 < source.scans <= 12


def test_heavy_user_weights_once_and_all_trend_points_share_a_scan():
    rows = [row(b"heavy", 0) for _ in range(100)] + [
        row(b"other", 10),
        row(b"missing", None),
    ]
    rows.append(row(b"heavy", 6, at=START + timedelta(days=1)))
    rows.append(row(b"prior", 50, at=START - timedelta(hours=1)))
    source = MemorySource(rows)
    days = [
        ur.Window(START + timedelta(days=i), START + timedelta(days=i + 1))
        for i in range(7)
    ]
    points = ur.reduce_users(source, "total", days, retained_from=START)
    assert source.scans == 1
    assert points[0].value == 5
    assert points[1].value == 6
    assert all(p.point_status == "no_value" for p in points[2:])
    (point,) = ur.reduce_users(source, "total", [WINDOW], retained_from=START)
    assert point.value == 8
    (prior,) = ur.reduce_users(
        source, "total", [WINDOW.previous()], retained_from=WINDOW.previous().start
    )
    assert prior.value == 50


def test_retention_and_guard_failures_do_not_return_partial_results():
    source = MemorySource([row()])
    with pytest.raises(ur.UserRulesUnavailable, match="retention_expired"):
        ur.reduce_users(
            source, "any", [WINDOW], retained_from=START + timedelta(microseconds=1)
        )
    assert source.scans == 0
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise ur.UserRulesUnavailable("expired_pin")

    with pytest.raises(ur.UserRulesUnavailable, match="expired_pin"):
        ur.reduce_users(
            MemorySource([row()], guard=guard), "any", [WINDOW], retained_from=START
        )


def test_source_sql_has_only_immutable_ranges_and_global_sentinel():
    branch = (
        "SELECT * FROM metric_field_values WHERE (generation, attempt_id) IN %(allow)s"
    )
    for _rule in ur.RULES:
        sql, params = ur.source_query(branch, ur._Range())
        assert sql.endswith("LIMIT 65537")
        assert "GROUP BY" not in sql and "ORDER BY" not in sql and "FINAL" not in sql
        assert "groupArray" not in sql
        assert (
            "status =" not in sql
            and "user_id =" not in sql
            and "unit_start >" not in sql
        )
        assert params == {}
        assert "cityHash64(unit_id)" not in sql
    singleton, params = ur.source_query(
        branch, ur._Range(depth=64, single=b"id\0quoted'")
    )
    assert "argMax(tuple(" in singleton and "LIMIT" not in singleton
    assert params["ur_unit"] == b"id\0quoted'".hex()
    for key in (
        "max_bytes_before_external_group_by",
        "max_bytes_ratio_before_external_group_by",
        "max_bytes_before_external_sort",
        "max_bytes_ratio_before_external_sort",
    ):
        assert ur.SOURCE_SETTINGS[key] == 0


class FakeClient:
    def __init__(self, values, *, fail=False):
        self.values = values
        self.fail = fail
        self.disconnected = False

    def execute_iter(self, sql, params, **kwargs):
        for value in self.values:
            yield tuple(getattr(value, key) for key in ur.Conversation.__slots__)
        if self.fail:
            raise RuntimeError("transport_failed")

    def disconnect(self):
        self.disconnected = True


def test_page_needs_eof_tie_retry_identity_and_bounded_payload():
    for values, error in [
        ([row(), row(value=2)], "conflicting_retry"),
        ([row(user=b"x" * 1025)], "identity_or_payload_limit"),
    ]:
        client = FakeClient(values)
        source = ur.NativeSource(
            client, ["SELECT 1"], {}, workspace_id=WORKSPACE, guard=lambda: None
        )
        with pytest.raises(ur.UserRulesUnavailable, match=error):
            source._page(ur._Range(), lambda: 10)
        assert client.disconnected
    client = FakeClient([row()], fail=True)
    source = ur.NativeSource(
        client, ["SELECT 1"], {}, workspace_id=WORKSPACE, guard=lambda: None
    )
    with pytest.raises(RuntimeError, match="transport_failed"):
        source._page(ur._Range(), lambda: 10)
    assert client.disconnected
    client = FakeClient([row(value=math.nan), row(value=math.nan)])
    source = ur.NativeSource(
        client, ["SELECT 1"], {}, workspace_id=WORKSPACE, guard=lambda: None
    )
    assert len(source._page(ur._Range(), lambda: 10)) == 1


def test_row_sentinel_is_not_a_complete_page_even_at_exact_limit():
    client = FakeClient([row(unit=str(i).encode()) for i in range(3)])
    source = ur.NativeSource(
        client,
        ["SELECT 1"],
        {},
        workspace_id=WORKSPACE,
        guard=lambda: None,
        limits=ur.Limits(rows=2),
    )
    with pytest.raises(ur._Split):
        source._page(ur._Range(), lambda: 10)
    assert client.disconnected


def test_ordered_prefix_still_requires_successful_terminal_after_sentinel():
    # The terminal must follow an already delivered native block, otherwise
    # driver block buffering raises before the page ever sees its sentinel.
    client = FakeClient([row(unit=f"{i:04}".encode()) for i in range(1025)], fail=True)
    source = ur.NativeSource(
        client,
        ["SELECT 1"],
        {},
        workspace_id=WORKSPACE,
        guard=lambda: None,
        limits=ur.Limits(rows=2),
    )
    with pytest.raises(RuntimeError, match="transport_failed"):
        source._page(ur._SourceRange(), lambda: 10, ordered=True)
    assert client.disconnected


def test_unsupported_limits_and_window_overlap_are_rejected():
    with pytest.raises(ValueError):
        ur.Limits(states=ur.MAX_STATES + 1)
    with pytest.raises(ValueError):
        ur.reduce_users(MemorySource([]), "any", [WINDOW, WINDOW], retained_from=START)
    with pytest.raises(ValueError):
        ur.reduce_users(MemorySource([]), "bad", [WINDOW], retained_from=START)


def test_reservations_cover_supported_cpython_layout():
    # Runtime allocator probe, not a claimed whole-request/server peak.
    import sys

    state = ur.RuleState("total", conversations=ur.MAX_COUNT, with_value=ur.MAX_COUNT)
    state.numeric = ur.ExactSum(ur.MAX_COUNT, ur.MAX_COEFFICIENT * ur.MAX_COUNT)
    state.validate()
    payload = sys.getsizeof(state) + sys.getsizeof(state.numeric)
    payload += sum(
        sys.getsizeof(getattr(state, name))
        for name in ("conversations", "with_value", "decisive", "sampled")
    )
    payload += sys.getsizeof(state.numeric.total) + sys.getsizeof(state.numeric.count)
    payload += (
        sys.getsizeof((0, b"u")) + sys.getsizeof(b"u") + 256
    )  # dictionary cell/resize allowance
    assert payload < ur.STATE_RESERVATION
    assert (
        ur.MAX_STATES * ur.STATE_RESERVATION
        + 2 * ur.MAX_PAGE_BYTES
        + ur.MAX_LIVE_KEY_BYTES
        < 192 * 1024 * 1024
    )


def test_application_adapter_off_default_and_shared_authority_binding(monkeypatch):
    from types import SimpleNamespace

    from tracer.services.derived_metrics import bounded_reads, feature_flag

    scope = SimpleNamespace(
        organization=UUID(int=2), project=UUID(int=3), workspace=WORKSPACE
    )
    manifest = object()
    store = SimpleNamespace(client=FakeClient([]))
    calls = []

    def branches(table, pin, **kwargs):
        calls.append((table, pin, kwargs))
        return [], {}

    monkeypatch.setattr(bounded_reads, "committed_branches", branches)
    monkeypatch.setattr(feature_flag, "settings", SimpleNamespace())
    with pytest.raises(ur.UserRulesUnavailable, match="metrics_disabled"):
        ur.source_for_manifest(
            store,
            manifest,
            scope=scope,
            field_id=UUID(int=4),
            field_version=1,
            guard=lambda: None,
        )
    assert calls == []
    # Opt in this one adapter test; no autouse flag fixture or production default.
    monkeypatch.setattr(feature_flag, "settings", SimpleNamespace(METRICS_ENABLED=1))
    actual = ur.source_for_manifest(
        store,
        manifest,
        scope=scope,
        field_id=UUID(int=4),
        field_version=1,
        guard=lambda: None,
    )
    table, pin, params = calls[0]
    assert table is ur.FIELD_VALUES and pin is manifest
    assert params["org_id"] == scope.organization
    assert params["project_id"] == scope.project
    assert params["key_in"] == {
        "field_id": (UUID(int=4),),
        "field_version": (1,),
        "level": ("conversation",),
    }
    assert "overlay" not in params
    assert actual.workspace == WORKSPACE


def test_native_size_errors_split_but_memory_errors_fail():
    from clickhouse_driver.errors import ServerException

    class ErrorClient(FakeClient):
        def execute_iter(self, *args, **kwargs):
            yield from super().execute_iter(*args, **kwargs)
            raise ServerException("synthetic", code=self.code)

    for code, expected in ((396, ur._Split), (241, ServerException)):
        client = ErrorClient([row()])
        client.code = code
        source = ur.NativeSource(
            client, ["SELECT 1"], {}, workspace_id=WORKSPACE, guard=lambda: None
        )
        with pytest.raises(expected):
            source._page(ur._Range(), lambda: 10)
        assert client.disconnected


def test_native_lexical_collision_ranges_are_exhaustive_exclusive():
    root = ur._Range(depth=64, bucket=0)
    keys = [b"", b"a", b"a\0", b"b", b"c", b"z\xff"]
    children = root.split(b"b")
    for key in keys:
        assert sum(child.contains(key, 0) for child in children) == 1
    assert not children[2].contains(b"b", 0)
    sql, params = ur.source_query(children and "SELECT 1", children[2])
    assert "unit_id > unhex(%(ur_lower)s)" in sql
    assert params["ur_lower"] == b"b".hex()


def test_all_ninety_trend_points_and_prior_boundaries():
    windows = [
        ur.Window(START + timedelta(days=i), START + timedelta(days=i + 1))
        for i in range(90)
    ]
    source = MemorySource(
        [row(at=window.start) for window in windows] + [row(at=windows[-1].end)]
    )
    points = ur.reduce_users(source, "any", windows, retained_from=START)
    assert source.scans == 1
    assert len(points) == 90
    assert all(point.eligible_users == point.counted_users == 1 for point in points)


def test_source_splits_use_exhaustive_index_aligned_ranges_immediately():
    root = ur._SourceRange()
    children = root.split(b"b")
    for key in (b"", b"a", b"a\0", b"b", b"b\0", b"\xff" * 1024):
        assert sum(child.contains(key, 0) for child in children) == 1
    for child in children:
        sql, _ = ur.source_query("SELECT 1", child)
        assert "cityHash64(unit_id)" not in sql
        assert "unit_id " in sql
        assert "GROUP BY" not in sql and "ORDER BY" not in sql


@pytest.mark.parametrize("rule", ["any", "every", "average", "total"])
def test_partition_split_keeps_complete_child_prefix_and_sampling(rule):
    # Root overflows after both sides have contributed; continuation must keep
    # earlier low-half data, drop high-half data, and count unassigned only once.
    rows = [
        row(b"a", 1, user_hash=0, unit=b"1"),
        row(b"z", 0, user_hash=1 << 63, unit=b"2"),
        row(b"", None, user_hash=1 << 63, unit=b"3", probability=0.5),
        row(b"b", None, user_hash=1, unit=b"4", probability=0.5),
        row(b"a", 0, user_hash=0, unit=b"5"),
        row(b"z", 1, user_hash=1 << 63, unit=b"6"),
    ]
    expected = ur.reduce_users(MemorySource(rows), rule, [WINDOW], retained_from=START)
    actual = MemorySource(rows, limits=ur.Limits(states=2))
    users = {}
    assert (
        ur.reduce_users(
            actual,
            rule,
            [WINDOW],
            retained_from=START,
            consumer=lambda p, u, v: users.__setitem__(u, v),
        )
        == expected
    )
    assert actual.scans == 2  # not root, then restart both children
    assert set(users) == {b"a", b"b", b"z"}
    assert users[b"a"].conversations == 2
    assert users[b"z"].conversations == 2
    assert users[b"b"].sampled == 1


def test_durable_guard_is_bounded_by_pages_not_physical_rows():
    calls = []
    source = ur.NativeSource(
        FakeClient([row(unit=str(i).encode()) for i in range(100)]),
        ["SELECT 1"],
        {},
        workspace_id=WORKSPACE,
        guard=lambda: calls.append(1),
    )
    assert ur.reduce_users(source, "any", [WINDOW], retained_from=START)[0].value == 1
    assert len(calls) <= 8


@pytest.mark.parametrize("rule", ["any", "every", "average", "total"])
def test_empty_hash_sibling_pruning_waits_for_full_source_eof(rule):
    # The overflowing prefix collides at hash zero. The late high-half user and
    # unassigned sampled row must survive, even though their sibling was empty
    # when its descriptor was scheduled. Mutable filters still follow latest.
    rows = [
        row(f"u{i}".encode(), i % 2, user_hash=0, unit=str(i).encode())
        for i in range(12)
    ]
    rows.extend(
        [
            row(b"late", 1, user_hash=1 << 63, unit=b"late"),
            row(b"", None, user_hash=ur.MAX_COUNT, unit=b"unassigned", probability=0.5),
        ]
    )
    expected = ur.reduce_users(MemorySource(rows), rule, [WINDOW], retained_from=START)
    source = MemorySource(rows, limits=ur.Limits(states=2))
    users = {}
    actual = ur.reduce_users(
        source,
        rule,
        [WINDOW],
        retained_from=START,
        consumer=lambda point, user, value: users.__setitem__(user, value),
    )
    assert actual == expected
    assert set(users) == {f"u{i}".encode() for i in range(12)} | {b"late"}
    assert actual[0].sampled_conversations == 1
    assert actual[0].conversations_without_user == 1
