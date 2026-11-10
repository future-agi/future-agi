"""Finite session residuals use relation history, never a relation-age window.

Native identities are manually enumerated; no app-compiler reference query or
production service is used. Reuse the isolated session RMT fixture.
"""

from datetime import timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.filters import (
    ClickHouseFilterBuilder,
    EvalFilterMetadata,
)
from tracer.services.clickhouse.query_builders.session_list import (
    SessionListQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.session_list import (
    SessionListQueryBuilderV2,
)
from tracer.tests.test_session_positive_witness_page import (
    OTHER,
    PROJECT,
    START,
    builder,
    cte,
    leaf,
)
from tracer.tests.test_session_positive_witness_page import (
    engine as session_engine,  # noqa: F401 - registers the shared pytest fixture under its local name.
)

pytestmark = pytest.mark.unit
LABEL, CONFIG, FOREIGN_CONFIG = (str(UUID(int=i)) for i in (50, 51, 52))


def relation(source, op="equals", value=None):
    if source == "has_eval":
        return leaf("has_eval", value, "boolean", source="SYSTEM_METRIC")
    annotation = source.endswith("score")
    boolean = source == "eval-bool"
    return leaf(
        LABEL if annotation else CONFIG,
        (5 if annotation else "Passed" if boolean else 50) if value is None else value,
        "boolean" if boolean else "number",
        op,
        "ANNOTATION" if annotation else "EVAL_METRIC",
    )


def target(
    source, op="equals", value=None, *, cls=SessionListQueryBuilderV2, org=False
):
    subject = builder(relation(source, op, value), cls=cls, org=org)
    subject.annotation_label_ids = [LABEL]
    subject.annotation_label_ids_by_project = {PROJECT: [LABEL], OTHER: []}
    subject._annotation_label_set_known = True
    subject.eval_config_ids = [CONFIG]
    subject._eval_config_ids_known = True
    subject.eval_filter_metadata = {
        CONFIG: EvalFilterMetadata(
            (CONFIG,), "PASS_FAIL" if source == "eval-bool" else "SCORE"
        )
    }
    return subject


@pytest.mark.parametrize("cls", [SessionListQueryBuilder, SessionListQueryBuilderV2])
@pytest.mark.parametrize(
    "org,source",
    [(False, "trace-score"), (True, "trace-score"), (False, "eval-number")],
)
def test_finite_residual_removes_only_relation_age_and_keeps_each_candidate_guard(
    cls, org, source
):
    subject = target(source, cls=cls, org=org)
    sql, params = subject.build_filter_match_query([str(UUID(int=100))])
    assert not subject.supports_candidate_first_page()
    assert not subject.supports_candidate_cursor_page()
    assert "candidate_relational_trace_ids" in sql
    assert "FROM resolved_root_sessions" in cte(sql, "candidate_relational_trace_ids")
    assert "latest_start_time >=" in cte(sql, "resolved_root_sessions")
    assert "latest_start_time <" in cte(sql, "resolved_root_sessions")
    assert "latest_is_deleted = 0" in cte(sql, "resolved_root_sessions")
    assert "candidate_filter_sessions" in cte(sql, "candidate_root_identities")
    assert "ORDER BY start_time DESC, toString(session_id) DESC" in sql
    assert params["bounded_match_limit"] == 1
    assert "s.created_at >=" not in sql and "eval_scan.created_at >=" not in sql
    if source.endswith("score"):
        # Direct trace Score arm and the span-ID Score arm each remain scoped.
        branches = 2 if org else 1
        assert sql.count("FROM model_hub_score AS s FINAL") == branches * 2
        assert sql.count("s.tracer_project_id = toUUID(") == branches * 2
        assert sql.count("toString(s.observation_span_id) IN (") == branches
        assert sql.count("candidate_relational_trace_ids") >= branches * 3 + 1
        assert "s.deleted = false AND s._peerdb_is_deleted = 0" in sql
    else:
        assert (
            "toString(eval_scan.trace_id) IN (SELECT trace_id FROM candidate_relational_trace_ids)"
            in sql
        )
        assert "ORDER BY eval_scan." in sql and "LIMIT 1 BY eval_scan.id" in sql
        assert sql.index("LIMIT 1 BY eval_scan.id") < sql.index("error = 0")
    compiler = (
        ClickHouseFilterBuilderV2
        if cls is SessionListQueryBuilderV2
        else ClickHouseFilterBuilder
    )
    default = compiler(project_id=PROJECT)
    assert default.score_date_scope is True
    assert "created_at >=" in default._score_date_filter()


@pytest.fixture(params=["tracer_eval_logger", "tracer_eval_logger_v2"])
def history_engine(session_engine, request, settings):  # noqa: F811 - pytest injects the imported session_engine fixture.
    engine = session_engine
    table = request.param
    settings.CH25_EVAL_LOGGER_TABLE = table
    direct = table.endswith("_v2")
    version = "_version" if direct else "_peerdb_version"
    state = "is_deleted UInt8" if direct else "deleted UInt8, _peerdb_is_deleted UInt8"
    engine.execute("""CREATE TABLE model_hub_score (
        id String, trace_id Nullable(UUID), observation_span_id Nullable(String),
        tracer_project_id UUID, label_id UUID, annotator_id Nullable(UUID),
        created_at DateTime64(6, 'UTC'), value String,
        deleted UInt8, _peerdb_is_deleted UInt8, _peerdb_version UInt64
    ) ENGINE=ReplacingMergeTree(_peerdb_version) ORDER BY id""")
    engine.execute(f"""CREATE TABLE {table} (
        id UUID, trace_id Nullable(UUID), observation_span_id Nullable(String),
        custom_eval_config_id UUID, created_at DateTime64(6, 'UTC'),
        output_bool Nullable(UInt8), output_float Nullable(Float64),
        output_str Nullable(String), output_str_list String, error UInt8,
        {state}, {version} UInt64
    ) ENGINE=ReplacingMergeTree({version}) ORDER BY id""")
    engine.execute("SYSTEM STOP MERGES model_hub_score")
    engine.execute(f"SYSTEM STOP MERGES {table}")

    def insert(source, number, name, revision=1, **changes):
        annotation = source.endswith("score")
        trace_id = str(UUID(int=1000 + number))
        common = {"created_at": START - timedelta(days=401 - revision)}
        if annotation:
            row = {
                "id": f"score-{number}",
                "trace_id": None if source == "span-score" else trace_id,
                "observation_span_id": f"child-{number}"
                if source == "span-score"
                else None,
                "tracer_project_id": OTHER if name == "foreign" else PROJECT,
                "label_id": LABEL,
                "annotator_id": None,
                "value": '{"rating":5}',
                "deleted": 0,
                "_peerdb_is_deleted": 0,
                "_peerdb_version": revision,
            }
        else:
            row = dict(
                id=str(UUID(int=2000 + number)),
                trace_id=trace_id,
                observation_span_id=f"root-{number}",
                custom_eval_config_id=FOREIGN_CONFIG if name == "foreign" else CONFIG,
                output_float=0.5,
                output_bool=1,
                output_str=None,
                output_str_list="[]",
                error=0,
                **(
                    {"is_deleted": 0}
                    if direct
                    else {"deleted": 0, "_peerdb_is_deleted": 0}
                ),
                **{version: revision},
            )
        row.update(common, **changes)
        engine.execute(
            f"INSERT INTO {'model_hub_score' if annotation else table} ({', '.join(row)}) VALUES ("
            + ", ".join(f"%({key})s" for key in row)
            + ")",
            row,
        )

    return engine, insert, direct


@pytest.mark.parametrize(
    "source", ["trace-score", "span-score", "eval-number", "eval-bool", "has_eval"]
)
def test_native_finite_session_history_preserves_latest_scope_and_absence(
    history_engine, source
):
    engine, insert, direct = history_engine
    annotation = source.endswith("score")
    names = [
        "live",
        "changed",
        "hard",
        "soft",
        "absent",
        "foreign",
        "outside",
        "end",
        "root-deleted",
        "unseeded",
    ]
    if not annotation:
        names += ["error", "blank"]
    identities = {name: str(UUID(int=100 + index)) for index, name in enumerate(names)}
    candidates = [sid for name, sid in identities.items() if name != "unseeded"]
    for index, name in enumerate(names):
        number = 100 + index
        start = (
            START - timedelta(microseconds=1)
            if name == "outside"
            else (
                START + timedelta(days=7)
                if name == "end"
                else START + timedelta(minutes=1)
            )
        )
        span = {"trace_id": str(UUID(int=1000 + number)), "start_time": start}
        engine.insert(number, **span)
        if source == "span-score":
            engine.insert(
                number, **span, id=f"child-{number}", parent_span_id=f"root-{number}"
            )
        if name == "foreign":
            engine.insert(number, **span, project_id=OTHER)
        if name == "root-deleted":
            engine.insert(number, **span, _version=2, is_deleted=1)
        if name == "absent":
            continue
        insert(source, number, name)
        changes = {}
        if name == "changed":
            changes = (
                {"value": '{"rating":8}'}
                if annotation
                else {"output_float": 0.8, "output_bool": 0}
            )
        elif name in {"hard", "soft"}:
            flag = "_peerdb_is_deleted" if name == "hard" else "deleted"
            changes = {"is_deleted" if direct and not annotation else flag: 1}
        elif name == "error":
            changes = {"error": 1}
        elif name == "blank":
            changes = {"output_float": None, "output_bool": None}
        if changes:
            insert(source, number, name, revision=2, **changes)

    missing = {"hard", "soft", "absent", "foreign"}
    if source == "has_eval":
        # Presence retains its existing live-row rule, including errored/blank evals.
        cases = [
            ("equals", True, {"live", "changed", "error", "blank"}),
            ("equals", False, missing),
        ]
    else:
        cases = [
            ("equals", None, {"live"}),
            ("not_equals", None, {"changed"}),
            ("is_null", None, missing | (set() if annotation else {"error", "blank"})),
            ("is_not_null", None, {"live", "changed"}),
        ]
    for op, value, expected in cases:
        sql, params = target(source, op, value).build_filter_match_query(candidates)
        actual = engine.execute(sql, params)
        assert {row["session_id"] for row in actual} == {
            identities[name] for name in expected
        }, (source, op)
        assert all(
            row["start_time"].startswith("2026-08-01 12:31:00.123456") for row in actual
        )
