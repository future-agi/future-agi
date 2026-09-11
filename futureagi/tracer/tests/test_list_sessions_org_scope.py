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
from copy import deepcopy
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
        """Patch CH-backed session name lookup so these unit tests stay local."""
        return mock.patch(
            "tracer.views.trace_session.TraceSessionView._fetch_session_names",
            return_value={},
        )

    def _patch_annotation_labels(self):
        return mock.patch(
            "tracer.views.trace_session.AnnotationsLabels.objects.filter",
            return_value=[],
        )

    def test_session_filter_resolves_external_id_and_drops_unknown_value(self):
        from tracer.services.clickhouse.query_builders.session_filters import (
            build_session_id_filter_clause,
        )
        from tracer.views.trace_session import _resolve_session_identity_filters

        session_id = str(uuid.uuid4())
        filters = [
            {
                "column_id": "session",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["customer-session", "missing-session"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with mock.patch(
            "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_filter_values",
            return_value={
                "customer-session": [session_id],
                "missing-session": [],
            },
        ) as resolve_mock:
            resolved = _resolve_session_identity_filters(
                filters,
                project_ids=[uuid.uuid4()],
            )

        resolve_mock.assert_called_once()
        assert resolved[0]["filter_config"]["filter_value"] == [session_id]
        params = {}
        assert (
            build_session_id_filter_clause(
                resolved,
                params,
                session_col="trace_session_id",
                param_prefix="session_",
            )
            == "trace_session_id IN %(session_1)s"
        )
        assert params == {"session_1": (session_id,)}

    def test_unresolved_negated_session_filter_is_a_noop(self):
        from tracer.services.clickhouse.query_builders.session_filters import (
            build_session_id_filter_clause,
        )
        from tracer.views.trace_session import _resolve_session_identity_filters

        filters = [
            {
                "column_id": "session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_in",
                    "filter_value": ["missing-session"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]
        with mock.patch(
            "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_filter_values",
            return_value={"missing-session": []},
        ):
            resolved = _resolve_session_identity_filters(
                filters,
                project_ids=[uuid.uuid4()],
            )

        assert resolved[0]["filter_config"]["filter_value"] == []
        assert (
            build_session_id_filter_clause(
                resolved,
                {},
                session_col="trace_session_id",
                param_prefix="session_",
            )
            == "1 = 1"
        )

    def test_session_resolution_batches_leaves_without_mutating_input(self):
        from tracer.views.trace_session import _resolve_session_identity_filters

        first_id = str(uuid.uuid4())
        second_id = str(uuid.uuid4())
        project_ids = [uuid.uuid4(), uuid.uuid4()]
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": ["2026-09-01", "2026-09-02"],
                },
            },
            {
                "column_id": "session",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "external-one",
                },
            },
            {
                "column_id": "session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_equals",
                    "filter_value": ["external-two"],
                },
            },
            {
                "column_id": "trace_session_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "is_null",
                },
            },
        ]
        original = deepcopy(filters)

        with mock.patch(
            "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_filter_values",
            return_value={
                "external-one": [first_id, first_id],
                "external-two": [second_id],
            },
        ) as resolve_mock:
            resolved = _resolve_session_identity_filters(
                filters,
                project_ids=project_ids,
            )

        resolve_mock.assert_called_once()
        assert resolve_mock.call_args.args[0] == ["external-one", "external-two"]
        assert resolve_mock.call_args.kwargs["project_ids"] == project_ids
        assert filters == original
        assert resolved[0] == original[0]
        assert resolved[1]["filter_config"]["filter_value"] == [first_id]
        assert resolved[2]["filter_config"]["filter_value"] == [second_id]
        assert resolved[3] == original[3]

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
        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"user-eve": eu_ids}, None),
            ),
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
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
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
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"user-eve": eu_ids}, None),
            ),
            self._patch_session_name_lookup(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
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
        assert len(synthetic) == 1, (
            f"expected one synthetic end_user_id filter, got: {captured['filters']}"
        )
        cfg = synthetic[0]["filter_config"]
        assert cfg["filter_op"] == "in"
        assert set(cfg["filter_value"]) == {str(_id) for _id in eu_ids}
        candidate_sql = analytics.execute_ch_query.call_args_list[0].args[0]
        assert candidate_sql.index("matching_user_sessions AS") < candidate_sql.index(
            "candidate_root_identities AS"
        )
        root_seed_sql = candidate_sql.split("candidate_root_identities AS (", 1)[
            1
        ].split("),\n        latest_roots AS (", 1)[0]
        assert "SELECT session_id FROM matching_user_root_ids" in root_seed_sql
        assert "LEFT JOIN ts_survivor_map AS user_session_aliases" in candidate_sql

    def test_user_id_filter_preserves_multi_value_list(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        alice_id = str(uuid.uuid4())
        bob_id = str(uuid.uuid4())

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["alice", "bob"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"alice": [alice_id], "bob": [bob_id]}, None),
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        resolve_mock.assert_called_once()
        assert resolve_mock.call_args.args[0] == ["alice", "bob"]
        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        cfg = synthetic[0]["filter_config"]
        assert cfg["filter_op"] == "in"
        assert cfg["filter_value"] == [alice_id, bob_id]

    def test_user_filter_alias_drops_unresolved_values(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        user_id = str(uuid.uuid4())

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["user_carol", "us"],
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"user_carol": [user_id], "us": []}, None),
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        resolve_mock.assert_called_once()
        assert resolve_mock.call_args.args[0] == ["user_carol", "us"]
        synthetic = [
            item
            for item in captured["filters"]
            if item.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        assert synthetic[0]["filter_config"]["filter_value"] == [user_id]

    @pytest.mark.parametrize("column_id", ["user", "user_id"])
    @pytest.mark.parametrize(
        "filter_op,filter_value,expected_op",
        [
            ("equals", "alice", "in"),
            ("in", ["alice"], "in"),
            ("not_equals", "alice", "not_in"),
            ("not_in", ["alice"], "not_in"),
            ("is_null", None, "is_null"),
            ("is_not_null", None, "is_not_null"),
        ],
    )
    def test_session_list_user_filter_operator_matrix(
        self,
        column_id,
        filter_op,
        filter_value,
        expected_op,
    ):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        alice_id = str(uuid.uuid4())

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        config = {
            "filter_type": "text",
            "filter_op": filter_op,
            "col_type": "SYSTEM_METRIC",
        }
        if filter_value is not None:
            config["filter_value"] = filter_value
        filters = [
            {
                "column_id": column_id,
                "property_id": f"system_attribute:sessions:{column_id}",
                "filter_config": config,
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"alice": [alice_id]}, None),
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        synthetic = [
            item
            for item in captured["filters"]
            if item.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        resolved_config = synthetic[0]["filter_config"]
        assert resolved_config["filter_op"] == expected_op
        if filter_op in {"is_null", "is_not_null"}:
            resolve_mock.assert_not_called()
            assert "filter_value" not in resolved_config
        else:
            resolve_mock.assert_called_once()
            assert resolve_mock.call_args.args[0] == ["alice"]
            assert resolved_config["filter_value"] == [alice_id]

    def test_user_filter_leaves_are_batched_but_remain_independent(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        alice_id = str(uuid.uuid4())

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["alice"],
                    "col_type": "SYSTEM_METRIC",
                },
            },
            {
                "column_id": "user",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "in",
                    "filter_value": ["missing"],
                    "col_type": "SYSTEM_METRIC",
                },
            },
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_in",
                    "filter_value": ["missing"],
                    "col_type": "SYSTEM_METRIC",
                },
            },
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"alice": [alice_id], "missing": []}, None),
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        resolve_mock.assert_called_once()
        assert resolve_mock.call_args.args[0] == ["alice", "missing", "missing"]
        synthetic = [
            item
            for item in captured["filters"]
            if item.get("column_id") == "end_user_id"
        ]
        assert [item["filter_config"]["filter_op"] for item in synthetic] == [
            "in",
            "in",
        ]
        assert synthetic[0]["filter_config"]["filter_value"] == [alice_id]
        assert synthetic[1]["filter_config"]["filter_value"] == [
            "00000000-0000-0000-0000-000000000000"
        ]

    @pytest.mark.parametrize(
        "families",
        [
            ("session", "user"),
            ("session", "user_id_type"),
            ("user", "user_id_type"),
            ("session", "user", "user_id_type"),
        ],
        ids=[
            "session-user",
            "session-user-type",
            "user-user-type",
            "session-user-user-type",
        ],
    )
    @pytest.mark.parametrize(
        "session_column", ["session", "session_id", "trace_session_id"]
    )
    @pytest.mark.parametrize("user_column", ["user", "user_id"])
    @pytest.mark.parametrize("profile", ["inclusive", "negated-null"])
    def test_session_list_identity_filter_combination_matrix(
        self,
        families,
        session_column,
        user_column,
        profile,
    ):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        session_id = str(uuid.uuid4())
        user_id = str(uuid.uuid4())
        filters = []
        if "session" in families:
            filters.append(
                {
                    "column_id": session_column,
                    "property_id": "system_attribute:sessions:session",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in" if profile == "inclusive" else "not_in",
                        "filter_value": ["external-session"],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            )
        if "user" in families:
            filters.append(
                {
                    "column_id": user_column,
                    "property_id": "system_attribute:sessions:user",
                    "filter_config": {
                        "filter_type": "text",
                        "filter_op": "in" if profile == "inclusive" else "not_in",
                        "filter_value": ["alice"],
                        "col_type": "SYSTEM_METRIC",
                    },
                }
            )
        if "user_id_type" in families:
            type_config = {
                "filter_type": "text",
                "filter_op": "in" if profile == "inclusive" else "is_null",
                "col_type": "SYSTEM_METRIC",
            }
            if profile == "inclusive":
                type_config["filter_value"] = ["email"]
            filters.append(
                {
                    "column_id": "user_id_type",
                    "property_id": "system_attribute:sessions:user_id_type",
                    "filter_config": type_config,
                }
            )

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        with (
            mock.patch(
                "tracer.services.clickhouse.v2.trace_session_dict_reader.resolve_session_filter_values",
                return_value={"external-session": [session_id]},
            ) as session_resolve_mock,
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"alice": [user_id]}, None),
            ) as user_resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        by_column = {
            item["column_id"]: item["filter_config"]
            for item in captured["filters"]
            if item.get("column_id") in {session_column, "end_user_id", "user_id_type"}
        }
        if "session" in families:
            session_resolve_mock.assert_called_once()
            assert by_column[session_column]["filter_value"] == [session_id]
        else:
            session_resolve_mock.assert_not_called()
        if "user" in families:
            user_resolve_mock.assert_called_once()
            assert by_column["end_user_id"]["filter_value"] == [user_id]
        else:
            user_resolve_mock.assert_not_called()
        if "user_id_type" in families:
            assert by_column["user_id_type"]["filter_op"] == (
                "in" if profile == "inclusive" else "is_null"
            )

    def test_user_id_filter_preserves_negated_operator(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}
        alice_id = str(uuid.uuid4())

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "not_equals",
                    "filter_value": "alice",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"alice": [alice_id]}, None),
            ),
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        assert synthetic[0]["filter_config"]["filter_op"] == "not_in"
        assert synthetic[0]["filter_config"]["filter_value"] == [alice_id]

    def test_user_id_null_filter_preserves_null_operator_without_resolution(self):
        from tracer.services.clickhouse.v2.query_builders.session_list import (
            SessionListQueryBuilderV2 as RealBuilder,
        )

        view = self._make_view()
        request = self._make_request()
        analytics = self._patch_analytics()
        captured = {}

        def _capture_builder(*args, **kwargs):
            captured["filters"] = list(kwargs.get("filters") or [])
            return RealBuilder(*args, **kwargs)

        filters = [
            {
                "column_id": "user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "is_null",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids"
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            self._patch_annotation_labels(),
            mock.patch(
                "tracer.views.trace_session.SessionListQueryBuilderV2",
                side_effect=_capture_builder,
                wraps=RealBuilder,
            ),
        ):
            view._list_sessions_clickhouse(
                request,
                project_id=uuid.uuid4(),
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(filters=filters),
            )

        resolve_mock.assert_not_called()
        synthetic = [
            f for f in captured["filters"] if f.get("column_id") == "end_user_id"
        ]
        assert len(synthetic) == 1
        cfg = synthetic[0]["filter_config"]
        assert cfg["filter_op"] == "is_null"
        assert "filter_value" not in cfg

    def test_end_user_display_reuses_filter_resolution(self):
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
        display = {
            "id": eu_ids[0],
            "user_id": "user-eve",
            "user_id_type": "DEVELOPER_IDENTIFIER",
            "user_id_hash": "deadbeef",
        }
        with (
            mock.patch(
                "tracer.views.trace_session._resolve_end_user_ids_for_user_ids",
                return_value=({"user-eve": eu_ids}, display),
            ) as resolve_mock,
            self._patch_session_name_lookup(),
            mock.patch.object(view, "_fetch_end_user_info", return_value={}),
        ):
            status, payload = view._list_sessions_clickhouse(
                request,
                project_id=None,
                project=None,
                analytics=analytics,
                validated_data=self._make_validated_data(),
                org_project_ids=[str(uuid.uuid4())],
            )

        resolve_mock.assert_called_once()

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
