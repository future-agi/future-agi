"""Score ingestion categories must not reach EvalLogger's numeric field."""

import pytest

from integrations.transformers.langfuse_transformer import LangfuseTransformer

pytestmark = pytest.mark.unit

TRACE_ID = "00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        pytest.param("CATEGORICAL", "positive", id="string-category"),
        pytest.param("categorical", "positive", id="lowercase-type"),
        pytest.param("CATEGORICAL", "0", id="numeric-looking-category"),
        pytest.param("CATEGORICAL", "", id="empty-category"),
    ],
)
def test_categorical_ingestion_value_uses_text_field(data_type, value):
    score = {
        "id": "score-category",
        "name": "sentiment",
        "observationId": "span-category",
        "comment": "Imported category",
        "dataType": data_type,
        "value": value,
    }

    result = LangfuseTransformer().transform_scores({"scores": [score]}, TRACE_ID)

    assert result == [
        {
            "langfuse_score_id": "score-category",
            "trace_id": TRACE_ID,
            "observation_id": "span-category",
            "eval_type_id": "sentiment",
            "output_float": None,
            "output_str": value,
            "eval_explanation": "Imported category",
            "output_bool": None,
        }
    ]


@pytest.mark.parametrize(
    ("fields", "output_float", "output_str", "output_bool"),
    [
        pytest.param(
            {"dataType": "CATEGORICAL", "value": 2, "stringValue": "positive"},
            2,
            "positive",
            None,
            id="categorical-numeric-snapshot",
        ),
        pytest.param(
            {"dataType": "CATEGORICAL", "stringValue": "positive"},
            None,
            "positive",
            None,
            id="categorical-text-only-snapshot",
        ),
        pytest.param(
            {"dataType": "CATEGORICAL", "value": None, "stringValue": "positive"},
            None,
            "positive",
            None,
            id="categorical-null-value-snapshot",
        ),
        pytest.param(
            {"dataType": "NUMERIC", "value": 0.9},
            0.9,
            "",
            None,
            id="numeric",
        ),
        pytest.param(
            {"dataType": "NUMERIC", "value": 3, "stringValue": "good"},
            3,
            "good",
            None,
            id="numeric-with-label",
        ),
        pytest.param(
            {"dataType": "BOOLEAN", "value": 1, "stringValue": "passed"},
            1,
            "passed",
            True,
            id="boolean-true",
        ),
        pytest.param(
            {"dataType": "BOOLEAN", "value": 0},
            0,
            "",
            False,
            id="boolean-false",
        ),
        pytest.param({"value": 0.5}, 0.5, "", None, id="unspecified-type"),
    ],
)
def test_snapshot_numeric_and_boolean_mappings_stay_compatible(
    fields, output_float, output_str, output_bool
):
    score = {"id": "score-control", "name": "quality", **fields}

    result = LangfuseTransformer().transform_scores({"scores": [score]}, TRACE_ID)

    assert len(result) == 1
    assert result[0]["output_float"] == output_float
    assert result[0]["output_str"] == output_str
    assert result[0]["output_bool"] is output_bool


def test_mixed_score_batch_keeps_each_value_in_its_own_field():
    scores = [
        {"id": "category", "dataType": "CATEGORICAL", "value": "positive"},
        {"id": "numeric", "dataType": "NUMERIC", "value": 0.75},
        {"id": "boolean", "dataType": "BOOLEAN", "value": 0},
        {
            "id": "snapshot",
            "dataType": "CATEGORICAL",
            "value": 1,
            "stringValue": "negative",
        },
    ]

    result = LangfuseTransformer().transform_scores({"scores": scores}, TRACE_ID)

    assert [
        (
            score["langfuse_score_id"],
            score["output_float"],
            score["output_str"],
            score["output_bool"],
        )
        for score in result
    ] == [
        ("category", None, "positive", None),
        ("numeric", 0.75, "", None),
        ("boolean", 0, "", False),
        ("snapshot", 1, "negative", None),
    ]
