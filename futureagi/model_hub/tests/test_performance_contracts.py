import uuid

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
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
