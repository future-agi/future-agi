from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import override_settings

from tracer.serializers.observation_span import SpanObserveListQuerySerializer
from tracer.serializers.trace import (
    TraceObserveListQuerySerializer,
    TraceVoiceCallListQuerySerializer,
)
from tracer.services.clickhouse.list_cursor import (
    ListCursorError,
    cursor_page_metadata,
    cursor_scope_for_request,
    decode_list_cursor,
    encode_list_cursor,
    exact_total_explicitly_required,
    list_cursor_boundary_fingerprint,
    normalize_cursor_query,
    normalize_filter_conjunction,
    snapshot_cursor_supported,
)


def _request(*, user_id="u1", org_id="o1", workspace_id="w1", auth_id="a1"):
    organization = SimpleNamespace(pk=org_id)
    user = SimpleNamespace(
        pk=user_id,
        organization=organization,
        default_workspace_id=workspace_id,
    )
    return SimpleNamespace(
        user=user,
        organization=organization,
        workspace=SimpleNamespace(pk=workspace_id),
        auth=SimpleNamespace(pk=auth_id),
    )


@pytest.mark.parametrize(
    ("query_params", "validated_data", "expected"),
    [
        ({}, {}, False),
        ({"allow_sampled": "false"}, {"allow_sampled": False}, True),
        (
            {"allow_sampled": "false", "cursor_mode": "true"},
            {"allow_sampled": False, "cursor_mode": True},
            True,
        ),
        (
            {"allow_sampled": "false", "cursor": "opaque"},
            {"allow_sampled": False, "cursor": "opaque"},
            True,
        ),
        ({"allow_sampled": "true"}, {"allow_sampled": True}, False),
    ],
)
def test_exact_total_is_required_only_by_explicit_false(
    query_params,
    validated_data,
    expected,
):
    request = SimpleNamespace(query_params=query_params)

    assert exact_total_explicitly_required(request, validated_data) is expected


@pytest.mark.parametrize(
    "validated_data",
    [
        {"allow_sampled": False, "cursor_mode": True},
        {"allow_sampled": False, "cursor": "opaque"},
    ],
)
def test_trace_cursor_contract_can_explicitly_accept_an_exact_lower_bound_total(
    validated_data,
):
    request = SimpleNamespace(query_params={"allow_sampled": "false"})

    assert (
        exact_total_explicitly_required(
            request,
            validated_data,
            allow_exact_cursor_lower_bound=True,
        )
        is False
    )


def _token(**overrides):
    request = overrides.pop("request", _request())
    scope = cursor_scope_for_request(request, project_ids=["p2", "p1"])
    values = {
        "resource": "traces",
        "scope": scope,
        "query": {
            "filters": [
                {
                    "column_id": "status",
                    "filter_config": {
                        "filter_op": "in",
                        "filter_value": ["error", "ok"],
                    },
                }
            ],
            "sort_params": [],
        },
        "page_size": 25,
        "window_start": datetime(2026, 1, 1, tzinfo=UTC),
        "window_end": datetime(2026, 8, 1, tzinfo=UTC),
        "order": (datetime(2026, 7, 1, tzinfo=UTC), "trace-2"),
        "seen_rows": 25,
    }
    values.update(overrides)
    return encode_list_cursor(**values), values


def test_cursor_round_trip_preserves_datetime_and_complete_order_tuple():
    token, values = _token()
    cursor = decode_list_cursor(
        token,
        resource=values["resource"],
        scope=values["scope"],
        query=values["query"],
        page_size=values["page_size"],
    )
    assert cursor.window_start == values["window_start"]
    assert cursor.window_end == values["window_end"]
    assert cursor.order == values["order"]
    assert cursor.seen_rows == 25


