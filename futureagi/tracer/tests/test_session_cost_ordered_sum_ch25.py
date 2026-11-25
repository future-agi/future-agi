"""Ordered compensated cost sums on the CI ClickHouse 25.3 sidecar.

One multiset of costs must give one Float64 value, whatever the insert order,
part layout, thread count or merge state. Values are compared as their bits
(reinterpretAsUInt64) and checked against math.fsum of the values the engine
read back, which is an exact, independent reference. An exact value is pinned
only when the reduced array has fewer than four elements, zero-cost elements
included: a sequential and a four-lane unrolled compensated sum can differ from
four elements on, even when the exact sum is representable. Longer arrays must
give one value within the compensated-sum error bound. Non-finite inputs are
pinned by IEEE class only.

Rows are inserted in the native binary format, one INSERT per row, so stored
values never pass through float text and every row starts in its own part.
Only list filter thresholds travel as SQL text: clickhouse-driver inlines them
with repr(), and the server must parse each one back to the same double.
"""

from __future__ import annotations

import itertools
import math
import random
import struct
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from uuid import UUID, uuid4

import pytest

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse.application_read_policy import (
    application_read_settings,
)
from tracer.services.clickhouse.query_builders.trace_usage import (
    deterministic_sum_sql,
    ordered_float_sum_sql,
)
from tracer.services.clickhouse.v2.query_builders.session_detail import (
    build_session_detail_aggregate_query,
    build_session_detail_traces_query,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_openrouter_broadcast_list_rollups_ch25 import (
    _REMAP_DDL,
    _SPANS_DDL,
    _TRACES_DDL,
)

pytestmark = pytest.mark.integration

START: Final[datetime] = datetime(2026, 10, 20, 12, tzinfo=UTC)
UNIT_ROUNDOFF: Final[float] = 2.0**-53
PRICES: Final[tuple[float, ...]] = (1.5e-07, 2e-06, 3e-06, 1e-05, 6e-05)
THREADS: Final[tuple[int, ...]] = (1, 8)
QueryAndParams = tuple[str, dict[str, Any]]

# Chosen offline: for each array, a plain (unsorted) Kahan pass over the ten
# rotations used below gives at least two distinct results, both for a
# sequential pass and for a four-lane unrolled pass. Through the helper every
# rotation must give one result, so a sort dropped by any optimizer shows here.
# Each string is one array, written with repr so float() reads back the exact
# value; the rows are inserted as binary Float64.
SORT_SENSITIVE_ARRAYS: Final[tuple[tuple[float, ...], ...]] = tuple(
    tuple(float(term) for term in line.split())
    for line in (
        "0.012910000000000001 0.004594 0.0005787 0.02135 1.86 19.66 0.0005991 0.07308 18.54 0.0004617",
        "36.96 26.0 12.75 26.17 0.00045615 0.00624 2.9175 0.16122 0.043500000000000004 0.000676 0.011340000000000001 0.008199 0.006385999999999999 0.004557",
        "0.2725 0.08568 0.0021200000000000004 23.41 0.10776000000000001 0.006840000000000001 0.03852 0.09486 31.86 0.003246",
        "0.001416 0.12666 0.005520000000000001 0.003156 0.59 7.0275 0.0984 0.00995 0.033830000000000006 30.3 15.55 0.012870000000000001 0.16074 8.145",
        "0.00019935 0.01315 9.9e-06 0.21546 0.005834 0.0075450000000000005 4.8825 0.008127 0.000504 0.09648 0.0004919999999999999",
        "0.00759 4.8725000000000005 0.011484000000000001 3.515 0.01446 0.00037214999999999997 0.08898 34.56 3.455 0.005248",
        "0.00407 0.005428 0.0002547 0.022660000000000003 27.03 7.829999999999999e-05 4.99 25.59 0.003932 16.29 0.00059985 0.00524",
        "0.002751 0.0001182 0.000744 0.013640000000000001 39.78 0.06810000000000001 7.01 35.5 24.85 9.01 0.00051915",
        "0.12342 0.0001338 0.006595999999999999 0.000225 8.8325 0.00015675 0.007332 0.00868 0.03563 0.00022244999999999999 0.004816 0.0004954499999999999",
        "10.31 26.09 0.011568 0.006725999999999999 6.2 0.033 0.0064919999999999995 26.25 32.52 0.005304",
        "0.13434000000000001 5.4675 12.49 5.67 0.005901 0.009393 0.00047745 0.014870000000000001 0.075 4.9625 1.9175 0.003828 3.4875000000000003",
        "0.014220000000000002 0.002097 28.32 0.002794 0.00012375 7.6775 0.005184 4.0675 0.016200000000000003 14.9",
        "0.005925000000000001 0.21504 9.93 0.10578 0.0001827 0.000408 0.00771 0.00055755 2.9050000000000002 0.004314 0.00649 0.012360000000000001 0.000465 0.00010095",
        "0.010818 33.78 8.615 0.007696 0.0030919999999999997 0.00011399999999999999 0.006370000000000001 3.78e-05 0.000508 0.002145",
        "0.1416 0.011430000000000001 0.00056715 0.011751000000000001 27.990000000000002 18.990000000000002 0.008208 0.003336 0.007942 0.00012524999999999998",
        "0.00293 2.6025 0.01484 0.000168 12.6 0.00858 38.56 0.004365 0.00059685 7.61 0.0035 2.0375 0.039560000000000005 0.06444",
        "0.0148 0.00016635 0.006626 0.0018679999999999999 0.00051675 0.01882 0.5 30.14 0.007338 0.0014559999999999998",
        "5.1000000000000005 0.00040605 0.011799 0.005901 0.14124 1.5225 0.0005535 1.6 0.0031000000000000003 0.22044 0.011211 8.85 1.11",
        "30.62 2.5949999999999997e-05 0.022330000000000003 4.615 0.011997 0.001704 6.48 3.67 0.00961 0.01764 0.0003342 4.165 0.00302 0.009093",
        "17.94 0.00091 0.005148 5.2325 0.010974000000000001 0.0001785 8.105 0.05562 0.0060999999999999995 0.005058 0.00057045",
    )
)


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs() -> Iterator[None]:
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column() -> Iterator[None]:
    yield


@pytest.fixture
def ch() -> Iterator[Any]:
    """A fresh owned database per test, so OPTIMIZE never merges another test."""
    with _ch_test_owned_database("test_session_cost_ordered_") as database:
        with _ch_test_native_client(database=database) as client:
            for ddl in (_SPANS_DDL, _TRACES_DDL, _REMAP_DDL):
                client.execute(ddl)
            client.execute(
                "CREATE TABLE cost_arrays (k UInt32, v Array(Float64)) "
                "ENGINE = MergeTree ORDER BY k"
            )
            client.execute("SYSTEM STOP MERGES spans")
            yield client


def _bits(value: float) -> int:
    unpacked: int = struct.unpack("<Q", struct.pack("<d", value))[0]
    return unpacked


def _rows(
    client: Any, query: QueryAndParams, *, threads: int | None = None
) -> list[dict[str, Any]]:
    sql, params = query
    settings: dict[str, Any] = dict(application_read_settings())
    if threads is not None:
        settings["max_threads"] = threads
    data, columns = client.execute(
        sql, params, with_column_types=True, settings=settings
    )
    return [
        dict(zip((column[0] for column in columns), row, strict=True)) for row in data
    ]


def _ordered_sums(
    client: Any, arrays: Sequence[Sequence[float]]
) -> list[tuple[int, float, list[float]]]:
    """(bits, value, the array as the engine stored it) per input array."""
    client.execute("TRUNCATE TABLE cost_arrays")
    client.execute(
        "INSERT INTO cost_arrays (k, v) VALUES",
        [(index, list(values)) for index, values in enumerate(arrays)],
    )
    helper = ordered_float_sum_sql("v")
    data = client.execute(
        f"SELECT reinterpretAsUInt64({helper}) AS total_bits, {helper} AS total, v "
        "FROM cost_arrays ORDER BY k"
    )
    return [(int(row[0]), float(row[1]), list(row[2])) for row in data]


def _within_compensated_bound(value: float, terms: Sequence[float]) -> bool:
    """|value - exact| <= 3u * sum|x|: the bound for any signs, one level."""
    exact = math.fsum(terms)
    magnitude = math.fsum(abs(term) for term in terms)
    return abs(value - exact) <= 3 * UNIT_ROUNDOFF * magnitude


# ---------------------------------------------------------------------------
# The helper on literal arrays
# ---------------------------------------------------------------------------


def test_every_order_of_three_terms_gives_the_exact_sum(ch: Any) -> None:
    results = _ordered_sums(ch, list(itertools.permutations((0.1, 0.2, 0.3))))
    assert len(results) == 6
    assert {bits for bits, _value, _stored in results} == {_bits(0.6)}


def test_long_array_gives_one_value_within_two_ulps_in_every_order(ch: Any) -> None:
    values = [(17 * index % 4001 + 1) * PRICES[index % 5] for index in range(2000)]
    permutations: list[list[float]] = []
    for seed in range(50):
        shuffled = list(values)
        random.Random(seed).shuffle(shuffled)
        permutations.append(shuffled)
    results = _ordered_sums(ch, permutations)
    assert len({bits for bits, _value, _stored in results}) == 1
    _bits_value, total, stored = results[0]
    exact = math.fsum(stored)
    assert abs(total - exact) <= 2 * math.ulp(exact)


@pytest.mark.parametrize(
    "index", range(len(SORT_SENSITIVE_ARRAYS)), ids=lambda i: f"array{i}"
)
def test_sort_is_kept_so_rotations_give_one_value(ch: Any, index: int) -> None:
    values = list(SORT_SENSITIVE_ARRAYS[index])
    rotations = [values[shift:] + values[:shift] for shift in range(10)]
    results = _ordered_sums(ch, rotations)
    assert len({bits for bits, _value, _stored in results}) == 1
    _bits_value, total, stored = results[0]
    exact = math.fsum(stored)
    assert abs(total - exact) <= 2 * math.ulp(exact)


@pytest.mark.parametrize(
    "values,expected",
    [
        pytest.param([], 0.0, id="empty"),
        pytest.param([-0.0], 0.0, id="negative-zero"),
        pytest.param([-0.0, -0.0], 0.0, id="negative-zeros"),
        pytest.param([0.0, -0.0], 0.0, id="mixed-zeros"),
        pytest.param([0.123], 0.123, id="one-term"),
    ],
)
def test_empty_zero_and_single_term_bits(
    ch: Any, values: list[float], expected: float
) -> None:
    ((bits, _value, _stored),) = _ordered_sums(ch, [values])
    # +0.0, never -0.0, for every all-zero input.
    assert bits == _bits(expected)


@pytest.mark.parametrize(
    "values,expected",
    [
        pytest.param([1.0, math.inf, math.inf], math.inf, id="inf"),
        pytest.param([-math.inf, 0.1, 0.2], -math.inf, id="negative-inf"),
        pytest.param([math.inf, -math.inf], math.nan, id="opposite-infs"),
        pytest.param([1e308, 1e308, 1e308], math.inf, id="finite-overflow"),
        pytest.param([9e307, 9e307, 9e307, 1.0], math.inf, id="finite-overflow-tail"),
    ],
)
def test_non_finite_terms_and_overflow_keep_the_plain_sum_class(
    ch: Any, values: list[float], expected: float
) -> None:
    ((_bits_value, total, _stored),) = _ordered_sums(ch, [values])
    if math.isnan(expected):
        assert math.isnan(total)
    else:
        assert total == expected


@pytest.mark.parametrize(
    "values",
    [
        pytest.param((0.0015, 0.0045, -0.006), id="refund-cancels"),
        pytest.param((0.1, 0.2, -0.3, 1e-12), id="refund-residue"),
    ],
)
def test_mixed_signs_give_one_value_within_the_absolute_bound(
    ch: Any, values: tuple[float, ...]
) -> None:
    results = _ordered_sums(ch, list(itertools.permutations(values)))
    assert len({bits for bits, _value, _stored in results}) == 1
    _bits_value, total, stored = results[0]
    assert _within_compensated_bound(total, stored)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "SELECT CAST(number, 'Nullable(Float64)') AS x FROM numbers(0)",
            id="nullable-no-rows",
        ),
        pytest.param(
            "SELECT CAST(NULL, 'Nullable(Float64)') AS x FROM numbers(3)",
            id="nullable-all-null",
        ),
        pytest.param(
            "SELECT toFloat64(number) AS x FROM numbers(0)", id="float-no-rows"
        ),
        pytest.param(
            "SELECT if(number = 1, NULL, toFloat64(number) / 10) AS x FROM numbers(4)",
            id="nullable-some-null",
        ),
    ],
)
def test_aggregate_helper_keeps_the_sum_result_and_type(ch: Any, source: str) -> None:
    helper = deterministic_sum_sql("x")
    ((total, total_type, plain, plain_type),) = ch.execute(
        f"SELECT {helper} AS total, toTypeName({helper}), sum(x), toTypeName(sum(x)) "
        f"FROM ({source})"
    )
    assert total_type == plain_type
    if plain is None:
        assert total is None
    else:
        assert _bits(total) == _bits(plain)


