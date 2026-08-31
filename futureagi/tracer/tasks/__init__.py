"""
Tracer Tasks Module

Re-exports all tasks from submodules for backward compatibility.
Tasks are organized by domain:

- error_analysis: Trace error analysis and clustering
- external_eval: External evaluation processing
- dataset: Dataset creation from spans
"""

# Dataset Tasks
from tracer.tasks.dataset import (
    CHUNK_SIZE,
    process_spans_chunk_task,
)

# Error Analysis Tasks
from tracer.tasks.error_analysis import (
    analyze_single_trace,
    cluster_project_errors,
    run_deep_analysis_on_demand,
)

# Eval Clustering Tasks
from tracer.tasks.eval_clustering import (
    cluster_eval_results_task,
)

# External Eval Tasks
from tracer.tasks.external_eval import (
    process_external_evals,
)

__all__ = [
    # Error Analysis
    "analyze_single_trace",
    "cluster_project_errors",
    "run_deep_analysis_on_demand",
    # Eval Clustering
    "cluster_eval_results_task",
    # External Eval
    "process_external_evals",
    # Dataset
    "process_spans_chunk_task",
    "CHUNK_SIZE",
]
