import uuid

import pytest
from rest_framework import status

from model_hub.serializers.eval_group import ApplyEvalGroupRequestSerializer


class TestApplyEvalGroupContracts:
    def test_apply_eval_group_accepts_canonical_payload(self):
        dataset_id = str(uuid.uuid4())
        serializer = ApplyEvalGroupRequestSerializer(
            data={
                "eval_group_id": str(uuid.uuid4()),
                "page_id": "DATASET",
                "filters": {"dataset_id": dataset_id},
                "mapping": {"hypothesis": "input", "reference": "expected"},
                "params": {"k": 3},
                "deselected_evals": [str(uuid.uuid4())],
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["page_id"] == "DATASET"
        assert str(serializer.validated_data["filters"]["dataset_id"]) == dataset_id

    def test_apply_eval_group_accepts_canonical_simulate_filters(self):
        simulate_id = str(uuid.uuid4())
        serializer = ApplyEvalGroupRequestSerializer(
            data={
                "eval_group_id": str(uuid.uuid4()),
                "page_id": "SIMULATE",
                "filters": {
                    "simulate_id": simulate_id,
                    "filters": [
                        {
                            "column_id": "duration",
                            "filter_config": {
                                "filter_type": "number",
                                "filter_op": "greater_than_or_equal",
                                "filter_value": 10,
                            },
                        }
                    ],
                },
                "mapping": {"hypothesis": "input"},
            }
        )

        assert serializer.is_valid(), serializer.errors
        assert str(serializer.validated_data["filters"]["simulate_id"]) == simulate_id

    def test_apply_eval_group_rejects_legacy_aliases(self):
        serializer = ApplyEvalGroupRequestSerializer(
            data={
                "evalGroupId": str(uuid.uuid4()),
                "pageId": "DATASET",
                "filters": {"dataset_id": str(uuid.uuid4())},
                "mapping": {"hypothesis": "input", "reference": "expected"},
            }
        )

        assert not serializer.is_valid()
        assert "evalGroupId" in serializer.errors
        assert "pageId" in serializer.errors

    def test_apply_eval_group_rejects_missing_page_scope(self):
        serializer = ApplyEvalGroupRequestSerializer(
            data={
                "eval_group_id": str(uuid.uuid4()),
                "page_id": "DATASET",
                "filters": {},
                "mapping": {"hypothesis": "input", "reference": "expected"},
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors

    def test_apply_eval_group_rejects_unknown_filter_keys(self):
        serializer = ApplyEvalGroupRequestSerializer(
            data={
                "eval_group_id": str(uuid.uuid4()),
                "page_id": "DATASET",
                "filters": {
                    "dataset_id": str(uuid.uuid4()),
                    "datasetId": str(uuid.uuid4()),
                },
                "mapping": {"hypothesis": "input", "reference": "expected"},
            }
        )

        assert not serializer.is_valid()
        assert "filters" in serializer.errors


@pytest.mark.integration
@pytest.mark.api
def test_apply_eval_group_api_rejects_legacy_aliases(auth_client):
    response = auth_client.post(
        "/model-hub/eval-groups/apply-eval-group/",
        {
            "evalGroupId": str(uuid.uuid4()),
            "pageId": "DATASET",
            "filters": {},
            "mapping": {},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