def test_cursor_boundary_fingerprint_is_stable_across_timestamp_resigning():
    with patch("django.core.signing.time.time", return_value=1_700_000_000):
        first_token, _ = _token()
    with patch("django.core.signing.time.time", return_value=1_700_000_100):
        rotated_token, _ = _token()

    assert first_token != rotated_token
    assert list_cursor_boundary_fingerprint(first_token) == (
        list_cursor_boundary_fingerprint(rotated_token)
    )


def test_cursor_round_trip_preserves_scan_checkpoint_without_version_state():
    token, values = _token(
        scan_slice_start=datetime(2026, 6, 30, 10, tzinfo=UTC),
        scan_slice_end=datetime(2026, 6, 30, 12, tzinfo=UTC),
        scan_before_start_time=datetime(2026, 6, 30, 11, 59, tzinfo=UTC),
        scan_before_id="candidate-9",
    )

    cursor = decode_list_cursor(
        token,
        resource=values["resource"],
        scope=values["scope"],
        query=values["query"],
        page_size=values["page_size"],
    )

    assert cursor.scan_slice_start == datetime(2026, 6, 30, 10, tzinfo=UTC)
    assert cursor.scan_slice_end == datetime(2026, 6, 30, 12, tzinfo=UTC)
    assert cursor.scan_before_start_time == datetime(2026, 6, 30, 11, 59, tzinfo=UTC)
    assert cursor.scan_before_id == "candidate-9"


def test_cursor_normalizes_naive_driver_scan_checkpoint_to_utc():
    token, values = _token(
        scan_slice_start=datetime(2026, 6, 30, 10),
        scan_slice_end=datetime(2026, 6, 30, 12),
        scan_before_start_time=datetime(2026, 6, 30, 11, 59),
        scan_before_id="candidate-9",
    )

    cursor = decode_list_cursor(
        token,
        resource=values["resource"],
        scope=values["scope"],
        query=values["query"],
        page_size=values["page_size"],
    )

    assert cursor.scan_slice_start == datetime(2026, 6, 30, 10, tzinfo=UTC)
    assert cursor.scan_slice_end == datetime(2026, 6, 30, 12, tzinfo=UTC)
    assert cursor.scan_before_start_time == datetime(
        2026,
        6,
        30,
        11,
        59,
        tzinfo=UTC,
    )


def test_cursor_normalizes_filter_order_and_in_value_order():
    left = {
        "filters": [
            {
                "column_id": "b",
                "filter_config": {"filter_op": "equals", "filter_value": "2"},
            },
            {
                "column_id": "a",
                "filter_config": {
                    "filter_op": "in",
                    "filter_value": ["z", "a"],
                },
            },
        ],
        "search": " value ",
    }
    right = {
        "search": "value",
        "filters": [
            {
                "column_id": "a",
                "filter_config": {
                    "filter_value": ["a", "z"],
                    "filter_op": "in",
                },
            },
            {
                "filter_config": {"filter_value": "2", "filter_op": "equals"},
                "column_id": "b",
            },
        ],
    }
    assert normalize_cursor_query(left) == normalize_cursor_query(right)


def test_filter_identity_deduplicates_values_without_losing_type_provenance():
    normalized = normalize_filter_conjunction(
        [
            {
                "column_id": "attempt",
                "display_name": "Attempt number",
                "source": "traces",
                "outputType": "score",
                "filter_config": {
                    "col_type": "SPAN_ATTRIBUTE",
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["1", 1, "1"],
                    "attribute_value_types": ["string", "number", "string"],
                },
            }
        ]
    )

    assert normalized == [
        {
            "column_id": "attempt",
            "source": "traces",
            "output_type": "score",
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["1", 1],
                "attribute_value_types": ["string", "number"],
            },
        }
    ]


