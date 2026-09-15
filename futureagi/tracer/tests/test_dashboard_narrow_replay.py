"""The dashboard exact replay resolves latest state on narrow columns.

Offline SQL shape only: the wide ``attrs_string`` Map must be decompressed in
the candidate leg and nowhere else, and every Map key the outer statement reads
must survive the winner's ``mapFilter``. Values are synthetic; no network.
"""

import re
import socket
from types import SimpleNamespace

import pytest
from clickhouse_driver.util.escape import escape_params

from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    _EXACT_REPLAY_IDENTITY_COLUMNS,
    _EXACT_REPLAY_UNPACKED_COLUMNS,
    DashboardQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT = "11111111-1111-4111-8111-111111111111"
DRIVER_CONTEXT = SimpleNamespace(
    server_info=SimpleNamespace(get_timezone=lambda: "UTC")
)
# Every way the dashboard builder can name a Map on the replay winner.
_MAP_READ_RE = re.compile(
    r"(?:has\(mapKeys\((?P<keys_map>attrs_\w+)\)\s*,\s*(?P<keys_key>%\(\w+\)s|'[^']*')\)"
    r"|mapContains\((?P<has_map>attrs_\w+)\s*,\s*(?P<has_key>%\(\w+\)s|'[^']*')\)"
    r"|(?P<index_map>attrs_\w+)\[(?P<index_key>%\(\w+\)s|'[^']*')\])"
)


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline dashboard test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _filter(key, values):
    return {
        "metric_type": "custom_attribute",
        "metric_name": key,
        "operator": "in",
        "value": list(values),
        "attribute_type": "string",
        "source": "traces",
        "canonical_filter": {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": list(values),
                "attribute_value_types": ["string"] * len(values),
            },
        },
    }


def _config(*, granularity="day", breakdown=None, metric="latency"):
    config = {
        "project_ids": [PROJECT],
        "granularity": granularity,
        "time_range": {"preset": "30D"},
        "metrics": [
            {
                "id": metric,
                "name": metric,
                "type": "system_metric",
                "source": "traces",
                "aggregation": "avg",
            }
        ],
        "filters": [_filter("workflow_stage", ["intake", "review"])],
        "breakdowns": [],
    }
    if breakdown is not None:
        config["breakdowns"] = [
            {
                "name": breakdown,
                "type": "custom_attribute",
                "source": "traces",
                "attribute_type": "string",
            }
        ]
    return config


def _build(config):
    builder = DashboardQueryBuilderV2(config)
    sql, params = builder._build_metric_query_for_snapshot_mode(
        builder.metrics[0], latest_state=True
    )
    return builder, sql, params


def _legs(sql):
    """Split the statement into candidate leg, replay leg and outer query."""

    candidate, rest = sql.split("FROM spans AS dashboard_replay_source", 1)
    replay, outer = rest.split(") AS dashboard_replay_identities", 1)
    return candidate, replay, outer


RECIPES = (
    ("scalar", "month", None),
    ("series", "day", None),
    ("breakdown", "day", "measurement"),
)


@pytest.mark.parametrize("recipe, granularity, breakdown", RECIPES)
def test_replay_leg_never_reads_an_attribute_map(recipe, granularity, breakdown):
    _, sql, params = _build(_config(granularity=granularity, breakdown=breakdown))
    candidate, replay, _ = _legs(sql)

    # The wide Map is decompressed once, in the witness leg that already needs
    # it, and the version-resolution leg reads identity columns and the version
    # only. That is the whole optimisation.
    assert "attrs_string" in candidate
    for column in ("attrs_string", "attrs_number", "attrs_bool"):
        assert column not in replay
    for column in _EXACT_REPLAY_UNPACKED_COLUMNS:
        assert f"dashboard_replay_source.{column}" not in replay
    assert "max(dashboard_replay_source._version)" in replay
    assert "HAVING max(dashboard_replay_source._version)" in replay

    # The sort that held every replayed row (Map included) is gone, and so is
    # the wide projection that fed it.
    assert "LIMIT 1 BY" not in sql
    assert "ORDER BY dashboard_replay_source" not in sql
    assert "SELECT dashboard_replay_source.*" not in sql

    # Exactly one scan per leg, and no FINAL anywhere.
    assert sql.count("FROM spans AS dashboard_candidate_source") == 1
    assert sql.count("FROM spans AS dashboard_replay_source") == 1
    assert "FINAL" not in sql
    assert "SAMPLE" not in sql
    assert "%(" not in sql % escape_params(params, context=DRIVER_CONTEXT)


