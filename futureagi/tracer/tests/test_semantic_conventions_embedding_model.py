"""Regression coverage for OpenInference embedding model-name fallback."""

import pytest

from tracer.utils.semantic_conventions import AttributeRegistry, get_attribute

_EXISTING_MODEL_ALIASES = (
    "llm.model_name",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "llm.request.model",
)


@pytest.mark.parametrize(
    "attributes",
    [
        {
            "openinference.span.kind": "EMBEDDING",
            "embedding.model_name": "text-embedding-3-small",
        },
        {"embedding": {"model_name": "text-embedding-3-small"}},
        {
            "llm.model_name": None,
            "gen_ai.request.model": None,
            "gen_ai.response.model": None,
            "llm.request.model": None,
            "embedding.model_name": "text-embedding-3-small",
        },
    ],
    ids=["flat", "nested", "null-primary-aliases"],
)
def test_embedding_model_is_resolved(attributes):
    assert get_attribute(attributes, "model_name") == "text-embedding-3-small"


def test_embedding_model_reports_its_source():
    attributes = {"embedding.model_name": "text-embedding-3-small"}

    assert AttributeRegistry.get_value_with_source(attributes, "model_name") == (
        "text-embedding-3-small",
        "embedding.model_name",
    )


def test_embedding_model_is_in_canonical_values():
    attributes = {
        "openinference.span.kind": "EMBEDDING",
        "embedding.model_name": "text-embedding-3-small",
    }

    values = AttributeRegistry.get_all_values(attributes)

    assert values["model_name"] == "text-embedding-3-small"
    assert AttributeRegistry.normalize_span_kind(values["span_kind"]) == "embedding"


@pytest.mark.parametrize("first_present", range(4), ids=_EXISTING_MODEL_ALIASES)
def test_existing_model_alias_precedence_is_preserved(first_present):
    # Insert the embedding key first to ensure alias priority wins over dict order.
    attributes = {"embedding.model_name": "text-embedding-3-small"}
    attributes.update(
        {
            alias: f"model-for-{alias}"
            for alias in _EXISTING_MODEL_ALIASES[first_present:]
        }
    )
    expected_alias = _EXISTING_MODEL_ALIASES[first_present]
    expected_model = f"model-for-{expected_alias}"

    assert get_attribute(attributes, "model_name") == expected_model
    assert AttributeRegistry.get_value_with_source(attributes, "model_name") == (
        expected_model,
        expected_alias,
    )


@pytest.mark.parametrize("alias", _EXISTING_MODEL_ALIASES)
def test_empty_existing_model_does_not_fall_back_to_embedding(alias):
    attributes = {
        alias: "",
        "embedding.model_name": "text-embedding-3-small",
    }

    assert get_attribute(attributes, "model_name", "default-model") == ""


@pytest.mark.parametrize(
    "attributes",
    [{}, {"embedding.model_name": None}],
    ids=["missing", "null-embedding"],
)
def test_missing_embedding_model_keeps_default_behavior(attributes):
    assert get_attribute(attributes, "model_name") is None
    assert get_attribute(attributes, "model_name", "default-model") == "default-model"
    assert AttributeRegistry.get_value_with_source(
        attributes, "model_name", "default-model"
    ) == ("default-model", None)
