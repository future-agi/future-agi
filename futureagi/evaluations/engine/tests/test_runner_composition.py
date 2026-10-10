"""Composition and error paths through ``runner.run_eval()``.

``run_eval()`` is the single point every evaluation in the platform flows
through: registry lookup -> instance creation -> param preparation ->
preprocessing -> execution -> formatting. Each collaborator had some coverage of
its own; the wiring between them had none, so a step dropped or reordered would
not have been caught.

These tests stub the collaborators and assert the wiring: what gets called, with
what, in what order, and how the pieces land in the returned ``EvalResult``.
Nothing here needs a database, a model, or a live LLM.
"""

from unittest.mock import MagicMock, patch

import pytest

from evaluations.engine.runner import EvalRequest, EvalResult, run_eval

RUNNER = "evaluations.engine.runner"
PREPROCESSING = "evaluations.engine.preprocessing"


class _Template:
    """Minimal stand-in for an EvalTemplate model instance."""

    def __init__(self, name="My Eval", config=None):
        self.name = name
        self.config = config if config is not None else {"eval_type_id": "Contains"}


@pytest.fixture
def instance():
    inst = MagicMock()
    inst.run.return_value = {"result": True}
    inst.cost = {"total": 0.01}
    inst.token_usage = {"prompt": 5, "completion": 2}
    return inst


@pytest.fixture
def wired(instance):
    """Patch every collaborator run_eval composes, yielding the mocks."""
    with (
        patch(f"{RUNNER}.get_eval_class") as get_cls,
        patch(f"{RUNNER}.create_eval_instance") as create,
        patch(f"{RUNNER}.prepare_run_params") as prep,
        patch(f"{PREPROCESSING}.preprocess_inputs") as pre,
        patch(f"{RUNNER}.extract_raw_result") as extract,
        patch(f"{RUNNER}.format_eval_value") as fmt,
    ):
        get_cls.return_value = MagicMock(name="EvalClass")
        create.return_value = (instance, "criteria-from-instance")
        prep.return_value = {"text": "hello"}
        pre.side_effect = lambda _name, params: params
        extract.return_value = {
            "data": {"echo": 1},
            "reason": "because",
            "failure": None,
            "runtime": 0.5,
            "model": "gpt-4o",
            "metrics": [{"id": "m", "value": 1}],
            "metadata": {"k": "v"},
            "output": "Pass/Fail",
        }
        fmt.return_value = True
        yield {
            "get_eval_class": get_cls,
            "create_eval_instance": create,
            "prepare_run_params": prep,
            "preprocess_inputs": pre,
            "extract_raw_result": extract,
            "format_eval_value": fmt,
            "instance": instance,
        }


class TestErrorPaths:
    def test_missing_eval_type_id_raises_with_the_template_name(self):
        request = EvalRequest(
            eval_template=_Template(name="Broken", config={}), inputs={}
        )

        with pytest.raises(ValueError) as exc_info:
            run_eval(request)

        assert "eval_type_id" in str(exc_info.value)
        assert "Broken" in str(exc_info.value)

    def test_empty_eval_type_id_is_treated_as_missing(self):
        request = EvalRequest(
            eval_template=_Template(config={"eval_type_id": ""}), inputs={}
        )
        with pytest.raises(ValueError):
            run_eval(request)

    def test_unregistered_eval_type_propagates_the_registry_error(self):
        """run_eval does not swallow the registry's ValueError."""
        request = EvalRequest(
            eval_template=_Template(config={"eval_type_id": "NoSuchEvaluatorXYZ"}),
            inputs={},
        )
        with pytest.raises(ValueError) as exc_info:
            run_eval(request)

        assert "NoSuchEvaluatorXYZ" in str(exc_info.value)

    def test_evaluator_exceptions_are_not_swallowed(self, wired):
        """A failure inside the evaluator must surface, not become a score."""
        wired["instance"].run.side_effect = RuntimeError("evaluator exploded")
        request = EvalRequest(eval_template=_Template(), inputs={"text": "hi"})

        with pytest.raises(RuntimeError, match="evaluator exploded"):
            run_eval(request)


