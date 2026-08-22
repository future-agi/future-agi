"""Immutable contracts and promotion policy for counterfactual replay."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias

from agentic_eval.core.replay.privacy import _isolated_copy, _safe_report_value


@dataclass(frozen=True, slots=True)
class ReplayCase:
    """A captured request and its optional production baseline."""

    case_id: str
    request: Mapping[str, Any]
    baseline: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id must be a non-empty string")
        object.__setattr__(self, "case_id", self.case_id.strip())
        if not isinstance(self.request, Mapping):
            raise TypeError("request must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(
            self,
            "request",
            _isolated_copy(self.request, label="replay request"),
        )
        object.__setattr__(
            self,
            "baseline",
            _isolated_copy(self.baseline, label="replay baseline"),
        )
        object.__setattr__(
            self,
            "metadata",
            _isolated_copy(self.metadata, label="replay metadata"),
        )


@dataclass(frozen=True, slots=True)
class Evaluation:
    """One evaluator's decision for a replayed case."""

    name: str
    score: float
    passed: bool
    baseline_score: float | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("evaluation name must be a non-empty string")
        object.__setattr__(self, "name", self.name.strip())
        if isinstance(self.score, bool):
            raise TypeError("evaluation score must be numeric, not bool")
        score = float(self.score)
        if not math.isfinite(score):
            raise ValueError("evaluation score must be finite")
        object.__setattr__(self, "score", score)
        if not isinstance(self.passed, bool):
            raise TypeError("evaluation passed must be a bool")
        if self.baseline_score is not None:
            if isinstance(self.baseline_score, bool):
                raise TypeError("baseline_score must be numeric, not bool")
            baseline_score = float(self.baseline_score)
            if not math.isfinite(baseline_score):
                raise ValueError("baseline_score must be finite")
            object.__setattr__(self, "baseline_score", baseline_score)
        if not isinstance(self.details, Mapping):
            raise TypeError("evaluation details must be a mapping")
        object.__setattr__(
            self,
            "details",
            _isolated_copy(self.details, label="evaluation details"),
        )

    @property
    def delta(self) -> float | None:
        """Return candidate score minus baseline score when comparable."""
        if self.baseline_score is None:
            return None
        return float(self.score) - float(self.baseline_score)


Evaluator: TypeAlias = Callable[
    [Any, Any, ReplayCase],
    Evaluation | Awaitable[Evaluation],
]


@dataclass(frozen=True, slots=True)
class ReplayResult:
    """Candidate and evaluator outcome for one replay case."""

    case_id: str
    fingerprint: str
    output: Any
    evaluations: tuple[Evaluation, ...]
    duration_ms: float
    candidate_duration_ms: float
    cache_hit: bool = False
    error_stage: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.error_type is None and all(
            evaluation.passed for evaluation in self.evaluations
        )


