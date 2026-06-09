"""Phase 4 — Span resolver tests.

Mirrors the Phase 1 matrix (baseline, exclude, cap, isolation,
list-endpoint parity) but targets ``resolve_filtered_span_ids`` and the
``/tracer/observation-span/list_spans_observe/`` endpoint.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.services.bulk_selection import (
    ResolveResult,
    resolve_filtered_span_ids,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def observe_project(db, organization, workspace):
    return Project.objects.create(
        name="BulkSel Span Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def parent_trace(db, observe_project):
    return Trace.objects.create(project=observe_project, name="span-parent")


@pytest.fixture
def seeded_spans(db, observe_project, parent_trace):
    """15 spans on observe_project with staggered start_times.

    Oldest-first insertion: ``seeded_spans[0]`` starts earliest, ``[-1]``
    latest. ``order_by("-start_time", "-id")`` returns them newest-first.
    """
    now = timezone.now()
    spans = []
    for i in range(15):
        s = ObservationSpan.objects.create(
            id=f"sp-{i:04d}-{parent_trace.id.hex[:8]}",
            project=observe_project,
            trace=parent_trace,
            name=f"span-{i}",
            observation_type="llm",
            start_time=now + timedelta(minutes=i),
            end_time=now + timedelta(minutes=i, seconds=1),
            parent_span_id=None,
        )
        spans.append(s)
    return spans


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestBaseline:
    def test_no_filter_returns_all_project_spans(
        self, observe_project, seeded_spans, organization
    ):
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert isinstance(result, ResolveResult)
        assert result.total_matching == 15
        assert len(result.ids) == 15
        assert result.truncated is False

    def test_no_filter_ordered_by_start_time_desc(
        self, observe_project, seeded_spans, organization
    ):
        """Latest start_time first."""
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert result.ids[0] == seeded_spans[-1].id
        assert result.ids[-1] == seeded_spans[0].id

    def test_none_filters_equivalent_to_empty(
        self, observe_project, seeded_spans, organization
    ):
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=None,  # type: ignore[arg-type]
            organization=organization,
        )
        assert result.total_matching == 15


# --------------------------------------------------------------------------
# exclude_ids
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestExcludeIds:
    def test_excludes_given_ids_from_result(
        self, observe_project, seeded_spans, organization
    ):
        exclude = {seeded_spans[0].id, seeded_spans[1].id}
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=exclude,
            organization=organization,
        )
        assert result.total_matching == 13
        assert len(result.ids) == 13
        for excluded_id in exclude:
            assert excluded_id not in result.ids

    def test_exclude_accepts_list_and_tuple(
        self, observe_project, seeded_spans, organization
    ):
        list_result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=[seeded_spans[0].id],
            organization=organization,
        )
        assert list_result.total_matching == 14

        tuple_result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=(seeded_spans[1].id,),
            organization=organization,
        )
        assert tuple_result.total_matching == 14

    def test_exclude_none_is_noop(
        self, observe_project, seeded_spans, organization
    ):
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=None,
            organization=organization,
        )
        assert result.total_matching == 15


# --------------------------------------------------------------------------
# Cap enforcement
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestCap:
    def test_cap_truncates_ids(
        self, observe_project, seeded_spans, organization
    ):
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=7,
        )
        assert len(result.ids) == 7
        assert result.total_matching == 15
        assert result.truncated is True

    def test_cap_above_total_is_not_truncated(
        self, observe_project, seeded_spans, organization
    ):
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=100,
        )
        assert result.truncated is False
        assert len(result.ids) == 15

    def test_cap_returns_most_recent_first(
        self, observe_project, seeded_spans, organization
    ):
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            cap=3,
        )
        # Last-inserted has latest start_time → first in latest-first order.
        assert result.ids == [s.id for s in seeded_spans[-1:-4:-1]]


# --------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------


@pytest.mark.django_db
class TestIsolation:
    def test_org_isolation(
        self, observe_project, seeded_spans, organization, db
    ):
        other_org = Organization.objects.create(name="Other Span Org")
        other_project = Project.objects.create(
            name="Other Span Project",
            organization=other_org,
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        with pytest.raises(Project.DoesNotExist):
            resolve_filtered_span_ids(
                project_id=other_project.id,
                filters=[],
                organization=organization,
            )

    def test_workspace_isolation(
        self, observe_project, seeded_spans, organization, workspace, user, db
    ):
        other_ws = Workspace.objects.create(
            name="Other WS",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
            workspace=other_ws,
        )
        assert result.total_matching == 0
        assert result.ids == []

    def test_project_scoping(
        self, observe_project, seeded_spans, organization, workspace, parent_trace
    ):
        sibling_project = Project.objects.create(
            name="Sibling Span Project",
            organization=organization,
            workspace=workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        sibling_trace = Trace.objects.create(project=sibling_project, name="sib")
        sibling_span = ObservationSpan.objects.create(
            id=f"sib-{sibling_trace.id.hex[:8]}",
            project=sibling_project,
            trace=sibling_trace,
            name="sibling-span",
            observation_type="llm",
            start_time=timezone.now(),
            end_time=timezone.now(),
            parent_span_id=None,
        )

        result = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        assert sibling_span.id not in result.ids
        assert result.total_matching == 15


# --------------------------------------------------------------------------
# List-endpoint parity
# --------------------------------------------------------------------------


def _list_endpoint_span_ids(auth_client, project_id, filters):
    import json

    resp = auth_client.get(
        "/tracer/observation-span/list_spans_observe/",
        {
            "project_id": str(project_id),
            "filters": json.dumps(filters),
            "page_number": 0,
            "page_size": 200,
        },
    )
    assert resp.status_code == 200, resp.data
    table = (resp.data.get("result") or {}).get("table", [])
    # list_spans_observe annotates each row with ``span_id`` = F("id").
    ids = set()
    for row in table:
        sid = row.get("span_id") or row.get("id")
        if sid is not None:
            ids.add(str(sid))
    return ids


@pytest.mark.django_db
class TestParityWithListEndpoint:
    def test_parity_no_filter(
        self, auth_client, observe_project, seeded_spans, organization
    ):
        resolver = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            organization=organization,
        )
        list_ids = _list_endpoint_span_ids(auth_client, observe_project.id, [])
        assert {str(i) for i in resolver.ids} == list_ids

    def test_parity_empty_filter_after_exclude(
        self, auth_client, observe_project, seeded_spans, organization
    ):
        excluded = {seeded_spans[0].id, seeded_spans[5].id}
        resolver = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=[],
            exclude_ids=excluded,
            organization=organization,
        )
        list_ids = _list_endpoint_span_ids(auth_client, observe_project.id, [])
        expected = list_ids - {str(i) for i in excluded}
        assert {str(i) for i in resolver.ids} == expected

    def test_parity_span_name_system_metric_filter(
        self, auth_client, observe_project, seeded_spans, organization
    ):
        seeded_spans[3].name = "vip tool span"
        seeded_spans[3].save(update_fields=["name"])
        seeded_spans[4].name = "ordinary tool span"
        seeded_spans[4].save(update_fields=["name"])
        filters = [
            {
                "column_id": "span_name",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "contains",
                    "filter_value": "vip tool",
                    "col_type": "SYSTEM_METRIC",
                },
            }
        ]

        resolver = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
        )
        list_ids = _list_endpoint_span_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {seeded_spans[3].id}

    def test_parity_span_attribute_filter(
        self, auth_client, observe_project, seeded_spans, organization
    ):
        seeded_spans[5].span_attributes = {"customer_tier": "vip", "risk_score": 92}
        seeded_spans[5].save(update_fields=["span_attributes"])
        seeded_spans[6].span_attributes = {"customer_tier": "free", "risk_score": 42}
        seeded_spans[6].save(update_fields=["span_attributes"])
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

        resolver = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
        )
        list_ids = _list_endpoint_span_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {seeded_spans[5].id}

    def test_parity_eval_metric_filter(
        self, auth_client, observe_project, seeded_spans, organization, workspace
    ):
        from model_hub.models.evals_metric import EvalTemplate
        from tracer.models.custom_eval_config import CustomEvalConfig
        from tracer.models.observation_span import EvalLogger

        template = EvalTemplate.objects.create(
            name="span_bulk_quality",
            organization=organization,
            workspace=workspace,
        )
        config = CustomEvalConfig.objects.create(
            name="Span Quality Eval",
            eval_template=template,
            project=observe_project,
        )
        EvalLogger.objects.create(
            trace=seeded_spans[7].trace,
            observation_span=seeded_spans[7],
            custom_eval_config=config,
            output_float=0.94,
        )
        EvalLogger.objects.create(
            trace=seeded_spans[8].trace,
            observation_span=seeded_spans[8],
            custom_eval_config=config,
            output_float=0.32,
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

        resolver = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
        )
        list_ids = _list_endpoint_span_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {seeded_spans[7].id}

    def test_parity_annotation_label_filter(
        self, auth_client, observe_project, seeded_spans, organization, workspace, user
    ):
        from model_hub.models.develop_annotations import AnnotationsLabels
        from model_hub.models.score import Score

        label = AnnotationsLabels.objects.create(
            name="span_bulk_quality",
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
        Score.objects.create(
            source_type="observation_span",
            observation_span=seeded_spans[9],
            label=label,
            value={"value": 91},
            annotator=user,
            organization=organization,
            workspace=workspace,
        )
        Score.objects.create(
            source_type="observation_span",
            observation_span=seeded_spans[10],
            label=label,
            value={"value": 49},
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

        resolver = resolve_filtered_span_ids(
            project_id=observe_project.id,
            filters=filters,
            organization=organization,
            user=user,
        )
        list_ids = _list_endpoint_span_ids(auth_client, observe_project.id, filters)

        assert {str(i) for i in resolver.ids} == list_ids == {seeded_spans[9].id}