def test_ordered_sum_type_is_float64(ch: Any) -> None:
    ch.execute("INSERT INTO cost_arrays (k, v) VALUES", [(0, [0.5, 0.25])])
    helper = ordered_float_sum_sql("v")
    assert ch.execute(f"SELECT toTypeName({helper}) FROM cost_arrays") == [("Float64",)]


# ---------------------------------------------------------------------------
# Session totals through the real builders
# ---------------------------------------------------------------------------


def _insert_span(
    client: Any,
    *,
    project: UUID,
    session: UUID,
    trace: str,
    span: str,
    cost: float,
    offset_seconds: int,
    parent: str = "",
    unknown_cost: bool = False,
) -> None:
    start = START + timedelta(seconds=offset_seconds)
    row: dict[str, Any] = {
        "project_id": project,
        "observation_type": "llm",
        "service_name": "",
        "start_time": start,
        "trace_id": trace,
        "id": span,
        "parent_span_id": parent,
        "name": "request",
        "end_time": start + timedelta(seconds=1),
        "trace_session_id": session,
        "prompt_tokens": 10,
        "completion_tokens": 2,
        "total_tokens": 12,
        "cost": cost,
        "attrs_string": {
            "fi.cost.kind": "unknown" if unknown_cost else "provider_reported"
        },
        "input": "synthetic input",
        "output": "synthetic output",
        "is_deleted": 0,
        "_version": 1,
    }
    # One INSERT per row: every span starts in its own part.
    client.execute(
        f"INSERT INTO spans ({', '.join(row)}) VALUES", [tuple(row.values())]
    )


