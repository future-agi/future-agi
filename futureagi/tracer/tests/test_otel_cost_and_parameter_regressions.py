"""Compatibility and bulk converters must retain cost and invocation parameters."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.utils.adapters.langfuse import LangfuseAdapter
from tracer.utils.adapters.openinference import OpenInferenceAdapter
from tracer.utils.adapters.openllmetry import OpenLLMetryAdapter
from tracer.utils.otel import (
    _convert_single_span,
    convert_otel_span_to_observation_span,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def conversion():
    project = SimpleNamespace(id="synthetic-project")

    def convert(attributes, converter="single"):
        span = {
            "span_id": "01" * 8,
            "trace_id": "02" * 16,
            "project_name": "test",
            "project_type": "observe",
            "start_time": 1792024600000000000,
            "end_time": 1792024601000000000,
            "attributes": {
                "gen_ai.span.kind": "LLM",
                "gen_ai.request.model": "synthetic-model",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 20,
                **deepcopy(attributes),
            },
        }
        if converter == "single":
            result = convert_otel_span_to_observation_span(
                span, organization_id="synthetic-org"
            )
        else:
            assert converter == "bulk"
            result = _convert_single_span(
                span,
                {("test", "synthetic-org", "observe"): project},
                {},
                "synthetic-org",
            )
        return result["observation_span"]

    with (
        patch("tracer.utils.otel.get_or_create_project", return_value=project),
        patch("tracer.utils.otel.get_or_create_project_version", return_value=None),
        patch(
            "tracer.utils.otel.calculate_cost_from_tokens", return_value=0.375
        ) as price,
    ):
        yield SimpleNamespace(convert=convert, price=price)


@pytest.mark.parametrize(
    "attributes, expected",
    [
        pytest.param({"gen_ai.cost.total": 0.25}, 0.25, id="canonical-total"),
        pytest.param({"llm.cost.total": "0.25"}, 0.25, id="legacy-total"),
        pytest.param({"gen_ai.cost.total": 0}, 0, id="explicit-zero"),
        pytest.param(
            {"gen_ai.cost.input": 0.125, "gen_ai.cost.output": 0.25},
            0.375,
            id="canonical-components",
        ),
        pytest.param(
            {"llm.cost.prompt": 0.125, "llm.cost.completion": 0.25},
            0.375,
            id="legacy-components",
        ),
    ],
)
def test_single_converter_retains_reported_cost(conversion, attributes, expected):
    single = conversion.convert(attributes)
    bulk = conversion.convert(attributes, "bulk")
    assert single["cost"] == expected
    assert bulk["cost"] == expected
    conversion.price.assert_not_called()


def test_single_converter_retains_token_cost(conversion):
    single = conversion.convert({})
    assert single["cost"] == 0.375
    conversion.price.assert_called_once_with(
        prompt_tokens=100,
        completion_tokens=20,
        model="synthetic-model",
        organization_id="synthetic-org",
    )
    assert conversion.convert({}, "bulk")["cost"] == single["cost"]


@pytest.mark.parametrize(
    "converter, raw, expected",
    [
        pytest.param(converter, raw, expected, id=f"{converter}-{label}")
        for converter in ("single", "bulk")
        for label, raw, expected in (
            ("json", '{"temperature": 0.7}', {"temperature": 0.7}),
            ("dict", {"temperature": 0.7}, {"temperature": 0.7}),
            ("empty-dict", {}, {}),
            ("non-json", "not-json", "not-json"),
        )
    ],
)
def test_legacy_invocation_parameters_are_decoded(conversion, converter, raw, expected):
    row = conversion.convert({"llm.invocation_parameters": raw}, converter)
    assert row["model_parameters"] == expected
    assert "llm.invocation_parameters" in row["span_attributes"]


@pytest.mark.parametrize(
    "converter, adapter_type, attributes",
    [
        pytest.param(converter, adapter_type, attributes, id=f"{converter}-{label}")
        for converter in ("single", "bulk")
        for label, adapter_type, attributes in (
            (
                "openinference",
                OpenInferenceAdapter,
                {
                    "openinference.span.kind": "LLM",
                    "llm.invocation_parameters": '{"temperature": 0.7}',
                },
            ),
            (
                "langfuse",
                LangfuseAdapter,
                {
                    "langfuse.observation.type": "generation",
                    "langfuse.observation.model.parameters": '{"temperature": 0.7}',
                },
            ),
            (
                "openllmetry",
                OpenLLMetryAdapter,
                {
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.temperature": 0.7,
                },
            ),
        )
    ],
)
def test_adapter_parameters_survive_conversion(
    conversion, converter, adapter_type, attributes
):
    normalized = adapter_type().normalize(deepcopy(attributes))
    assert "llm.invocation_parameters" in normalized
    assert "gen_ai.request.parameters" not in normalized
    row = conversion.convert(normalized, converter)
    assert row["model_parameters"] == {"temperature": 0.7}


@pytest.mark.parametrize("converter", ["single", "bulk"])
@pytest.mark.parametrize(
    "canonical, expected",
    [
        pytest.param('{"temperature": 0.2}', {"temperature": 0.2}, id="json"),
        pytest.param({"temperature": 0.2}, {"temperature": 0.2}, id="dict"),
        pytest.param(None, None, id="null"),
        pytest.param({}, {}, id="empty-dict"),
        pytest.param([], [], id="empty-list"),
        pytest.param("", "", id="empty-string"),
        pytest.param(0, 0, id="zero"),
        pytest.param(False, False, id="false"),
        pytest.param("not-json", "not-json", id="non-json"),
    ],
)
def test_canonical_parameters_take_precedence(
    conversion, converter, canonical, expected
):
    row = conversion.convert(
        {
            "gen_ai.request.parameters": canonical,
            "llm.invocation_parameters": '{"temperature": 0.7}',
        },
        converter,
    )
    assert row["model_parameters"] == expected
    assert type(row["model_parameters"]) is type(expected)


@pytest.mark.parametrize("converter", ["single", "bulk"])
def test_missing_parameters_remain_none(conversion, converter):
    assert conversion.convert({}, converter)["model_parameters"] is None