def test_filter_identity_ignores_labels_but_retains_semantic_routing_metadata():
    base = {
        "column_id": "quality",
        "source": "traces",
        "output_type": "PASS_FAIL",
        "filter_config": {
            "col_type": "EVAL_METRIC",
            "filter_type": "boolean",
            "filter_op": "equals",
            "filter_value": True,
        },
    }
    relabeled = {**base, "display_name": "Quality (renamed)"}
    other_source = {**base, "source": "simulation"}

    assert normalize_filter_conjunction([base]) == normalize_filter_conjunction(
        [relabeled]
    )
    assert normalize_filter_conjunction([base]) != normalize_filter_conjunction(
        [other_source]
    )


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ({"resource": "spans"}, "cursor_mismatch"),
        ({"page_size": 50}, "cursor_mismatch"),
        ({"query": {"filters": []}}, "cursor_mismatch"),
    ],
)
def test_cursor_rejects_request_mismatch(change, expected_code):
    token, values = _token()
    decode_args = {
        "resource": values["resource"],
        "scope": values["scope"],
        "query": values["query"],
        "page_size": values["page_size"],
        **change,
    }
    with pytest.raises(ListCursorError) as exc_info:
        decode_list_cursor(token, **decode_args)
    assert exc_info.value.code == expected_code
    assert "cursor" in str(exc_info.value).lower()


def test_cursor_rejects_tenant_auth_and_project_replay():
    token, values = _token()
    other_scope = cursor_scope_for_request(
        _request(user_id="u2", org_id="o2", workspace_id="w2", auth_id="a2"),
        project_ids=["p1", "p2"],
    )
    with pytest.raises(ListCursorError, match="does not match") as exc_info:
        decode_list_cursor(
            token,
            resource=values["resource"],
            scope=other_scope,
            query=values["query"],
            page_size=values["page_size"],
        )
    assert exc_info.value.code == "cursor_mismatch"


def test_org_cursor_cannot_be_replayed_in_single_project_scope():
    request = _request()
    org_scope = cursor_scope_for_request(request, project_ids=["p1", "p2"])
    token, values = _token(
        request=request,
        scope=org_scope,
        order=(datetime(2026, 7, 1, tzinfo=UTC), "trace-2", "p2"),
    )
    single_project_scope = cursor_scope_for_request(request, project_ids=["p1"])

    with pytest.raises(ListCursorError) as exc_info:
        decode_list_cursor(
            token,
            resource=values["resource"],
            scope=single_project_scope,
            query=values["query"],
            page_size=values["page_size"],
        )

    assert exc_info.value.code == "cursor_mismatch"


def test_cursor_rejects_tampering_without_exposing_signing_details():
    token, values = _token()
    with pytest.raises(ListCursorError) as exc_info:
        decode_list_cursor(
            f"{token[:-1]}x",
            resource=values["resource"],
            scope=values["scope"],
            query=values["query"],
            page_size=values["page_size"],
        )
    assert exc_info.value.code == "invalid_cursor"
    assert str(exc_info.value) == "The continuation cursor is invalid."


@override_settings(TRACER_LIST_CURSOR_MAX_AGE_SECONDS=1)
def test_cursor_rejects_expired_token(monkeypatch):
    from django.core import signing

    monkeypatch.setattr(signing.time, "time", lambda: 1_000.0)
    token, values = _token()
    monkeypatch.setattr(signing.time, "time", lambda: 1_010.0)
    with pytest.raises(ListCursorError) as exc_info:
        decode_list_cursor(
            token,
            resource=values["resource"],
            scope=values["scope"],
            query=values["query"],
            page_size=values["page_size"],
        )
    assert exc_info.value.code == "cursor_expired"