def _insert_session(
    client: Any, project: UUID, costs: Sequence[float], *, first_offset: int = 0
) -> UUID:
    """One single-span trace per cost, each inserted on its own."""
    session = uuid4()
    for index, cost in enumerate(costs):
        trace = str(uuid4())
        _insert_span(
            client,
            project=project,
            session=session,
            trace=trace,
            span=f"span-{index}",
            cost=cost,
            offset_seconds=first_offset + index,
        )
    return session


def _time_filter() -> dict[str, Any]:
    return {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [
                START.isoformat(),
                (START + timedelta(days=1)).isoformat(),
            ],
        },
    }


def _usage_filter(column: str, operation: str, value: float) -> dict[str, Any]:
    return {
        "column_id": column,
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "number",
            "filter_op": operation,
            "filter_value": value,
        },
    }


def _detail_total(
    client: Any, project: UUID, session: UUID, *, threads: int | None = None
) -> Any:
    (row,) = _rows(
        client,
        build_session_detail_aggregate_query(str(project), (str(session),)),
        threads=threads,
    )
    return row["total_cost"]


def _detail_trace_costs(client: Any, project: UUID, session: UUID) -> list[float]:
    rows = _rows(
        client,
        build_session_detail_traces_query(
            str(project), (str(session),), limit=200, offset=0
        ),
    )
    return [float(row["total_cost"]) for row in rows]


