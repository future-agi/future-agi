"""Deterministic execution engine for candidate agent changes.

The engine is provider-neutral. A candidate callable can invoke the Future AGI
Gateway, a hosted API, or a local OpenAI-compatible runtime such as MLX-LM,
oMLX, Ollama, or llama.cpp.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from agentic_eval.core.replay.models import (
    Evaluation,
    Evaluator,
    RegressionPolicy,
    ReplayCase,
    ReplayReport,
    ReplayResult,
)
from agentic_eval.core.replay.privacy import (
    _isolated_copy,
    _request_digest,
    _sensitive_key_set,
)

Candidate: TypeAlias = Callable[
    [Mapping[str, Any]],
    Any | Awaitable[Any],
]


@dataclass(frozen=True, slots=True)
class _PreparedCase:
    case: ReplayCase
    fingerprint: str
    execution_digest: str


@dataclass(frozen=True, slots=True)
class _CandidateOutcome:
    output: Any
    duration_ms: float
    error_type: str | None = None
    error_message: str | None = None


async def _invoke_callable(
    function: Callable[..., Any],
    *args: Any,
    timeout_seconds: float | None,
) -> Any:
    async def invoke() -> Any:
        call_method = getattr(function, "__call__", None)
        is_async = inspect.iscoroutinefunction(function) or (
            call_method is not None and inspect.iscoroutinefunction(call_method)
        )
        if is_async:
            result = function(*args)
        else:
            result = await asyncio.to_thread(function, *args)
        if inspect.isawaitable(result):
            return await result
        return result

    if timeout_seconds is None:
        return await invoke()
    return await asyncio.wait_for(invoke(), timeout=timeout_seconds)


def _callable_name(function: Callable[..., Any]) -> str:
    return getattr(function, "__name__", type(function).__qualname__)


class CounterfactualReplay:
    """Replay captured requests through a candidate and regression gate."""

    def __init__(
        self,
        candidate: Candidate,
        evaluators: Sequence[Evaluator] = (),
        *,
        policy: RegressionPolicy | None = None,
        concurrency: int = 8,
        deduplicate_requests: bool = True,
        candidate_timeout_seconds: float | None = None,
        evaluator_timeout_seconds: float | None = None,
        capture_error_messages: bool = False,
        additional_sensitive_keys: Iterable[str] = (),
    ) -> None:
        if not callable(candidate):
            raise TypeError("candidate must be callable")
        if policy is not None and not isinstance(policy, RegressionPolicy):
            raise TypeError("policy must be a RegressionPolicy or None")
        if isinstance(concurrency, bool) or not isinstance(concurrency, int):
            raise TypeError("concurrency must be an int")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if not isinstance(deduplicate_requests, bool):
            raise TypeError("deduplicate_requests must be a bool")
        if not isinstance(capture_error_messages, bool):
            raise TypeError("capture_error_messages must be a bool")
        normalized_timeouts: dict[str, float | None] = {}
        for timeout_name, timeout in (
            ("candidate_timeout_seconds", candidate_timeout_seconds),
            ("evaluator_timeout_seconds", evaluator_timeout_seconds),
        ):
            if timeout is None:
                normalized_timeouts[timeout_name] = None
                continue
            if isinstance(timeout, bool):
                raise TypeError(f"{timeout_name} must be a number or None")
            if not isinstance(timeout, (int, float)):
                raise TypeError(f"{timeout_name} must be a number or None")
            normalized_timeout = float(timeout)
            if not math.isfinite(normalized_timeout) or normalized_timeout <= 0:
                raise ValueError(f"{timeout_name} must be positive and finite")
            normalized_timeouts[timeout_name] = normalized_timeout
        evaluator_tuple = tuple(evaluators)
        if any(not callable(evaluator) for evaluator in evaluator_tuple):
            raise TypeError("every evaluator must be callable")

        self._candidate = candidate
        self._evaluators = evaluator_tuple
        self._policy = policy or RegressionPolicy()
        self._concurrency = concurrency
        self._deduplicate_requests = deduplicate_requests
        self._candidate_timeout_seconds = normalized_timeouts[
            "candidate_timeout_seconds"
        ]
        self._evaluator_timeout_seconds = normalized_timeouts[
            "evaluator_timeout_seconds"
        ]
        self._capture_error_messages = capture_error_messages
        self._sensitive_keys = _sensitive_key_set(additional_sensitive_keys)

    async def _execute_candidate(
        self,
        case: ReplayCase,
        semaphore: asyncio.Semaphore,
    ) -> _CandidateOutcome:
        started: float | None = None
        try:
            async with semaphore:
                started = time.perf_counter()
                output = await _invoke_callable(
                    self._candidate,
                    _isolated_copy(case.request, label="candidate request"),
                    timeout_seconds=self._candidate_timeout_seconds,
                )
            return _CandidateOutcome(
                output=output,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as error:
            duration_ms = 0.0
            if started is not None:
                duration_ms = (time.perf_counter() - started) * 1000
            return _CandidateOutcome(
                output=None,
                duration_ms=duration_ms,
                error_type=type(error).__qualname__,
                error_message=(
                    str(error) if self._capture_error_messages else None
                ),
            )

    async def _process_case(
        self,
        prepared: _PreparedCase,
        candidate_task: asyncio.Task[_CandidateOutcome],
        semaphore: asyncio.Semaphore,
        *,
        cache_hit: bool,
    ) -> ReplayResult:
        started = time.perf_counter()
        outcome = await asyncio.shield(candidate_task)
        if outcome.error_type is not None:
            return ReplayResult(
                case_id=prepared.case.case_id,
                fingerprint=prepared.fingerprint,
                output=None,
                evaluations=(),
                duration_ms=(time.perf_counter() - started) * 1000,
                candidate_duration_ms=outcome.duration_ms,
                cache_hit=cache_hit,
                error_stage="candidate",
                error_type=outcome.error_type,
                error_message=outcome.error_message,
                metadata=_isolated_copy(
                    prepared.case.metadata,
                    label="result metadata",
                ),
            )

        try:
            result_output = _isolated_copy(
                outcome.output,
                label="candidate output",
            )
        except Exception as error:
            return ReplayResult(
                case_id=prepared.case.case_id,
                fingerprint=prepared.fingerprint,
                output=None,
                evaluations=(),
                duration_ms=(time.perf_counter() - started) * 1000,
                candidate_duration_ms=outcome.duration_ms,
                cache_hit=cache_hit,
                error_stage="candidate-output-isolation",
                error_type=type(error).__qualname__,
                error_message=(
                    str(error) if self._capture_error_messages else None
                ),
                metadata=_isolated_copy(
                    prepared.case.metadata,
                    label="result metadata",
                ),
            )

        evaluations: list[Evaluation] = []
        evaluation_names: set[str] = set()
        for evaluator in self._evaluators:
            evaluator_name = _callable_name(evaluator)
            try:
                async with semaphore:
                    evaluation = await _invoke_callable(
                        evaluator,
                        _isolated_copy(
                            result_output,
                            label="evaluator candidate output",
                        ),
                        _isolated_copy(
                            prepared.case.baseline,
                            label="evaluator baseline",
                        ),
                        _isolated_copy(
                            prepared.case,
                            label="evaluator replay case",
                        ),
                        timeout_seconds=self._evaluator_timeout_seconds,
                    )
                if not isinstance(evaluation, Evaluation):
                    raise TypeError(
                        f"evaluator {evaluator_name!r} returned "
                        f"{type(evaluation).__qualname__}; expected Evaluation"
                    )
                if evaluation.name in evaluation_names:
                    raise ValueError(
                        f"duplicate evaluation name {evaluation.name!r}"
                    )
                evaluation_names.add(evaluation.name)
                evaluations.append(evaluation)
            except Exception as error:
                return ReplayResult(
                    case_id=prepared.case.case_id,
                    fingerprint=prepared.fingerprint,
                    output=result_output,
                    evaluations=tuple(evaluations),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    candidate_duration_ms=outcome.duration_ms,
                    cache_hit=cache_hit,
                    error_stage=f"evaluator:{evaluator_name}",
                    error_type=type(error).__qualname__,
                    error_message=(
                        str(error) if self._capture_error_messages else None
                    ),
                    metadata=_isolated_copy(
                        prepared.case.metadata,
                        label="result metadata",
                    ),
                )

        return ReplayResult(
            case_id=prepared.case.case_id,
            fingerprint=prepared.fingerprint,
            output=result_output,
            evaluations=tuple(evaluations),
            duration_ms=(time.perf_counter() - started) * 1000,
            candidate_duration_ms=outcome.duration_ms,
            cache_hit=cache_hit,
            metadata=_isolated_copy(
                prepared.case.metadata,
                label="result metadata",
            ),
        )

    async def run(self, cases: Iterable[ReplayCase]) -> ReplayReport:
        """Run an ordered, bounded, deduplicated replay suite."""
        case_list = tuple(cases)
        if any(not isinstance(case, ReplayCase) for case in case_list):
            raise TypeError("every replay case must be a ReplayCase")
        case_ids = [case.case_id for case in case_list]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case_id values must be unique within a replay run")

        prepared_cases_list: list[_PreparedCase] = []
        for case in case_list:
            snapshot = ReplayCase(
                case_id=case.case_id,
                request=case.request,
                baseline=case.baseline,
                metadata=case.metadata,
            )
            prepared_cases_list.append(
                _PreparedCase(
                    case=snapshot,
                    fingerprint=_request_digest(
                        snapshot.request,
                        sensitive_keys=self._sensitive_keys,
                    ),
                    execution_digest=_request_digest(
                        snapshot.request,
                        sensitive_keys=None,
                    ),
                )
            )
        prepared_cases = tuple(prepared_cases_list)

        started_at_unix = time.time()
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(self._concurrency)
        candidate_tasks: dict[str, asyncio.Task[_CandidateOutcome]] = {}
        process_tasks: list[asyncio.Task[ReplayResult]] = []

        for index, prepared in enumerate(prepared_cases):
            task_key = prepared.execution_digest
            if not self._deduplicate_requests:
                task_key = f"{task_key}:{index}"
            cache_hit = task_key in candidate_tasks
            if not cache_hit:
                candidate_tasks[task_key] = asyncio.create_task(
                    self._execute_candidate(prepared.case, semaphore)
                )
            process_tasks.append(
                asyncio.create_task(
                    self._process_case(
                        prepared,
                        candidate_tasks[task_key],
                        semaphore,
                        cache_hit=cache_hit,
                    )
                )
            )

        try:
            results = tuple(await asyncio.gather(*process_tasks))
        except BaseException:
            for task in process_tasks:
                task.cancel()
            for task in candidate_tasks.values():
                task.cancel()
            await asyncio.gather(
                *process_tasks,
                *candidate_tasks.values(),
                return_exceptions=True,
            )
            raise

        return ReplayReport(
            results=results,
            policy=self._policy,
            started_at_unix=started_at_unix,
            duration_ms=(time.perf_counter() - started) * 1000,
            sensitive_keys=self._sensitive_keys,
        )
