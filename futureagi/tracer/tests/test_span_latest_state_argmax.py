"""The span read path resolves latest state by six-key argMax, never FINAL.

Three contracts are pinned here.

1. **Shape.** Every span acquisition, classification, content and list
   statement collapses ``spans`` with one packed ``argMax(tuple(...), _version)``
   grouped by the deployed ReplacingMergeTree sorting key, and no statement
   carries ``FINAL``.
2. **Projection.** The collapse projects the invariant floor plus exactly the
   stored columns its consuming statement reads — no less (an unprojected
   column is an unknown identifier at runtime) and no fat payload column more
   (an aggregate pins columns ClickHouse would otherwise prune out of the
   ``SELECT *`` source this replaced).
3. **Equivalence.** On a synthetic multi-version population the collapse elects
   the same whole physical row as a ``FINAL`` twin, and for equal-version ties —
   where no storage winner exists — it still elects one whole row rather than a
   mixture. The group keys and packed column order are read back out of the
   rendered SQL, so the model is checked against the builder rather than a
   restatement of it.

The window-wide oracle statement and its ``FINAL`` twin (``oracle_sql``) are the
reference digest recipe for the read-only production replay; the twin exists
only here, never on the read path.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tracer.services.clickhouse.v2.query_builders.span_list import (
    _PHYSICAL_SPAN_COLUMNS,
    _PHYSICAL_SPAN_FLOOR_COLUMNS,
    _PHYSICAL_SPAN_GROUP_BY_SQL,
    _PHYSICAL_SPAN_KEY_COLUMNS,
    SpanListQueryBuilderV2,
)

PROJECT = "11111111-1111-1111-1111-111111111111"
OTHER_PROJECT = "22222222-2222-2222-2222-222222222222"
START = datetime(2026, 8, 8, 12)
WINDOW_END = START + timedelta(days=7)
SCHEMA_DIR = (
    Path(__file__).resolve().parents[1] / "services" / "clickhouse" / "v2" / "schema"
)

# Stored columns no span statement filters or presents; an aggregate must never
# name one unless the consuming statement reads it.
PAYLOAD_COLUMNS = (
    "input",
    "output",
    "attributes_extra",
    "resource_attrs",
    "metadata",
    "span_events",
    "tags",
    "status_message",
)


def time_filter(start=START, end=WINDOW_END):
    return {
        "column_id": "created_at",
        "filter_config": {
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [start.isoformat(), end.isoformat()],
        },
    }


def attr_filter(filter_type="text", filter_op="in", filter_value=("acct-1", "acct-2")):
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": filter_type,
        "filter_op": filter_op,
        "filter_value": list(filter_value)
        if isinstance(filter_value, tuple)
        else filter_value,
    }
    if filter_type == "text":
        config["attribute_value_types"] = ["string"] * len(config["filter_value"])
    return {"column_id": "account_id", "filter_config": config}


def column_filter(column_id="model", filter_value="model-a"):
    return {
        "column_id": column_id,
        "filter_config": {
            "filter_type": "text",
            "filter_op": "equals",
            "filter_value": filter_value,
        },
    }


def builder(filters=None, **kwargs):
    return SpanListQueryBuilderV2(
        **{
            "project_id": PROJECT,
            "filters": filters
            if filters is not None
            else [time_filter(), attr_filter()],
            "bounded_internal_scan": True,
            **kwargs,
        }
    )


def seed_row(**overrides):
    return {
        "project_id": PROJECT,
        "trace_id": "trace",
        "id": "span",
        "observation_type": "span",
        "service_name": "service-a",
        "start_time": START + timedelta(minutes=20),
        "_version": 1,
        **overrides,
    }


def rendered_statements():
    """Every span statement whose source is a latest-state collapse."""

    target = builder()
    numeric = builder([time_filter(), attr_filter("number", "equals", 1)])
    native = builder([time_filter(), attr_filter(), column_filter()])
    annotated = builder(
        [
            time_filter(),
            {
                "column_id": "has_annotation",
                "filter_config": {
                    "filter_type": "boolean",
                    "filter_op": "equals",
                    "filter_value": True,
                },
            },
        ],
        annotation_label_ids=["33333333-3333-3333-3333-333333333333"],
    )
    org = builder(project_ids=[PROJECT, OTHER_PROJECT])
    identity_only = builder(bounded_identity_only=True)
    plain = SpanListQueryBuilderV2(
        project_id=PROJECT, filters=[time_filter()], page_size=25
    )
    # The remap wrap re-projects the resolved id through a second layer.
    remapped = SpanListQueryBuilderV2(
        project_id=PROJECT,
        filters=[time_filter()],
        page_size=25,
        end_user_id="44444444-4444-4444-4444-444444444444",
    )
    slice_bounds = {
        "slice_start": START,
        "slice_end": START + timedelta(hours=1),
        "limit": 26,
    }
    statements = {
        "seed": target.build_filter_seed_page(**slice_bounds),
        "seed_numeric": numeric.build_filter_seed_page(**slice_bounds),
        "seed_native": native.build_filter_seed_page(**slice_bounds),
        "seed_org": org.build_filter_seed_page(**slice_bounds),
        "navigation_seed": target.build_filter_navigation_seed_page(
            direction="newer", **slice_bounds
        ),
        "classify": target.build_filter_match_query_from_seed_rows([seed_row()]),
        "classify_numeric": numeric.build_filter_match_query_from_seed_rows(
            [seed_row()]
        ),
        "classify_annotation": annotated.build_filter_match_query_from_seed_rows(
            [seed_row()]
        ),
        "classify_identity_only": identity_only.build_filter_match_query_from_seed_rows(
            [seed_row()]
        ),
        "content": target.build_content_query(
            ["span"],
            span_identities=[(PROJECT, "trace", "span", START, "span", "service-a")],
        ),
        "list": plain.build(),
        "list_end_user_remap": remapped.build(),
        # The sparse probe carries no value predicate; the ordered one does.
        "anchor_probe": builder([time_filter()]).build_filter_anchor_probe(limit=26),
        "anchor_probe_filtered": target.build_filter_anchor_probe(limit=26),
    }
    return {name: sql for name, (sql, _params) in statements.items()}


COLLAPSE_RE = re.compile(
    r"SELECT (project_id,.*?AS _physical_winner.*?)\s*FROM spans\s*"
    r"(PREWHERE.*?GROUP BY [^\n]*)\s*\) AS (latest_\w+)",
    re.DOTALL,
)


def collapse_sources(sql):
    """Each latest-state source in *sql* as (projection, scope) text pairs."""

    return [(match.group(1), match.group(2)) for match in COLLAPSE_RE.finditer(sql)]


def group_by_keys(scope_sql):
    keys = re.search(r"GROUP BY ([^\n]+)", scope_sql).group(1)
    return [key.strip() for key in keys.split(",")]


def projected_columns(projection_sql):
    aliases = re.findall(r"_physical_winner\.\d+ AS (\w+)", projection_sql)
    return set(_PHYSICAL_SPAN_KEY_COLUMNS) | set(aliases)


def packed_columns(projection_sql):
    packed = re.search(r"argMax\(tuple\((.*?)\), _version\)", projection_sql, re.DOTALL)
    return tuple(column.strip() for column in packed.group(1).split(","))


@pytest.mark.unit
def test_projected_column_set_is_the_deployed_stored_column_set():
    body = (
        (SCHEMA_DIR / "002_spans_v2.sql")
        .read_text()
        .split("CREATE TABLE IF NOT EXISTS spans", 1)[1]
    )
    stored = []
    for line in body.splitlines():
        text = line.strip()
        if text.startswith(("INDEX ", "PROJECTION ")):
            break
        declared = re.match(r"([A-Za-z_]\w*)\s+\S", text)
        if declared and not text.startswith("--") and "MATERIALIZED" not in text:
            stored.append(declared.group(1))
    assert tuple(stored) == _PHYSICAL_SPAN_COLUMNS
    # Later schema files may only add computed columns to ``spans``; a stored
    # one would have to join the projection too, so fail loudly if that changes.
    for schema in sorted(SCHEMA_DIR.glob("*.sql")):
        for statement in re.findall(
            r"ALTER TABLE spans\b(.*?);", schema.read_text(), re.DOTALL
        ):
            for added in re.findall(
                r"ADD COLUMN IF NOT EXISTS(.*?)(?=,\s*(?:ADD|MODIFY)\b|$)",
                statement,
                re.DOTALL,
            ):
                assert "MATERIALIZED" in added or "ALIAS" in added, (
                    schema.name,
                    added.strip(),
                )


SPANS_FINAL_RE = re.compile(r"\bspans\s+FINAL\b")


@pytest.mark.unit
def test_no_span_statement_reads_through_final():
    # Annotation/eval residuals still read their own CDC mirror with FINAL;
    # this contract is about the ``spans`` table the owner's rule names.
    for name, sql in rendered_statements().items():
        assert SPANS_FINAL_RE.search(sql) is None, name


@pytest.mark.unit
def test_every_collapse_packs_one_winner_grouped_by_the_sorting_key():
    for name, sql in rendered_statements().items():
        sources = collapse_sources(sql)
        assert sources, name
        for projection, scope in sources:
            # One packed winner per source: independent per-column aggregates
            # could assemble a row out of two equal-version rows.
            assert projection.count("argMax(") == 1, name
            assert projection.count("argMax(tuple(") == 1, name
            assert f"GROUP BY {_PHYSICAL_SPAN_GROUP_BY_SQL}" in scope, name
            # Grouping on the declared sorting key lets the appended
            # optimize_aggregation_in_order stream the collapse in key order.
            assert "optimize_aggregation_in_order = 1" in sql, name
            packed = packed_columns(projection)
            assert len(set(packed)) == len(packed), name
            assert not set(packed) & set(_PHYSICAL_SPAN_KEY_COLUMNS), name


@pytest.mark.unit
def test_collapse_projects_every_stored_column_its_consumer_reads():
    for name, sql in rendered_statements().items():
        for projection, scope in collapse_sources(sql):
            available = projected_columns(projection)
            consumer = sql.replace(projection, " ").replace(scope, " ")
            referenced = {
                column
                for column in _PHYSICAL_SPAN_COLUMNS
                if re.search(rf"\b{column}\b", consumer)
            }
            assert not referenced - available, (name, sorted(referenced - available))


@pytest.mark.unit
def test_collapse_omits_payload_columns_no_consumer_reads():
    statements = rendered_statements()
    projection, _scope = collapse_sources(statements["seed"])[0]
    assert projected_columns(projection) == _PHYSICAL_SPAN_FLOOR_COLUMNS | {
        "attrs_string"
    }
    for column in PAYLOAD_COLUMNS:
        assert column not in projection
    # Content is the one statement that does read the payload.
    content_projection, _ = collapse_sources(statements["content"])[0]
    for column in ("input", "output", "attributes_extra"):
        assert column in packed_columns(content_projection)


# ── window-wide oracle ────────────────────────────────────────────────────────

ORACLE_GROUP_BY = (
    "project_id, trace_id, id, toStartOfHour(start_time), "
    "observation_type, service_name"
)
ORACLE_ORDER_BY = (
    "st DESC, id DESC, trace_id DESC, toString(project_id) DESC, "
    "observation_type DESC, service_name DESC"
)


def oracle_sql(*, final_twin=False):
    """The window-wide latest-state reference for one (filter, window).

    One statement, no slicing and no early stop: the digest every measured page
    is read against. ``final_twin`` renders the same question through the engine
    merge instead of the collapse — the cross-check that licenses replacing
    ``FINAL`` on the read path, and the only place a span read may name it.
    """

    if final_twin:
        return f"""
        SELECT project_id, trace_id, id, toStartOfHour(start_time) AS h,
               observation_type, service_name, start_time AS st
        FROM spans FINAL
        PREWHERE project_id = %(project_id)s
          AND start_time >= %(window_start)s AND start_time < %(window_end)s
        WHERE is_deleted = 0
          AND mapContains(attrs_string, %(attribute_key)s)
          AND lowerUTF8(toString(attrs_string[%(attribute_key)s]))
              IN %(attribute_values)s
        ORDER BY {ORACLE_ORDER_BY}
        LIMIT %(oracle_limit)s
        """
    return f"""
        SELECT project_id, trace_id, id, toStartOfHour(start_time) AS h,
               observation_type, service_name,
               argMax(
                   tuple(start_time, is_deleted, mapContains(attrs_string, %(attribute_key)s),
                         attrs_string[%(attribute_key)s]),
                   _version
               ) AS w,
               w.1 AS st, w.2 AS del, w.3 AS ex, w.4 AS val
        FROM spans
        PREWHERE project_id = %(project_id)s
          AND start_time >= %(window_start)s AND start_time < %(window_end)s
        GROUP BY {ORACLE_GROUP_BY}
        HAVING del = 0 AND ex AND lowerUTF8(toString(val)) IN %(attribute_values)s
        ORDER BY {ORACLE_ORDER_BY}
        LIMIT %(oracle_limit)s
        """


@pytest.mark.unit
def test_window_wide_oracle_asks_one_exact_unsliced_question():
    reference, twin = oracle_sql(), oracle_sql(final_twin=True)
    assert "FINAL" not in reference and "FROM spans FINAL" in twin
    for sql in (reference, twin):
        # The oracle is uncapped and unsampled; only the page-depth limit and
        # the request window bound it, and both arms order identically.
        assert "SAMPLE" not in sql and "toStartOfHour" in sql
        assert sql.count("LIMIT") == 1 and "%(oracle_limit)s" in sql
        assert ORACLE_ORDER_BY in sql
    assert f"GROUP BY {ORACLE_GROUP_BY}" in reference
    # Deletion and the attribute value are decided on latest state in both.
    assert "HAVING del = 0" in reference and "WHERE is_deleted = 0" in twin


# ── stub-transport equivalence on a synthetic multi-version population ───────


def population():
    """A multi-version population with every case that separates the shapes."""

    base = {
        "project_id": PROJECT,
        "observation_type": "span",
        "service_name": "service-a",
        "trace_id": "trace",
        "start_time": START + timedelta(minutes=20),
        "is_deleted": 0,
        "attrs_string": {"account_id": "acct-1"},
    }
    return [
        # multi-version live: the later version's value is the public one
        {**base, "id": "live", "_version": 1},
        {**base, "id": "live", "_version": 2, "attrs_string": {"account_id": "acct-2"}},
        # latest version is a tombstone
        {**base, "id": "gone", "_version": 1},
        {**base, "id": "gone", "_version": 2, "is_deleted": 1},
        # an older tombstone does NOT remove a revived identity
        {**base, "id": "revived", "_version": 1, "is_deleted": 1},
        {**base, "id": "revived", "_version": 2},
        # corrected producer time inside the same hour keeps one identity
        {**base, "id": "corrected", "_version": 1},
        {
            **base,
            "id": "corrected",
            "_version": 2,
            "start_time": START + timedelta(minutes=5),
        },
        # a correction that crosses the hour is a separate stored row
        {
            **base,
            "id": "crossed",
            "_version": 2,
            "start_time": START + timedelta(minutes=95),
        },
        {**base, "id": "crossed", "_version": 1},
        # equal versions: no storage winner exists at all
        {**base, "id": "tied", "_version": 7, "attrs_string": {"account_id": "acct-1"}},
        {**base, "id": "tied", "_version": 7, "attrs_string": {"account_id": "acct-2"}},
        # a same-id row of another service is a different physical span
        {**base, "id": "live", "service_name": "service-b", "_version": 5},
        # another project is never in scope
        {**base, "id": "live", "project_id": OTHER_PROJECT, "_version": 9},
    ]


def group_key(row, keys):
    return tuple(
        row["start_time"].replace(minute=0, second=0, microsecond=0)
        if key == "toStartOfHour(start_time)"
        else row[key]
        for key in keys
    )


def collapse(rows, keys, packed):
    """argMax(tuple(packed), _version) per group: one whole row per key."""

    winners = {}
    for row in rows:
        key = group_key(row, keys)
        current = winners.get(key)
        if current is None or row["_version"] > current["_version"]:
            winners[key] = row
    return {
        key: {column: row[column] for column in packed if column in row}
        for key, row in winners.items()
    }


def final_twin(rows, keys):
    """ReplacingMergeTree(_version, is_deleted) FINAL: the same election, by key."""

    winners = {}
    for row in rows:
        key = group_key(row, keys)
        current = winners.get(key)
        if current is None or row["_version"] >= current["_version"]:
            winners[key] = row
    return winners


@pytest.mark.unit
def test_collapse_elects_the_same_whole_row_as_a_final_twin():
    projection, scope = collapse_sources(
        builder().build_filter_seed_page(
            slice_start=START, slice_end=START + timedelta(hours=2), limit=26
        )[0]
    )[0]
    keys = group_by_keys(scope)
    packed = packed_columns(projection) + _PHYSICAL_SPAN_KEY_COLUMNS
    rows = [row for row in population() if row["project_id"] == PROJECT]

    collapsed = collapse(rows, keys, packed)
    twin = final_twin(rows, keys)
    assert set(collapsed) == set(twin)
    assert len(collapsed) == 8
    for key, winner in collapsed.items():
        versions = [row for row in rows if group_key(row, keys) == key]
        top = max(row["_version"] for row in versions)
        tied = [row for row in versions if row["_version"] == top]
        if len(tied) == 1:
            # A unique storage winner: field for field, the same row.
            for column in packed:
                assert winner[column] == twin[key][column], (key, column)
        else:
            # No storage winner exists. The two models break the tie the other
            # way round on purpose; one tuple still forbids a mixture.
            assert any(
                all(winner[column] == row[column] for column in packed) for row in tied
            ), key

    by_id = {}
    for winner in collapsed.values():
        by_id.setdefault(winner["id"], []).append(winner)
    # The later version's value is the published one.
    assert [
        winner["attrs_string"]["account_id"]
        for winner in by_id["live"]
        if winner["service_name"] == "service-a"
    ] == ["acct-2"]
    # Same textual id, another service: a separate physical span, not a version.
    assert len(by_id["live"]) == 2
    live = {winner["is_deleted"] for winner in by_id["gone"]}
    assert live == {1}
    assert {winner["is_deleted"] for winner in by_id["revived"]} == {0}
    # A correction inside the hour keeps one identity and republishes the time.
    assert [winner["start_time"] for winner in by_id["corrected"]] == [
        START + timedelta(minutes=5)
    ]
    # A correction across the hour boundary is a second stored row.
    assert len(by_id["crossed"]) == 2


@pytest.mark.unit
def test_a_four_key_collapse_would_merge_distinct_physical_spans():
    keys = ["project_id", "trace_id", "id", "toStartOfHour(start_time)"]
    rows = [row for row in population() if row["project_id"] == PROJECT]
    packed = ("start_time", "is_deleted", "attrs_string", "_version")
    six_key = collapse(
        rows,
        [key.strip() for key in _PHYSICAL_SPAN_GROUP_BY_SQL.split(",")],
        packed + _PHYSICAL_SPAN_KEY_COLUMNS,
    )
    assert len(collapse(rows, keys, packed)) < len(six_key)


@pytest.mark.integration
def test_engine_collapse_and_final_twin_return_the_same_rows(tmp_path):
    """The authoritative cross-check, on a real ReplacingMergeTree."""

    pytest.importorskip("chdb", reason="optional isolated ClickHouse engine")
    from chdb.session import Session

    session = Session(str(tmp_path / "span-argmax"))
    try:
        session.query("""CREATE TABLE spans (
            project_id UUID, observation_type LowCardinality(String),
            service_name LowCardinality(String), start_time DateTime64(6, 'UTC'),
            trace_id String, id String,
            attrs_string Map(String, String),
            is_deleted UInt8 DEFAULT 0, _version UInt64
        ) ENGINE = ReplacingMergeTree(_version, is_deleted)
          PARTITION BY toDate(start_time)
          PRIMARY KEY (project_id, observation_type, service_name,
                       toStartOfHour(start_time))
          ORDER BY (project_id, observation_type, service_name,
                    toStartOfHour(start_time), trace_id, id)""")
        session.query("SYSTEM STOP MERGES spans")
        for row in population():
            session.query(
                "INSERT INTO spans (project_id, observation_type, service_name, "
                "start_time, trace_id, id, attrs_string, is_deleted, _version) "
                f"VALUES ('{row['project_id']}', '{row['observation_type']}', "
                f"'{row['service_name']}', "
                f"toDateTime64('{row['start_time'].isoformat(sep=' ')}', 6, 'UTC'), "
                f"'{row['trace_id']}', '{row['id']}', "
                f"{{'account_id': '{row['attrs_string']['account_id']}'}}, "
                f"{row['is_deleted']}, {row['_version']})"
            )

        def identities(sql):
            return sorted(
                str(session.query(sql, "CSV")).strip().splitlines(),
            )

        scope = (
            f"project_id = toUUID('{PROJECT}') "
            f"AND start_time >= toDateTime64('{START.isoformat(sep=' ')}', 6, 'UTC') "
            "AND start_time < toDateTime64("
            f"'{(START + timedelta(hours=3)).isoformat(sep=' ')}', 6, 'UTC')"
        )
        select = (
            "trace_id, id, observation_type, service_name, "
            "toStartOfHour(start_time), start_time, attrs_string['account_id']"
        )
        twin = identities(
            f"SELECT {select} FROM spans FINAL PREWHERE {scope} WHERE is_deleted = 0"
        )
        collapsed = identities(f"""
            SELECT {select} FROM (
                SELECT project_id, observation_type, service_name, trace_id, id,
                       argMax(tuple(start_time, attrs_string, is_deleted, _version),
                              _version) AS _physical_winner,
                       _physical_winner.1 AS start_time,
                       _physical_winner.2 AS attrs_string,
                       _physical_winner.3 AS is_deleted,
                       _physical_winner.4 AS _version
                FROM spans
                PREWHERE {scope}
                GROUP BY {_PHYSICAL_SPAN_GROUP_BY_SQL}
            ) WHERE is_deleted = 0
        """)
        tied_value = "'tied'"
        assert [row for row in twin if tied_value not in row] == [
            row for row in collapsed if tied_value not in row
        ]
        assert len([row for row in collapsed if tied_value in row]) == 1
    finally:
        session.close()
