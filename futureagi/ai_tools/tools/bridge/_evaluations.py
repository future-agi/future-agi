"""DRF bridge registrations for evaluations (Phase 2A Packet E, cluster 5).

All views live in model_hub/views/separate_evals.py (APIViews — handlers are
HTTP-verb methods, so the tools dict keys below are verb keys and `method`
must be set explicitly; @validated_request request serializers resolve
automatically from the handler closure).

Same-name HW conversions (legacy ai_tools/tools/evaluations/ modules deleted
in the same change): list_eval_templates, create_eval_template,
get_eval_template, update_eval_template, delete_eval_template,
duplicate_eval_template, test_eval_template, create_composite_eval,
execute_composite_eval, get_eval_logs, get_eval_log_detail,
get_eval_playground, get_eval_code_snippet.

Adjudications (documented per spec):
- update_eval_template -> EvalTemplateUpdateView (:2354, PUT <id>/update/,
  V2 schema) over the legacy UpdateEvalTemplateView (:6611, POST) — the V2
  view is the maintained path with the revamped scoring fields, mirroring
  EvalTemplateCreateV2View.
- get_eval_logs -> GetAPICallLogDetailsView (:424, the per-template log
  TABLE) and get_eval_log_detail -> GetAPICallLogView (:557, a SINGLE log
  row by log_id). The spec's ":557/:424" order is inverted relative to the
  classes' (confusing) names; mapped semantically to match the legacy tools.
- get_error_localization_status merged INTO get_error_localization_results:
  CellErrorLocalizerView.get returns status AND analysis in one payload, and
  neither legacy module was ever imported/registered (dead code, deleted).

Deferred / blocked (do NOT register here):
- GroundTruthUploadView (:4374) — multipart file upload is unproven through
  _build_drf_request (JSON-only factory). Documented gap.
- EvalTemplateBulkDeleteView (:1780) — bridged in Phase 3A as
  bulk_delete_eval_templates (confirmation-gated; end of this module).
- §6.3 ORM-direct HW tools with NO DRF endpoint behind them (endpoint must be
  built first; they stay hand-written): compare_evaluations, get_evaluation,
  list_evaluations, delete_eval_logs, evaluate_with_agent. Same for
  apply_eval_group_to_dataset + the eval_group family (deliberate keepers)
  and list_dataset_evals (datasets cluster).
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.separate_evals import (
    CellErrorLocalizerView,
    CompositeEvalAdhocExecuteView,
    CompositeEvalCreateView,
    CompositeEvalDetailView,
    CompositeEvalExecuteView,
    DeleteEvalTemplateView,
    DuplicateEvalTemplateView,
    EvalCodeSnippetAPIView,
    EvalFeedbackListView,
    EvalMetricView,
    EvalPlayGroundAPIView,
    EvalPlayGroundFeedbackAPIView,
    EvalTemplateBulkDeleteView,
    EvalTemplateCreateV2View,
    EvalTemplateDetailView,
    EvalTemplateListChartsView,
    EvalTemplateListView,
    EvalTemplateUpdateView,
    EvalTemplateVersionCreateView,
    EvalTemplateVersionListView,
    EvalUsageStatsView,
    GetAPICallLogDetailsView,
    GetAPICallLogView,
    GetEvalTemplateNameView,
    GetEvalTemplates,
    GroundTruthConfigView,
    GroundTruthDataView,
    GroundTruthDeleteView,
    GroundTruthListView,
    GroundTruthMappingView,
    GroundTruthRoleMappingView,
    GroundTruthStatusView,
    GroundTruthTriggerEmbeddingView,
    RestoreVersionView,
    SetDefaultVersionView,
    TestEvaluationTemplateAPIView,
    TraceEvalView,
    VersionCompareView,
)

# --- Eval templates: list / CRUD-ish lifecycle (same-name conversions) ------
expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "list_eval_templates",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "List eval templates (paginated) with 30-day run metrics. "
                "owner_filter: 'all' (default), 'user' (your org's custom "
                "evals), or 'system' (built-in catalog). Supports search and "
                "sort_by name/updated_at/created_at."
            ),
        }
    },
)(EvalTemplateListView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "create_eval_template",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Create an eval template. eval_type: 'llm' (LLM-judge with "
                "`instructions`), 'code' (provide `code` + code_language), or "
                "'agent'. output_type: pass_fail / percentage / "
                "deterministic; optional pass_threshold (0-1), choice_scores, "
                "tags, model (default turing_large). Set is_draft=true for a "
                "draft."
            ),
        }
    },
)(EvalTemplateCreateV2View)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_eval_template",
            "method": "GET",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Get one eval template with all fields (instructions/criteria, "
                "model, output type, scoring config, tags, version info). The "
                "`required_keys` field lists the mapping keys this eval needs "
                "(e.g. input, output, context) — use it to build the "
                "config.mapping when attaching the eval to an experiment "
                "(create_experiment) or a dataset; map each key to a column."
            ),
        }
    },
)(EvalTemplateDetailView)

expose_to_mcp(
    category="evaluations",
    tools={
        "put": {
            "name": "update_eval_template",
            "method": "PUT",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Update a user-owned eval template (V2 schema): name, "
                "instructions, model, output_type, pass_threshold, "
                "choice_scores, tags, code, etc. Only provided fields change; "
                "publish=true commits a new version."
            ),
        }
    },
)(EvalTemplateUpdateView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "delete_eval_template",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Soft-delete a user-owned eval template (and its configs/"
                "logs). Pass eval_template_id from list_eval_templates."
            ),
        }
    },
)(DeleteEvalTemplateView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "duplicate_eval_template",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Duplicate a user-owned eval template under a new (unique) "
                "name. Pass eval_template_id and name."
            ),
        }
    },
)(DuplicateEvalTemplateView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "test_eval_template",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Dry-run an eval template definition against sample inputs "
                "WITHOUT saving it. Requires name, output_type, config "
                "(with `mapping` of template variables to sample values) and "
                "template_type ('futureagi', 'llm', or 'function')."
            ),
        }
    },
)(TestEvaluationTemplateAPIView)

# --- Eval logs / metrics / charts / catalog ---------------------------------
expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_eval_logs",
            "method": "GET",
            "entity": "eval_template",
            "description": (
                "Evaluation execution logs/history TABLE for an eval "
                "template (success+error runs with cost, status, timestamps). "
                "Paginated; source filters to 'logs' (default), 'feedback', "
                "or 'eval_playground'. Use get_eval_log_detail for one row."
            ),
            "query_params": {
                "eval_template_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the eval template. **How to get it:** call "
                        "`list_eval_templates` and copy the 'id'."
                    ),
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Rows per page (default 10).",
                },
                "current_page_index": {
                    "type": int,
                    "required": False,
                    "description": "0-based page index (default 0).",
                },
                "source": {
                    "type": str,
                    "required": False,
                    "description": "'logs' (default), 'feedback', or 'eval_playground'.",
                },
            },
        }
    },
)(GetAPICallLogDetailsView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_eval_log_detail",
            "method": "GET",
            "entity": "eval_log",
            "description": (
                "Get one evaluation log row by log_id: inputs (mappings), "
                "output, source, required keys, and error-localizer state."
            ),
            "query_params": {
                "log_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID (log_id) of the eval log entry. **How to get "
                        "it:** call `get_eval_logs` first."
                    ),
                },
            },
        }
    },
)(GetAPICallLogView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "get_eval_metrics",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Aggregate metric data (averages/distribution) for an eval "
                "template's successful runs. Pass eval_template_id."
            ),
        }
    },
)(EvalMetricView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "get_eval_template_charts",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "30-day daily run-count and failure-rate chart data for a "
                "list of eval template_ids (list of UUIDs)."
            ),
        }
    },
)(EvalTemplateListChartsView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "get_eval_template_name",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Quick id/name/description lookup of usable eval templates "
                "(system + your org's), optionally filtered by search_text."
            ),
        }
    },
)(GetEvalTemplateNameView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "get_eval_templates_catalog",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Legacy eval-templates dashboard table: templates that have "
                "actually been RUN, with 30-day averages and run counts "
                "(paginated, search_text filter)."
            ),
        }
    },
)(GetEvalTemplates)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_eval_usage_stats",
            "method": "GET",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Usage stats + chart data + paginated run logs for one eval "
                "template over a period."
            ),
            "query_params": {
                "page": {
                    "type": int,
                    "required": False,
                    "description": "0-based page of the embedded log table.",
                },
                "page_size": {"type": int, "required": False},
                "period": {
                    "type": str,
                    "required": False,
                    "description": "One of 30m, 6h, 1d, 7d, 30d (default), 90d, 180d, 365d.",
                },
            },
        }
    },
)(EvalUsageStatsView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "list_eval_feedback",
            "method": "GET",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Paginated user feedback (thumbs/retune requests) recorded "
                "against an eval template's results."
            ),
            "query_params": {
                "page": {"type": int, "required": False},
                "page_size": {"type": int, "required": False},
            },
        }
    },
)(EvalFeedbackListView)

# --- Eval template version control (net-new) --------------------------------
expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "list_eval_template_versions",
            "method": "GET",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "List all saved versions of an eval template (version number, "
                "default flag, criteria/model snapshot, author, date)."
            ),
        }
    },
)(EvalTemplateVersionListView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "create_eval_template_version",
            "method": "POST",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Snapshot the current state of an eval template as a new "
                "version. Optional criteria/model/config_snapshot overrides."
            ),
        }
    },
)(EvalTemplateVersionCreateView)

expose_to_mcp(
    category="evaluations",
    tools={
        "put": {
            "name": "set_default_eval_template_version",
            "method": "PUT",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "path_kwargs": {
                "version_id": {
                    "description": "UUID of the version to make default.",
                    "id_source": "list_eval_template_versions",
                }
            },
            "description": (
                "Set a specific version of an eval template as its default "
                "(active) version."
            ),
        }
    },
)(SetDefaultVersionView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "restore_eval_template_version",
            "method": "POST",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "path_kwargs": {
                "version_id": {
                    "description": "UUID of the old version to restore from.",
                    "id_source": "list_eval_template_versions",
                }
            },
            "description": (
                "Restore an old eval-template version by creating a NEW "
                "version with the old version's config (the old version is "
                "not modified)."
            ),
        }
    },
)(RestoreVersionView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "compare_eval_template_versions",
            "method": "GET",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Field-by-field diff between two versions of an eval "
                "template (version NUMBERS, not ids — see "
                "list_eval_template_versions)."
            ),
            "query_params": {
                "a": {
                    "type": str,
                    "required": True,
                    "description": "First version number to compare.",
                },
                "b": {
                    "type": str,
                    "required": True,
                    "description": "Second version number to compare.",
                },
            },
        }
    },
)(VersionCompareView)

# --- Composite evals ---------------------------------------------------------
expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "create_composite_eval",
            "method": "POST",
            "entity": "composite_eval",
            "description": (
                "Create a composite eval from existing eval templates "
                "(child_template_ids). Optional aggregation_function "
                "(weighted_avg default / avg / min / max / pass_rate) and "
                "child_weights ({child_id: weight})."
            ),
        }
    },
)(CompositeEvalCreateView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_composite_eval",
            "method": "GET",
            "detail": True,
            "entity": "composite_eval",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Get a composite eval's detail with its child evals, order, "
                "weights, and aggregation settings."
            ),
        }
    },
)(CompositeEvalDetailView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "execute_composite_eval",
            "method": "POST",
            "detail": True,
            "entity": "composite_eval",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Execute all child evals of a saved composite eval against "
                "the given `mapping` (template variable -> value) and "
                "aggregate the results. Runs LLM evaluations — costs tokens."
            ),
        }
    },
)(CompositeEvalExecuteView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "execute_composite_eval_adhoc",
            "method": "POST",
            "entity": "composite_eval",
            "description": (
                "Execute a composite eval configuration WITHOUT saving it: "
                "pass child_template_ids, mapping, and aggregation settings "
                "to test before creating. Runs LLM evaluations — costs tokens."
            ),
        }
    },
)(CompositeEvalAdhocExecuteView)

# --- Ground-truth management (net-new; upload stays a documented gap) -------
_GT_ID = {
    "pk_field": "ground_truth_id",
    "pk_kwarg": "ground_truth_id",
    "id_source": "list_ground_truths",
}

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "list_ground_truths",
            "method": "GET",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "List the ground-truth datasets attached to an eval template "
                "(name, row count, embedding status, mappings)."
            ),
        }
    },
)(GroundTruthListView)

expose_to_mcp(
    category="evaluations",
    tools={
        "put": {
            "name": "update_ground_truth_mapping",
            "method": "PUT",
            "detail": True,
            "entity": "ground_truth",
            **_GT_ID,
            "description": (
                "Set a ground truth's variable_mapping (JSON object mapping "
                "eval template variables to ground-truth columns)."
            ),
        }
    },
)(GroundTruthMappingView)

expose_to_mcp(
    category="evaluations",
    tools={
        "put": {
            "name": "update_ground_truth_role_mapping",
            "method": "PUT",
            "detail": True,
            "entity": "ground_truth",
            **_GT_ID,
            "description": (
                "Set a ground truth's role_mapping (JSON object mapping roles "
                "input / expected_output / score / reasoning to columns)."
            ),
        }
    },
)(GroundTruthRoleMappingView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_ground_truth_data",
            "method": "GET",
            "detail": True,
            "entity": "ground_truth",
            **_GT_ID,
            "description": "Page through a ground truth's rows.",
            "query_params": {
                "page": {
                    "type": int,
                    "required": False,
                    "description": "1-based page (default 1).",
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Rows per page (default 50, max 100).",
                },
            },
        }
    },
)(GroundTruthDataView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_ground_truth_status",
            "method": "GET",
            "detail": True,
            "entity": "ground_truth",
            **_GT_ID,
            "description": (
                "Processing/embedding status of a ground-truth dataset."
            ),
        }
    },
)(GroundTruthStatusView)

expose_to_mcp(
    category="evaluations",
    tools={
        "delete": {
            "name": "delete_ground_truth",
            "method": "DELETE",
            "detail": True,
            "entity": "ground_truth",
            **_GT_ID,
            "description": "Soft-delete a ground-truth dataset from its eval template.",
        }
    },
)(GroundTruthDeleteView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_ground_truth_config",
            "method": "GET",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Get an eval template's ground-truth configuration (enabled "
                "flag, mode, linked ground_truth_id)."
            ),
        }
    },
)(GroundTruthConfigView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "trigger_ground_truth_embedding",
            "method": "POST",
            "detail": True,
            "entity": "ground_truth",
            **_GT_ID,
            "description": (
                "Start (or restart) embedding generation for a ground-truth "
                "dataset so it can be used for similarity lookup. No-op if "
                "already processing."
            ),
        }
    },
)(GroundTruthTriggerEmbeddingView)

# --- Error localization / playground / snippets -----------------------------
expose_to_mcp(
    category="evaluations",
    tools={
        # CellErrorLocalizerView.get is the poll endpoint: returns the task's
        # status AND the error analysis once completed (the legacy, never-
        # registered get_error_localization_status is merged into this).
        "get": {
            "name": "get_error_localization_results",
            "method": "GET",
            "detail": True,
            "entity": "cell",
            "pk_field": "cell_id",
            "pk_kwarg": "cell_id",
            "description": (
                "Error-localization status + results for a dataset eval cell: "
                "task status (pending/processing/completed/failed), the "
                "error_analysis block, selected input key, and inputs. "
                "cell_id is the dataset cell's UUID."
            ),
        }
    },
)(CellErrorLocalizerView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "get_eval_playground",
            "method": "POST",
            "entity": "eval_template",
            "description": (
                "Run an eval template once in the playground against ad-hoc "
                "inputs: pass template_id and mapping (template variable -> "
                "value). Optional model, error_localizer, and trace/span/"
                "session/row context ids. Runs an LLM call — costs tokens."
            ),
        }
    },
)(EvalPlayGroundAPIView)

expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "submit_eval_playground_feedback",
            "method": "POST",
            "entity": "eval_log",
            "description": (
                "Record feedback on a playground eval run (log_id from "
                "get_eval_logs): action_type 'retune' or 'recalculate', a "
                "value, and an optional explanation."
            ),
        }
    },
)(EvalPlayGroundFeedbackAPIView)

expose_to_mcp(
    category="evaluations",
    tools={
        "get": {
            "name": "get_eval_code_snippet",
            "method": "GET",
            "entity": "eval_template",
            "description": (
                "Generate Python / curl / JavaScript SDK snippets for running "
                "an eval template programmatically."
            ),
            "query_params": {
                "template_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the eval template. **How to get it:** call "
                        "`list_eval_templates` and copy the 'id'."
                    ),
                },
                "model": {
                    "type": str,
                    "required": False,
                    "description": "Model name to embed in the snippet (default turing_large).",
                },
                "mapping": {
                    "type": str,
                    "required": False,
                    "description": "JSON-encoded mapping of template variables to values.",
                },
            },
        }
    },
)(EvalCodeSnippetAPIView)

expose_to_mcp(
    category="evaluations",
    tools={
        # TraceEvalView.post has no @validated_request — it parses request.data
        # into the TraceEvalRequest pydantic type, so the input schema is
        # declared explicitly here.
        "post": {
            "name": "get_trace_evals",
            "method": "POST",
            "detail": True,
            "entity": "eval_template",
            "pk_field": "eval_template_id",
            "pk_kwarg": "template_id",
            "id_source": "list_eval_templates",
            "description": (
                "Run an eval template against a trace: extracts the trace's "
                "input/output and evaluates them, returning score/passed/"
                "reason. Runs an LLM call — costs tokens."
            ),
            "query_params": {
                "trace_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the trace to evaluate. **How to get it:** "
                        "call `list_traces` and copy the 'id'."
                    ),
                },
                "model": {
                    "type": str,
                    "required": False,
                    "description": "Eval model (default turing_large).",
                },
                "pass_context": {
                    "type": bool,
                    "required": False,
                    "description": "Pass full trace context to the evaluator.",
                },
            },
        }
    },
)(TraceEvalView)


# ---------------------------------------------------------------------------
# Phase 3A — destructive views (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py). execution_policy pinned explicitly for grep.
# ---------------------------------------------------------------------------


def _preview_bulk_delete_eval_templates(params: dict, context) -> str:
    from model_hub.models.choices import OwnerChoices
    from model_hub.models.evals_metric import EvalTemplate

    ids = params.get("template_ids") or []
    templates = list(
        EvalTemplate.objects.filter(
            id__in=ids,
            organization=context.organization,
            owner=OwnerChoices.USER.value,
            deleted=False,
        ).values_list("name", "id")
    )
    lines = [
        f"Will delete **{len(templates)} user-owned eval template(s)** "
        f"(of {len(ids)} requested; system templates are never deleted):"
    ]
    for name, tid in templates[:10]:
        lines.append(f"- '{name}' (`{str(tid)[:8]}…`)")
    if len(templates) > 10:
        lines.append(f"- … and {len(templates) - 10} more")
    skipped = len(ids) - len(templates)
    if skipped > 0:
        lines.append(
            f"({skipped} requested id(s) are not deletable user templates "
            "in this organization and will be ignored.)"
        )
    lines.append("")
    lines.append("This cannot be undone.")
    return "\n".join(lines)


# bulk_delete_eval_templates -> EvalTemplateBulkDeleteView.post (APIView verb
# handler; EvalTemplateBulkDeleteRequestSerializer auto-resolves: template_ids).
expose_to_mcp(
    category="evaluations",
    tools={
        "post": {
            "name": "bulk_delete_eval_templates",
            "method": "POST",
            "entity": "eval template",
            "execution_policy": "destructive",
            "confirm_preview": _preview_bulk_delete_eval_templates,
            "description": (
                "Bulk delete user-owned eval templates by id (soft delete; "
                "system templates are skipped). DESTRUCTIVE: requires user "
                "confirmation (preview first, then re-call with "
                "confirm=true)."
            ),
        },
    },
)(EvalTemplateBulkDeleteView)
