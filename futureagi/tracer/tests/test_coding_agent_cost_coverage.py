from unittest.mock import patch

import pytest

from tracer.utils.normalization import normalize_attributes
from tracer.utils.otel import calculate_cost


@pytest.mark.parametrize(
    "key",
    [
        "gen_ai.usage.cache_read_tokens",
        "gen_ai.usage.cache_write_tokens",
        "gen_ai.usage.cache_read.input_tokens",
        "gen_ai.usage.cache_write.input_tokens",
        "gen_ai.usage.output_tokens.reasoning",
        "gen_ai.usage.reasoning.output_tokens",
    ],
)
def test_qualified_detailed_usage_without_reported_usd_is_unknown(key):
    attrs = normalize_attributes(
        {key: 40}, {"name": "github.copilot", "version": "1.0.98"}
    )
    with patch(
        "tracer.utils.otel.calculate_cost_from_tokens", return_value=0.5
    ) as price:
        assert calculate_cost(attrs, 100, 20, "synthetic-model") == 0
        assert attrs["fi.cost.kind"] == "unknown"
        price.assert_not_called()