@override_settings(TRACER_LIST_CURSOR_MAX_AGE_SECONDS=1)
def test_org_composite_cursor_keeps_the_same_ttl(monkeypatch):
    from django.core import signing

    monkeypatch.setattr(signing.time, "time", lambda: 1_000.0)
    token, values = _token(order=(datetime(2026, 7, 1, tzinfo=UTC), "trace-2", "p2"))
    cursor = decode_list_cursor(
        token,
        resource=values["resource"],
        scope=values["scope"],
        query=values["query"],
        page_size=values["page_size"],
    )
    assert cursor.order == values["order"]

    monkeypatch.setattr(signing.time, "time", lambda: 1_010.0)
    with pytest.raises(ListCursorError) as exc_info:
        decode_list_cursor(
            token,
            resource=values["resource"],
            scope=values["scope"],
            query=values["query"],
            page_size=values["page_size"],
        )
    assert exc_info.value.code == "cursor_expired"


@pytest.mark.parametrize(
    "serializer_cls",
    [TraceObserveListQuerySerializer, SpanObserveListQuerySerializer],
)
def test_observe_list_serializers_reject_cursor_with_explicit_numbered_page(
    serializer_cls,
):
    serializer = serializer_cls(
        data={"cursor": "opaque", "cursor_mode": True, "page_number": 1}
    )

    assert not serializer.is_valid()
    assert "cursor" in serializer.errors


@pytest.mark.parametrize(
    "serializer_cls",
    [TraceObserveListQuerySerializer, SpanObserveListQuerySerializer],
)
def test_observe_list_serializers_accept_additive_cursor_mode(serializer_cls):
    serializer = serializer_cls(data={"cursor_mode": True, "page_number": 0})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["cursor_mode"] is True


@pytest.mark.parametrize(
    "serializer_cls",
    [TraceObserveListQuerySerializer, SpanObserveListQuerySerializer],
)
def test_observe_list_serializers_reject_fresh_cursor_mode_on_deep_page(
    serializer_cls,
):
    serializer = serializer_cls(data={"cursor_mode": True, "page_number": 2})

    assert not serializer.is_valid()
    assert "cursor_mode" in serializer.errors


@pytest.mark.parametrize(
    "serializer_cls",
    [TraceObserveListQuerySerializer, SpanObserveListQuerySerializer],
)
def test_observe_list_serializers_keep_numbered_deep_pages_backward_compatible(
    serializer_cls,
):
    serializer = serializer_cls(data={"page_number": 2})

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["page_number"] == 2
    assert serializer.validated_data["cursor_mode"] is False


