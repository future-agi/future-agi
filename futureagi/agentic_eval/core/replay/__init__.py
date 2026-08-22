"""Counterfactual replay and regression-gate primitives."""

from agentic_eval.core.replay.engine import Candidate, CounterfactualReplay
from agentic_eval.core.replay.models import (
    Evaluation,
    Evaluator,
    RegressionPolicy,
    ReplayCase,
    ReplayRegressionError,
    ReplayReport,
    ReplayResult,
)
from agentic_eval.core.replay.privacy import request_fingerprint

__all__ = [
    "Candidate",
    "CounterfactualReplay",
    "Evaluation",
    "Evaluator",
    "RegressionPolicy",
    "ReplayCase",
    "ReplayRegressionError",
    "ReplayReport",
    "ReplayResult",
    "request_fingerprint",
]
