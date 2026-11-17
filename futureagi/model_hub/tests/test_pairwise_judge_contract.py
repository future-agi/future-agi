"""Judge operations publish typed errors in the views and saved API contract."""

import json
from pathlib import Path

import pytest

from model_hub.serializers.contracts import ModelHubErrorResponseSerializer
from model_hub.serializers.pairwise_judges import (
    PairwiseJudgeJobSerializer,
    PairwiseJudgeListSerializer,
)
from model_hub.serializers.pairwise_preferences import PairwiseSummarySerializer
from model_hub.views.pairwise_judges import (
    PairwiseJudgeCancelView,
    PairwiseJudgeCreateView,
    PairwiseJudgeDetailView,
)
from model_hub.views.pairwise_preferences import PairwisePreferenceView


@pytest.mark.parametrize(
    "suffix,method,view,success",
    [
        (
            "judges/",
            "post",
            PairwiseJudgeCreateView,
            {200: PairwiseJudgeJobSerializer, 202: PairwiseJudgeJobSerializer},
        ),
        (
            "judges/",
            "get",
            PairwiseJudgeCreateView,
            {200: PairwiseJudgeListSerializer},
        ),
        (
            "judges/{job_id}/",
            "get",
            PairwiseJudgeDetailView,
            {200: PairwiseJudgeJobSerializer},
        ),
        (
            "judges/{job_id}/cancel/",
            "post",
            PairwiseJudgeCancelView,
            {200: PairwiseJudgeJobSerializer},
        ),
        (
            "summary/",
            "get",
            PairwisePreferenceView,
            {200: PairwiseSummarySerializer},
        ),
    ],
)
def test_judge_operations_publish_typed_errors(suffix, method, view, success):
    error_codes = (400, 401, 403, 404, 409, 500)
    handler = getattr(view, method)
    assert handler._swagger_auto_schema["responses"] == {
        **success,
        **dict.fromkeys(error_codes, ModelHubErrorResponseSerializer),
    }

    root = Path(__file__).resolve().parents[3]
    contract = json.loads((root / "api_contracts/openapi/swagger.json").read_text())
    path = "/model-hub/experiments/v2/{experiment_id}/pairwise/" + suffix
    responses = contract["paths"][path][method]["responses"]
    assert {
        int(code): response.get("schema")
        for code, response in responses.items()
        if int(code) >= 400
    } == {code: {"$ref": "#/definitions/ModelHubErrorResponse"} for code in error_codes}
