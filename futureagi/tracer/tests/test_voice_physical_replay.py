"""Voice CH25 latest-state replay on inline values; no server or DDL."""

import json
from datetime import timedelta

import pytest

from tracer.services.clickhouse.v2.query_builders.voice_call_list import (
    VoiceCallListQueryBuilderV2,
)
from tracer.tests.test_trace_root_physical_replay import (
    NOW,
    PROJECT,
    execute,
    make_builder,
    physical_row,
    root_row,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def engine():
    return pytest.importorskip("chdb")


def voice_root(**changes):
    return root_row(_root_observation_type="conversation", **changes)


def execute_voice(engine, builder, rows, query):
    """Use actual JSON/typed maps in the common offline physical-row fixture."""

    class VoiceFixture:
        def query(self, sql, fmt):
            # The common fixture has constant content. Bind synthetic content
            # to version instead to exercise replacement, clearing and NULL.
            sql = (
                sql.replace(
                    "map('company_id', 'company') AS attrs_string",
                    "if(_version = 1, map('call_logs', 'large log', 'old', 'value'), "
                    "map('current', 'value')) AS attrs_string",
                )
                .replace(
                    "'{}' AS attributes_extra",
                    "CAST(if(_version = 1, "
                    '\'{"call_logs": ["large log"], "old": true}\', '
                    "'{\"current\": true}'), 'JSON(max_dynamic_paths=0)') AS attributes_extra",
                )
                .replace(
                    "'provider' AS provider",
                    "if(_version = 1, 'old provider', CAST(NULL, 'Nullable(String)')) AS provider",
                )
            )
            return engine.query(sql, fmt)

    return execute(VoiceFixture(), builder, rows, query)


def test_voice_replay_contract_requires_full_physical_metadata():
    builder = make_builder(VoiceCallListQueryBuilderV2)
    row = voice_root()
    identity = builder.bounded_filter_page_hydration_identity(row)
    assert len(identity) == 8
    assert identity[4:6] == ("conversation", "svc")
    for key in (
        "_root_observation_type",
        "_root_service_name",
        "_root_start_hour",
        "_root_version",
    ):
        assert (
            builder.bounded_filter_page_hydration_identity(
                {field: value for field, value in row.items() if field != key}
            )
            is None
        )
    with pytest.raises(ValueError, match="complete root identities"):
        builder.build_content_query(["root"], root_identities=[identity[:4]])
    with pytest.raises(ValueError, match="escaped requested spans"):
        builder.build_content_query(["another-root"], root_identities=[identity])
    with pytest.raises(ValueError, match="scoped root identity"):
        builder.content_root_identities_for_rows(
            [voice_root(project_id="other-project")]
        )


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "case,changes,expected",
    [
        (
            "corrected-time-deletion",
            {"start_time": NOW - timedelta(seconds=59), "is_deleted": 1},
            0,
        ),
        ("corrected-time-live", {"start_time": NOW - timedelta(seconds=59)}, 1),
        (
            "different-service-deletion",
            {"service_name": "other-service", "is_deleted": 1},
            1,
        ),
        (
            "different-hour-deletion",
            {"start_time": NOW - timedelta(hours=1), "is_deleted": 1},
            1,
        ),
        ("different-kind-deletion", {"observation_type": "SPAN", "is_deleted": 1}, 1),
        ("no-longer-root", {"parent_span_id": "parent"}, 0),
    ],
)
def test_voice_classifier_obeys_physical_replacement_key(
    engine, days, case, changes, expected
):
    builder = make_builder(VoiceCallListQueryBuilderV2, days=days)
    rows = [
        physical_row(observation_type="conversation", _version=1),
        physical_row(**{"observation_type": "conversation", "_version": 2, **changes}),
    ]
    actual = execute_voice(
        engine,
        builder,
        rows,
        builder.build_filter_identity_match_query_from_seed_rows([voice_root()]),
    )
    assert len(actual) == expected, case
    if actual:
        assert actual[0]["_root_observation_type"] == "conversation"
        assert actual[0]["_root_service_name"] == "svc"
        light = execute_voice(
            engine, builder, rows, builder.build_filter_page_hydration_query(actual)
        )
        assert builder.content_root_rows_match(actual, light)
        content = execute_voice(
            engine,
            builder,
            rows,
            builder.build_content_query(
                ["root"],
                root_identities=builder.content_root_identities_for_rows(light),
            ),
        )
        assert builder.content_root_rows_match(light, content)
        assert content[0]["span_id"] == content[0]["root_span_id"] == "root"
        assert "call_logs" not in json.loads(content[0]["span_attributes"])
        assert "call_logs" not in content[0]["attrs_string"]


@pytest.mark.parametrize(
    "mutation",
    [
        {"_version": 3},
        {"_version": 3, "is_deleted": 1},
        {"_version": 3, "parent_span_id": "parent"},
        {"_version": 3, "start_time": NOW - timedelta(seconds=59)},
    ],
)
def test_voice_content_rejects_changes_since_page_selection(engine, mutation):
    builder = make_builder(VoiceCallListQueryBuilderV2)
    selected = [voice_root()]
    rows = [
        physical_row(observation_type="conversation"),
        physical_row(observation_type="conversation", **mutation),
    ]
    content = execute_voice(
        engine,
        builder,
        rows,
        builder.build_content_query(
            ["root"], root_identities=builder.content_root_identities_for_rows(selected)
        ),
    )
    assert not builder.content_root_rows_match(selected, content)


def test_voice_content_uses_one_latest_tuple_and_clears_removed_fields(engine):
    builder = make_builder(VoiceCallListQueryBuilderV2)
    selected = [voice_root()]
    rows = [
        physical_row(observation_type="conversation", _version=version)
        for version in (1, 2)
    ]
    content = execute_voice(
        engine,
        builder,
        rows,
        builder.build_content_query(
            ["root"], root_identities=builder.content_root_identities_for_rows(selected)
        ),
    )
    assert builder.content_root_rows_match(selected, content)
    assert content[0]["provider"] is None
    assert json.loads(content[0]["span_attributes"]) == {"current": True}
    assert content[0]["attrs_string"] == {"current": "value"}
    assert content[0]["project_id"] == PROJECT
