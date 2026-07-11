"""DRF bridge registrations for model_hub-side optimization (Phase 2A Packet E,
cluster 6 part).

DatasetOptimizationViewSet (model_hub/views/dataset_optimization.py) has
``queryset = OptimizeDataset.objects.all()`` — the SAME model the hand-written
optimization tools queried via the ORM. Adjudication (per spec §2 Packet E):
same capability -> SAME-NAME bridge conversions, zero prompt/skill churn. The
seven converted legacy modules in ai_tools/tools/optimization/ are deleted in
this change; their import lines are removed from ai_tools/tools/__init__.py.

Same-name HW conversions (7, count-neutral — names stay in baseline.txt):
  get_optimization_steps   -> DatasetOptimizationViewSet.steps
  get_optimization_graph   -> DatasetOptimizationViewSet.graph
  stop_optimization_run    -> DatasetOptimizationViewSet.stop
  get_optimization_trial   -> DatasetOptimizationViewSet.trial_detail
  get_trial_prompt         -> DatasetOptimizationViewSet.trial_prompt
  get_trial_scenarios      -> DatasetOptimizationViewSet.trial_scenarios
  get_trial_evaluations    -> DatasetOptimizationViewSet.trial_evaluations
Input field names match the legacy tools exactly (optimization_id [+ trial_id]).

Kept hand-written (documented adjudication):
- create_optimization_run / list_optimization_runs: the ViewSet's create/list
  are already bridged as create_dataset_optimization / list_dataset_optimizations
  (_misc_viewsets.py). Re-bridging the same actions under the legacy names would
  double-register the capability; retiring the legacy names instead would churn
  baseline.txt and L1 prompts. Left as-is for a follow-up retirement slice.

Behavior deltas vs the HW tools (improvements, documented):
- Org + workspace scoping now runs (get_queryset filters by organization via
  column -> dataset; the HW tools had NO org filter — cross-tenant read hole
  closed). 3B cross-tenant sweep tag: none needed (org-scoped, not org-only).
- The ViewSet's queryset only surfaces new-flow runs
  (optimizer_algorithm__isnull=False); legacy-flow rows are no longer readable
  through these tools.

CRUD registration for this ViewSet stays in _misc_viewsets.py — per A10 this
module only ADDS @actions to the already-registered class.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.dataset_optimization import DatasetOptimizationViewSet

_OPT_ID = {
    "pk_field": "optimization_id",
    "id_source": "list_optimization_runs",
}
_TRIAL_KWARG = {
    "trial_id": {
        "description": (
            "UUID of the trial within this optimization run."
        ),
        "id_source": "get_optimization_steps",
    }
}

expose_to_mcp(
    category="optimization",
    tools={
        "steps": {
            "name": "get_optimization_steps",
            "entity": "optimization_run",
            **_OPT_ID,
            "description": (
                "Step-by-step progress of an optimization run: "
                "initialization, baseline eval, optimization trials (with "
                "trial ids), and finalization — each with status and "
                "timestamps."
            ),
        },
        "graph": {
            "name": "get_optimization_graph",
            "entity": "optimization_run",
            **_OPT_ID,
            "description": (
                "Score-vs-trial graph data for an optimization run "
                "(baseline and per-trial average scores)."
            ),
        },
        "stop": {
            "name": "stop_optimization_run",
            "entity": "optimization_run",
            **_OPT_ID,
            # Deliberately body-less POST: the handler reads no request data.
            "query_params": {},
            "description": (
                "Stop (cancel) a RUNNING or PENDING optimization run. Cancels "
                "the underlying workflow and marks the run cancelled; runs in "
                "any other status are refused."
            ),
        },
        "trial_detail": {
            "name": "get_optimization_trial",
            "entity": "optimization_run",
            **_OPT_ID,
            "path_kwargs": _TRIAL_KWARG,
            "description": (
                "Full details for one optimization trial: score vs baseline, "
                "status, and the trial's configuration/results."
            ),
        },
        "trial_prompt": {
            "name": "get_trial_prompt",
            "entity": "optimization_run",
            **_OPT_ID,
            "path_kwargs": _TRIAL_KWARG,
            "description": (
                "The optimized prompt produced by a trial, side-by-side with "
                "the baseline prompt and the score change."
            ),
        },
        "trial_scenarios": {
            "name": "get_trial_scenarios",
            "entity": "optimization_run",
            **_OPT_ID,
            "path_kwargs": _TRIAL_KWARG,
            "description": (
                "Per-scenario (per-row) results for an optimization trial: "
                "inputs, outputs, and scores for each evaluated case."
            ),
        },
        "trial_evaluations": {
            "name": "get_trial_evaluations",
            "entity": "optimization_run",
            **_OPT_ID,
            "path_kwargs": _TRIAL_KWARG,
            "description": (
                "Evaluation results for an optimization trial grouped by eval "
                "metric, with average scores compared against the baseline "
                "trial."
            ),
        },
    },
)(DatasetOptimizationViewSet)