def _hydration_total(
    client: Any, project: UUID, session: UUID, *, threads: int | None = None
) -> Any:
    builder = SessionListQueryBuilderV2(
        project_id=str(project), filters=[_time_filter()]
    )
    rows = _rows(
        client, builder.build_page_hydration_query([str(session)]), threads=threads
    )
    (row,) = [row for row in rows if str(row["session_id"]) == str(session)]
    return row["total_cost"]


def _admitted_sessions(
    client: Any,
    project: UUID,
    operation: str,
    value: float,
    *,
    threads: int | None = None,
) -> set[str]:
    builder = SessionListQueryBuilderV2(
        project_id=str(project),
        filters=[_time_filter(), _usage_filter("total_cost", operation, value)],
        page_size=100,
    )
    return {
        str(row["session_id"])
        for row in _rows(client, builder.build_candidate_page_query(), threads=threads)
    }


def _list_matches_detail(
    client: Any, project: UUID, session: UUID, *, threads: int
) -> int:
    """Bits of the detail total, once the list has shown and filtered on it."""
    total = _detail_total(client, project, session, threads=threads)
    # The list shows the same bits as the detail header ...
    hydrated = _hydration_total(client, project, session, threads=threads)
    assert _bits(hydrated) == _bits(total)
    # ... and filters on exactly that value.
    for operation in ("greater_than_or_equal", "less_than_or_equal"):
        admitted = _admitted_sessions(
            client, project, operation, total, threads=threads
        )
        assert str(session) in admitted, operation
    excluded = _admitted_sessions(
        client, project, "greater_than", total, threads=threads
    )
    assert str(session) not in excluded
    return _bits(total)


