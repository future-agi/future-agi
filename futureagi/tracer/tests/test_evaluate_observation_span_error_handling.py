"""Unexpected span-evaluation failures remain visible as failed results.

These no-task-ID entry points do not promise automatic retries: experiment
reruns replace the live result, while Observe retains its existing result.
Task-bound execution and reconciliation are covered by their own suites.
"""

import pytest

from tracer.models.observation_span import EvalLogger
from tracer.utils.eval import (
    evaluate_observation_span,
    evaluate_observation_span_observe,
)


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

        # The Temporal wrapper closes DB connections; keep pytest's transaction open.
        result = evaluate_observation_span._original_func(
            observation_span.id, custom_eval_config.id
        )

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

        result = evaluate_observation_span_observe._original_func(
            observation_span.id, custom_eval_config.id
        )

        assert result is False
        row = EvalLogger.objects.get(
            observation_span=observation_span, custom_eval_config=custom_eval_config
        )
        assert row.error is True
        assert "boom" in row.error_message

    @pytest.mark.parametrize("failure_stage", ["mapping", "execution"])
    @pytest.mark.parametrize("observe", [False, True], ids=["experiment", "observe"])
    def test_failure_is_visible_and_repeat_preserves_entry_point_semantics(
        self,
        mocker,
        auth_client,
        observation_span,
        custom_eval_config,
        project_version,
        failure_stage,
        observe,
    ):
        project_version.eval_tags = [
            {
                "type": "OBSERVATION_SPAN_TYPE",
                "value": observation_span.observation_type,
                "custom_eval_config_id": str(custom_eval_config.id),
            }
        ]
        project_version.save(update_fields=["eval_tags"])
        observation_span.project_version = project_version
        observation_span.save(update_fields=["project_version"])
        mapping = mocker.patch("tracer.utils.eval._process_mapping", return_value={})
        execution = mocker.patch("tracer.utils.eval._execute_evaluation")
        failing_call = mapping if failure_stage == "mapping" else execution
        failing_call.side_effect = RuntimeError("Synthetic evaluation unavailable")
        evaluate = (
            evaluate_observation_span_observe if observe else evaluate_observation_span
        )._original_func

        # Keep the real loader, PostgreSQL writer and authenticated read endpoint.
        assert evaluate(observation_span.id, custom_eval_config.id) is False
        rows = EvalLogger.objects.filter(
            observation_span=observation_span, custom_eval_config=custom_eval_config
        )
        first = rows.get()
        assert first.error is True
        assert first.output_str == "ERROR"
        assert first.eval_task_id is None
        assert first.output_metadata == {"error": "Synthetic evaluation unavailable"}
        response = auth_client.get(
            "/tracer/observation-span/retrieve_loading/",
            {"observation_span_id": observation_span.id},
        )
        assert response.status_code == 200
        metric = response.data["result"]["evals_metrics"][
            f"{custom_eval_config.id}**{observation_span.id}"
        ]
        assert metric["error"] is True
        assert "Synthetic evaluation unavailable" in metric["explanation"]
        assert not metric.get("loading", False)

        repeated = evaluate(observation_span.id, custom_eval_config.id)
        assert rows.count() == 1
        history = EvalLogger.all_objects.filter(
            observation_span=observation_span, custom_eval_config=custom_eval_config
        )
        if observe:
            # A recorded failure satisfies the existing-result guard; no retry.
            assert repeated is None
            assert rows.get().id == first.id
            assert history.count() == 1
            assert failing_call.call_count == 1
        else:
            # An explicit experiment rerun replaces, rather than duplicates, live state.
            assert repeated is False
            assert rows.get().id != first.id
            assert history.count() == 2
            assert history.get(id=first.id).deleted is True
            assert failing_call.call_count == 2
        assert rows.get().error is True
