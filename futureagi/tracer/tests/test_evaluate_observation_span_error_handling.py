"""Regression for #2334: a non-ValueError failure during evaluation must
still leave a visible, errored EvalLogger row — not silently drop the span
with no eval record and no retry.
"""

import pytest

from tracer.models.observation_span import EvalLogger
from tracer.utils.eval import evaluate_observation_span, evaluate_observation_span_observe


@pytest.mark.integration
class TestEvaluateObservationSpanErrorHandling:
    def test_non_value_error_still_creates_error_eval_logger(
        self, mocker, observation_span, custom_eval_config
    ):
        mocker.patch(
            "tracer.services.clickhouse.v2.eval_loader.get_observation_span",
            return_value=observation_span,
        )
        mocker.patch("tracer.utils.eval._process_mapping", return_value={})
        mocker.patch(
            "tracer.utils.eval._execute_evaluation",
            side_effect=RuntimeError("boom"),
        )

        result = evaluate_observation_span(observation_span.id, custom_eval_config.id)

        assert result is False
        row = EvalLogger.objects.get(
            observation_span=observation_span, custom_eval_config=custom_eval_config
        )
        assert row.error is True
        assert "boom" in row.error_message

    def test_observe_variant_also_creates_error_eval_logger(
        self, mocker, observation_span, custom_eval_config
    ):
        mocker.patch(
            "tracer.services.clickhouse.v2.eval_loader.get_observation_span",
            return_value=observation_span,
        )
        mocker.patch("tracer.utils.eval._process_mapping", return_value={})
        mocker.patch(
            "tracer.utils.eval._execute_evaluation",
            side_effect=RuntimeError("boom"),
        )

        result = evaluate_observation_span_observe(
            observation_span.id, custom_eval_config.id
        )

        assert result is False
        row = EvalLogger.objects.get(
            observation_span=observation_span, custom_eval_config=custom_eval_config
        )
        assert row.error is True
