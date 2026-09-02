"""Regression test for TH-5574 — Trace View selection counts off by one.

The bounded direct-write reader fetches a ``page_size + 1`` has-more sentinel,
then returns exactly ``page_size`` rows plus explicit ``has_more`` metadata.
The consuming view must preserve that contract so "select all on this page"
never reports 26 selections for a 25-row page.

This pins the handoff in ``TraceView._list_traces_of_session_clickhouse`` (the
``list_traces_of_session`` endpoint named in the ticket).
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

import pytest

from tracer.selectors.trace_filter_reads import BoundedFilterPage


@pytest.mark.unit
class TestTracesOfSessionPagination:
    def _make_view(self):
        from tracer.views.trace import TraceView

        view = TraceView.__new__(TraceView)
        view._gm = SimpleNamespace(
            success_response=lambda payload: ("ok", payload),
            bad_request=lambda msg: ("bad_request", msg),
            custom_error_response=lambda *args, **kwargs: ("error", args, kwargs),
        )
        return view

    def _make_request(self, *, page_size):
        org = SimpleNamespace(id=uuid.uuid4())
        return SimpleNamespace(
            query_params={"page_number": "0", "page_size": str(page_size)},
            organization=org,
            user=SimpleNamespace(organization=org),
        )

    def _routing_analytics(
        self,
        *,
        trace_rows,
        content_complete=True,
        attribute_rows=None,
        eval_rows=None,
    ):
        """Return exact page-scoped enrichment rows without a ClickHouse hit."""

        project_by_trace = {
            str(row.get("trace_id")): row.get("project_id") for row in trace_rows
        }

        def _side_effect(query, params=None, **kwargs):
            if params and params.get("requested_attribute_keys"):
                return SimpleNamespace(data=list(attribute_rows or []))
            if params and params.get("eval_config_ids"):
                return SimpleNamespace(data=list(eval_rows or []))
            if content_complete and params and params.get("content_trace_ids"):
                content_rows = []
                content_identities = params.get("content_root_identities") or tuple(
                    (
                        project_by_trace.get(str(trace_id)),
                        str(trace_id),
                    )
                    for trace_id in params["content_trace_ids"]
                )
                for identity in content_identities:
                    row_project_id, trace_id = identity[:2]
                    content_row = {
                        "trace_id": str(trace_id),
                        "input": None,
                        "output": None,
                        "attrs_string": {},
                        "attrs_number": {},
                        "attrs_bool": {},
                        "attributes_extra": "{}",
                        "metadata": "{}",
                        "trace_tags": [],
                    }
                    # Legacy single-project CH rows do not include project_id;
                    # leaving the key absent lets the view add its exact scope.
                    # A present null is a malformed composite identity.
                    if row_project_id is not None:
                        content_row["project_id"] = str(row_project_id)
                    content_rows.append(content_row)
                return SimpleNamespace(data=content_rows)
            return SimpleNamespace(data=[])

        analytics = mock.MagicMock()
        analytics.execute_ch_query.side_effect = _side_effect
        return analytics

    @staticmethod
    def _bounded_page(trace_rows, *, total, has_more=False):
        return BoundedFilterPage(
            rows=list(trace_rows),
            has_more=has_more,
            complete=True,
            status="complete",
            error_code=None,
            total_rows_lower_bound=total,
            elapsed_ms=1.0,
            query_count=1,
            rows_returned=len(trace_rows),
            result_payload_bytes=1,
            attempts=(),
        )

    def test_page_trimmed_to_page_size(self):
        """A page that fetched page_size + 1 rows returns exactly page_size."""
        page_size = 25
        view = self._make_view()
        request = self._make_request(page_size=page_size)

        # The bounded V2 reader consumes its sentinel internally and exposes it
        # as ``has_more``; the view receives exactly one public page.
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(page_size)]
        total = 40
        analytics = self._routing_analytics(trace_rows=trace_rows)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=total, has_more=True),
            ),
        ):
            # No eval configs for this project → discovery short-circuits with
            # candidate_ids == [] (no PG/CH eval round-trip). This test pins the
            # pagination trim, not eval columns.
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                # Pagination now comes from the serializer-validated query data
                # (request.validated_query_data), not request.query_params.
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": page_size,
                    "allow_sampled": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        # The sentinel row must be trimmed — exactly page_size, not page_size + 1.
        assert len(payload["table"]) == page_size
        # total_rows comes from the (correct) uniq() count, unchanged by the trim.
        assert payload["metadata"]["total_rows"] == total
        assert not any(
            (
                call.args[1] if len(call.args) > 1 else call.kwargs.get("params") or {}
            ).get("requested_attribute_keys")
            for call in analytics.execute_ch_query.call_args_list
        )

    @pytest.mark.parametrize(
        ("start_time", "expected"),
        (
            (
                datetime(2026, 8, 15, 12, 34, 56, 789000, tzinfo=UTC),
                "2026-08-15T12:34:56.789000Z",
            ),
            (
                datetime(
                    2026,
                    8,
                    15,
                    18,
                    4,
                    56,
                    789000,
                    tzinfo=timezone(timedelta(hours=5, minutes=30)),
                ),
                "2026-08-15T12:34:56.789000Z",
            ),
            (
                datetime(2026, 8, 15, 12, 34, 56, 789000),
                "2026-08-15T12:34:56.789000Z",
            ),
        ),
    )
    def test_created_at_is_canonical_rfc3339_utc(self, start_time, expected):
        view = self._make_view()
        request = self._make_request(page_size=5)
        project_id = str(uuid.uuid4())
        trace_rows = [
            {
                "project_id": project_id,
                "trace_id": str(uuid.uuid4()),
                "start_time": start_time,
            }
        ]
        analytics = self._routing_analytics(trace_rows=trace_rows)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=1),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status_name, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 5,
                    "allow_sampled": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status_name == "ok"
        created_at = payload["table"][0]["created_at"]
        assert created_at == expected
        assert created_at.count("Z") == 1
        assert "+00:00Z" not in created_at

    def test_created_at_invalid_type_remains_rejected(self):
        from tracer.views.trace import _format_trace_list_created_at

        with pytest.raises(AttributeError):
            _format_trace_list_created_at("2026-08-15T12:34:56Z")

    def test_requested_filtered_custom_attribute_is_hydrated_exactly(self):
        view = self._make_view()
        request = self._make_request(page_size=5)
        project_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        trace_rows = [{"project_id": project_id, "trace_id": trace_id}]
        analytics = self._routing_analytics(
            trace_rows=trace_rows,
            attribute_rows=[
                {
                    "project_id": project_id,
                    "trace_id": trace_id,
                    "attribute_key": "final_status",
                    "attribute_value_json": '{"code":7}',
                },
            ],
        )
        custom_filter = {
            "column_id": "final_status",
            "filter_config": {
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": ["Rechazado"],
                "col_type": "SPAN_ATTRIBUTE",
            },
        }

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=1),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status_name, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={
                    "filters": [custom_filter],
                    "page_number": 0,
                    "page_size": 5,
                    "allow_sampled": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status_name == "ok"
        assert payload["table"][0]["final_status"] == {"code": 7}
        attr_calls = [
            call
            for call in analytics.execute_ch_query.call_args_list
            if (
                call.args[1] if len(call.args) > 1 else call.kwargs.get("params") or {}
            ).get("requested_attribute_keys")
        ]
        assert len(attr_calls) == 1
        assert attr_calls[0].args[1]["requested_attribute_keys"] == ["final_status"]
        assert attr_calls[0].args[1]["attr_trace_identities"] == (
            (project_id, trace_id),
        )

    def test_high_fanout_custom_attribute_has_no_fixed_5000_value_failure(self):
        view = self._make_view()
        request = self._make_request(page_size=5)
        project_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        trace_rows = [{"project_id": project_id, "trace_id": trace_id}]
        analytics = self._routing_analytics(
            trace_rows=trace_rows,
            # ClickHouse has already collapsed any number of physical/history
            # values (including >5,000) to the latest live value for this key.
            attribute_rows=[
                {
                    "project_id": project_id,
                    "trace_id": trace_id,
                    "attribute_key": "high_cardinality",
                    "attribute_value_json": '"latest-of-5002"',
                }
            ],
        )

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=1),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status_name, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 5,
                    "attribute_keys": ["high_cardinality"],
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status_name == "ok"
        assert payload["table"][0]["high_cardinality"] == "latest-of-5002"
        attr_call = next(
            call
            for call in analytics.execute_ch_query.call_args_list
            if (
                call.args[1] if len(call.args) > 1 else call.kwargs.get("params") or {}
            ).get("requested_attribute_keys")
        )
        assert (
            "argMax(candidate_attribute_value_json, tuple(start_time, id))"
            in attr_call.args[0]
        )
        assert "LIMIT" not in attr_call.args[0]

    def test_duplicate_projected_attribute_identity_fails_closed(self):
        view = self._make_view()
        request = self._make_request(page_size=5)
        project_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        trace_rows = [{"project_id": project_id, "trace_id": trace_id}]
        analytics = self._routing_analytics(
            trace_rows=trace_rows,
            attribute_rows=[
                {
                    "project_id": project_id,
                    "trace_id": trace_id,
                    "attribute_key": "final_status",
                    "attribute_value_json": '"older"',
                },
                {
                    "project_id": project_id,
                    "trace_id": trace_id,
                    "attribute_key": "final_status",
                    "attribute_value_json": '"newer"',
                },
            ],
        )

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=1),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            response = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 5,
                    "attribute_keys": ["final_status"],
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert response[0] == "error"
        assert response[1][0] == 503
        assert response[2]["code"] == "service_unavailable"

    def test_malformed_packed_eval_replay_fails_closed(self):
        """A successful CH response with a corrupt cell is not valid data."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        project_id = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        trace_rows = [{"project_id": project_id, "trace_id": trace_id}]
        analytics = self._routing_analytics(
            trace_rows=trace_rows,
            # Exact packed cells have eleven fields.  A short cell must never
            # be silently zipped into a plausible-looking partial eval row.
            eval_rows=[{"trace_id": trace_id, "eval_rows": [("too-short",)]}],
        )
        config = SimpleNamespace(id=uuid.uuid4())

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=1),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = [config]
            response = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 5,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert response[0] == "error"
        assert response[1][0] == 503
        assert response[2]["code"] == "service_unavailable"

    def test_span_trace_map_skipped_without_annotation_labels(self):
        """No annotation labels -> the annotation map is a guaranteed no-op,
        so the span->trace map query must not run at all."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(trace_rows=trace_rows)

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=3),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, _ = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 5,
                    "allow_sampled": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        analytics.get_span_trace_map.assert_not_called()

    def test_span_trace_map_runs_with_annotation_labels(self):
        """Scored spans map through CH with project, window, and score-id scope."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(trace_rows=trace_rows)
        label = mock.Mock()
        label.id = uuid.uuid4()
        label.type = "text"
        label.name = "Quality"
        label.settings = {}
        project_id = str(uuid.uuid4())

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[label],
            ),
            mock.patch(
                "tracer.views.trace._annotation_score_span_ids",
                return_value=("scored-span-7", "scored-span-4999"),
            ),
            mock.patch(
                "tracer.views.trace.update_span_column_config_based_on_annotations",
                side_effect=lambda column_config, _labels: column_config,
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=3),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status, _ = view._list_traces_of_session_clickhouse(
                request,
                project_id=project_id,
                validated_data={
                    "filters": [],
                    "page_number": 0,
                    "page_size": 5,
                    "allow_sampled": True,
                },
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert status == "ok"
        analytics.get_span_trace_map.assert_called_once()
        assert analytics.get_span_trace_map.call_args.kwargs["project_id"] == project_id
        assert analytics.get_span_trace_map.call_args.kwargs["scored_span_ids"] == (
            "scored-span-7",
            "scored-span-4999",
        )

    def test_org_page_annotation_replay_uses_composite_candidate_identities(self):
        """Cross-project user traces stay tenant-exact even when ids collide."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        project_a = str(uuid.uuid4())
        project_b = str(uuid.uuid4())
        shared_trace_id = str(uuid.uuid4())
        trace_rows = [
            {
                "project_id": project_a,
                "trace_id": shared_trace_id,
                "root_span_id": str(uuid.uuid4()),
                "start_time": datetime(2026, 8, 7, 12, 0, tzinfo=UTC),
            },
            {
                "project_id": project_b,
                "trace_id": shared_trace_id,
                "root_span_id": str(uuid.uuid4()),
                "start_time": datetime(2026, 8, 7, 12, 1, tzinfo=UTC),
            },
        ]
        analytics = self._routing_analytics(trace_rows=trace_rows)
        analytics.get_span_trace_map.return_value = {
            (project_a, "span-shared"): (project_a, shared_trace_id),
            (project_b, "span-shared"): (project_b, shared_trace_id),
        }
        label_a = SimpleNamespace(id=uuid.uuid4(), type="text")
        label_b = SimpleNamespace(id=uuid.uuid4(), type="text")
        annotation_builder = mock.MagicMock(
            return_value={
                (project_a, shared_trace_id): {
                    str(label_a.id): {"score": "a", "annotators": {}}
                },
                (project_b, shared_trace_id): {
                    str(label_b.id): {"score": "b", "annotators": {}}
                },
            }
        )
        has_annotation_filter = {
            "column_id": "has_annotation",
            "filter_config": {
                "filter_type": "boolean",
                "filter_op": "is",
                "filter_value": True,
            },
        }

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_by_project",
                return_value={project_a: [label_a], project_b: [label_b]},
            ),
            mock.patch(
                "tracer.views.trace._annotation_score_span_ids_by_project",
                return_value={
                    project_a: ("span-shared",),
                    project_b: ("span-shared",),
                },
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                annotation_builder,
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=2),
            ),
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            status_name, payload = view._list_traces_of_session_clickhouse(
                request,
                project_id=None,
                validated_data={
                    "filters": [has_annotation_filter],
                    "page_number": 0,
                    "page_size": 5,
                },
                analytics=analytics,
                org_project_ids=[project_a, project_b],
                org=request.organization,
            )

        assert status_name == "ok"
        assert {(row["project_id"], row["trace_id"]) for row in payload["table"]} == {
            (project_a, shared_trace_id),
            (project_b, shared_trace_id),
        }
        analytics.get_span_trace_map.assert_called_once_with(
            [shared_trace_id, shared_trace_id],
            trace_identities=(
                (project_a, shared_trace_id),
                (project_b, shared_trace_id),
            ),
            scored_span_identities=(
                (project_a, "span-shared"),
                (project_b, "span-shared"),
            ),
            timeout_ms=mock.ANY,
            settings=mock.ANY,
        )
        assert annotation_builder.call_args.kwargs["trace_identities"] == (
            (project_a, shared_trace_id),
            (project_b, shared_trace_id),
        )
        assert annotation_builder.call_args.kwargs[
            "annotation_label_ids_by_project"
        ] == {
            project_a: [str(label_a.id)],
            project_b: [str(label_b.id)],
        }

    def test_content_shortfall_returns_retryable_error(self):
        """A latest-state content replay shortfall must fail closed."""
        view = self._make_view()
        request = self._make_request(page_size=5)
        trace_rows = [{"trace_id": str(uuid.uuid4())} for _ in range(3)]
        analytics = self._routing_analytics(
            trace_rows=trace_rows,
            content_complete=False,
        )

        with (
            mock.patch("tracer.views.trace.CustomEvalConfig") as mock_cfg,
            mock.patch(
                "tracer.views.trace.get_annotation_labels_for_project",
                return_value=[],
            ),
            mock.patch(
                "tracer.views.trace._build_annotation_map_from_scores",
                return_value={},
            ),
            mock.patch(
                "tracer.selectors.trace_filter_reads.read_bounded_filter_page",
                return_value=self._bounded_page(trace_rows, total=3),
            ),
            mock.patch("tracer.views.trace.logger") as mock_logger,
        ):
            mock_cfg.objects.filter.return_value.select_related.return_value = []
            response = view._list_traces_of_session_clickhouse(
                request,
                project_id=str(uuid.uuid4()),
                validated_data={"filters": [], "page_number": 0, "page_size": 5},
                analytics=analytics,
                org_project_ids=None,
                org=request.organization,
            )

        assert response[0] == "error"
        assert response[1][0] == 503
        assert response[2]["code"] == "service_unavailable"
        warning_events = [
            c.args[0] for c in mock_logger.warning.call_args_list if c.args
        ]
        assert "trace_list_content_replay_incomplete" in warning_events
