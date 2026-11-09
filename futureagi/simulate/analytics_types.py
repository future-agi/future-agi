"""Typed response accumulators for simulation analytics.

These describe the existing wire shapes; serializers remain the API contract.
Keeping the heterogeneous response separate from its homogeneous trend lists
lets static checking verify list mutation, numeric aggregation and sorting.
"""

from typing import NotRequired, TypedDict


class ScenarioPerformance(TypedDict):
    name: str
    test_count: int
    total_score: float
    scores: list[float]


class ScenarioPerformanceSummary(TypedDict):
    scenario_name: str
    test_count: int
    performance_score: float


class RunTestInfo(TypedDict):
    id: str
    name: str
    description: str | None
    total_test_executions: int
    total_calls: int


class ExecutionTrend(TypedDict):
    test_execution_id: str
    test_execution_name: str
    created_at: str
    total_calls: int


class FailRateTrend(ExecutionTrend):
    fail_rate: float
    failed_calls: int


class EvaluationScoreTrend(ExecutionTrend):
    score_percentage: float
    scored_calls: int


class PerformanceComparison(FailRateTrend):
    status: str
    completed_calls: int
    success_rate: float
    avg_score: float
    duration_seconds: float | None


class AnalyticsSummary(TypedDict):
    avg_success_rate: float
    avg_fail_rate: float
    avg_evaluation_score: float
    total_executions: int
    best_success_rate: float
    worst_success_rate: float
    best_evaluation_score: float
    worst_evaluation_score: float


class RunTestAnalytics(TypedDict):
    run_test_info: RunTestInfo
    fail_rate_trends: list[FailRateTrend]
    evaluation_score_trends: list[EvaluationScoreTrend]
    performance_comparison: list[PerformanceComparison]
    summary_stats: NotRequired[AnalyticsSummary]