def _reads_across_threads_and_merge(
    client: Any, project: UUID, sessions: Sequence[UUID]
) -> dict[UUID, set[int]]:
    """Detail and list reads at every thread count, on unmerged parts and after."""
    seen: dict[UUID, set[int]] = {session: set() for session in sessions}
    for threads in THREADS:
        for _repeat in range(5):
            for session in sessions:
                total = _detail_total(client, project, session, threads=threads)
                seen[session].add(_bits(total))
        for session in sessions:
            seen[session].add(
                _list_matches_detail(client, project, session, threads=threads)
            )
    client.execute("SYSTEM START MERGES spans")
    client.execute("OPTIMIZE TABLE spans FINAL")
    assert client.execute(
        "SELECT count() FROM system.parts "
        "WHERE database = currentDatabase() AND table = 'spans' AND active"
    ) == [(1,)]
    for threads in THREADS:
        for session in sessions:
            seen[session].add(
                _list_matches_detail(client, project, session, threads=threads)
            )
    return seen


def test_session_total_is_one_value_across_reads_layouts_and_surfaces(
    ch: Any,
) -> None:
    project = uuid4()
    small = _insert_session(ch, project, (0.1, 0.2, 0.3))
    realistic_costs = [
        (137 * index % 3001 + 50) * PRICES[index % 5] for index in range(40)
    ]
    large = _insert_session(ch, project, realistic_costs, first_offset=100)
    seen = _reads_across_threads_and_merge(ch, project, (small, large))

    assert seen[small] == {_bits(0.6)}
    assert len(seen[large]) == 1
    large_total = _detail_total(ch, project, large)
    trace_costs = _detail_trace_costs(ch, project, large)
    assert len(trace_costs) == 40
    exact = math.fsum(trace_costs)
    assert abs(large_total - exact) <= 2 * math.ulp(exact)


def test_session_total_contract_for_empty_unknown_one_and_two_traces(
    ch: Any,
) -> None:
    project = uuid4()
    assert _detail_total(ch, project, uuid4()) is None

    unknown = uuid4()
    _insert_span(
        ch,
        project=project,
        session=unknown,
        trace=str(uuid4()),
        span="unknown",
        cost=0.0,
        offset_seconds=0,
        unknown_cost=True,
    )
    assert _detail_total(ch, project, unknown) is None

    single = _insert_session(ch, project, (0.1234567,), first_offset=10)
    (trace_cost,) = _detail_trace_costs(ch, project, single)
    assert _bits(_detail_total(ch, project, single)) == _bits(trace_cost)
    assert _bits(trace_cost) == _bits(0.1234567)

    pair = _insert_session(ch, project, (0.1, 0.2), first_offset=20)
    # Two terms: the compensated sum is the plain IEEE sum, 0.30000000000000004.
    assert _bits(_detail_total(ch, project, pair)) == _bits(0.1 + 0.2)