class TestHappyPathComposition:
    def test_each_stage_is_invoked_once(self, wired):
        run_eval(EvalRequest(eval_template=_Template(), inputs={"text": "hi"}))

        assert wired["get_eval_class"].call_count == 1
        assert wired["create_eval_instance"].call_count == 1
        assert wired["prepare_run_params"].call_count == 1
        assert wired["preprocess_inputs"].call_count == 1
        assert wired["instance"].run.call_count == 1
        assert wired["extract_raw_result"].call_count == 1
        assert wired["format_eval_value"].call_count == 1

    def test_registry_is_queried_with_the_templates_eval_type_id(self, wired):
        template = _Template(config={"eval_type_id": "Regex"})
        run_eval(EvalRequest(eval_template=template, inputs={}))

        wired["get_eval_class"].assert_called_once_with("Regex")

    def test_resolved_class_is_handed_to_instance_creation(self, wired):
        run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        kwargs = wired["create_eval_instance"].call_args.kwargs
        assert kwargs["eval_class"] is wired["get_eval_class"].return_value

    def test_prepared_params_reach_the_evaluator(self, wired):
        wired["prepare_run_params"].return_value = {"text": "prepared"}
        run_eval(EvalRequest(eval_template=_Template(), inputs={"text": "raw"}))

        wired["instance"].run.assert_called_once_with(text="prepared")

    def test_preprocessing_sits_between_params_and_execution(self, wired):
        """The evaluator sees the preprocessed params, not the raw ones."""
        wired["preprocess_inputs"].side_effect = lambda _n, p: {**p, "added": "by-pre"}
        run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        assert wired["instance"].run.call_args.kwargs["added"] == "by-pre"

    def test_preprocessing_is_keyed_by_template_name(self, wired):
        run_eval(EvalRequest(eval_template=_Template(name="Dead Air"), inputs={}))

        assert wired["preprocess_inputs"].call_args[0][0] == "Dead Air"

    def test_result_fields_are_mapped_from_the_extracted_response(self, wired):
        result = run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        assert isinstance(result, EvalResult)
        assert result.value is True
        assert result.data == {"echo": 1}
        assert result.reason == "because"
        assert result.failure is None
        assert result.runtime == 0.5
        assert result.model_used == "gpt-4o"
        assert result.metrics == [{"id": "m", "value": 1}]
        assert result.metadata == {"k": "v"}
        assert result.output_type == "Pass/Fail"

    def test_output_type_defaults_to_score_when_absent(self, wired):
        wired["extract_raw_result"].return_value = {}
        result = run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        assert result.output_type == "score"

    def test_cost_and_token_usage_are_lifted_off_the_instance(self, wired):
        result = run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        assert result.cost == {"total": 0.01}
        assert result.token_usage == {"prompt": 5, "completion": 2}

    def test_missing_cost_attributes_are_tolerated(self, wired):
        """Deterministic evaluators carry no cost/token_usage."""
        bare = MagicMock(spec=["run"])
        bare.run.return_value = {"result": True}
        wired["create_eval_instance"].return_value = (bare, "criteria")

        result = run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        assert result.cost is None
        assert result.token_usage is None

    def test_timing_is_populated_and_self_consistent(self, wired):
        result = run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        assert result.start_time is not None
        assert result.end_time is not None
        assert result.end_time >= result.start_time
        assert result.duration == pytest.approx(
            result.end_time - result.start_time, abs=1e-6
        )


class TestRequestOptions:
    def test_skip_params_preparation_bypasses_prepare_run_params(self, wired):
        request = EvalRequest(
            eval_template=_Template(),
            inputs={"text": "raw", "other": 1},
            skip_params_preparation=True,
        )
        run_eval(request)

        wired["prepare_run_params"].assert_not_called()
        assert wired["instance"].run.call_args.kwargs == {"text": "raw", "other": 1}

    def test_criteria_override_replaces_the_instance_criteria(self, wired):
        request = EvalRequest(
            eval_template=_Template(),
            inputs={},
            criteria_override="use this instead",
        )
        run_eval(request)

        assert (
            wired["prepare_run_params"].call_args.kwargs["criteria"]
            == "use this instead"
        )

    def test_instance_criteria_is_used_when_no_override_given(self, wired):
        run_eval(EvalRequest(eval_template=_Template(), inputs={}))

        assert (
            wired["prepare_run_params"].call_args.kwargs["criteria"]
            == "criteria-from-instance"
        )

    def test_config_overrides_are_copied_not_shared(self, wired):
        """run_eval builds its own dict, so a caller's mapping is not mutated."""
        overrides = {"a": 1}
        run_eval(
            EvalRequest(
                eval_template=_Template(), inputs={}, config_overrides=overrides
            )
        )

        passed = wired["create_eval_instance"].call_args.kwargs["config"]
        assert passed == {"a": 1}
        assert passed is not overrides


