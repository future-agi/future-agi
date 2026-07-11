"""DRF bridge registrations for experiments (Phase 2A Packet B, cluster 2).

All views live in model_hub/views/experiments.py and
model_hub/views/experiment_feedback_v2.py (APIViews — handlers are HTTP-verb
methods, so the tools dict keys below are verb keys and `method` must be set
explicitly; @validated_request request serializers resolve automatically from
the handler closure).

V1-skip policy (documented per spec): where V1/V2 views duplicate a
capability, ONLY the V2 view is bridged and the legacy hand-written tool is
re-homed same-name onto it. Skipped V1 views: ExperimentsTableView (V1
get/post/put), ExperimentsTableListView/ExperimentsTableDetailView,
ExperimentListAPIView, ExperimentStatsView, ExperimentDeleteView,
ExperimentRerunView, GetRowDiffView, ExperimentDatasetComparisonView.
V2 experiments are the maintained path (snapshot_dataset + structured
ExperimentPromptConfig records); the V1 endpoints serve only pre-snapshot
rows and the FE no longer creates them.

Same-name HW conversions (legacy ai_tools/tools/experiments/ modules deleted
in the same change): list_experiments, create_experiment, delete_experiment,
rerun_experiment, get_experiment_stats, get_experiment_results,
get_experiment_comparison, compare_experiments.

Adjudication (spec flagged compare_experiments vs get_experiment_comparison):
- legacy `compare_experiments` COMPUTED weighted rankings (weights input) →
  ExperimentDatasetComparisonV2View.post (computes + persists rankings from
  a weights dict).
- legacy `get_experiment_comparison` READ the stored ExperimentComparison
  rows → ExperimentComparisonDetailsView.get (returns the latest stored
  ranked comparison per variant).

Behavior delta vs the legacy tools (deliberate): experiment identifiers must
be UUIDs (from list_experiments) — the V2 serializers validate UUIDField and
the old name-resolution shim is gone with the ORM-direct code.

Deliberately NOT bridged here: ExperimentFeedbackCreateV2View has no
destructive siblings; there are no experiment bulk/hard-delete @actions, so
this packet has no Phase-3A deferrals (the V2 delete is the plain delete
endpoint, allowed per the existing delete_* CRUD pattern).
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.experiment_feedback_v2 import (
    ExperimentFeedbackCreateV2View,
    ExperimentFeedbackDetailsV2View,
    ExperimentFeedbackGetTemplateV2View,
    ExperimentFeedbackSubmitV2View,
)
from model_hub.views.experiments import (
    AddExperimentEvalView,
    DatasetExperimentsView,
    DownloadExperimentsView,
    ExperimentComparisonDetailsView,
    ExperimentDatasetComparisonV2View,
    ExperimentDeleteV2View,
    ExperimentDerivedVariablesView,
    ExperimentEvaluationStatsView,
    ExperimentJsonSchemaView,
    ExperimentListV2APIView,
    ExperimentNameSuggestionView,
    ExperimentNameValidationView,
    ExperimentRerunCellsV2View,
    ExperimentRerunV2View,
    ExperimentsTableV2View,
    ExperimentStatsV2View,
    ExperimentStopV2View,
    GetRowDiffV2View,
    RunAdditionalEvaluationsView,
)

# --- List / detail / create / update (same-name conversions + update) -------
expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "list_experiments",
            "method": "GET",
            "entity": "experiment",
            "description": (
                "List experiments (A/B tests of prompt/model/agent variants "
                "over a dataset) in the workspace, newest first. Returns "
                "name, status, dataset, variant counts. Filter by status, "
                "dataset_id or search by name."
            ),
            "query_params": {
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Filter by experiment name (icontains).",
                },
                "status": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Filter by status, e.g. 'not_started', 'queued', "
                        "'running', 'completed', 'failed', 'cancelled'."
                    ),
                },
                "dataset_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Only experiments on this dataset (UUID from "
                        "list_datasets)."
                    ),
                },
                "ordering": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Sort order: 'created_at', '-created_at', 'name' or "
                        "'-name' (default '-created_at')."
                    ),
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "Page number, 1-indexed.",
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Experiments per page.",
                    # TH-4667: the paginator reads `limit`; without this
                    # remap page_size was silently ignored (always 10 rows).
                    "actual": "limit",
                },
            },
        },
    },
)(ExperimentListV2APIView)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_results",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Get a V2 experiment's full detail: status, dataset, the "
                "prompt/agent variant configs with their per-variant status "
                "and metrics, and attached evaluation metrics. Use "
                "get_experiment_comparison for stored rankings and "
                "list_dataset_experiments for the row-level result grid."
            ),
        },
        "post": {
            "name": "create_experiment",
            "method": "POST",
            "entity": "experiment",
            # F4 (TH-5467): the create does slow SYNCHRONOUS work — it snapshots
            # the dataset and starts a Temporal workflow — before returning the
            # experiment id; the eval/variant rows then run asynchronously in the
            # workflow. On a non-trivial dataset that synchronous leg can exceed
            # the agent's 30s default tool budget and surface a spurious timeout
            # even though the experiment WAS created. Raise this one tool's
            # budget so the agent gets the real "created" result back and can
            # poll list_experiments / get_experiment_results for run status.
            "exec_timeout": 90,
            "description": (
                "Create and start a V2 experiment comparing prompt/model or "
                "agent variants on a dataset, then auto-run an eval over the "
                "results. This call has STRICT prerequisites — gather them "
                "FIRST or it will fail validation:\n"
                "PREREQUISITES (call these before create_experiment):\n"
                "1) dataset_id — from list_datasets.\n"
                "2) For each LLM prompt variant: prompt_id AND prompt_version "
                "(both UUIDs, from list_prompt_templates then "
                "list_prompt_versions). The version MUST be COMMITTED, not a "
                "draft — draft versions are rejected ('cannot be used in "
                "experiments'); commit one with commit_prompt_version if "
                "needed. (Agent variants use agent_id + agent_version "
                "instead; tts/stt/image variants use inline messages.)\n"
                "3) For each eval in user_eval_metrics: template_id (from "
                "list_eval_templates) AND a config.mapping that supplies "
                "EVERY one of that template's required input keys (e.g. "
                "input, output, context). Missing a key fails with 'Missing "
                "required mapping keys: <key>'. Discover the required keys "
                "with get_eval_template (its `required_keys` field) and map "
                "each key to a dataset column NAME — get the dataset's columns "
                "from get_dataset_rows (its `column_config` / `table` headers) "
                "or get_dataset — e.g. config={'mapping': {'input': "
                "'question', 'output': 'answer', 'context': 'retrieved_docs'}}.\n"
                "REQUEST SHAPE: prompt_config (>=1) — LLM prompt entry = "
                "{name, prompt_id, prompt_version, model}; agent entry = "
                "{name, agent_id, agent_version}. user_eval_metrics (>=1) — "
                "{template_id, name, config:{mapping:{...}}}. A dataset "
                "snapshot is taken and a Temporal workflow runs it "
                "automatically; poll list_experiments / get_experiment_results "
                "for status."
            ),
        },
        "put": {
            "name": "update_experiment",
            "method": "PUT",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Update a V2 experiment (partial: column_id, prompt_config, "
                "user_eval_metrics). Diff-based: only new/changed variant "
                "configs or evals are re-run; sending unchanged data does "
                "nothing. Name and experiment_type are not editable."
            ),
        },
    },
)(ExperimentsTableV2View)

# --- Delete / rerun / stop lifecycle ----------------------------------------
expose_to_mcp(
    category="experiments",
    tools={
        "delete": {
            "name": "delete_experiment",
            "method": "DELETE",
            "entity": "experiment",
            "description": (
                "Soft-delete one or more experiments by UUID. Cancels any "
                "running workflows and removes the experiments from "
                "listings."
            ),
            "query_params": {
                "experiment_ids": {
                    "type": list,
                    "required": True,
                    "description": (
                        "Experiment UUIDs to delete (from list_experiments)."
                    ),
                },
            },
        },
    },
)(ExperimentDeleteV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "rerun_experiment",
            "method": "POST",
            "entity": "experiment",
            "description": (
                "Re-run one or more experiments by UUID: resets results and "
                "re-processes all rows with the current variant configs "
                "(previous results are overwritten). Poll list_experiments "
                "or get_experiment_results for status."
            ),
        },
    },
)(ExperimentRerunV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "rerun_experiment_cells",
            "method": "POST",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Re-run a subset of an experiment: specific cells "
                "(cells=[{column_id,row_id}]), whole variant columns "
                "(source_ids=[EDT ids]) or only some evals "
                "(user_eval_metric_ids). At least one selector is required; "
                "failed_only=true restricts to failed cells."
            ),
        },
    },
)(ExperimentRerunCellsV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "stop_experiment",
            "method": "POST",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Stop a running or queued experiment: marks it CANCELLED and "
                "cancels its Temporal workflows (main run + cell reruns)."
            ),
        },
    },
)(ExperimentStopV2View)

# --- Stats / comparisons / row data ------------------------------------------
expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_stats",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Get aggregate per-variant statistics for an experiment: "
                "average response time, prompt/completion/total tokens and "
                "eval score columns for each variant (and the base column "
                "if set)."
            ),
        },
    },
)(ExperimentStatsV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_evaluation_stats",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "path_kwargs": {
                "evaluation_id": {
                    "description": (
                        "UUID of the evaluation metric (UserEvalMetric) to "
                        "get stats for — the 'id' of an entry in the "
                        "experiment's user_eval_metrics (see "
                        "get_experiment_results)."
                    ),
                },
            },
            "description": (
                "Get one evaluation's per-variant score breakdown inside an "
                "experiment: each variant's eval column with average score "
                "and distribution for that single eval metric."
            ),
        },
    },
)(ExperimentEvaluationStatsView)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "compare_experiments",
            "method": "POST",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Compute weighted rankings of an experiment's variants: "
                "normalizes eval scores, response time and token usage, "
                "applies the weights dict (e.g. {'scores': 0.4, "
                "'response_time': 0.3, 'total_tokens': 0.3}) and persists "
                "the ranked comparison. Read it back with "
                "get_experiment_comparison."
            ),
        },
    },
)(ExperimentDatasetComparisonV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_comparison",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Get the stored A/B comparison results for an experiment: "
                "per-variant rank, overall rating, raw and normalized "
                "metrics (score, response time, tokens) and the weights "
                "used. Run compare_experiments first if no comparison "
                "exists yet."
            ),
        },
    },
)(ExperimentComparisonDetailsView)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "list_dataset_experiments",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Page through an experiment's result grid: the snapshot "
                "dataset's rows with every variant's output cell and eval "
                "cells. Heavy payload — keep page_size small. Set "
                "get_diff=true to include diffs against the base column; "
                "column_config_only=true returns just the column layout."
            ),
            "query_params": {
                "page_size": {
                    "type": int,
                    "required": False,
                    "default": 10,
                    "description": "Rows per page (default 10).",
                },
                "current_page_index": {
                    "type": int,
                    "required": False,
                    "default": 0,
                    "description": "Page index, 0-based.",
                },
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Full-text search over cell values.",
                },
                "get_diff": {
                    "type": bool,
                    "required": False,
                    "description": (
                        "Include per-cell diff against the base column."
                    ),
                },
                "column_config_only": {
                    "type": bool,
                    "required": False,
                    "description": (
                        "Return only the column configuration (no rows)."
                    ),
                },
            },
        },
    },
)(DatasetExperimentsView)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "get_experiment_row_diff",
            "method": "POST",
            "entity": "experiment",
            "description": (
                "Diff specific result cells of a V2 experiment against the "
                "base/compare columns: pass the experiment_id plus row_ids, "
                "column_ids and compare_column_ids from "
                "list_dataset_experiments; returns per-cell text diffs."
            ),
        },
    },
)(GetRowDiffV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "export_experiments_csv",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Export an experiment's results as CSV text: dataset "
                "columns, each variant's outputs and eval score columns, "
                "one row per dataset row."
            ),
        },
    },
)(DownloadExperimentsView)

# --- Evals on experiments -----------------------------------------------------
expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "add_experiment_evaluation",
            "method": "POST",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Attach a new evaluation metric to an experiment from an "
                "eval template (template_id from list_eval_templates, a "
                "unique name, and the eval's config/mapping). Set run=true "
                "to execute it immediately on all variants; "
                "save_as_template=true also saves a reusable copy of the "
                "template."
            ),
        },
    },
)(AddExperimentEvalView)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "run_experiment_evaluations",
            "method": "POST",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Run (or re-run) already-attached evaluation metrics on an "
                "experiment: pass eval_template_ids = UserEvalMetric UUIDs "
                "for this experiment's dataset; their eval columns are "
                "reset and recomputed in the background."
            ),
        },
    },
)(RunAdditionalEvaluationsView)

# --- Naming / schema helpers ---------------------------------------------------
expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "suggest_experiment_name",
            "method": "GET",
            "detail": True,
            "pk_field": "dataset_id",
            "pk_kwarg": "dataset_id",
            "id_source": "list_datasets",
            "entity": "dataset",
            "description": (
                "Generate a unique suggested experiment name for a dataset "
                "(format DS_{dataset}_exp_{yy/mm/dd}[_vN]). Use before "
                "create_experiment."
            ),
        },
    },
)(ExperimentNameSuggestionView)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "validate_experiment_name",
            "method": "GET",
            "entity": "experiment",
            "description": (
                "Check whether an experiment name is unique within a "
                "dataset. Returns is_valid plus a message when the name is "
                "taken."
            ),
            "query_params": {
                "dataset_id": {
                    "type": str,
                    "required": True,
                    "description": "Dataset UUID (from list_datasets).",
                },
                "name": {
                    "type": str,
                    "required": True,
                    "description": "Candidate experiment name to validate.",
                },
            },
        },
    },
)(ExperimentNameValidationView)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_json_schema",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Get JSON column schemas and image-column metadata for an "
                "experiment's snapshot dataset — which columns hold "
                "structured JSON (and their shape) or images."
            ),
        },
    },
)(ExperimentJsonSchemaView)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_derived_variables",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Get derived variables exposed by run-prompt columns in an "
                "experiment's snapshot dataset (variables usable in "
                "downstream prompt/eval mappings)."
            ),
        },
    },
)(ExperimentDerivedVariablesView)

# --- Experiment feedback (V2) ---------------------------------------------------
expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_feedback_template",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Get the feedback form definition for one of an "
                "experiment's eval metrics: output type (pass_fail / "
                "choices / score), the valid choices and eval description. "
                "Call before create_experiment_feedback."
            ),
            "query_params": {
                "user_eval_metric_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the experiment's eval metric (from "
                        "get_experiment_results user_eval_metrics)."
                    ),
                },
            },
        },
    },
)(ExperimentFeedbackGetTemplateV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "create_experiment_feedback",
            "method": "POST",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Record reviewer feedback on one evaluated experiment cell: "
                "source must be 'experiment', source_id the EVAL COLUMN "
                "UUID, row_id the snapshot row, user_eval_metric the metric "
                "UUID, plus the corrected value and an optional "
                "explanation. Returns the feedback id for "
                "submit_experiment_feedback."
            ),
        },
    },
)(ExperimentFeedbackCreateV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "get": {
            "name": "get_experiment_feedback_details",
            "method": "GET",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "List previously recorded feedback on an experiment's eval "
                "results (value, comment, action taken), optionally "
                "filtered to one eval metric and/or one row."
            ),
            "query_params": {
                "user_eval_metric_id": {
                    "type": str,
                    "required": False,
                    "description": "Filter by eval metric UUID.",
                },
                "row_id": {
                    "type": str,
                    "required": False,
                    "description": "Filter by snapshot row UUID.",
                },
            },
        },
    },
)(ExperimentFeedbackDetailsV2View)

expose_to_mcp(
    category="experiments",
    tools={
        "post": {
            "name": "submit_experiment_feedback",
            "method": "POST",
            "detail": True,
            "pk_field": "experiment_id",
            "pk_kwarg": "experiment_id",
            "id_source": "list_experiments",
            "entity": "experiment",
            "description": (
                "Act on recorded feedback (feedback_id from "
                "create_experiment_feedback): action_type 'retune' queues "
                "the metric for retuning; 'recalculate_row' / "
                "'recalculate_dataset' / 'retune_recalculate' embed the "
                "feedback for few-shot RAG and re-run the affected eval "
                "cells via a Temporal workflow."
            ),
        },
    },
)(ExperimentFeedbackSubmitV2View)
