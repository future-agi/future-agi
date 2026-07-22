import pytest

from model_hub.utils.function_eval_params import normalize_eval_runtime_config


def _k_schema(required=False, nullable=True, minimum=1):
    return {
        "function_params_schema": {
            "k": {
                "type": "integer",
                "default": None,
                "required": required,
                "nullable": nullable,
                "minimum": minimum,
            }
        }
    }


def test_normalize_integer_accepts_positive_sign_string():
    normalized = normalize_eval_runtime_config(_k_schema(), {"params": {"k": "+5"}})
    assert normalized["params"]["k"] == 5


def test_normalize_integer_rejects_double_negative_string_with_clean_error():
    with pytest.raises(ValueError, match="k must be an integer"):
        normalize_eval_runtime_config(_k_schema(), {"params": {"k": "--5"}})


def test_normalize_integer_treats_whitespace_only_string_as_empty():
    normalized = normalize_eval_runtime_config(_k_schema(), {"params": {"k": "   "}})
    assert normalized["params"]["k"] is None


def test_fallback_schema_resolves_from_evals_source_of_truth():
    normalized = normalize_eval_runtime_config(
        {"eval_type_id": "RecallAtK"}, {"params": {"k": "3"}}
    )
    assert normalized["params"]["k"] == 3
