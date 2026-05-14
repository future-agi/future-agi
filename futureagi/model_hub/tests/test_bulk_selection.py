"""Phase 1 — Backend filter resolver (source_type=trace) tests.

Covers:
  - No-filter baseline
  - exclude_ids
  - Cap enforcement
  - Org isolation
  - Workspace isolation
  - Project scoping
  - User-scoped filter validation (my_annotations, annotator)
  - Filter parity with list_traces_of_session for each FilterEngine branch
"""

from __future__ import annotations

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    ResolveResult,
    resolve_filtered_trace_ids,
)
from tracer.models.project import Project
from tracer.models.trace import Trace


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def observe_project(db, organization, workspace):
    """Observe-type project in the default org/workspace."""
    return Project.objects.create(
        name="BulkSel Observe Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def seeded_traces(db, observe_project):
    """25 traces on the observe_project.

    Start time annotation falls back to ``created_at`` when there's no root
    span; traces are created in order so index 0 is the oldest and index 24
    is the newest (latest-first ordering reverses the list).
    """
    traces = []
    for i in range(25):
        t = Trace.objects.create(project=observe_project, name=f"t-{i}")
        traces.append(t)
    return traces


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestBaseline:
    def test_no_filter_returns_all_project_traces(
        self, observe_project, seeded_traces, organization
    ):
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert isinstance(result, ResolveResult)
        assert result.total_matching == 25
        assert len(result.ids) == 25
        assert result.truncated is False

    def test_no_filter_ordered_by_start_time_desc(
        self, observe_project, seeded_traces, organization
    ):
        """Newest-first ordering: last-created trace is first in the result."""
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        # seeded_traces index 24 is the most-recently created
        assert result.ids[0] == seeded_traces[-1].id
        assert result.ids[-1] == seeded_traces[0].id

    def test_none_filters_equivalent_to_empty(
        self, observe_project, seeded_traces, organization
    ):
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=None,  # type: ignore[arg-type]
            organization=organization,
        )
        assert result.total_matching == 25


# --------------------------------------------------------------------------
# exclude_ids
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestExcludeIds:
    def test_excludes_given_ids_from_result(
        self, observe_project, seeded_traces, organization
    ):
        exclude = {seeded_traces[0].id, seeded_traces[1].id}
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=exclude,
            organization=organization,
        )
        assert result.total_matching == 23
        assert len(result.ids) == 23
        for excluded_id in exclude:
            assert excluded_id not in result.ids

    def test_exclude_accepts_list_and_tuple(
        self, observe_project, seeded_traces, organization
    ):
        # list
        list_result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=[seeded_traces[0].id],
            organization=organization,
        )
        assert list_result.total_matching == 24

        # tuple
        tuple_result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=(seeded_traces[1].id,),
            organization=organization,
        )
        assert tuple_result.total_matching == 24

    def test_exclude_none_is_noop(
        self, observe_project, seeded_traces, organization
    ):
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=None,
            organization=organization,
        )
        assert result.total_matching == 25


# --------------------------------------------------------------------------
# Cap enforcement
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestCap:
    def test_cap_truncates_ids(
        self, observe_project, seeded_traces, organization
    ):
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=10,
        )
        assert len(result.ids) == 10
        assert result.total_matching == 25
        assert result.truncated is True

    def test_cap_above_total_is_not_truncated(
        self, observe_project, seeded_traces, organization
    ):
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=100,
        )
        assert result.truncated is False
        assert len(result.ids) == 25

    def test_cap_returns_most_recent_first(
        self, observe_project, seeded_traces, organization
    ):
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=3,
        )
        # Last-created trace is newest → first in latest-first ordering.
        assert result.ids == [t.id for t in seeded_traces[-1:-4:-1]]


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestIsolation:
    def test_org_isolation(
        self, observe_project, seeded_traces, organization, db
    ):
        """Traces from another org are never returned."""
        other_org = Organization.objects.create(name="Other Org")
        other_project = Project.objects.create(
            name="Other Project",
            organization=other_org,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        other_trace = Trace.objects.create(
            project=other_project, name="other-trace"
        )

        # Caller from the default org, trying to "reach" into other_project
        # by passing other_project.id should fail with Project.DoesNotExist
        # (org scoping in _build_trace_base_queryset).
        with pytest.raises(Project.DoesNotExist):
            resolve_filtered_trace_ids(
                project_id=other_project.id,
                filters=[],
                organization=organization,
            )

        # Sanity: our org's call returns only our traces.
        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert other_trace.id not in result.ids

    def test_workspace_isolation(
        self, observe_project, seeded_traces, organization, workspace, user, db
    ):
        """Passing a different workspace excludes the project's traces."""
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )

        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            workspace=other_ws,
        )
        assert result.total_matching == 0
        assert result.ids == []

    def test_project_scoping(
        self, observe_project, seeded_traces, organization, workspace
    ):
        """Only traces from the target project are returned."""
        other_project = Project.objects.create(
            name="Sibling Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        sibling_trace = Trace.objects.create(
            project=other_project, name="sibling"
        )

        result = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert sibling_trace.id not in result.ids
        assert result.total_matching == 25


