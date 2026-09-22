"""Exact candidate acquisition on native constant fixtures; no tables or DDL.

Candidates may include stale witnesses. They must retain every possible match,
and their activity metrics/order must use all latest live spans, not just matches.
This is not production-performance or full Users API qualification.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.server_readonly import without_query_settings
from tracer.services.clickhouse.v2.id_remap_sql import resolved_id_expr
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.tests.test_trace_root_physical_replay import values_where
from tracer.tests.test_user_latest_window_replay import assert_window_replay, cte

PROJECT, FOREIGN = str(UUID(int=1001)), str(UUID(int=1002))
START = datetime(2026, 8, 1, 12, 15, tzinfo=UTC)
LONG = "long ASCII %_\\' text " * 100
CONTEXT = SimpleNamespace(server_info=SimpleNamespace(get_timezone=lambda: "UTC"))


def leaf(value, op="equals"):
    picker = op == "typed_in"
    return {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": "in" if picker else op,
            "filter_value": value,
            **({"attribute_value_types": ["string"] * len(value)} if picker else {}),
        },
    }


def builder(items, days=7):
    return UserListQueryBuilderV2(
        organization_id=PROJECT,
        project_ids=[PROJECT],
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        START.isoformat(),
                        (START + timedelta(days=days)).isoformat(),
                    ],
                },
            },
            *items,
        ],
    )


@pytest.mark.parametrize(
    "value,op",
    [
        ("123456", "equals"),
        (LONG, "equals"),
        (["Alpha", "Beta"], "in"),
        (["123456"], "typed_in"),
        # A picked string is the stored string and compares raw, whatever it
        # looks like: the value bloom witnesses it exactly.
        (["true"], "typed_in"),
        (['{"a":1,"b":2}', "[1,2]"], "typed_in"),
    ],
    ids=[
        "digit-string",
        "long-text",
        "multi-value",
        "picker",
        "picked-boolean-word",
        "picked-json-looking",
    ],
)
def test_plain_text_witness_prunes_groups_not_latest_activity(value, op):
    sql, params = builder([leaf(value, op)]).build_dimension_candidate_query(limit=65)
    assert "scalar_witness_identities AS" in sql
    witness = cte(sql, "scalar_witness_identities")
    assert "attrs_string" in witness
    assert "LIMIT" not in witness and "is_deleted" not in witness
    assert "attrs_string" not in cte(sql, "exact_usage")
    assert "filtered_end_users" not in cte(sql, "exact_usage")
    assert "INNER JOIN exact_usage AS xu ON xu.end_user_id = eu.end_user_id" in sql
    assert_window_replay(sql, params)


@pytest.mark.parametrize(
    "value,op",
    [
        ("", "equals"),
        ("true", "equals"),
        (" FALSE ", "equals"),
        ('{"a":1}', "equals"),
        ("[1,2]", "equals"),
        ("\u0130", "equals"),
        ("value", "not_equals"),
        (None, "is_null"),
        ("value", "contains"),
        (["safe", "true"], "in"),
        (['{"a":1}'], "in"),
        # A picked string is exact under the raw witness, but its lowering
        # is still Python's: a non-ASCII picked string keeps the complete path.
        (["gôld"], "typed_in"),
    ],
)
def test_unproven_text_shapes_keep_existing_complete_path(value, op):
    sql, _ = builder([leaf(value, op)]).build_dimension_candidate_query(limit=65)
    assert "scalar_witness_identities AS" not in sql


def population(needle):
    rows = []

    def add(
        user,
        identity,
        *,
        value=None,
        minute=20,
        version=1,
        deleted=0,
        cost=1,
        service="svc",
        project=PROJECT,
    ):
        rows.append(
            (
                project,
                service,
                "SPAN",
                identity,
                str(UUID(int=user)),
                START.replace(minute=minute, tzinfo=None).isoformat(" "),
                version,
                deleted,
                START.replace(minute=minute + 1, tzinfo=None).isoformat(" "),
                cost,
                [] if value is None else ["tag"],
                [] if value is None else [value],
            )
        )

    add(50, "matching-alias", value=needle, cost=2)
    add(10, "later-unmatched", value="unrelated", minute=40, cost=7)
    add(40, "reassigned", value=needle)
    add(20, "reassigned", value=needle, version=2, cost=3)
    add(40, "retained-old-user-activity", value="unrelated", cost=4)
    add(30, "deleted", value=needle)
    add(30, "deleted", value=needle, version=2, deleted=1)
    add(60, "never-matched", value="unrelated")
    add(70, "missing-string-key")
    add(80, "moved-out", value=needle)
    add(80, "moved-out", value=needle, version=2, minute=10)
    add(100, "deleted", value=needle, service="different-service")
    add(110, "foreign", value=needle, project=FOREIGN)
    add(120, "field-removed", value=needle)
    add(120, "field-removed", version=2)
    add(130, "without-curated-user", value=needle)
    return rows


def execute(
    rows,
    items,
    *,
    days=7,
    before=None,
    limit=65,
    redundant_membership=False,
    redundant_dimension_having=False,
    users=None,
):
    engine = pytest.importorskip("chdb")
    sql, params = builder(items, days).build_dimension_candidate_query(
        limit=limit,
        before_first_seen=before[0] if before else None,
        before_end_user_id=before[1] if before else None,
    )
    if redundant_dimension_having:
        # Restore the removed second binding of the witness population as a
        # differential control. It cost a whole extra witness scan and can only
        # drop users whose latest state carries no witnessed span at all.
        anchor = "\n            GROUP BY end_user_id\n"
        assert sql.count(anchor) == 1
        sql = sql.replace(
            anchor,
            anchor + "            HAVING end_user_id IN "
            "(SELECT end_user_id FROM scalar_candidate_users)\n",
        )
    if redundant_membership:
        # Restore the removed, redundant semijoin as a differential control.
        # Both versions retain the authoritative final dimension INNER JOIN.
        assert sql.count("WHERE latest_is_deleted = 0") == 1
        sql = sql.replace(
            "WHERE latest_is_deleted = 0",
            "WHERE latest_is_deleted = 0 AND "
            f"{resolved_id_expr('latest_end_user_id', 'span_eu_remap')} IN "
            "(SELECT end_user_id FROM filtered_end_users)",
        )
    rendered = without_query_settings(sql) % escape_params(params, CONTEXT)
    # These dimension fixtures have one current row per key. FINAL is unnecessary
    # only for these constant CTEs; no span predicate or version replay is removed.
    rendered = rendered.replace("end_users AS eu FINAL", "end_users AS eu")
    rendered = rendered.replace("end_user_id_remap FINAL", "end_user_id_remap")
    columns = (
        "project_id UUID, service_name String, observation_type String, id String, "
        "end_user_id Nullable(UUID), start_time DateTime64(6, 'UTC'), _version UInt64, "
        "is_deleted UInt8, end_time DateTime64(6, 'UTC'), cost Float64, "
        "fixture_keys Array(String), fixture_values Array(String)"
    )
    literals = escape_params(
        {
            "columns": columns,
            "rows": tuple(rows),
            "users": tuple(
                (str(UUID(int=n)), FOREIGN if n == 110 else PROJECT)
                for n in (users or (10, 20, 30, 40, 50, 60, 70, 80, 100, 110, 120))
            ),
            "remaps": (
                (str(UUID(int=10)), str(UUID(int=90))),
                (str(UUID(int=50)), str(UUID(int=90))),
            ),
            "org": PROJECT,
            "start": START.replace(tzinfo=None).isoformat(" "),
        },
        CONTEXT,
    )
    query = f"""WITH spans AS (
        SELECT *, id AS trace_id, mapFromArrays(fixture_keys, fixture_values) AS attrs_string,
            CAST(map(), 'Map(String, Float64)') AS attrs_number,
            CAST(map(), 'Map(String, UInt8)') AS attrs_bool,
            toUInt64(1) AS total_tokens, toUInt64(1) AS prompt_tokens,
            toUInt64(0) AS completion_tokens
        FROM values({literals["columns"]}, {literals["rows"][1:-1]})
    ), end_users AS (
        SELECT *, toUUID({literals["org"]}) AS organization_id,
            toString(end_user_id) AS user_id, 'custom' AS user_id_type,
            toUInt64(0) AS user_id_hash, toUInt8(0) AS is_deleted,
            toDateTime64({literals["start"]}, 6, 'UTC') AS first_seen, first_seen AS version
        FROM values('end_user_id UUID, project_id UUID', {literals["users"][1:-1]})
    ), end_user_id_remap AS (
        SELECT * FROM values('old_id UUID, new_id UUID', {literals["remaps"][1:-1]})
    ), {rendered.lstrip()[4:]}
    SETTINGS max_threads=1, max_execution_time=5, max_memory_usage=268435456
    """
    return [
        json.loads(line)
        for line in str(engine.query(values_where(query), "JSONEachRow")).splitlines()
        if line
    ]


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "needle,op",
    [
        ("123456", "equals"),
        (LONG, "equals"),
        ("Alpha", "in"),
        ("\N{KELVIN SIGN}ey", "equals"),
        ("\N{KELVIN SIGN}" * 10, "in"),
        ("123456", "typed_in"),
        (LONG, "typed_in"),
    ],
    ids=[
        "digit-string",
        "long-text",
        "multi-value",
        "kelvin",
        "many-kelvins",
        "picker",
        "long-picker",
    ],
)
def test_native_candidates_preserve_corrected_users_full_metrics_and_pagination(
    days, needle, op
):
    rows = population(needle)
    expected = (
        [needle.lower()]
        if op == "typed_in"
        else [needle.lower(), "another"]
        if op == "in"
        else needle.lower()
    )
    item = leaf(expected, op)
    actual = execute(rows, [item], days=days)
    assert actual == execute(rows, [item], days=days, redundant_membership=True)
    assert actual == execute(rows, [item], days=days, redundant_dimension_having=True)
    by_user = {row["end_user_id"]: row for row in actual}
    # Removed-field and reassigned-old-user witnesses remain conservative; final
    # membership is decided later. Never-matched users are safely pruned here.
    assert set(by_user) == {str(UUID(int=n)) for n in (10, 20, 40, 100, 120)}
    baseline = execute(rows, [], days=days)
    assert actual == [row for row in baseline if row["end_user_id"] in by_user]
    assert by_user[str(UUID(int=10))]["total_cost"] == 9
    assert int(by_user[str(UUID(int=10))]["num_traces"]) == 2
    assert by_user[str(UUID(int=20))]["total_cost"] == 3
    assert by_user[str(UUID(int=40))]["total_cost"] == 4
    paged, cursor = [], None
    for _ in range(4):
        page = execute(rows, [item], days=days, before=cursor, limit=2)
        if not page:
            break
        paged.extend(page)
        cursor = (
            datetime.fromisoformat(page[-1]["last_active"]),
            page[-1]["end_user_id"],
        )
    assert paged == actual


def swap_row(user, identity, value, *, minute, version):
    return (
        PROJECT,
        "svc",
        "SPAN",
        identity,
        str(UUID(int=user)),
        START.replace(minute=minute, tzinfo=None).isoformat(" "),
        version,
        0,
        START.replace(minute=minute + 1, tzinfo=None).isoformat(" "),
        1,
        ["tag"],
        [value],
    )


def test_single_witness_binding_only_widens_a_set_the_manager_already_rejects():
    """Dropping the second binding never loses a candidate, and never a match.

    THE RULE. A user belongs on the page only if the LATEST version of one of
    its spans satisfies the filter. That version is itself a physical row
    carrying both the witnessed value and the user, so its identity is in the
    witness set and the user is in the witness population. A user the removed
    HAVING would have dropped therefore has no witnessed span in latest state
    and cannot match; the manager's own per-user attribute check rejects it.

    A user in the witness population is never dropped in either direction, so
    the change can only widen an already conservative candidate set.
    """
    needle = "123456"
    rows = population(needle)
    # `swapped` is admitted by the alias filter through its old user, and its
    # LATEST version hands the span to a user with no witnessed span anywhere.
    rows.append(swap_row(140, "swapped-witness", needle, minute=20, version=1))
    rows.append(swap_row(140, "swapped", "unrelated", minute=25, version=1))
    rows.append(swap_row(150, "swapped", "unrelated", minute=25, version=2))
    users = (10, 20, 30, 40, 50, 60, 70, 80, 100, 110, 120, 140, 150)
    item = leaf(needle, "equals")
    widened = execute(rows, [item], users=users)
    narrowed = execute(rows, [item], users=users, redundant_dimension_having=True)
    swapped = str(UUID(int=150))
    assert {row["end_user_id"] for row in narrowed} < {
        row["end_user_id"] for row in widened
    }
    assert {row["end_user_id"] for row in widened} - {
        row["end_user_id"] for row in narrowed
    } == {swapped}
    # Nothing a page publishes moved: shared users keep identical metrics and
    # the surviving order is the same sequence with the extra row spliced in.
    assert [row for row in widened if row["end_user_id"] != swapped] == narrowed
    # The widened row carries no witnessed value in latest state, which is why
    # the manager rejects it rather than publishing it.
    assert not [row for row in rows if row[4] == swapped and needle in row[-1]]


@pytest.mark.parametrize(
    "filter_type,value", [("text", "123456"), ("number", 7)], ids=["text", "numeric"]
)
def test_widened_candidate_without_a_recorded_value_is_never_published(
    filter_type, value
):
    """The second half of the rule, on the side that decides publication.

    The numeric witness is the case that has no text accelerator: its
    `attribute_exact_text_filters` is empty, so the per-batch prune returns the
    candidates untouched and `_row_matches_filters` is the only reader left.
    Enrichment records absence for every requested user, so a candidate the
    widened acquisition admitted but that carries no value for the filtered key
    fails the exact check instead of reaching the page.
    """
    from tracer.services.users_list_manager import UsersListManager

    leaf_item = {
        "column_id": "tag",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": filter_type,
            "filter_op": "equals",
            "filter_value": value,
        },
    }
    manager = UsersListManager(
        organization_id=PROJECT,
        allowed_project_ids=[PROJECT],
        project_id=PROJECT,
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        START.isoformat(),
                        (START + timedelta(days=7)).isoformat(),
                    ],
                },
            },
            leaf_item,
        ],
        requested_columns=["user_id"],
        attribute_keys=["tag"],
    )
    assert manager.filters_need_enrichment
    assert bool(manager.attribute_exact_text_filters) is (filter_type == "text")
    widened = str(UUID(int=150))
    manager._attribute_values_by_user[widened] = {}
    manager._attribute_value_types_by_user[widened] = {}
    assert not manager._row_matches_filters(
        {"end_user_id": widened, "user_id": "widened"}
    )
