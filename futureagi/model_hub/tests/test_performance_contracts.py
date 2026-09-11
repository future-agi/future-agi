import uuid

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models import DatasetProperties
from model_hub.models.ai_model import AIModel
from model_hub.models.metric import Metric
from model_hub.models.performance_report import PerformanceReport
from model_hub.serializers.contracts import (
    PerformanceExportRequestSerializer,
    PerformanceQueryRequestSerializer,
    PerformanceTagDistributionRequestSerializer,
)


def _performance_filter():
    return {
        "type": "property",
        "datatype": "string",
        "operator": "equal",
        "values": ["vip"],
        "key": "customer_tier",
        "key_id": "",
    }


def _performance_dataset(metric_id=None):
    return {
        "environment": "Production",
        "version": "v1",
        "metric_id": str(metric_id or uuid.uuid4()),
        "filters": [_performance_filter()],
    }


def _performance_query_payload(metric_id):
    return {
        "datasets": [_performance_dataset(metric_id)],
        "filters": [_performance_filter()],
        "breakdown": [],
        "agg_by": "daily",
        "start_date": "2026-01-01 00:00:00",
        "end_date": "2026-01-02 00:00:00",
    }


def _performance_detail_payload(metric_id):
    return {
        "dataset": _performance_dataset(metric_id),
        "filters": [_performance_filter()],
        "page": 1,
        "start_date": "2026-01-01 00:00:00",
        "end_date": "2026-01-02 00:00:00",
    }


def _performance_tag_payload(metric_id):
    return {
        "dataset": _performance_dataset(metric_id),
        "filters": [_performance_filter()],
        "agg_by": "daily",
        "start_date": "2026-01-01 00:00:00",
        "end_date": "2026-01-02 00:00:00",
        "graph_type": "all",
    }


def _performance_report_payload(metric_id, name="Daily quality"):
    return {
        "name": name,
        "datasets": [_performance_dataset(metric_id)],
        "filters": [_performance_filter()],
        "breakdown": [],
        "aggregation": "daily",
        "start_date": "2026-01-01 00:00:00",
        "end_date": "2026-01-02 00:00:00",
    }