# --------------------------------------------------------------------------
# User-scoped filter validation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestUserScopedFilters:
    def test_raises_when_my_annotations_without_user(
        self, observe_project, organization
    ):
        with pytest.raises(ValueError, match="user-scoped"):
            resolve_filtered_trace_ids(
                project_id=observe_project.id,
                filters=[
                    {
                        "column_id": "my_annotations",
                        "filter_config": {
                            "filter_type": "boolean",
                            "filter_op": "equals",
                            "filter_value": True,
                        },
                    }
                ],
                organization=organization,
                user=None,
            )

    def test_raises_when_annotator_without_user(
        self, observe_project, organization
    ):
        with pytest.raises(ValueError, match="user-scoped"):
            resolve_filtered_trace_ids(
                project_id=observe_project.id,
                filters=[
                    {
                        "column_id": "annotator",
                        "filter_config": {
                            "filter_type": "string",
                            "filter_op": "equals",
                            "filter_value": "alice",
                        },
                    }
                ],
                organization=organization,
                user=None,
            )

    def test_user_scoped_accepts_camelcase_column_id(
        self, observe_project, organization
    ):
        """camelCase form of the column_id must also trip the guard."""
        with pytest.raises(ValueError, match="user-scoped"):
            resolve_filtered_trace_ids(
                project_id=observe_project.id,
                filters=[
                    {
                        "columnId": "my_annotations",
                        "filter_config": {
                            "filter_type": "boolean",
                            "filter_op": "equals",
                            "filter_value": True,
                        },
                    }
                ],
                organization=organization,
                user=None,
            )

    def test_validator_silent_when_user_provided(self, user):
        """Validator does not raise when user is provided for user-scoped cols."""
        from model_hub.services.bulk_selection import _validate_user_scoped_filters

        _validate_user_scoped_filters(
            [
                {
                    "column_id": "my_annotations",
                    "filter_config": {
                        "filter_type": "boolean",
                        "filter_op": "equals",
                        "filter_value": True,
                    },
                }
            ],
            user=user,
        )  # must not raise

    def test_validator_silent_when_no_user_scoped_columns(self):
        """No user-scoped columns present → no user required, no error."""
        from model_hub.services.bulk_selection import _validate_user_scoped_filters

        _validate_user_scoped_filters(
            [{"column_id": "latency", "filter_config": {}}], user=None
        )  # must not raise


# --------------------------------------------------------------------------
# Filter parity with list endpoint (one per FilterEngine branch)
# --------------------------------------------------------------------------


def _list_endpoint_ids(auth_client, project_id, filters):
    """Fetch trace IDs from the list endpoint for the given filter payload.

    The list_traces_of_session response shape uses ``trace_id`` (not ``id``)
    as the row identifier — see ``tracer/views/trace.py:3208``.
    """
    import json

    resp = auth_client.get(
        "/tracer/trace/list_traces_of_session/",
        {
            "project_id": str(project_id),
            "filters": json.dumps(filters),
            "page_number": 0,
            "page_size": 200,
        },
    )
    assert resp.status_code == 200, resp.data
    return {r["trace_id"] for r in (resp.data.get("result") or {}).get("table", [])}