@pytest.mark.parametrize("recipe, granularity, breakdown", RECIPES)
def test_winner_carries_every_map_key_the_outer_statement_reads(
    recipe, granularity, breakdown
):
    _, sql, params = _build(_config(granularity=granularity, breakdown=breakdown))
    candidate, _, outer = _legs(sql)

    narrowed = re.search(
        r"mapFilter\(\(k, v\) -> \(k IN \((?P<keys>.*?)\)\), "
        r"dashboard_candidate_source\.attrs_string\)",
        candidate,
    )
    assert narrowed is not None
    carried = set(re.findall(r"'([^']*)'", narrowed.group("keys")))
    assert carried

    reads = list(_MAP_READ_RE.finditer(outer))
    assert reads, "the outer statement is expected to read attrs_string"
    for match in reads:
        column = (
            match.group("keys_map") or match.group("has_map") or match.group("index_map")
        )
        token = (
            match.group("keys_key") or match.group("has_key") or match.group("index_key")
        )
        if column != "attrs_string":
            # attrs_number/attrs_bool are carried whole; nothing to check.
            continue
        key = (
            params[token[2:-2]]
            if token.startswith("%(")
            else token[1:-1]
        )
        assert key in carried, f"outer reads attrs_string[{key!r}], winner drops it"

    # Any other way of naming attrs_string in the outer query would read a
    # narrowed Map as if it were the stored one.
    assert outer.count("attrs_string") == sum(
        1
        for match in reads
        if (match.group("keys_map") or match.group("has_map") or match.group("index_map"))
        == "attrs_string"
    )


def test_winner_reproduces_the_stored_projection_minus_fat_payload():
    builder, sql, _ = _build(_config())
    packed = builder._exact_replay_packed_columns(("k",), overflow=False)
    packed_columns = [column for column, _ in packed]

    assert set(packed_columns).isdisjoint(_EXACT_REPLAY_IDENTITY_COLUMNS)
    assert set(packed_columns).isdisjoint(_EXACT_REPLAY_UNPACKED_COLUMNS)
    # Nothing silently disappears: identity + winner + version is the stored
    # ``SELECT *`` projection.
    assert (
        set(packed_columns)
        | set(_EXACT_REPLAY_IDENTITY_COLUMNS)
        | _EXACT_REPLAY_UNPACKED_COLUMNS
    ) == set(CH_INSERT_COLUMNS)
    for position, column in enumerate(packed_columns, start=1):
        assert f"dashboard_candidate_winner.{position} AS {column}" in sql
    assert "dashboard_replay_version AS _version" in sql


def test_overflow_json_filters_pack_the_overflow_column_and_others_do_not():
    plain = _config()
    _, plain_sql, _ = _build(plain)
    assert "dashboard_candidate_source.attributes_extra" not in plain_sql

    overflow = _config()
    overflow["filters"].append(
        {
            "metric_type": "custom_attribute",
            "metric_name": "tool_calls",
            "attribute_type": "string",
            "source": "traces",
            "canonical_filter": {
                "column_id": "tool_calls",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "array",
                    "filter_op": "contains",
                    "filter_value": ["search"],
                },
            },
        }
    )
    _, overflow_sql, _ = _build(overflow)
    assert "attributes_extra" in overflow_sql.split(
        "FROM spans AS dashboard_replay_source", 1
    )[1]
    assert "dashboard_candidate_source.attributes_extra" in overflow_sql


def test_unenumerable_attribute_key_keeps_the_untouched_final_source():
    """A Map narrowed to keys we could not enumerate would read as absent."""

    builder = DashboardQueryBuilderV2(_config())
    builder._latest_state_spans_required = True
    # One ordinary witness-bearing filter, so candidate discovery still has a
    # witness and the fallback is this guard rather than the pre-existing
    # "no witness" branch, plus one attribute filter with no enumerable key.
    blind = {
        "metric_type": "custom_attribute",
        "metric_name": "not a valid key",
        "operator": "is_set",
        "attribute_type": "string",
        "source": "traces",
    }
    builder.global_filters = [*builder.global_filters, blind]

    assert builder._exact_filter_candidate_plan([])
    assert builder._exact_replay_attribute_keys([]) is None
    assert builder._exact_filter_replay_source([], "spans", {}) is None
    assert builder._spans_source("latency", [], "spans", params={}).startswith(
        "(\n                    SELECT finalized.* REPLACE("
    )