def _create_model(organization, workspace, name):
    return AIModel.objects.create(
        user_model_id=name,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _create_metric(model, name="API quality"):
    return Metric.objects.create(
        name=name,
        text_prompt="Score the response.",
        criteria_breakdown=["Score the response."],
        model=model,
        metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT,
        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
        tags=["quality:good", "quality:bad"],
    )


def _create_property(model, organization, workspace):
    return DatasetProperties.objects.create(
        model=model,
        environment="Production",
        version="v1",
        name="customer_tier",
        datatype="string",
        values=["vip", "standard"],
        explanation="Customer tier",
        organization=organization,
        workspace=workspace,
    )


def _create_report(model, organization, workspace, metric, name="Daily quality"):
    return PerformanceReport.no_workspace_objects.create(
        model=model,
        organization=organization,
        workspace=workspace,
        **_performance_report_payload(metric.id, name=name),
    )


def _other_workspace(organization, user):
    return Workspace.no_workspace_objects.create(
        name=f"Performance other workspace {uuid.uuid4()}",
        organization=organization,
        is_active=True,
        created_by=user,
    )


def _stub_performance_clickhouse(monkeypatch):
    monkeypatch.setattr(
        "model_hub.views.performance.get_performance_details_query",
        lambda *args, **kwargs: [["2026-01-01 00:00:00", 1]],
    )
    monkeypatch.setattr(
        "model_hub.views.performance.calculate_performance_details",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "model_hub.views.performance.get_all_tags_distribution",
        lambda *args, **kwargs: [["quality:good", 1]],
    )
    monkeypatch.setattr(
        "model_hub.views.performance.get_top_tags_distribution",
        lambda *args, **kwargs: [["quality:good", 1]],
    )


class TestPerformanceContracts:
    def test_performance_query_accepts_canonical_payload(self):
        serializer = PerformanceQueryRequestSerializer(
            data={
                "datasets": [_performance_dataset()],
                "filters": [_performance_filter()],
                "breakdown": [{"key": "customer_tier", "key_id": "prop-1"}],
                "agg_by": "daily",
                "start_date": "2026-01-01 00:00:00",
                "end_date": "2026-01-31 23:59:59",
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["datasets"][0]["metric_id"]

    def test_performance_query_rejects_top_level_camel_case_contract(self):
        serializer = PerformanceQueryRequestSerializer(
            data={
                "datasets": [_performance_dataset()],
                "filters": [],
                "breakdown": [],
                "aggBy": "daily",
                "startDate": "2026-01-01 00:00:00",
                "endDate": "2026-01-31 23:59:59",
            }
        )

        assert not serializer.is_valid()
        assert "aggBy" in serializer.errors
        assert "startDate" in serializer.errors
        assert "endDate" in serializer.errors

    def test_performance_query_rejects_nested_camel_case_contract(self):
        serializer = PerformanceQueryRequestSerializer(
            data={
                "datasets": [
                    {
                        "environment": "Production",
                        "version": "v1",
                        "metricId": str(uuid.uuid4()),
                        "filters": [
                            {
                                **_performance_filter(),
                                "keyId": "legacy",
                            }
                        ],
                    }
                ],
                "filters": [],
                "breakdown": [{"key": "customer_tier", "keyId": "prop-1"}],
                "agg_by": "daily",
                "start_date": "2026-01-01 00:00:00",
                "end_date": "2026-01-31 23:59:59",
            }
        )

        assert not serializer.is_valid()
        assert "datasets" in serializer.errors
        assert "breakdown" in serializer.errors

    def test_performance_tag_distribution_rejects_legacy_graph_type_alias(self):
        serializer = PerformanceTagDistributionRequestSerializer(
            data={
                "dataset": _performance_dataset(),
                "filters": [],
                "agg_by": "daily",
                "start_date": "2026-01-01 00:00:00",
                "end_date": "2026-01-31 23:59:59",
                "graphType": "all",
            }
        )

        assert not serializer.is_valid()
        assert "graphType" in serializer.errors

    def test_performance_export_accepts_canonical_payload(self):
        serializer = PerformanceExportRequestSerializer(
            data={
                "dataset": _performance_dataset(),
                "filters": [_performance_filter()],
                "start_date": "2026-01-01 00:00:00",
                "end_date": "2026-01-31 23:59:59",
            }
        )

        assert serializer.is_valid(), serializer.errors

    def test_performance_export_rejects_legacy_date_aliases(self):
        serializer = PerformanceExportRequestSerializer(
            data={
                "dataset": _performance_dataset(),
                "filters": [],
                "startDate": "2026-01-01 00:00:00",
                "endDate": "2026-01-31 23:59:59",
            }
        )

        assert not serializer.is_valid()
        assert "startDate" in serializer.errors
        assert "endDate" in serializer.errors


@pytest.mark.integration
@pytest.mark.api
class TestPerformanceApiContracts:
    def test_model_performance_and_report_lifecycle_are_workspace_scoped(
        self, auth_client, organization, workspace, monkeypatch
    ):
        _stub_performance_clickhouse(monkeypatch)
        model = _create_model(organization, workspace, "performance-active-model")
        metric = _create_metric(model)
        _create_property(model, organization, workspace)

        options_response = auth_client.get(
            f"/model-hub/performance/options/{model.id}/",
            {"metric_id": str(metric.id)},
        )
        assert options_response.status_code == status.HTTP_200_OK
        options_result = options_response.json()["result"]
        assert options_result["performance_metric"][0]["id"] == str(metric.id)
        assert options_result["properties"][0]["name"] == "customer_tier"
        assert options_result["performance_tags"] == ["quality:bad", "quality:good"]

        graph_response = auth_client.post(
            f"/model-hub/performance/{model.id}/",
            _performance_query_payload(metric.id),
            format="json",
        )
        assert graph_response.status_code == status.HTTP_200_OK
        assert graph_response.json()["Dataset 1"] == [["2026-01-01 00:00:00", 1]]

        detail_response = auth_client.post(
            f"/model-hub/performance/detail/{model.id}/",
            _performance_detail_payload(metric.id),
            format="json",
        )
        assert detail_response.status_code == status.HTTP_200_OK
        assert detail_response.json()["result"] == []

        export_response = auth_client.post(
            f"/model-hub/performance/export/{model.id}/",
            _performance_detail_payload(metric.id),
            format="json",
        )
        assert export_response.status_code == status.HTTP_200_OK
        assert "Model Input" in export_response.content.decode()

        tag_response = auth_client.post(
            f"/model-hub/performance/tag-distribution/{model.id}/",
            _performance_tag_payload(metric.id),
            format="json",
        )
        assert tag_response.status_code == status.HTTP_200_OK
        assert tag_response.json()["result"]["good"] == [["quality:good", 1]]

        create_response = auth_client.post(
            f"/model-hub/performance/report/{model.id}/",
            _performance_report_payload(metric.id),
            format="json",
        )
        assert create_response.status_code == status.HTTP_201_CREATED
        report_id = create_response.json()["result"]["id"]

        report = PerformanceReport.no_workspace_objects.get(id=report_id)
        assert report.model_id == model.id
        assert report.organization_id == organization.id
        assert report.workspace_id == workspace.id

        list_response = auth_client.get(f"/model-hub/performance/report/{model.id}/")
        assert list_response.status_code == status.HTTP_200_OK
        assert any(row["id"] == report_id for row in list_response.json()["results"])

        delete_response = auth_client.delete(
            f"/model-hub/performance/report/{model.id}/{report_id}/"
        )
        assert delete_response.status_code == status.HTTP_200_OK
        deleted_report = PerformanceReport.all_objects.get(id=report_id)
        assert deleted_report.deleted is True
        assert deleted_report.deleted_at is not None

    def test_model_performance_hidden_workspace_rows_return_not_found(
        self, auth_client, organization, workspace, user, monkeypatch
    ):
        _stub_performance_clickhouse(monkeypatch)
        other_workspace = _other_workspace(organization, user)
        other_model = _create_model(
            organization,
            other_workspace,
            "performance-other-workspace-model",
        )
        other_metric = _create_metric(other_model, name="Other API quality")
        _create_property(other_model, organization, other_workspace)
        other_report = _create_report(
            other_model,
            organization,
            other_workspace,
            other_metric,
            name="Other daily quality",
        )

        probes = [
            lambda: auth_client.get(
                f"/model-hub/performance/options/{other_model.id}/"
            ),
            lambda: auth_client.post(
                f"/model-hub/performance/{other_model.id}/",
                _performance_query_payload(other_metric.id),
                format="json",
            ),
            lambda: auth_client.post(
                f"/model-hub/performance/detail/{other_model.id}/",
                _performance_detail_payload(other_metric.id),
                format="json",
            ),
            lambda: auth_client.post(
                f"/model-hub/performance/export/{other_model.id}/",
                _performance_detail_payload(other_metric.id),
                format="json",
            ),
            lambda: auth_client.post(
                f"/model-hub/performance/tag-distribution/{other_model.id}/",
                _performance_tag_payload(other_metric.id),
                format="json",
            ),
            lambda: auth_client.get(f"/model-hub/performance/report/{other_model.id}/"),
            lambda: auth_client.post(
                f"/model-hub/performance/report/{other_model.id}/",
                _performance_report_payload(other_metric.id, name="Hidden report"),
                format="json",
            ),
            lambda: auth_client.delete(
                f"/model-hub/performance/report/{other_model.id}/{other_report.id}/"
            ),
        ]

        for probe in probes:
            response = probe()
            assert response.status_code == status.HTTP_404_NOT_FOUND

        other_report.refresh_from_db()
        assert other_report.deleted is False
        assert other_report.deleted_at is None

    def test_performance_graph_rejects_legacy_body_aliases(
        self, auth_client, organization, workspace
    ):
        model = AIModel.objects.create(
            user_model_id="performance-contract-model",
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.post(
            f"/model-hub/performance/{model.id}/",
            {
                "datasets": [],
                "filters": [],
                "breakdown": [],
                "aggBy": "daily",
                "startDate": "2026-01-01 00:00:00",
                "endDate": "2026-01-31 23:59:59",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_performance_export_rejects_legacy_body_aliases(
        self, auth_client, organization, workspace
    ):
        model = AIModel.objects.create(
            user_model_id="performance-export-contract-model",
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.post(
            f"/model-hub/performance/export/{model.id}/",
            {
                "dataset": _performance_dataset(),
                "filters": [],
                "startDate": "2026-01-01 00:00:00",
                "endDate": "2026-01-31 23:59:59",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