class TestProtectCalls:
    @pytest.mark.parametrize(
        "call_type,expected_model",
        [("protect", "protect"), ("protect_flash", "protect_flash")],
    )
    def test_default_model_is_set_for_protect_calls(
        self, wired, call_type, expected_model
    ):
        request = EvalRequest(
            eval_template=_Template(), inputs={"call_type": call_type}
        )
        run_eval(request)

        assert request.model == expected_model
        assert wired["create_eval_instance"].call_args.kwargs["model"] == expected_model

    def test_an_explicit_model_is_not_overridden(self, wired):
        request = EvalRequest(
            eval_template=_Template(),
            inputs={"call_type": "protect"},
            model="gpt-4o",
        )
        run_eval(request)

        assert request.model == "gpt-4o"

    def test_eval_name_is_defaulted_into_run_params(self, wired):
        wired["prepare_run_params"].return_value = {}
        run_eval(
            EvalRequest(
                eval_template=_Template(name="Toxicity"),
                inputs={"call_type": "protect"},
            )
        )

        assert wired["instance"].run.call_args.kwargs["eval_name"] == "Toxicity"

    def test_existing_eval_name_is_not_clobbered(self, wired):
        wired["prepare_run_params"].return_value = {"eval_name": "already-set"}
        run_eval(
            EvalRequest(eval_template=_Template(), inputs={"call_type": "protect"})
        )

        assert wired["instance"].run.call_args.kwargs["eval_name"] == "already-set"

    def test_max_tokens_is_forwarded_from_inputs(self, wired):
        wired["prepare_run_params"].return_value = {}
        run_eval(
            EvalRequest(
                eval_template=_Template(),
                inputs={"call_type": "protect", "max_tokens": 128},
            )
        )

        assert wired["instance"].run.call_args.kwargs["max_tokens"] == 128

    def test_non_protect_calls_do_not_get_a_default_model(self, wired):
        request = EvalRequest(eval_template=_Template(), inputs={"call_type": "eval"})
        run_eval(request)

        assert request.model is None


class TestCustomCodeEvalRuntimeParams:
    def _request(self, runtime_config):
        return EvalRequest(
            eval_template=_Template(config={"eval_type_id": "CustomCodeEval"}),
            inputs={},
            runtime_config=runtime_config,
        )

    def test_runtime_params_are_merged_into_run_params(self, wired):
        wired["prepare_run_params"].return_value = {}
        run_eval(self._request({"params": {"threshold": 5}}))

        assert wired["instance"].run.call_args.kwargs["threshold"] == 5

    def test_runtime_params_do_not_override_prepared_params(self, wired):
        """setdefault semantics: prepared params win."""
        wired["prepare_run_params"].return_value = {"threshold": 99}
        run_eval(self._request({"params": {"threshold": 5}}))

        assert wired["instance"].run.call_args.kwargs["threshold"] == 99

    def test_non_dict_runtime_params_are_ignored(self, wired):
        wired["prepare_run_params"].return_value = {}
        run_eval(self._request({"params": ["not", "a", "dict"]}))

        assert wired["instance"].run.call_args.kwargs == {}

    def test_other_eval_types_ignore_runtime_params(self, wired):
        wired["prepare_run_params"].return_value = {}
        run_eval(
            EvalRequest(
                eval_template=_Template(config={"eval_type_id": "Contains"}),
                inputs={},
                runtime_config={"params": {"threshold": 5}},
            )
        )

        assert "threshold" not in wired["instance"].run.call_args.kwargs