@dataclass(frozen=True, slots=True)
class RegressionPolicy:
    """Promotion criteria for a complete replay report."""

    minimum_case_count: int = 1
    minimum_evaluation_count: int = 1
    minimum_pass_rate: float = 1.0
    maximum_error_rate: float = 0.0
    maximum_regression_rate: float = 0.0
    maximum_mean_score_drop: float = 0.0
    score_regression_tolerance: float = 0.0

    def __post_init__(self) -> None:
        for field_name in ("minimum_case_count", "minimum_evaluation_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an int")
            if value < 0:
                raise ValueError(f"{field_name} cannot be negative")
        for field_name in (
            "minimum_pass_rate",
            "maximum_error_rate",
            "maximum_regression_rate",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between 0 and 1")
            object.__setattr__(self, field_name, value)
        for field_name in (
            "maximum_mean_score_drop",
            "score_regression_tolerance",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{field_name} must be finite and non-negative")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class ReplayReport:
    """Ordered replay results plus an auditable promotion decision."""

    results: tuple[ReplayResult, ...]
    policy: RegressionPolicy
    started_at_unix: float
    duration_ms: float
    sensitive_keys: frozenset[str] = field(repr=False, compare=False)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed_count(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def error_count(self) -> int:
        return sum(result.error_type is not None for result in self.results)

    @property
    def evaluation_count(self) -> int:
        return sum(len(result.evaluations) for result in self.results)

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.passed_count / self.total

    @property
    def error_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.error_count / self.total

    @property
    def score_deltas(self) -> tuple[float, ...]:
        return tuple(
            delta
            for result in self.results
            if result.error_type is None
            for evaluation in result.evaluations
            if (delta := evaluation.delta) is not None
        )

    @property
    def regression_count(self) -> int:
        tolerance = self.policy.score_regression_tolerance
        return sum(delta < -tolerance for delta in self.score_deltas)

    @property
    def regression_rate(self) -> float:
        deltas = self.score_deltas
        if not deltas:
            return 0.0
        return self.regression_count / len(deltas)

    @property
    def mean_score_drop(self) -> float:
        deltas = self.score_deltas
        if not deltas:
            return 0.0
        tolerance = self.policy.score_regression_tolerance
        drops = [0.0 if delta >= -tolerance else -delta for delta in deltas]
        return sum(drops) / len(deltas)

    @property
    def violations(self) -> tuple[str, ...]:
        violations: list[str] = []
        if self.total < self.policy.minimum_case_count:
            violations.append(
                f"case count {self.total} is below required "
                f"{self.policy.minimum_case_count}"
            )
        if (
            self.results
            and self.evaluation_count < self.policy.minimum_evaluation_count
        ):
            violations.append(
                f"evaluation count {self.evaluation_count} is below required "
                f"{self.policy.minimum_evaluation_count}"
            )
        if self.results and self.pass_rate < self.policy.minimum_pass_rate:
            violations.append(
                f"pass rate {self.pass_rate:.4f} is below required "
                f"{self.policy.minimum_pass_rate:.4f}"
            )
        if self.results and self.error_rate > self.policy.maximum_error_rate:
            violations.append(
                f"error rate {self.error_rate:.4f} exceeds allowed "
                f"{self.policy.maximum_error_rate:.4f}"
            )
        if self.regression_rate > self.policy.maximum_regression_rate:
            violations.append(
                f"regression rate {self.regression_rate:.4f} exceeds allowed "
                f"{self.policy.maximum_regression_rate:.4f}"
            )
        if self.mean_score_drop > self.policy.maximum_mean_score_drop:
            violations.append(
                f"mean score drop {self.mean_score_drop:.6f} exceeds allowed "
                f"{self.policy.maximum_mean_score_drop:.6f}"
            )
        return tuple(violations)

    @property
    def accepted(self) -> bool:
        return not self.violations

    def raise_for_regressions(self) -> None:
        """Raise with all failed policy conditions when promotion is unsafe."""
        if not self.accepted:
            raise ReplayRegressionError(self)

    def to_dict(
        self,
        *,
        include_outputs: bool = False,
        include_error_messages: bool = False,
        include_metadata: bool = False,
        include_evaluation_details: bool = False,
    ) -> dict[str, Any]:
        """Serialize a credential-redacted, CI-friendly report.

        Outputs, exception messages, case metadata, and evaluator details are
        omitted by default because any of them may contain prompts, user data,
        provider URLs, or credentials.
        """
        result_rows: list[dict[str, Any]] = []
        for result in self.results:
            row: dict[str, Any] = {
                "case_id": result.case_id,
                "fingerprint": result.fingerprint,
                "passed": result.passed,
                "cache_hit": result.cache_hit,
                "duration_ms": result.duration_ms,
                "candidate_duration_ms": result.candidate_duration_ms,
                "error_stage": result.error_stage,
                "error_type": result.error_type,
                "evaluations": [
                    {
                        "name": evaluation.name,
                        "score": evaluation.score,
                        "passed": evaluation.passed,
                        "baseline_score": evaluation.baseline_score,
                        "delta": evaluation.delta,
                    }
                    for evaluation in result.evaluations
                ],
            }
            if include_metadata:
                row["metadata"] = _safe_report_value(
                    result.metadata,
                    sensitive_keys=self.sensitive_keys,
                )
            if include_evaluation_details:
                for evaluation_row, evaluation in zip(
                    row["evaluations"],
                    result.evaluations,
                    strict=True,
                ):
                    evaluation_row["details"] = _safe_report_value(
                        evaluation.details,
                        sensitive_keys=self.sensitive_keys,
                    )
            if include_outputs:
                row["output"] = _safe_report_value(
                    result.output,
                    sensitive_keys=self.sensitive_keys,
                    include_object_repr=True,
                )
            if include_error_messages and result.error_message is not None:
                row["error_message"] = result.error_message
            result_rows.append(row)

        return {
            "accepted": self.accepted,
            "violations": list(self.violations),
            "started_at_unix": self.started_at_unix,
            "duration_ms": self.duration_ms,
            "summary": {
                "total": self.total,
                "passed": self.passed_count,
                "errors": self.error_count,
                "evaluations": self.evaluation_count,
                "pass_rate": self.pass_rate,
                "error_rate": self.error_rate,
                "comparable_evaluations": len(self.score_deltas),
                "regressions": self.regression_count,
                "regression_rate": self.regression_rate,
                "mean_score_drop": self.mean_score_drop,
            },
            "policy": {
                "minimum_case_count": self.policy.minimum_case_count,
                "minimum_evaluation_count": (
                    self.policy.minimum_evaluation_count
                ),
                "minimum_pass_rate": self.policy.minimum_pass_rate,
                "maximum_error_rate": self.policy.maximum_error_rate,
                "maximum_regression_rate": self.policy.maximum_regression_rate,
                "maximum_mean_score_drop": (
                    self.policy.maximum_mean_score_drop
                ),
                "score_regression_tolerance": (
                    self.policy.score_regression_tolerance
                ),
            },
            "results": result_rows,
        }

    def to_json(
        self,
        *,
        include_outputs: bool = False,
        include_error_messages: bool = False,
        include_metadata: bool = False,
        include_evaluation_details: bool = False,
        indent: int | None = 2,
    ) -> str:
        return json.dumps(
            self.to_dict(
                include_outputs=include_outputs,
                include_error_messages=include_error_messages,
                include_metadata=include_metadata,
                include_evaluation_details=include_evaluation_details,
            ),
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )


class ReplayRegressionError(RuntimeError):
    """Raised when a replay report fails its promotion policy."""

    def __init__(self, report: ReplayReport) -> None:
        self.report = report
        super().__init__("; ".join(report.violations))
