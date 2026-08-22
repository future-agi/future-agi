"""Behavioral tests for deterministic counterfactual replay."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass

import pytest

from agentic_eval.core.replay import (
    CounterfactualReplay,
    Evaluation,
    RegressionPolicy,
    ReplayCase,
    ReplayRegressionError,
    request_fingerprint,
)


def run(coroutine):
    return asyncio.run(coroutine)


def test_public_fingerprint_is_stable_and_redacts_nested_credentials():
    left = {
        "messages": [{"role": "user", "content": "hello"}],
        "headers": {"Authorization": "Bearer one"},
        "openai_api_key": "key-one",
        "temperature": 0,
    }
    right = {
        "temperature": 0,
        "openai_api_key": "key-two",
        "headers": {"Authorization": "Bearer two"},
        "messages": [{"content": "hello", "role": "user"}],
    }

    assert request_fingerprint(left) == request_fingerprint(right)
    assert request_fingerprint(left) != request_fingerprint(
        {**left, "temperature": 0.1}
    )


def test_custom_sensitive_keys_are_redacted():
    left = {"prompt": "hello", "tenant_credential": "one"}
    right = {"prompt": "hello", "tenant_credential": "two"}

    assert request_fingerprint(
        left,
        additional_sensitive_keys=("tenant_credential",),
    ) == request_fingerprint(
        right,
        additional_sensitive_keys=("tenant_credential",),
    )


def test_dataclass_credentials_are_redacted_from_public_fingerprints():
    @dataclass
    class RequestPayload:
        prompt: str
        api_key: str

    left = {"payload": RequestPayload(prompt="hello", api_key="one")}
    right = {"payload": RequestPayload(prompt="hello", api_key="two")}

    assert request_fingerprint(left) == request_fingerprint(right)

    report = run(
        CounterfactualReplay(
            lambda request: request["payload"],
            policy=RegressionPolicy(minimum_evaluation_count=0),
        ).run([ReplayCase("case", left)])
    )
    assert report.to_dict(include_outputs=True)["results"][0]["output"] == {
        "api_key": "[REDACTED]",
        "prompt": "hello",
    }


def test_identical_requests_share_candidate_execution_but_not_evaluation():
    calls = 0

    async def candidate(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return request["value"] * 2

    def quality(output, baseline, case):
        return Evaluation(
            name="quality",
            score=float(output),
            passed=output >= baseline,
            baseline_score=float(baseline),
            details={"case": case.case_id},
        )

    report = run(
        CounterfactualReplay(candidate, [quality]).run(
            [
                ReplayCase("passes", {"value": 2}, baseline=3),
                ReplayCase("fails", {"value": 2}, baseline=5),
            ]
        )
    )

    assert calls == 1
    assert [result.case_id for result in report.results] == ["passes", "fails"]
    assert report.results[0].cache_hit is False
    assert report.results[1].cache_hit is True
    assert report.results[0].evaluations[0].baseline_score == 3
    assert report.results[1].evaluations[0].baseline_score == 5
    assert report.pass_rate == 0.5


def test_different_credentials_never_share_candidate_execution():
    calls = []

    def candidate(request):
        calls.append(request["api_key"])
        return "ok"

    cases = [
        ReplayCase("first", {"prompt": "same", "api_key": "one"}),
        ReplayCase("second", {"prompt": "same", "api_key": "two"}),
    ]
    report = run(
        CounterfactualReplay(
            candidate,
            policy=RegressionPolicy(minimum_pass_rate=1.0),
        ).run(cases)
    )

    assert sorted(calls) == ["one", "two"]
    assert report.results[0].fingerprint == report.results[1].fingerprint
    assert not any(result.cache_hit for result in report.results)


def test_concurrency_is_bounded_and_result_order_is_preserved():
    lock = threading.Lock()
    active = 0
    peak = 0

    def candidate(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(request["delay"])
            return request["value"]
        finally:
            with lock:
                active -= 1

    cases = [
        ReplayCase("slow", {"value": 1, "delay": 0.03}),
        ReplayCase("fast", {"value": 2, "delay": 0.0}),
        ReplayCase("middle", {"value": 3, "delay": 0.01}),
    ]
    report = run(CounterfactualReplay(candidate, concurrency=2).run(cases))

    assert peak == 2
    assert [result.case_id for result in report.results] == [
        "slow",
        "fast",
        "middle",
    ]
    assert [result.output for result in report.results] == [1, 2, 3]


def test_candidate_receives_a_copy_of_the_request():
    request = {"messages": [{"content": "original"}]}

    def candidate(candidate_request):
        candidate_request["messages"][0]["content"] = "mutated"
        return candidate_request

    report = run(
        CounterfactualReplay(candidate).run([ReplayCase("case", request)])
    )

    assert request == {"messages": [{"content": "original"}]}
    assert report.results[0].output["messages"][0]["content"] == "mutated"


def test_each_evaluator_receives_an_isolated_output_copy():
    observations = []

    def candidate(request):
        return {"items": [1]}

    def mutating_evaluator(output, baseline, case):
        output["items"].append(2)
        return Evaluation("mutating", 1.0, True)

    def observing_evaluator(output, baseline, case):
        observations.append(output)
        return Evaluation("observing", 1.0, True)

    report = run(
        CounterfactualReplay(
            candidate,
            [mutating_evaluator, observing_evaluator],
        ).run([ReplayCase("case", {"prompt": "hello"})])
    )

    assert report.accepted is True
    assert observations == [{"items": [1]}]
    assert report.results[0].output == {"items": [1]}


def test_each_evaluator_receives_an_isolated_case_copy():
    observations = []

    def mutating_evaluator(output, baseline, case):
        case.request["messages"][0]["content"] = "mutated"
        case.metadata["suite"] = "mutated"
        return Evaluation("mutating", 1.0, True)

    def observing_evaluator(output, baseline, case):
        observations.append((case.request, case.metadata))
        return Evaluation("observing", 1.0, True)

    original_request = {"messages": [{"content": "original"}]}
    original_metadata = {"suite": "held-out"}
    case = ReplayCase(
        "case",
        original_request,
        metadata=original_metadata,
    )
    original_request["messages"][0]["content"] = "outside mutation"
    original_metadata["suite"] = "outside mutation"

    report = run(
        CounterfactualReplay(
            lambda request: "ok",
            [mutating_evaluator, observing_evaluator],
        ).run([case])
    )

    assert report.accepted is True
    assert observations == [
        (
            {"messages": [{"content": "original"}]},
            {"suite": "held-out"},
        )
    ]


def test_candidate_and_evaluator_work_share_the_concurrency_bound():
    lock = threading.Lock()
    active = 0
    peak = 0

    def measured_call(delay):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(delay)
        finally:
            with lock:
                active -= 1

    def candidate(request):
        measured_call(0.01)
        return request["value"]

    def evaluator(output, baseline, case):
        measured_call(0.02)
        return Evaluation("quality", 1.0, True)

    report = run(
        CounterfactualReplay(
            candidate,
            [evaluator],
            concurrency=2,
        ).run(
            [ReplayCase(str(index), {"value": index}) for index in range(6)]
        )
    )

    assert report.accepted is True
    assert peak == 2


def test_policy_reports_every_failed_promotion_condition():
    def candidate(request):
        return request["score"]

    def evaluator(output, baseline, case):
        return Evaluation(
            name="quality",
            score=output,
            passed=output >= 0.8,
            baseline_score=baseline,
        )

    policy = RegressionPolicy(
        minimum_case_count=3,
        minimum_pass_rate=1.0,
        maximum_error_rate=0.0,
        maximum_regression_rate=0.25,
        maximum_mean_score_drop=0.1,
    )
    report = run(
        CounterfactualReplay(candidate, [evaluator], policy=policy).run(
            [
                ReplayCase("gain", {"score": 1.0}, baseline=0.5),
                ReplayCase("loss", {"score": 0.0}, baseline=1.0),
            ]
        )
    )

    assert report.total == 2
    assert report.pass_rate == 0.5
    assert report.regression_rate == 0.5
    assert report.mean_score_drop == 0.5
    assert report.accepted is False
    assert len(report.violations) == 4

    with pytest.raises(ReplayRegressionError) as error:
        report.raise_for_regressions()
    assert error.value.report is report


def test_score_regression_tolerance_ignores_numerical_noise():
    def candidate(request):
        return 0.999999

    def evaluator(output, baseline, case):
        return Evaluation("quality", output, True, baseline)

    report = run(
        CounterfactualReplay(
            candidate,
            [evaluator],
            policy=RegressionPolicy(score_regression_tolerance=0.00001),
        ).run([ReplayCase("noise", {"prompt": "hello"}, baseline=1.0)])
    )

    assert report.regression_count == 0
    assert report.mean_score_drop == 0.0
    assert report.accepted is True


def test_empty_suite_fails_closed_by_default():
    report = run(CounterfactualReplay(lambda request: request).run([]))

    assert report.total == 0
    assert report.pass_rate == 0.0
    assert report.accepted is False
    assert report.violations == ("case count 0 is below required 1",)


def test_successful_candidate_without_evaluations_is_not_promotable_by_default():
    case = ReplayCase("case", {"prompt": "hello"})

    report = run(CounterfactualReplay(lambda request: "ok").run([case]))

    assert report.evaluation_count == 0
    assert report.accepted is False
    assert report.violations == (
        "evaluation count 0 is below required 1",
    )

    smoke_report = run(
        CounterfactualReplay(
            lambda request: "ok",
            policy=RegressionPolicy(minimum_evaluation_count=0),
        ).run([case])
    )
    assert smoke_report.accepted is True


def test_candidate_errors_are_isolated_and_messages_are_opt_in():
    def candidate(request):
        if request["fail"]:
            raise RuntimeError("Bearer provider-secret")
        return "ok"

    report = run(
        CounterfactualReplay(
            candidate,
            policy=RegressionPolicy(
                minimum_pass_rate=0.0,
                maximum_error_rate=0.5,
            ),
            capture_error_messages=True,
        ).run(
            [
                ReplayCase(
                    "bad",
                    {"fail": True, "authorization": "Bearer request-secret"},
                    metadata={"password": "metadata-secret", "suite": "held-out"},
                ),
                ReplayCase("good", {"fail": False}),
            ]
        )
    )

    assert report.results[0].error_stage == "candidate"
    assert report.results[0].error_type == "RuntimeError"
    assert report.results[1].passed is True

    safe_payload = report.to_dict()
    safe_json = json.dumps(safe_payload)
    assert "provider-secret" not in safe_json
    assert "request-secret" not in safe_json
    assert "metadata-secret" not in safe_json
    assert "output" not in safe_payload["results"][1]
    assert "metadata" not in safe_payload["results"][0]

    diagnostic_payload = report.to_dict(
        include_error_messages=True,
        include_metadata=True,
    )
    assert diagnostic_payload["results"][0]["metadata"] == {
        "password": "[REDACTED]",
        "suite": "held-out",
    }
    assert diagnostic_payload["results"][0]["error_message"] == (
        "Bearer provider-secret"
    )


def test_outputs_are_omitted_unless_explicitly_requested_and_then_redacted():
    def candidate(request):
        return {"answer": "ok", "token": "output-secret"}

    report = run(
        CounterfactualReplay(candidate).run(
            [ReplayCase("case", {"prompt": "hello"})]
        )
    )

    assert "output" not in report.to_dict()["results"][0]
    assert report.to_dict(include_outputs=True)["results"][0]["output"] == {
        "answer": "ok",
        "token": "[REDACTED]",
    }


def test_evaluator_error_marks_only_its_case_as_error():
    def candidate(request):
        return request["value"]

    def evaluator(output, baseline, case):
        if case.case_id == "bad":
            raise ValueError("unsafe detail")
        return Evaluation("quality", 1.0, True)

    report = run(
        CounterfactualReplay(
            candidate,
            [evaluator],
            policy=RegressionPolicy(
                minimum_pass_rate=0.5,
                maximum_error_rate=0.5,
            ),
        ).run(
            [
                ReplayCase("bad", {"value": 1}),
                ReplayCase("good", {"value": 2}),
            ]
        )
    )

    assert report.results[0].error_stage == "evaluator:evaluator"
    assert report.results[0].error_type == "ValueError"
    assert report.results[1].passed is True
    assert report.accepted is True


def test_uncopyable_candidate_output_becomes_a_structured_isolation_error():
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("cannot copy")

    report = run(
        CounterfactualReplay(
            lambda request: Uncopyable(),
            policy=RegressionPolicy(
                minimum_evaluation_count=0,
                minimum_pass_rate=0.0,
                maximum_error_rate=1.0,
            ),
        ).run([ReplayCase("case", {"prompt": "hello"})])
    )

    assert report.results[0].error_stage == "candidate-output-isolation"
    assert report.results[0].error_type == "TypeError"


def test_candidate_timeout_is_reported_without_canceling_other_cases():
    async def candidate(request):
        await asyncio.sleep(request["delay"])
        return "ok"

    report = run(
        CounterfactualReplay(
            candidate,
            candidate_timeout_seconds=0.01,
            policy=RegressionPolicy(
                minimum_pass_rate=0.5,
                maximum_error_rate=0.5,
            ),
        ).run(
            [
                ReplayCase("timeout", {"delay": 0.05}),
                ReplayCase("success", {"delay": 0.0}),
            ]
        )
    )

    assert report.results[0].error_type == "TimeoutError"
    assert report.results[1].passed is True


def test_evaluator_timeout_is_a_structured_case_error():
    async def evaluator(output, baseline, case):
        await asyncio.sleep(0.05)
        return Evaluation("quality", 1.0, True)

    report = run(
        CounterfactualReplay(
            lambda request: "ok",
            [evaluator],
            evaluator_timeout_seconds=0.01,
            policy=RegressionPolicy(
                minimum_evaluation_count=0,
                minimum_pass_rate=0.0,
                maximum_error_rate=1.0,
            ),
        ).run([ReplayCase("case", {"prompt": "hello"})])
    )

    assert report.results[0].error_stage == "evaluator:evaluator"
    assert report.results[0].error_type == "TimeoutError"


def test_invalid_evaluator_return_is_a_structured_error():
    def invalid_evaluator(output, baseline, case):
        return True

    report = run(
        CounterfactualReplay(
            lambda request: "ok",
            [invalid_evaluator],
            policy=RegressionPolicy(
                minimum_pass_rate=0.0,
                maximum_error_rate=1.0,
            ),
        ).run([ReplayCase("case", {"prompt": "hello"})])
    )

    assert report.results[0].error_stage == "evaluator:invalid_evaluator"
    assert report.results[0].error_type == "TypeError"


def test_duplicate_evaluation_names_are_rejected():
    def first(output, baseline, case):
        return Evaluation("quality", 1.0, True)

    def second(output, baseline, case):
        return Evaluation("quality", 1.0, True)

    report = run(
        CounterfactualReplay(
            lambda request: "ok",
            [first, second],
            policy=RegressionPolicy(
                minimum_pass_rate=0.0,
                maximum_error_rate=1.0,
            ),
        ).run([ReplayCase("case", {"prompt": "hello"})])
    )

    assert report.results[0].error_stage == "evaluator:second"
    assert report.results[0].error_type == "ValueError"


def test_duplicate_case_ids_fail_before_candidate_execution():
    calls = 0

    def candidate(request):
        nonlocal calls
        calls += 1
        return "ok"

    with pytest.raises(ValueError, match="case_id values must be unique"):
        run(
            CounterfactualReplay(candidate).run(
                [
                    ReplayCase("duplicate", {"value": 1}),
                    ReplayCase("duplicate", {"value": 2}),
                ]
            )
        )

    assert calls == 0


def test_non_json_request_values_fail_before_candidate_execution():
    calls = 0

    def candidate(request):
        nonlocal calls
        calls += 1
        return "ok"

    with pytest.raises(TypeError, match="unsupported value type: object"):
        run(
            CounterfactualReplay(candidate).run(
                [ReplayCase("case", {"unsupported": object()})]
            )
        )

    assert calls == 0


def test_deduplication_can_be_disabled():
    calls = 0

    def candidate(request):
        nonlocal calls
        calls += 1
        return "ok"

    report = run(
        CounterfactualReplay(
            candidate,
            deduplicate_requests=False,
        ).run(
            [
                ReplayCase("first", {"prompt": "same"}),
                ReplayCase("second", {"prompt": "same"}),
            ]
        )
    )

    assert calls == 2
    assert not any(result.cache_hit for result in report.results)


def test_evaluator_generators_are_not_consumed_during_validation():
    def evaluator(output, baseline, case):
        return Evaluation("quality", 1, True)

    evaluators = (item for item in [evaluator])
    report = run(
        CounterfactualReplay(lambda request: "ok", evaluators).run(
            [ReplayCase("case", {"prompt": "hello"})]
        )
    )

    assert [item.name for item in report.results[0].evaluations] == ["quality"]


def test_replay_rejects_non_case_items_before_candidate_execution():
    calls = 0

    def candidate(request):
        nonlocal calls
        calls += 1
        return "ok"

    with pytest.raises(TypeError, match="every replay case must be a ReplayCase"):
        run(CounterfactualReplay(candidate).run([{"request": {}}]))

    assert calls == 0


def test_request_mapping_keys_must_be_strings():
    with pytest.raises(TypeError, match="must use string keys"):
        request_fingerprint({1: "not-json"})

def test_evaluation_details_are_omitted_unless_requested_and_redacted():
    def evaluator(output, baseline, case):
        return Evaluation(
            "quality",
            1.0,
            True,
            details={"reason": "correct", "client_secret": "hidden"},
        )

    report = run(
        CounterfactualReplay(
            lambda request: "ok",
            [evaluator],
        ).run([ReplayCase("case", {"prompt": "hello"})])
    )

    safe_evaluation = report.to_dict()["results"][0]["evaluations"][0]
    assert "details" not in safe_evaluation

    detailed_evaluation = report.to_dict(
        include_evaluation_details=True
    )["results"][0]["evaluations"][0]
    assert detailed_evaluation["details"] == {
        "client_secret": "[REDACTED]",
        "reason": "correct",
    }


def test_constructor_rejects_invalid_control_values():
    with pytest.raises(TypeError, match="policy"):
        CounterfactualReplay(lambda request: request, policy={})
    with pytest.raises(TypeError, match="concurrency"):
        CounterfactualReplay(lambda request: request, concurrency=True)
    with pytest.raises(TypeError, match="deduplicate_requests"):
        CounterfactualReplay(
            lambda request: request,
            deduplicate_requests=1,
        )
    with pytest.raises(TypeError, match="capture_error_messages"):
        CounterfactualReplay(
            lambda request: request,
            capture_error_messages=1,
        )
    with pytest.raises(TypeError, match="candidate_timeout_seconds"):
        CounterfactualReplay(
            lambda request: request,
            candidate_timeout_seconds=True,
        )

    with pytest.raises(TypeError, match="additional sensitive keys"):
        CounterfactualReplay(
            lambda request: request,
            additional_sensitive_keys=(1,),
        )


def test_evaluation_and_policy_values_are_normalized():
    evaluation = Evaluation(
        name=" quality ",
        score=1,
        passed=True,
        baseline_score="0.5",
    )
    policy = RegressionPolicy(minimum_pass_rate=1, maximum_error_rate=0)

    assert evaluation.name == "quality"
    assert evaluation.score == 1.0
    assert evaluation.baseline_score == 0.5
    assert policy.minimum_pass_rate == 1.0
    assert policy.maximum_error_rate == 0.0

    with pytest.raises(TypeError, match="numeric, not bool"):
        Evaluation("invalid", True, True)