@pytest.mark.django_db
class TestParityWithListEndpoint:
    def test_parity_no_filter(
        self, auth_client, observe_project, seeded_traces, organization
    ):
        """Empty filter: resolver set equals list-endpoint set."""
        resolver = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        list_ids = _list_endpoint_ids(auth_client, observe_project.id, [])
        assert {str(i) for i in resolver.ids} == list_ids

    def test_parity_empty_filter_after_exclude(
        self, auth_client, observe_project, seeded_traces, organization
    ):
        """exclude_ids parity: resolver's non-excluded set matches list endpoint."""
        excluded = {seeded_traces[0].id, seeded_traces[5].id}
        resolver = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=excluded,
            organization=organization,
        )
        list_ids = _list_endpoint_ids(auth_client, observe_project.id, [])
        expected = list_ids - {str(i) for i in excluded}
        assert {str(i) for i in resolver.ids} == expected

    def test_parity_trace_name_system_metric_filter(
        self, auth_client, observe_project, seeded_traces, organization
    ):
        from django.utils import timezone
        from tracer.models.observation_span import ObservationSpan

        match = seeded_traces[7]
        skip = seeded_traces[8]
        ObservationSpan.objects.create(
            id=f"root-{match.id.hex}",
            project=observe_project,
            trace=match,
            name="vip checkout trace",
            observation_type="chain",
            start_time=timezone.now(),
            parent_span_id=None,
        )
        ObservationSpan.objects.create(
            id=f"root-{skip.id.hex}",
            project=observe_project,
            trace=skip,
            name="ordinary trace",
            observation_type="chain",
            start_time=timezone.now(),
            parent_span_id=None,
        )
        filters = [
            {
                "column_id": "trace_name",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "vip checkout",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        resolver = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
        )
        list_ids = _list_endpoint_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {str(match.id)}

    def test_parity_span_attribute_filter(
        self, auth_client, observe_project, seeded_traces, organization
    ):
        from django.utils import timezone
        from tracer.models.observation_span import ObservationSpan

        match = seeded_traces[9]
        skip = seeded_traces[10]
        ObservationSpan.objects.create(
            id=f"root-{match.id.hex}",
            project=observe_project,
            trace=match,
            name="vip root",
            observation_type="chain",
            span_attributes={"customer_tier": "vip", "risk_score": 92},
            start_time=timezone.now(),
            parent_span_id=None,
        )
        ObservationSpan.objects.create(
            id=f"root-{skip.id.hex}",
            project=observe_project,
            trace=skip,
            name="free root",
            observation_type="chain",
            span_attributes={"customer_tier": "free", "risk_score": 42},
            start_time=timezone.now(),
            parent_span_id=None,
        )
        filters = [
            {
                "column_id": "customer_tier",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": "vip",
                    "col_type": "SPAN_ATTRIBUTE",
                },
            }
        ]

        resolver = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
        )
        list_ids = _list_endpoint_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {str(match.id)}

    def test_parity_eval_metric_filter(
        self, auth_client, observe_project, seeded_traces, organization, workspace
    ):
        from django.utils import timezone
        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.observation_span import EvalLogger, ObservationSpan

        template = EvalTemplate.objects.create(
            name="bulk_selection_quality",
            organization=organization,
            workspace=workspace,
        )
        config = CustomEvalConfig.objects.create(
            name="Quality Eval",
            eval_template=template,
            project=observe_project,
        )
        match = seeded_traces[11]
        skip = seeded_traces[12]
        match_span = ObservationSpan.objects.create(
            id=f"root-{match.id.hex}",
            project=observe_project,
            trace=match,
            name="high quality",
            observation_type="chain",
            start_time=timezone.now(),
            parent_span_id=None,
        )
        skip_span = ObservationSpan.objects.create(
            id=f"root-{skip.id.hex}",
            project=observe_project,
            trace=skip,
            name="low quality",
            observation_type="chain",
            start_time=timezone.now(),
            parent_span_id=None,
        )
        EvalLogger.objects.create(
            trace=match,
            observation_span=match_span,
            custom_eval_config=config,
            output_float=0.91,
        )
        EvalLogger.objects.create(
            trace=skip,
            observation_span=skip_span,
            custom_eval_config=config,
            output_float=0.41,
        )
        filters = [
            {
                "column_id": str(config.id),
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than_or_equal",
                    "filter_value": 80,
                    "col_type": "EVAL_METRIC",
                },
            }
        ]

        resolver = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
        )
        list_ids = _list_endpoint_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {str(match.id)}

    def test_parity_annotation_label_filter(
        self, auth_client, observe_project, seeded_traces, organization, workspace, user
    ):
        from django.utils import timezone
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score
        from tracer.models.observation_span import ObservationSpan

        label = AnnotationsLabels.objects.create(
            name="bulk_quality",
            type="numeric",
            organization=organization,
            workspace=workspace,
            settings={
                "min": 0,
                "max": 100,
                "step_size": 1,
                "display_type": "slider",
            },
        )
        match = seeded_traces[13]
        skip = seeded_traces[14]
        ObservationSpan.objects.create(
            id=f"root-{match.id.hex}",
            project=observe_project,
            trace=match,
            name="annotated high",
            observation_type="chain",
            start_time=timezone.now(),
            parent_span_id=None,
        )
        ObservationSpan.objects.create(
            id=f"root-{skip.id.hex}",
            project=observe_project,
            trace=skip,
            name="annotated low",
            observation_type="chain",
            start_time=timezone.now(),
            parent_span_id=None,
        )
        Score.objects.create(
            source_type="trace",
            trace=match,
            label=label,
            value={"value": 93},
            annotator=user,
            organization=organization,
            workspace=workspace,
        )
        Score.objects.create(
            source_type="trace",
            trace=skip,
            label=label,
            value={"value": 50},
            annotator=user,
            organization=organization,
            workspace=workspace,
        )
        filters = [
            {
                "column_id": str(label.id),
                "filter_config": {
                    "filter_type": "number",
                    "filter_op": "greater_than",
                    "filter_value": 80,
                    "col_type": "ANNOTATION",
                },
            }
        ]

        resolver = resolve_filtered_trace_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
            user=user,
        )
        list_ids = _list_endpoint_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {str(match.id)}