def test_voice_list_serializer_accepts_one_based_additive_cursor_mode():
    serializer = TraceVoiceCallListQuerySerializer(
        data={
            "project_id": "00000000-0000-4000-8000-000000000001",
            "cursor_mode": True,
            "page": 1,
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["cursor_mode"] is True


def test_voice_list_serializer_rejects_cursor_with_explicit_numbered_page():
    serializer = TraceVoiceCallListQuerySerializer(
        data={
            "project_id": "00000000-0000-4000-8000-000000000001",
            "cursor": "opaque",
            "page": 2,
        }
    )

    assert not serializer.is_valid()
    assert "cursor" in serializer.errors


def test_voice_list_serializer_keeps_numbered_pages_backward_compatible():
    serializer = TraceVoiceCallListQuerySerializer(
        data={
            "project_id": "00000000-0000-4000-8000-000000000001",
            "page": 2,
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["page"] == 2
    assert serializer.validated_data["cursor_mode"] is False


def _filter(column_id, *, col_type="SPAN_ATTRIBUTE", filter_type="text"):
    filter_value = {"tier": "value"} if filter_type in {"map", "json"} else "value"
    return {
        "column_id": column_id,
        "filter_config": {
            "col_type": col_type,
            "filter_type": filter_type,
            "filter_op": "equals",
            "filter_value": filter_value,
        },
    }


@pytest.mark.parametrize("resource", ["observe_traces", "observe_spans"])
def test_keyset_cursor_accepts_span_local_scalar_map_and_json_filters(resource):
    filters = [
        _filter("final_status"),
        _filter("customer_map", filter_type="map"),
        _filter("payload_json", filter_type="json"),
    ]

    assert snapshot_cursor_supported(filters, resource=resource) is True


@pytest.mark.parametrize("resource", ["observe_traces", "observe_spans"])
@pytest.mark.parametrize(
    "filter_item",
    [
        _filter("quality", col_type="EVAL_METRIC"),
        _filter("reviewed", col_type="ANNOTATION"),
        _filter("user_id", col_type="TRACE_END_USER"),
    ],
)
def test_keyset_cursor_supports_independently_mutable_relations(resource, filter_item):
    assert snapshot_cursor_supported([filter_item], resource=resource) is True


def test_legacy_fallback_omits_cursor_metadata_even_when_more_rows_exist():
    assert (
        cursor_page_metadata(
            enabled=False,
            has_more=True,
            seen_rows=25,
            next_cursor=None,
        )
        == {}
    )


def test_cursor_metadata_never_claims_terminal_without_a_required_continuation():
    with pytest.raises(RuntimeError, match="requires a continuation"):
        cursor_page_metadata(
            enabled=True,
            has_more=True,
            seen_rows=25,
            next_cursor=None,
        )

    token, _ = _token()
    fingerprint = list_cursor_boundary_fingerprint(token)
    assert cursor_page_metadata(
        enabled=True,
        has_more=True,
        seen_rows=25,
        next_cursor=token,
    ) == {
        "total_rows": 25,
        "total_rows_exact": None,
        "total_rows_is_lower_bound": True,
        "has_more": True,
        "next_cursor": token,
        "next_cursor_fingerprint": fingerprint,
    }
    assert (
        cursor_page_metadata(
            enabled=True,
            has_more=True,
            seen_rows=25,
            next_cursor=token,
            unseen_row_proven=True,
        )["total_rows"]
        == 26
    )


def test_terminal_cursor_metadata_reports_the_exact_seen_total():
    assert cursor_page_metadata(
        enabled=True,
        has_more=False,
        seen_rows=42,
        next_cursor=None,
    ) == {
        "total_rows": 42,
        "total_rows_exact": 42,
        "total_rows_is_lower_bound": False,
        "has_more": False,
        "next_cursor": None,
        "next_cursor_fingerprint": None,
    }


def test_cursor_payload_has_no_merge_unstable_version_state():
    from django.conf import settings
    from django.core import signing

    token, _values = _token()
    payload = signing.loads(
        token,
        key=settings.SECRET_KEY,
        salt="tracer.clickhouse-list-cursor.v3",
    )

    assert payload["v"] == 3
    assert "version_ceiling" not in payload
    assert "relation_version_ceilings" not in payload


def test_v2_snapshot_cursor_is_rejected_after_contract_bump():
    from django.conf import settings
    from django.core import signing

    token, values = _token()
    current_payload = signing.loads(
        token,
        key=settings.SECRET_KEY,
        salt="tracer.clickhouse-list-cursor.v3",
    )
    old_payload = {
        **current_payload,
        "v": 2,
        "version_ceiling": 123,
        "relation_version_ceilings": {"spans": 123},
    }
    old_token = signing.dumps(
        old_payload,
        key=settings.SECRET_KEY,
        salt="tracer.clickhouse-list-cursor.v2",
        compress=True,
    )

    with pytest.raises(ListCursorError) as exc_info:
        decode_list_cursor(
            old_token,
            resource=values["resource"],
            scope=values["scope"],
            query=values["query"],
            page_size=values["page_size"],
        )
    assert exc_info.value.code == "invalid_cursor"


def test_cursor_datetime_precision_matches_canonical_ch25_schema():
    schema = (
        Path(__file__).parents[1]
        / "services"
        / "clickhouse"
        / "v2"
        / "schema"
        / "002_spans_v2.sql"
    ).read_text()

    # Python datetime preserves six fractional digits, so the signed cursor's
    # ordering timestamp is lossless for the canonical direct-write schema.
    assert "start_time          DateTime64(6, 'UTC')" in schema
