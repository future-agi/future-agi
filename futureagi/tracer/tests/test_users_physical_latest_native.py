"""Actual Users SQL on disposable native fixtures; main owns DDL execution.

No candidate SQL rewrites or FINAL/date oracle. Expected identities, metrics
and order are stated independently of the query under test. Reuse the existing
six-key physical and relation fixtures, never a configured service database.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.filters import EvalFilterMetadata
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager
from tracer.tests.test_relational_filter_native import (
    CONFIG,
    _drop_legacy_ch_spans_mvs,  # noqa: F401 -- suppress service fixtures
    _ensure_test_score_tenant_column,  # noqa: F401
    identity,
    leaf,
    offline_only,  # noqa: F401
)
from tracer.tests.test_relational_filter_native import (
    relational_engine as relational_engine,
)
from tracer.tests.test_span_physical_identity_latest import (
    OTHER_PROJECT,
    PROJECT,
    START,
    time_filter,
)
from tracer.tests.test_span_physical_identity_latest import engine as engine
from tracer.tests.test_span_score_physical_identity import annotation
from tracer.tests.test_span_score_physical_identity import (
    scored_engine as scored_engine,
)

pytestmark = [pytest.mark.unit, pytest.mark.integration]
WINDOW = START + timedelta(minutes=15)
END = START + timedelta(hours=1)


def uid(number):
    return str(UUID(int=number))


def users_builder(*, workspace=False, **kwargs):
    return UserListQueryBuilderV2(
        organization_id=PROJECT,
        **(
            {"project_ids": [PROJECT, OTHER_PROJECT]}
            if workspace
            else {"project_id": PROJECT}
        ),
        filters=[time_filter(WINDOW, END)],
        **kwargs,
    )


@pytest.fixture
def users_engine(engine):  # noqa: F811 -- fixture injection
    execute, insert = engine
    execute("""CREATE TABLE end_users (
        project_id UUID, organization_id UUID, end_user_id UUID,
        user_id String, user_id_type String DEFAULT 'custom',
        user_id_hash UInt64 DEFAULT 0, first_seen DateTime64(6, 'UTC'),
        version DateTime64(6, 'UTC'), is_deleted UInt8 DEFAULT 0
    ) ENGINE=ReplacingMergeTree(version) ORDER BY (project_id, end_user_id)""")
    execute("""CREATE TABLE end_user_id_remap (
        old_id UUID, new_id UUID, version DateTime64(6, 'UTC')
    ) ENGINE=ReplacingMergeTree(version) ORDER BY old_id""")
    for number in (10, 20, 30, 50, 60, 70, 80, 100, 110, 120):
        execute(
            "INSERT INTO end_users (project_id, organization_id, end_user_id, user_id, first_seen, version) "
            "VALUES (%(project)s, %(org)s, %(user)s, %(label)s, %(time)s, %(time)s)",
            {
                "project": OTHER_PROJECT if number == 120 else PROJECT,
                "org": PROJECT,
                "user": uid(number),
                "label": f"user-{number}",
                "time": START,
            },
        )
    execute(
        "INSERT INTO end_user_id_remap VALUES (%(a)s,%(new)s,%(time)s),(%(b)s,%(new)s,%(time)s)",
        {"a": uid(10), "b": uid(50), "new": uid(40), "time": START},
    )
    return execute, insert


@pytest.mark.parametrize("workspace", [False, True])
def test_native_user_id_candidate_filter_preserves_latest_labels_aliases_and_deletions(
    users_engine, workspace
):
    execute, insert = users_engine
    for number, minute in ((10, 30), (50, 35), (20, 45), (70, 55), (120, 50)):
        insert(
            id=f"label-test-{number}",
            end_user_id=uid(number),
            cost=1,
            project_id=OTHER_PROJECT if number == 120 else PROJECT,
            end_time=(START + timedelta(minutes=minute)).isoformat(" "),
        )
    insert(id="label-test-70", end_user_id=uid(70), is_deleted=1, _version=2)
    execute(
        "INSERT INTO end_users (project_id, organization_id, end_user_id, user_id, first_seen, version) "
        "VALUES (%(project)s, %(project)s, %(user)s, 'target-user', %(start)s, %(version)s)",
        {
            "project": PROJECT,
            "user": uid(10),
            "start": START,
            "version": START + timedelta(seconds=1),
        },
    )
    target = users_builder(workspace=workspace)
    target.filters.append(
        leaf("text", "in", ["TARGET-USER"], column="user_id", source="SYSTEM_METRIC")
    )
    # The matching canonical user is older than unrelated candidates. A page
    # limit must not force the manager to replay every rejected user first.
    rows = execute(
        *target.build_dimension_candidate_query(
            limit=1, window_start=WINDOW, window_end=END
        )
    )
    assert len(rows) == 1
    assert rows[0]["end_user_id"] == uid(10)
    assert rows[0]["user_id"] == "target-user"
    assert rows[0]["total_cost"] == 2
    assert datetime.fromisoformat(rows[0]["last_active"]) == START + timedelta(
        minutes=35
    )
    # The old label and a folded alias label must not become native matches.
    for old_label in ("user-10", "user-50", "user-70"):
        target.filters[-1] = leaf(
            "text", "in", [old_label], column="user_id", source="SYSTEM_METRIC"
        )
        assert (
            execute(
                *target.build_dimension_candidate_query(
                    limit=1, window_start=WINDOW, window_end=END
                )
            )
            == []
        )


def test_native_user_id_candidate_filter_keeps_unicode_case_matches(users_engine):
    execute, insert = users_engine
    execute(
        "INSERT INTO end_users (project_id, organization_id, end_user_id, user_id, first_seen, version) "
        "VALUES (%(project)s, %(project)s, %(user)s, %(label)s, %(start)s, %(version)s)",
        {
            "project": PROJECT,
            "user": uid(20),
            "label": "K",
            "start": START,
            "version": START + timedelta(seconds=1),
        },
    )
    insert(id="unicode-label", end_user_id=uid(20))
    target = users_builder()
    target.filters.append(
        leaf("text", "in", ["k"], column="user_id", source="SYSTEM_METRIC")
    )
    rows = execute(
        *target.build_dimension_candidate_query(
            limit=1, window_start=WINDOW, window_end=END
        )
    )
    assert [row["end_user_id"] for row in rows] == [uid(20)]
    assert UsersListManager._candidate_value_matches(
        rows[0]["user_id"], "in", ["k"], case_insensitive=True
    )


@pytest.mark.parametrize("workspace", [False, True])
def test_native_users_population_activity_order_aliases_and_null_pages(
    users_engine, workspace
):
    execute, insert = users_engine

    def activity(number, name, minute, **changes):
        insert(
            id=name,
            end_user_id=uid(number),
            cost=1,
            end_time=None
            if minute is None
            else (START + timedelta(minutes=minute)).isoformat(" "),
            **changes,
        )

    activity(40, "alias", 40, attrs_number={"agent.duration_s": 2})
    activity(10, "full-activity", 50)
    activity(20, "b", 45)
    activity(30, "tie", 50)
    activity(60, "null", None)
    for number, name in ((70, "tombstone"), (80, "moved")):
        activity(number, name, 59)
        activity(
            number,
            name,
            59,
            start_time=START + timedelta(minutes=10),
            _version=2,
            is_deleted=int(number == 70),
        )
    activity(10, "reassigned", 59)
    activity(20, "reassigned", 46, _version=2)
    activity(10, "same", 59, is_deleted=1)
    activity(100, "same", 41, service_name="service-b")
    activity(110, "same", 42, observation_type="tool")
    activity(10, "foreign", 59, project_id=uid(999))
    activity(120, "workspace", 55, project_id=OTHER_PROJECT)

    expected = ([120] if workspace else []) + [30, 10, 20, 110, 100, 60]
    target = users_builder(workspace=workspace)
    all_rows = execute(
        *target.build_dimension_candidate_query(
            limit=100, window_start=WINDOW, window_end=END
        )
    )
    assert [item["end_user_id"] for item in all_rows] == [uid(n) for n in expected]
    costs = {item["end_user_id"]: item["total_cost"] for item in all_rows}
    assert costs[uid(10)] == costs[uid(20)] == 2
    assert (
        next(item for item in all_rows if item["end_user_id"] == uid(10))["user_id"]
        == "user-10"
    )
    pages, before_time, before_id = [], None, None
    for _ in range(len(expected) + 1):
        rows = execute(
            *target.build_dimension_candidate_query(
                limit=2,
                window_start=WINDOW,
                window_end=END,
                before_first_seen=before_time,
                before_end_user_id=before_id,
            )
        )
        if not rows:
            break
        pages.extend(item["end_user_id"] for item in rows)
        before_id = rows[-1]["end_user_id"]
        before_time = (
            datetime.fromisoformat(rows[-1]["last_active"])
            if rows[-1]["last_active"]
            else None
        )
    assert pages == [uid(n) for n in expected]

    # A complete positive witness narrows groups, not their full-window activity.
    target.filters.append(
        leaf(
            "number",
            "greater_than",
            1,
            column="agent.duration_s",
            source="SPAN_ATTRIBUTE",
        )
    )
    narrowed = execute(
        *target.build_dimension_candidate_query(
            limit=2, window_start=WINDOW, window_end=END
        )
    )
    assert [item["end_user_id"] for item in narrowed] == [uid(10)]
    assert narrowed[0]["total_cost"] == 2
    assert datetime.fromisoformat(narrowed[0]["last_active"]) == START + timedelta(
        minutes=50
    )


@pytest.mark.parametrize("family", ["ANNOTATION", "EVAL_METRIC"])
@pytest.mark.parametrize(
    "operation", ["equals", "not_equals", "is_null", "is_not_null"]
)
@pytest.mark.parametrize("scored_engine", ["Nullable(UUID)"], indirect=True)
def test_native_users_actual_relation_boundary(relational_engine, family, operation):  # noqa: F811 -- fixture injection
    execute, insert_span, insert_score, insert_eval = relational_engine
    names = (
        "live",
        "changed",
        "hard",
        "soft",
        "absent",
        "moved",
        "gone",
        "remapped",
        "role",
        "rooted",
        "foreign",
        "error",
        "outside",
    )
    user_ids = {name: uid(200 + i) for i, name in enumerate(names)}
    alias = uid(400)
    for name in names:
        span = {
            "id": name,
            "trace_id": identity(name),
            "end_user_id": user_ids[name],
            "parent_span_id": "parent" if name == "rooted" else None,
        }
        if name == "remapped":
            span["end_user_id"] = alias
        if name == "foreign":
            span["project_id"] = OTHER_PROJECT
        if name == "outside":
            span["start_time"] = START - timedelta(days=1)
        insert_span(**span)
        corrections = {
            "moved": {"start_time": START + timedelta(minutes=10)},
            "gone": {"start_time": START + timedelta(minutes=10), "is_deleted": 1},
            "role": {"parent_span_id": "parent"},
            "rooted": {"parent_span_id": None},
        }
        if name in corrections:
            insert_span(**{**span, "_version": 2, **corrections[name]})
        if name == "absent":
            continue
        score = {
            "id": name,
            "trace_id": identity(name),
            "created_at": START - timedelta(days=400),
        }
        insert_score(**score)
        insert_eval(name, output_float=0.05)
        if name in {"changed", "hard", "soft", "error"}:
            insert_score(
                **score,
                _peerdb_version=2,
                value='{"rating":7}' if name == "changed" else '{"rating":5}',
                _peerdb_is_deleted=int(name == "hard"),
                deleted=int(name == "soft"),
            )
            insert_eval(
                name,
                revision=2,
                output_float=0.07 if name == "changed" else 0.05,
                hard_deleted=int(name == "hard"),
                soft_deleted=int(name == "soft"),
                error=int(name == "error"),
            )
    # Nonmatching spans must not turn a negative/absence test into a match.
    insert_span(
        id="unannotated", trace_id=identity("unannotated"), end_user_id=user_ids["live"]
    )
    id_map = {value: value for value in user_ids.values()}
    id_map[alias] = user_ids["remapped"]
    target = users_builder(
        candidate_end_user_ids=list(user_ids.values()),
        candidate_scan_end_user_ids=list(id_map),
        candidate_end_user_id_map=id_map,
    )
    item = (
        annotation(operation)
        if family == "ANNOTATION"
        else leaf("number", operation, 5)
    )
    actual = execute(
        *target.build_relation_filter_user_query(
            [item],
            eval_filter_metadata_by_project={
                PROJECT: {CONFIG: EvalFilterMetadata((CONFIG,), "SCORE")}
            },
        )
    )
    population = set(names) - {"moved", "gone", "foreign", "outside"}
    positive = {"live", "remapped", "rooted"} | (
        {"error"} if family == "ANNOTATION" else {"role"}
    )
    expected = {
        "equals": positive,
        "not_equals": {"changed"},
        "is_not_null": positive | {"changed"},
        "is_null": population - positive - {"changed"},
    }[operation]
    assert {item["end_user_id"] for item in actual} == {
        user_ids[name] for name in expected
    }


@pytest.mark.parametrize(
    "operation", ["equals", "not_equals", "is_null", "is_not_null"]
)
@pytest.mark.parametrize("scored_engine", ["Nullable(UUID)"], indirect=True)
def test_native_users_workspace_eval_config_collision(relational_engine, operation):  # noqa: F811 -- fixture injection
    execute, insert_span, _, insert_eval = relational_engine
    other_config = uid(500)
    for project, user in ((PROJECT, uid(501)), (OTHER_PROJECT, uid(502))):
        insert_span(
            id="collision",
            trace_id=identity("collision"),
            project_id=project,
            end_user_id=user,
        )
    insert_eval("collision", output_float=0.05)
    insert_eval(
        "other-config",
        trace_id=identity("collision"),
        observation_span_id="collision",
        custom_eval_config_id=other_config,
        output_float=0.07,
    )
    target = users_builder(workspace=True, candidate_end_user_ids=[uid(501), uid(502)])
    actual = execute(
        *target.build_relation_filter_user_query(
            [leaf("number", operation, 5)],
            eval_filter_metadata_by_project={
                PROJECT: {CONFIG: EvalFilterMetadata((CONFIG,), "SCORE")},
                OTHER_PROJECT: {CONFIG: EvalFilterMetadata((other_config,), "SCORE")},
            },
        )
    )
    expected = {
        "equals": {uid(501)},
        "not_equals": {uid(502)},
        "is_null": set(),
        "is_not_null": {uid(501), uid(502)},
    }[operation]
    assert {item["end_user_id"] for item in actual} == expected


def test_native_users_gap_text_witness_microsecond_boundary(users_engine):
    execute, insert = users_engine
    start = WINDOW.replace(microsecond=123456)
    for number, delta in ((10, 0), (20, -1), (30, 1)):
        insert(
            id=f"u{number}",
            end_user_id=uid(number),
            start_time=start + timedelta(microseconds=delta),
            attrs_string={"tag": "YES"},
        )
    sql, params = users_builder().build_attribute_user_candidates_query(
        text_values_by_key={"tag": ("yes",)},
        window_start=start,
        window_end=start + timedelta(microseconds=1),
        candidate_scan_ids=tuple(uid(n) for n in (10, 20, 30)),
    )
    assert execute(sql, params) == [{"end_user_id": uid(10)}]


def test_native_users_gap_nullable_metrics_and_unrounded_filter(users_engine):
    execute, insert = users_engine
    execute("ALTER TABLE spans ADD COLUMN trace_session_id Nullable(UUID)")
    execute("""CREATE TABLE trace_session_id_remap (
        old_id UUID, new_id UUID, version DateTime64(6, 'UTC')
    ) ENGINE=ReplacingMergeTree(version) ORDER BY old_id""")
    start = START + timedelta(minutes=20)
    for index, latency in enumerate((1, 2, 2)):
        insert(
            id=f"s{index}",
            trace_id=identity(f"s{index}"),
            end_user_id=uid(10),
            trace_session_id=uid(600 + index),
            latency_ms=latency,
            end_time=(start + timedelta(milliseconds=latency)).isoformat(" "),
        )
    insert(id="nullable", end_user_id=uid(10), latency_ms=100, status="ERROR")
    insert(id="nullable", end_user_id=uid(10), latency_ms=None, status=None, _version=2)
    insert(
        id="nullable",
        end_user_id=uid(20),
        latency_ms=5,
        status="ERROR",
        service_name="service-b",
    )
    fields = {
        "avg_trace_latency",
        "num_traces_with_errors",
        "avg_session_duration",
        "num_sessions",
    }
    metrics = {}
    for sql, params, _ in users_builder().build_requested_page_metric_queries(
        [uid(10), uid(20)], fields
    ):
        for item in execute(sql, params):
            metrics.setdefault(item["end_user_id"], {}).update(item)
    assert metrics[uid(10)]["avg_trace_latency"] == pytest.approx(5 / 3)
    assert metrics[uid(10)]["num_traces_with_errors"] == 0
    assert metrics[uid(20)]["num_traces_with_errors"] == 1
    assert metrics[uid(10)]["num_sessions"] == 3
    assert metrics[uid(10)]["avg_session_duration"] == pytest.approx(0.005 / 3)
    for field, threshold, shown in (
        ("avg_trace_latency", 1.6668, 1.67),
        ("avg_session_duration", 0.0018, 0.0),
    ):
        manager = UsersListManager(
            organization_id=PROJECT,
            allowed_project_ids=[PROJECT],
            requested_columns=[],
            filters=[leaf("number", "less_than", threshold, column=field, source="")],
        )
        rows = [{"end_user_id": uid(10)}]
        manager._apply_page_metrics(rows, metrics)
        assert rows[0][field] == shown
        assert manager._row_matches_filters(rows[0])


@pytest.mark.parametrize("workspace", [False, True])
@pytest.mark.parametrize("scored_engine", ["Nullable(UUID)"], indirect=True)
def test_native_users_gap_eval_summary_fullkey_postwinner(
    users_engine,
    relational_engine,  # noqa: F811 -- fixture injection
    workspace,
):
    execute, insert, _, insert_eval = relational_engine
    other_config = uid(700)
    for name in ("live", "second", "third", "moved", "gone", "reassigned"):
        insert(id=name, trace_id=identity(name), end_user_id=uid(40))
        changes = {
            "moved": {"start_time": START + timedelta(minutes=10)},
            "gone": {"start_time": START + timedelta(minutes=10), "is_deleted": 1},
            "reassigned": {"end_user_id": uid(20)},
        }.get(name)
        if changes:
            insert(
                id=name,
                trace_id=identity(name),
                **{"end_user_id": uid(40), "_version": 2, **changes},
            )
        insert_eval(
            name,
            output_bool=int(name != "third"),
            output_float=0.9
            if name == "second"
            else 0.014
            if name in {"live", "third"}
            else 0.02
            if name == "reassigned"
            else 0.99,
        )
    # Latest eval value and tombstones stay post-FINAL; all history is 400 days old.
    insert_eval("second", revision=2, output_bool=0, output_float=0.014)
    insert_eval(
        "dead-score", trace_id=identity("live"), output_bool=1, output_float=0.99
    )
    insert_eval(
        "dead-score",
        revision=2,
        trace_id=identity("live"),
        output_bool=1,
        output_float=0.99,
        hard_deleted=1,
    )
    for name, dimension in (
        ("moved", {"service_name": "service-b"}),
        ("gone", {"observation_type": "tool"}),
    ):
        insert(id=name, trace_id=identity(name), end_user_id=uid(20), **dimension)
    insert(
        id="live",
        trace_id=identity("live"),
        project_id=OTHER_PROJECT,
        end_user_id=uid(120),
    )
    insert_eval(
        "other-project",
        trace_id=identity("live"),
        custom_eval_config_id=other_config,
        output_bool=0,
        output_float=0.07,
    )
    insert(
        id="foreign",
        trace_id=identity("foreign"),
        project_id=uid(999),
        end_user_id=uid(10),
    )
    insert_eval("foreign", output_bool=1, output_float=0.99)
    sql, params = users_builder(workspace=workspace).build_eval_query(
        [uid(10), uid(20), uid(120)],
        allowed_eval_config_ids_by_project={
            PROJECT: [CONFIG],
            OTHER_PROJECT: [other_config],
        },
    )
    actual = {item["end_user_id"]: item for item in execute(sql, params)}
    assert set(actual) == {uid(10), uid(20)} | ({uid(120)} if workspace else set())
    assert actual[uid(10)]["bool_eval_pass_rate"] == pytest.approx(100 / 3)
    assert actual[uid(10)]["avg_output_float"] == pytest.approx(0.014)
    assert actual[uid(20)]["avg_output_float"] == pytest.approx(
        (0.99 + 0.99 + 0.02) / 3
    )
    if workspace:
        assert actual[uid(120)]["avg_output_float"] == pytest.approx(0.07)
    for field, threshold, shown in (
        ("bool_eval_pass_rate", 33.332, 33.33),
        ("avg_output_float", 0.012, 0.01),
    ):
        manager = UsersListManager(
            organization_id=PROJECT,
            allowed_project_ids=[PROJECT],
            requested_columns=[],
            filters=[
                leaf("number", "greater_than", threshold, column=field, source="")
            ],
        )
        rows = [{"end_user_id": uid(10)}]
        manager._apply_evals(rows, actual)
        assert rows[0][field] == shown
        assert manager._row_matches_filters(rows[0])
