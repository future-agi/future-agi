"""Finite attribute witnesses must not skip any exact Users matches."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import pytest

from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline, ReadDeadlineExceeded
from tracer.services.users_list_manager import (
    UsersListManager,
    _users_attr_enrichment_query,
)

pytestmark = pytest.mark.unit
START = datetime(2026, 8, 28, tzinfo=UTC)
END = START + timedelta(days=7)
PROJECT = str(UUID(int=101))
ORG = str(UUID(int=102))
USER = str(UUID(int=103))
SERVICE = "tracer.services.users_list_manager.V2AnalyticsQueryService"


def manager(value="10000001", operation="in", filter_type="string"):
    return UsersListManager(
        organization_id=ORG,
        allowed_project_ids=[PROJECT],
        requested_columns=[],
        filters=[
            {
                "column_id": "company_id",
                "filter_config": {
                    "filter_type": filter_type,
                    "filter_op": operation,
                    "filter_value": [value] if operation == "in" else value,
                },
            }
        ],
    )


def candidates(count):
    return [
        {"end_user_id": str(UUID(int=i + 1)), "first_seen": END - timedelta(seconds=i)}
        for i in range(count)
    ]


def prune(subject, rows):
    return subject._prune_attribute_candidate_batch(
        rows, deadline=ReadDeadline.start(8000), window_start=START, window_end=END
    )


def test_seed_or_predicates_are_scoped_parameterized_and_not_latest_truth():
    builder = UserListQueryBuilder(organization_id=ORG, project_ids=[PROJECT])
    sql, params = builder.build_attribute_user_candidates_query(
        text_values_by_key={
            "company_id": ("10000001", "10000002"),
            "prompt_slug": ("agent'a",),
        },
        window_start=START,
        window_end=END,
        candidate_scan_ids=(USER,),
    )
    assert "project_id IN %(project_ids)s" in sql
    assert "start_time >= fromUnixTimestamp64Micro(%(attribute_window_start_us)s" in sql
    assert "start_time < fromUnixTimestamp64Micro(%(attribute_window_end_us)s" in sql
    assert "end_user_id IN %(attribute_scan_ids)s" in sql
    assert ") OR (" in sql
    assert "FINAL" not in sql and "is_deleted" not in sql
    assert "LIMIT" not in sql  # never truncate the finite witness set
    assert params["project_ids"] == (PROJECT,)
    assert params["attribute_scan_ids"] == (USER,)
    assert "agent'a" not in sql
    assert params["attribute_seed_values_0"] == ("10000001", "10000002")
    assert "attribute_seed_index_values_0" in params
    assert "attribute_seed_index_values_1" not in params


def test_matching_alias_retains_the_emitter_and_all_aliases():
    rows = candidates(2)
    alias = str(UUID(int=901))
    rows[0]["_candidate_scan_end_user_ids"] = (rows[0]["end_user_id"], alias)
    with patch(SERVICE) as service:
        execute = service.return_value.execute_ch_query
        execute.return_value = SimpleNamespace(data=[{"end_user_id": alias}])
        result = prune(manager(), rows)
    assert result[0]["_is_attribute_candidate"]
    assert (
        result[0]["_candidate_scan_end_user_ids"]
        == rows[0]["_candidate_scan_end_user_ids"]
    )
    assert not result[1]["_is_attribute_candidate"]
    assert set(execute.call_args.args[1]["attribute_scan_ids"]) == {
        alias,
        *(r["end_user_id"] for r in rows),
    }
    assert execute.call_args.kwargs["timeout_ms"] <= 1500


def test_multi_key_enrichment_witness_keeps_every_or_arm_inside_scope():
    sql, _ = _users_attr_enrichment_query(
        project_ids=[PROJECT],
        attribute_keys=("company_id", "tier"),
        start_date=START,
        end_date=END,
        candidate_text_values_by_key={"company_id": ("10000001",), "tier": ("gold",)},
    )
    seed = sql.split("candidate_span_identities AS (", 1)[1].split(
        "latest_candidate_spans", 1
    )[0]
    assert "AND ((" in seed
    assert ") OR (" in seed
    assert "IN %(candidate_attribute_values_1)s\n            ))" in seed


def test_sparse_match_after_first_eight_users_is_retained():
    rows = candidates(200)
    with patch(SERVICE) as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[rows[150]]
        )
        result = prune(manager(), rows)
    assert len(result) == 200
    assert [r["end_user_id"] for r in result if r["_is_attribute_candidate"]] == [
        rows[150]["end_user_id"]
    ]


def test_dense_matches_consume_only_a_page_prefix_not_remaining_candidates():
    rows = candidates(200)
    with patch(SERVICE) as service:
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=rows)
        result = prune(manager(), rows)
    assert len(result) == 25
    assert result[-1]["end_user_id"] == rows[24]["end_user_id"]


def test_no_witnesses_advances_batch_without_metric_hydration():
    rows = candidates(200)
    subject = manager()

    def dimension_page(**kwargs):
        offset = (
            int(UUID(kwargs["before_end_user_id"]))
            if kwargs["before_end_user_id"]
            else 0
        )
        return rows[offset : offset + kwargs["limit"]]

    with (
        patch(SERVICE) as service,
        patch.object(subject, "_read_dimension_candidates", side_effect=dimension_page),
        patch.object(subject, "_read_exact_candidate_rows", return_value=[]) as hydrate,
    ):
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=[])
        result = subject.list_cursor_payload(page_size=25)
    assert all(call.kwargs["candidate_ids"] == [] for call in hydrate.call_args_list)
    assert result.payload["table"] == []
    assert not result.has_more
    assert result.checkpoint_order == (
        "physical_latest_users_v1",
        rows[-1]["first_seen"],
        rows[-1]["end_user_id"],
    )


def test_optional_timeout_falls_back_to_first_eight_without_discarding_remainder():
    rows = candidates(256)
    with patch(SERVICE) as service:
        service.return_value.execute_ch_query.side_effect = ReadDeadlineExceeded(
            "test budget"
        )
        subject = manager()
        result = prune(subject, rows)
        assert prune(subject, rows[8:]) == rows[8:16]
        assert service.return_value.execute_ch_query.call_count == 1
    assert result == rows[:8]
    assert all("_is_attribute_candidate" not in row for row in result)


def test_sql_defects_are_not_silently_treated_as_no_matches():
    with patch(SERVICE) as service:
        service.return_value.execute_ch_query.side_effect = ValueError("bad SQL")
        with pytest.raises(ValueError, match="bad SQL"):
            prune(manager(), candidates(2))


@pytest.mark.parametrize(
    "value,operation,kind",
    [
        ("x", "not_in", "string"),
        ("x", "is_null", "string"),
        (12, "in", "number"),
        ("true", "in", "string"),
        ('{"a": 1}', "in", "string"),
    ],
)
def test_non_equivalent_or_negative_types_do_not_prune(value, operation, kind):
    subject = manager(value, operation, kind)
    rows = candidates(2)
    with patch(SERVICE) as service:
        assert prune(subject, rows) == rows
        service.assert_not_called()


def test_stale_witness_still_has_to_pass_latest_replay_and_filter():
    subject = manager()
    rows = candidates(2)
    with (
        patch(SERVICE) as service,
        patch.object(subject, "_read_dimension_candidates", return_value=rows),
        patch.object(
            subject,
            "_read_exact_candidate_rows",
            return_value=[{**rows[1], "company_id": "different"}],
        ) as hydrate,
    ):
        service.return_value.execute_ch_query.return_value = SimpleNamespace(
            data=[rows[1]]
        )
        result = subject.list_cursor_payload(page_size=25)
    assert hydrate.call_args.kwargs["candidate_ids"] == [rows[1]["end_user_id"]]
    assert result.payload["table"] == []
    assert not result.has_more


def test_pruning_never_leaks_matching_predicate_into_all_spans_metrics():
    subject = manager()
    builder = subject._exact_candidate_builder(
        candidate_ids=[USER],
        candidate_scan_ids=[USER],
        candidate_end_user_id_map={USER: USER},
        frozen_filters=subject._frozen_filters(
            subject.filters, window_start=START, window_end=END
        ),
    )
    sql, params = builder.build_candidate_page_query()
    assert "company_id" not in sql
    assert all("company_id" not in str(value) for value in params.values())
    assert "argMax" in sql and "_version" in sql


def test_page_size_ten_preserves_unconsumed_witnesses_and_raw_cursor():
    subject = manager()
    rows = candidates(40)
    with (
        patch(SERVICE) as service,
        patch.object(subject, "_read_dimension_candidates", return_value=rows),
        patch.object(
            subject,
            "_read_exact_candidate_rows",
            return_value=[{**row, "company_id": "10000001"} for row in rows[:25]],
        ),
    ):
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=rows)
        result = subject.list_cursor_payload(page_size=10)
    assert len(result.payload["table"]) == 10
    assert result.has_more
    assert result.seen_rows == 10
    assert result.checkpoint_order == (
        "physical_latest_users_v1",
        rows[9]["first_seen"],
        rows[9]["end_user_id"],
    )


def test_shortened_prefix_has_more_even_without_dimension_lookahead():
    subject = manager()
    rows = candidates(40)
    with (
        patch(SERVICE) as service,
        patch.object(subject, "_read_dimension_candidates", return_value=rows),
        patch.object(
            subject,
            "_read_exact_candidate_rows",
            return_value=[{**row, "company_id": "10000001"} for row in rows[:25]],
        ),
    ):
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=rows)
        result = subject.list_cursor_payload(page_size=25)
    assert len(result.payload["table"]) == 25
    assert result.has_more
    assert result.checkpoint_order == (
        "physical_latest_users_v1",
        rows[24]["first_seen"],
        rows[24]["end_user_id"],
    )


def test_latest_hydration_timeout_does_not_advance_over_witnesses():
    subject = manager()
    rows = candidates(64)
    with (
        patch(SERVICE) as service,
        patch.object(subject, "_read_dimension_candidates", return_value=rows),
        patch.object(
            subject,
            "_read_exact_candidate_rows",
            side_effect=ReadDeadlineExceeded("hydrate failed"),
        ),
    ):
        service.return_value.execute_ch_query.return_value = SimpleNamespace(data=rows)
        with pytest.raises(ReadDeadlineExceeded, match="hydrate failed"):
            subject.list_cursor_payload(page_size=25)


def test_expired_attribute_wall_does_not_recursively_split_time_buckets():
    deadline = Mock(spec=ReadDeadline)
    deadline.remaining_ms.side_effect = ReadDeadlineExceeded("request expired")
    with patch(SERVICE) as service:
        with pytest.raises(ReadDeadlineExceeded, match="request expired"):
            manager()._read_span_attributes(
                [{"end_user_id": USER}],
                deadline,
                start_date=START,
                end_date=END,
            )
    deadline.remaining_ms.assert_called_once()
    service.return_value.execute_ch_query.assert_not_called()
