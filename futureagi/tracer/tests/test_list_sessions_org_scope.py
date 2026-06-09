"""
Regression tests for the org-scope + user_id code path through
``TraceSessionView._list_sessions_clickhouse``.

Previously the method referenced ``org`` before it was defined — the
identifier was only assigned later in the formatted-result decoration
block. Whenever a ``user_id`` query parameter was set the method
NameError'd on the EndUser lookup, the outer ``try/except`` swallowed
the exception, and the request silently fell through to the PG path
(which then timed out — TH-5092).

These tests pin:
  1. The method completes without raising when ``user_id`` is set in
     org-scope mode (the bug case).
  2. The synthetic ``end_user_id IN (...)`` filter is appended to the
     filter list passed into ``SessionListQueryBuilder``.
  3. End-user display fields are stitched onto the formatted output
     from a single EndUser lookup (no second round-trip).
"""

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest


@pytest.mark.unit
class TestListSessionsClickHouseOrgScope:
    """Direct unit tests for ``_list_sessions_clickhouse``."""

    def _make_request(self, *, user_id=None, query_params=None):
        params = dict(query_params or {})
        if user_id:
            params["user_id"] = user_id
        return SimpleNamespace(
            query_params=params,
            organization=SimpleNamespace(id=uuid.uuid4()),
            user=SimpleNamespace(organization=SimpleNamespace(id=uuid.uuid4())),
        )

    def _make_view(self):
        """Construct a TraceSessionView without invoking ModelViewSet.__init__.

        The view's only attributes we need are ``_gm.success_response`` and
        the methods we're testing. Building one through DRF requires a full
        DB stack, so we fabricate just enough surface area here.
        """
        from tracer.views.trace_session import TraceSessionView

        view = TraceSessionView.__new__(TraceSessionView)
        view._gm = SimpleNamespace(
            success_response=lambda payload: ("ok", payload),
            bad_request=lambda msg: ("bad_request", msg),
        )
        return view

    def _make_validated_data(self, filters=None, sort_params=None):
        return {
            "filters": filters or [],
            "sort_params": sort_params or [],
        }

    def _patch_endusers(self, ids, *, with_display=True):
        """Patch ``EndUser.objects.filter`` chain so we don't touch the DB."""
        rows = []
        for _id in ids:
            row = {"id": _id}
            if with_display:
                row.update(
                    {
                        "user_id": "user-eve",
                        "user_id_type": "DEVELOPER_IDENTIFIER",
                        "user_id_hash": "deadbeef",
                    }
                )
            rows.append(row)

        chain = mock.MagicMock()
        chain.filter.return_value = chain
        chain.values.return_value = rows
        chain.values_list.return_value = rows
        return mock.patch(
            "tracer.views.trace_session.EndUser.objects.filter",
            return_value=chain,
        )

    def _patch_analytics(self):
        """Stub ``analytics.execute_ch_query`` so build() runs but no CH hit."""
        analytics = mock.MagicMock()
        analytics.execute_ch_query.return_value = SimpleNamespace(data=[])
        return analytics

    def _patch_session_name_lookup(self):
        """Patch the TraceSession.objects.filter().values_list() call used
        to map session_id → session_name. With no rows in the page this
        path is a no-op, but the patch shields against a real DB hit."""
        chain = mock.MagicMock()
        chain.filter.return_value = chain
        chain.values_list.return_value = []
        return mock.patch(
            "tracer.views.trace_session.TraceSession.objects.filter",
            return_value=chain,
        )

    def test_runs_without_nameerror_when_user_id_set_org_scope(self):
        """Repro of TH-5092: ``org`` was undefined when ``user_id`` was set.

        The previous code raised ``NameError: name 'org' is not defined``
        at the EndUser lookup, the wrapping ``try/except`` swallowed it,
        and the request silently fell through to the PG path. After the
        fix, the call completes and returns a (mocked) success response.
        """
        view = self._make_view()
        request = self._make_request(user_id="user-eve")
        analytics = self._patch_analytics()

        eu_ids = [str(uuid.uuid4())]
        with self._patch_endusers(eu_ids), self._patch_session_name_lookup():
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        assert status == "ok"
        # Phase 1 build() and count fast path produce one execute call;
        # absent data the count is inferred without a second CH call.
        assert analytics.execute_ch_query.call_count >= 1

    def test_synthetic_end_user_id_filter_is_injected(self):
        """The user_id query param must surface as a synthetic
        ``end_user_id IN (...)`` filter on the builder."""
        # Resolve the real builder class BEFORE patching, so the
        # side_effect can construct a real instance instead of
        # re-entering the mocked symbol (which would recurse forever).
        from tracer.services.clickhouse.query_builders import (
            SessionListQueryBuilder as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request(user_id="user-eve")
        analytics = self._patch_analytics()
        eu_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        captured = {}

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        with (
            self._patch_endusers(eu_ids),
            self._patch_session_name_lookup(),
            mock.patch(
                "tracer.services.clickhouse.query_builders." "SessionListQueryBuilder",
                side_effect=_capture_builder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert (
            len(synthetic) == 1
        ), f"expected one synthetic end_user_id filter, got: {captured['filters']}"
        cfg = synthetic[0]["filter_config"]
        assert cfg["filter_op"] == "in"
        assert set(cfg["filter_value"]) == {str(_id) for _id in eu_ids}

    def test_end_user_display_injected_without_extra_db_call(self):
        """When ``user_id`` resolves, the EndUser display fields should be
        injected onto the formatted rows from the SAME query that built
        the synthetic filter — no second EndUser.objects.filter call."""
        view = self._make_view()
        request = self._make_request(user_id="user-eve")
        # Return one synthetic session row from the CH stub so the
        # injection branch actually runs.
        analytics = mock.MagicMock()
        session_row = {
            "session_id": uuid.uuid4(),
            "session_start": None,
            "session_end": None,
            "duration": 0,
            "total_cost": 0,
            "total_tokens": 0,
            "traces_count": 0,
        }
        analytics.execute_ch_query.return_value = SimpleNamespace(data=[session_row])

        eu_ids = [str(uuid.uuid4())]
        with (
            self._patch_endusers(eu_ids) as filter_mock,
            self._patch_session_name_lookup(),
        ):
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        # Exactly one EndUser query (the consolidated one), not two.
        assert filter_mock.call_count == 1, (
            f"expected 1 EndUser.objects.filter call, got {filter_mock.call_count} — "
            "user-info decoration should reuse the resolved EndUser, "
            "not issue a second query"
        )

        assert status == "ok"
        rows = payload["table"]
        assert rows, "expected at least one formatted row"
        assert rows[0]["user_id"] == "user-eve"
        assert rows[0]["user_id_type"] == "DEVELOPER_IDENTIFIER"
        assert rows[0]["user_id_hash"] == "deadbeef"

    def test_no_user_id_skips_enduser_lookup(self):
        """Absent ``user_id`` must NOT trigger any EndUser query."""
        view = self._make_view()
        request = self._make_request()  # no user_id
        analytics = self._patch_analytics()

        with self._patch_endusers([]) as filter_mock, self._patch_session_name_lookup():
            view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        assert filter_mock.call_count == 0
