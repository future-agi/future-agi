"""Bridge registration for tracer ViewSets — traces, spans, sessions,
eval tasks, alert monitors, span attributes, trace annotations.
Tool names auto-derived for CRUD; custom @actions individually wired
(Phase 2A Packet D).

Packet D adjudications (documented skips — do NOT register):
- Ingestion endpoints (`TraceView.bulk_create`, `ObservationSpanView.
  bulk_create`, `create_otel_span`): SDK ingestion surface, not chat tools.
- UI-internal list variants (`retrieve_loading`, `list_spans_observe`,
  `get_trace_id_by_index_observe`, `get_trace_id_by_index_spans_as_*`):
  duplicate the bridged list/index tools with frontend-pagination quirks —
  default-skip per spec.
- `TraceAnnotationView` CRUD: every CRUD handler returns 405 ("deprecated,
  use bulk-annotation"); only `get_annotation_values` is live — bridging
  dead handlers would ship six always-failing tools. The write path is
  `submit_bulk_annotations` (BulkAnnotationView), bridged below.
- `EvalTaskView.mark_eval_tasks_deleted`: bridged in Phase 3A as
  `bulk_delete_eval_tasks` (confirmation-gated; see end of this module).
"""

from ai_tools.drf_bridge import expose_to_mcp
from tracer.views.annotation import (
    BulkAnnotationView,
    GetAnnotationLabelsView,
    TraceAnnotationView,
)
from tracer.views.eval_task import EvalTaskView
from tracer.views.monitor import UserAlertMonitorLogView, UserAlertMonitorView
from tracer.views.observation_span import ObservationSpanView
from tracer.views.project_version import ProjectVersionView
from tracer.views.span_attributes import (
    SpanAttributeDetailView,
    SpanAttributeKeysView,
    SpanAttributeValuesView,
)
from tracer.views.trace import TraceView
from tracer.views.trace_session import TraceSessionView

# Shared doc for the JSON-encoded filter list most tracer GET endpoints take.
_FILTERS_DOC = (
    "Optional JSON-encoded filter list (the same shape the Observe UI "
    "sends); omit for no filtering."
)

# entity 'trace' -> list_traces, get_trace, etc.
expose_to_mcp(category="tracing")(TraceView)

# export_traces_csv -> TraceView.get_trace_export_data (custom @action,
# detail=False, GET): the same trace CSV export the Observe UI offers (TH-5415).
# It returns a FileResponse (text/csv) / HttpResponse, surfaced as CSV text by
# the bridge's _unwrap_response. project_id is required; filters is the optional
# JSON filter list the UI passes (omit it to export all traces in the project).
expose_to_mcp(
    category="tracing",
    tools={
        "get_trace_export_data": {
            "name": "export_traces_csv",
            "method": "GET",
            "description": (
                "Export a trace project's traces as CSV (the same export the "
                "Observe UI offers). Provide `project_id`; omit `filters` to "
                "export all traces. Returns CSV text."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": "UUID of the trace project to export.",
                },
                "filters": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional JSON-encoded filter list (same shape the "
                        "Observe UI sends); omit to export all traces."
                    ),
                },
            },
        }
    },
)(TraceView)

# TraceView custom @actions (Packet D). update_trace_tags REPLACES the
# retired hand-written add_trace_tags/remove_trace_tags/list_trace_tags
# (read tags via get_trace; write the full list here — no thin aliases).
expose_to_mcp(
    category="tracing",
    tools={
        "update_tags": {
            "name": "update_trace_tags",
            "description": (
                "Replace a trace's tags with the given list (the UI's tag "
                "editor). To add or remove a tag: call `get_trace` to read "
                "the current tags, edit the list, then submit the FULL "
                "resulting list here. An empty list clears all tags."
            ),
            "pk_field": "trace_id",
            "id_source": "list_traces",
        },
        "get_properties": {
            "name": "get_trace_properties",
            "description": (
                "List the aggregation properties available for trace "
                "graphing (Count, Average, Sum, P50/P75/P95, etc.)."
            ),
        },
        "get_eval_names": {
            "name": "get_trace_eval_names",
            "description": (
                "List the evaluation configs that actually have data for an "
                "observe project — names, ids, output types and choices. "
                "Use it to discover which evals can be graphed/filtered on, "
                "or to check whether an eval config exists for a project."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project. **How to get it:** "
                        "call `list_trace_projects`."
                    ),
                },
                "name": {
                    "type": str,
                    "required": False,
                    "description": "Optional case-insensitive name filter.",
                },
            },
        },
        "get_graph_methods": {
            "name": "get_trace_graph_methods",
            "description": (
                "Fetch time-series graph data for an observe project — one "
                "metric (eval, annotation, or system metric) bucketed by "
                "interval. The trace-level observe graph."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
                "req_data_config": {
                    "type": dict,
                    "required": True,
                    "description": (
                        "Metric selector: {\"type\": \"SYSTEM_METRIC\"|"
                        "\"EVAL\"|\"ANNOTATION\", \"id\": <metric name / "
                        "eval config id / label id>}; eval metrics may add "
                        "\"output_type\"/\"choices\"/\"value\"."
                    ),
                },
                "interval": {
                    "type": str,
                    "required": False,
                    "description": "Bucket size: hour, day (default), week, or month.",
                },
                "property": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Aggregation property (default 'average'; see "
                        "get_trace_properties)."
                    ),
                },
                "filters": {"type": list, "required": False, "description": _FILTERS_DOC},
            },
        },
        "compare_traces": {
            "name": "compare_traces",
            "description": (
                "Compare traces across project versions (experiment runs): "
                "per-version trace at the given index with node type, "
                "latency, cost, spans and eval results side by side."
            ),
            "query_params": {
                "project_version_ids": {
                    "type": list,
                    "required": True,
                    "description": (
                        "List of project version UUIDs to compare. **How to "
                        "get them:** call `list_project_versions`."
                    ),
                },
                "index": {
                    "type": int,
                    "required": False,
                    "description": "0-based trace index to compare (default 0).",
                },
            },
        },
        "get_trace_id_by_index": {
            "name": "get_trace_id_by_index",
            "description": (
                "Get the previous/next trace ids around a given trace for "
                "prev/next navigation within a project version."
            ),
            "query_params": {
                "trace_id": {
                    "type": str,
                    "required": True,
                    "description": "UUID of the current trace.",
                },
                "project_version_id": {
                    "type": str,
                    "required": True,
                    "description": "UUID of the project version the trace belongs to.",
                },
                "filters": {"type": str, "required": False, "description": _FILTERS_DOC},
            },
        },
        "list_traces_of_session": {
            "name": "list_session_traces",
            "description": (
                "List traces for an observe/experiment project (optionally "
                "scoped to one session) with latency, cost, token and eval "
                "columns — the Observe trace table. Omit project_id for an "
                "org-wide listing across all projects."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "UUID of the observe/experiment project "
                        "(`list_trace_projects`). Omit for org-wide."
                    ),
                },
                "session_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional session UUID to scope to one session "
                        "(`list_sessions`)."
                    ),
                },
                "project_version_id": {
                    "type": str,
                    "required": False,
                    "description": "Optional project version UUID filter.",
                },
                "filters": {"type": str, "required": False, "description": _FILTERS_DOC},
                "page_number": {
                    "type": int,
                    "required": False,
                    "description": "0-indexed page number (default 0).",
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Rows per page, 1-500 (default 30).",
                },
            },
        },
        "list_voice_calls": {
            "name": "list_voice_calls",
            "description": (
                "List voice/conversation call traces for a project with "
                "call-level fields (status, duration, system metrics) — the "
                "Voice Calls table. Use `get_voice_call_detail` for one "
                "call's heavy fields."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
                "filters": {"type": str, "required": False, "description": _FILTERS_DOC},
                "page": {
                    "type": int,
                    "required": False,
                    "description": "1-based page number (default 1).",
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Rows per page, 1-500 (default 30).",
                },
                "remove_simulation_calls": {
                    "type": bool,
                    "required": False,
                    "description": "When true, exclude simulation-generated calls.",
                },
            },
        },
        "voice_call_detail": {
            "name": "get_voice_call_detail",
            "description": (
                "Get the heavy detail fields for a single voice call trace: "
                "transcript/messages, per-stage latencies (transcriber/"
                "model/voice), evals and metadata."
            ),
            "query_params": {
                "trace_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the voice call trace. **How to get it:** "
                        "call `list_voice_calls` and copy the trace id."
                    ),
                },
            },
        },
        "agent_graph": {
            "name": "get_agent_graph",
            "description": (
                "Get the aggregate agent graph for a project: nodes "
                "(distinct span types/names) and edges (parent-to-child "
                "transitions) computed across all traces, with per-node "
                "metrics."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
                "filters": {"type": str, "required": False, "description": _FILTERS_DOC},
            },
        },
    },
)(TraceView)

# entity 'observation_span' -> list_observation_spans, get_observation_span
# but existing tools call them 'list_spans', 'get_span' — override the names.
expose_to_mcp(
    category="tracing",
    tools={
        "list": {"name": "list_spans"},
        "retrieve": {"name": "get_span"},
    },
)(ObservationSpanView)

# ObservationSpanView custom @actions (Packet D).
# get_span_eval_attributes replaces the retired hand-written
# get_project_eval_attributes; delete_span_annotation_label is the live
# replacement surface for the long-unregistered delete_trace_annotation.
expose_to_mcp(
    category="tracing",
    tools={
        "root_spans": {
            "name": "list_root_spans",
            "description": (
                "Given trace ids, return each trace's root span id (the "
                "span with no parent). Useful before span-level operations "
                "that need the root span."
            ),
            "query_params": {
                "trace_ids": {
                    "type": list,
                    "required": True,
                    "description": (
                        "List of trace UUIDs (from `list_traces` / "
                        "`list_session_traces`)."
                    ),
                },
            },
        },
        "submit_feedback": {
            "name": "submit_span_feedback",
            "description": (
                "Submit human feedback on an eval result for a span: agree/"
                "disagree value plus optional explanation and improvement "
                "notes. The span must have a logged result for the given "
                "eval config."
            ),
            "serializer": "SubmitFeedbackSerializer",
        },
        "update_tags": {
            "name": "update_span_tags",
            "description": (
                "Replace an observation span's tags with the given list. "
                "Read current tags via `get_span`, edit, then submit the "
                "FULL list. An empty list clears all tags."
            ),
            "query_params": {
                "span_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "ID of the observation span (from `list_spans`)."
                    ),
                },
                "tags": {
                    "type": list,
                    "required": True,
                    "description": "The full replacement list of tag strings.",
                },
            },
        },
        "get_graph_methods": {
            "name": "list_span_graph_methods",
            "description": (
                "Fetch span-level time-series graph data for an observe "
                "project — one metric (eval, annotation, or system metric) "
                "bucketed by interval. Span-level counterpart of "
                "get_trace_graph_methods."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
                "req_data_config": {
                    "type": dict,
                    "required": True,
                    "description": (
                        "Metric selector: {\"type\": \"SYSTEM_METRIC\"|"
                        "\"EVAL\"|\"ANNOTATION\", \"id\": <metric name / "
                        "eval config id / label id>}."
                    ),
                },
                "interval": {
                    "type": str,
                    "required": False,
                    "description": "Bucket size: hour, day (default), week, or month.",
                },
                "property": {
                    "type": str,
                    "required": False,
                    "description": "Aggregation property (default 'average').",
                },
                "filters": {"type": list, "required": False, "description": _FILTERS_DOC},
            },
        },
        "get_span_attributes_list": {
            "name": "get_span_attributes_list",
            "description": (
                "List the distinct span-attribute keys present in a "
                "project's spans — the attributes available for filtering "
                "and graphing."
            ),
            "query_params": {
                # dict, not str: the bridge JSON-encodes dict query params,
                # and the LLM-side _clean_params parses stringified JSON
                # back to a dict — a str type here could never validate.
                "filters": {
                    "type": dict,
                    "required": True,
                    "description": (
                        "Project scope object: "
                        "{\"project_id\": \"<uuid>\"} "
                        "(`list_trace_projects` for the uuid)."
                    ),
                },
            },
        },
        "get_eval_attributes_list": {
            "name": "get_span_eval_attributes",
            "description": (
                "List the attribute paths the eval picker exposes per row "
                "type: span attribute keys for spans/voiceCalls, trace "
                "fields + spans.<n>.<key> for traces, session fields for "
                "sessions. Use when configuring eval mappings."
            ),
            "query_params": {
                # dict, not str — see get_span_attributes_list above.
                "filters": {
                    "type": dict,
                    "required": True,
                    "description": (
                        "Project scope object: "
                        "{\"project_id\": \"<uuid>\"} "
                        "(`list_trace_projects` for the uuid)."
                    ),
                },
                "row_type": {
                    "type": str,
                    "required": False,
                    "description": (
                        "One of spans (default), traces, sessions, voiceCalls."
                    ),
                },
            },
        },
        "get_observation_span_fields": {
            "name": "get_observation_span_fields",
            "description": (
                "List the observation-span model fields with their data "
                "types (text/json/float/...) — the fields available to "
                "eval mappings and filters."
            ),
        },
        "get_evaluation_details": {
            "name": "get_span_evaluation_details",
            "description": (
                "Get the full eval result detail for one span x one eval "
                "config: score/verdict, explanation, and metadata."
            ),
            "query_params": {
                "observation_span_id": {
                    "type": str,
                    "required": True,
                    "description": "ID of the observation span (`list_spans`).",
                },
                "custom_eval_config_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the eval config (`list_custom_eval_configs` "
                        "or `get_trace_eval_names`)."
                    ),
                },
            },
        },
        "get_spans_export_data": {
            "name": "get_spans_export_data",
            "description": (
                "Export a project's observe span table as CSV text (the "
                "same export the Observe UI offers). NOTE: limited to the "
                "first page post-CH-migration; large exports are a tracked "
                "follow-up."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
                "filters": {"type": str, "required": False, "description": _FILTERS_DOC},
            },
        },
        "add_annotations": {
            "name": "add_span_annotations",
            "description": (
                "Add annotation values to a span (or to a trace's root span "
                "when only trace_id is given). annotation_values maps "
                "annotation label UUIDs to values valid for each label's "
                "type (text/score/boolean/choices). Get label ids from "
                "`get_annotation_labels`."
            ),
        },
        "delete_annotation_label": {
            "name": "delete_span_annotation_label",
            "description": (
                "Delete a trace/span annotation label by id (refused while "
                "the label is still used by active annotation tasks). Also "
                "soft-deletes the label's scores. Get label ids from "
                "`get_annotation_labels`."
            ),
            "query_params": {
                "label_id": {
                    "type": str,
                    "required": True,
                    "in": "query",
                    "description": (
                        "UUID of the annotation label to delete "
                        "(`get_annotation_labels`)."
                    ),
                },
            },
        },
    },
)(ObservationSpanView)

# Trace sessions: list_sessions, get_session (get_session was deleted as
# composite — bridge gives us a clean REST replacement)
expose_to_mcp(
    category="tracing",
    tools={
        "list": {"name": "list_sessions"},
        "retrieve": {"name": "get_session"},
    },
)(TraceSessionView)

# Eval tasks: list_eval_tasks, get_eval_task, update_eval_task
expose_to_mcp(
    category="tracing",
    tools={
        "list": {
            "name": "list_eval_tasks",
            # TH-4667: EvalTaskView.get_queryset filters by the `name` query
            # param (name__icontains), not `search` — remap so the advertised
            # search actually filters. page/page_size are auto-detected from
            # the DRF paginator (page + limit).
            "list_params": {"search": "name"},
        },
        "retrieve": {"name": "get_eval_task"},
        "create": {"name": "create_eval_task"},
        "update": {"name": "update_eval_task"},
        "destroy": {"name": "delete_eval_task"},
        # Per-span eval scores/verdicts — readable after a task completes
        # (TH-5411). EvalTaskView.get_usage is a detail=False GET keyed by the
        # eval_task_id query param, so the bridge treats it as a non-detail GET
        # (query params, no `id`). Bridges the existing API; no custom tool.
        "get_usage": {
            "name": "get_eval_task_results",
            "method": "GET",
            "detail": False,
            "description": (
                "Read the results of an eval task — the per-span scores and "
                "verdicts the task produced. Use after a task completes (or "
                "while running) to inspect actual outputs, not just status. "
                "Provide eval_task_id (from list_eval_tasks / create_eval_task). "
                "Optional: eval_id to filter to one eval when the task ran "
                "several; span_aggregation=true for a task-wide rollup instead "
                "of per-span rows."
            ),
            "query_params": {
                "eval_task_id": {
                    "type": str,
                    "required": True,
                    "description": "The eval task id (from list_eval_tasks).",
                },
                "eval_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Filter to one eval config when the task ran multiple."
                    ),
                },
                "span_aggregation": {
                    "type": bool,
                    "required": False,
                    "description": (
                        "When true, return the task-wide aggregated rollup "
                        "instead of per-span rows."
                    ),
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Rows per page (per-span mode).",
                },
            },
        },
        # Packet D: remaining EvalTaskView @actions. get_eval_task_logs and
        # pause_eval_task are SAME-NAME conversions of the retired
        # hand-written tools (ai_tools/tools/tracing/) onto the real DRF
        # handlers; unpause_eval_task closes a gap (no tool existed at all).
        "get_eval_task_logs": {
            "name": "get_eval_task_logs",
            "description": (
                "Get an eval task's execution log summary: pass/fail/"
                "warning counts plus error groups (each distinct error type "
                "with a count and a sample message). Use it to diagnose why "
                "an eval task is failing."
            ),
            "query_params": {
                "eval_task_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the eval task (from `list_eval_tasks`)."
                    ),
                },
            },
        },
        "pause_eval_task": {
            "name": "pause_eval_task",
            "description": (
                "Pause a RUNNING eval task. Paused tasks can be resumed "
                "with `unpause_eval_task`."
            ),
            "query_params": {
                "eval_task_id": {
                    "type": str,
                    "required": True,
                    "in": "query",
                    "description": (
                        "UUID of the running eval task (`list_eval_tasks`)."
                    ),
                },
            },
        },
        "unpause_eval_task": {
            "name": "unpause_eval_task",
            "description": (
                "Resume a PAUSED eval task: status returns to pending and "
                "processing restarts from offset 0."
            ),
            "query_params": {
                "eval_task_id": {
                    "type": str,
                    "required": True,
                    "in": "query",
                    "description": (
                        "UUID of the paused eval task (`list_eval_tasks`)."
                    ),
                },
            },
        },
        "list_eval_tasks_with_project_name": {
            "name": "list_eval_tasks_with_project_name",
            "description": (
                "List eval tasks WITH their project names resolved — the "
                "cross-project eval tasks table. Filterable by project and "
                "task name."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": False,
                    "description": "Optional project UUID filter.",
                },
                "name": {
                    "type": str,
                    "required": False,
                    "description": "Optional case-insensitive task name filter.",
                },
                "filters": {"type": str, "required": False, "description": _FILTERS_DOC},
                "page_number": {
                    "type": int,
                    "required": False,
                    "description": "0-indexed page number (default 0).",
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Rows per page, 1-500 (default 10).",
                },
            },
        },
        "get_eval_details": {
            "name": "get_eval_details",
            "description": (
                "Get an eval task's configuration detail: the rich eval "
                "config objects it runs (name, template, mapping, model, "
                "output type) plus task filters and status."
            ),
            "query_params": {
                "eval_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the EVAL TASK (despite the param name) — "
                        "from `list_eval_tasks`."
                    ),
                },
            },
        },
        # mark_eval_tasks_deleted — bridged in the Phase 3A block below
        # as bulk_delete_eval_tasks (confirmation-gated).
    },
)(EvalTaskView)

# Span attribute APIViews (Packet D) — ClickHouse-backed attribute
# discovery for the observe filter/eval pickers.
expose_to_mcp(
    category="tracing",
    tools={
        "get": {
            "name": "list_span_attribute_keys",
            "method": "GET",
            "description": (
                "List the span attribute keys observed in a project's spans "
                "with their detected type (string/number/boolean) and "
                "occurrence count."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
            },
        },
    },
)(SpanAttributeKeysView)

expose_to_mcp(
    category="tracing",
    tools={
        "get": {
            "name": "list_span_attribute_values",
            "method": "GET",
            "description": (
                "List the distinct values of one span attribute key in a "
                "project, with optional substring search — for building "
                "attribute filters."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
                "key": {
                    "type": str,
                    "required": True,
                    "description": (
                        "The span attribute key (from "
                        "`list_span_attribute_keys`)."
                    ),
                },
                "q": {
                    "type": str,
                    "required": False,
                    "description": "Optional value substring filter.",
                },
                "limit": {
                    "type": int,
                    "required": False,
                    "description": "Max values to return (1-500).",
                },
            },
        },
    },
)(SpanAttributeValuesView)

expose_to_mcp(
    category="tracing",
    tools={
        "get": {
            "name": "get_span_attribute_detail",
            "method": "GET",
            "description": (
                "Get the detail/statistics for one span attribute key in a "
                "project: detected type plus value distribution (string) or "
                "min/max/avg (number) or true/false counts (boolean)."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the observe project (`list_trace_projects`)."
                    ),
                },
                "key": {
                    "type": str,
                    "required": True,
                    "description": (
                        "The span attribute key (from "
                        "`list_span_attribute_keys`)."
                    ),
                },
            },
        },
    },
)(SpanAttributeDetailView)

# Trace annotations (Packet D). TraceAnnotationView CRUD is deprecated
# (every handler returns 405) — only the read @action is bridged; writes go
# through BulkAnnotationView / add_span_annotations.
expose_to_mcp(
    category="annotations",
    tools={
        "get_annotation_values": {
            "name": "get_annotation_values",
            "description": (
                "Read the human annotation values recorded on a span or "
                "trace — per-label values with annotator emails — plus any "
                "span notes. Provide observation_span_id or trace_id."
            ),
            "query_params": {
                "observation_span_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "ID of the observation span (from `list_spans`); "
                        "give this OR trace_id."
                    ),
                },
                "trace_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "UUID of the trace (from `list_traces`); give this "
                        "OR observation_span_id."
                    ),
                },
                "annotators": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional JSON-encoded list of annotator user UUIDs "
                        "to include."
                    ),
                },
                "exclude_annotators": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional JSON-encoded list of annotator user UUIDs "
                        "to exclude."
                    ),
                },
            },
        },
    },
)(TraceAnnotationView)

expose_to_mcp(
    category="annotations",
    tools={
        "post": {
            "name": "submit_bulk_annotations",
            "method": "POST",
            "description": (
                "Submit annotation values and/or notes for up to 1000 spans "
                "in one call. records: [{\"observation_span_id\": <span id>, "
                "\"annotations\": [{\"annotation_label_name\": <label "
                "name>, \"value\": <value valid for the label type>}], "
                "\"notes\": [{\"text\": ...}]}]. Get label names from "
                "`get_annotation_labels`."
            ),
        },
    },
)(BulkAnnotationView)

expose_to_mcp(
    category="annotations",
    tools={
        "get": {
            "name": "get_annotation_labels",
            "method": "GET",
            "description": (
                "List the trace/span annotation labels visible in the "
                "workspace (optionally scoped to one project): id, name, "
                "type (text/score/boolean/choices) and settings. These are "
                "the labels `add_span_annotations` / "
                "`submit_bulk_annotations` write against."
            ),
            "query_params": {
                "project_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional project UUID to scope labels to one "
                        "project (`list_trace_projects`)."
                    ),
                },
            },
        },
    },
)(GetAnnotationLabelsView)

# Alert monitors: list_alert_monitors, create_alert_monitor, etc.
expose_to_mcp(
    category="tracing",
    tools={
        "list": {
            "name": "list_alert_monitors",
            # TH-4667: UserAlertMonitorView reads `search_text` (get_queryset)
            # and a manual `page_size` — remap. `page` is deliberately NOT
            # advertised: the view's `page_number` is 0-indexed, which
            # contradicts the advertised 1-indexed semantics.
            "list_params": {"search": "search_text", "page_size": "page_size"},
        },
        "retrieve": {"name": "get_alert_monitor"},
        "create": {
            "name": "create_alert_monitor",
            # Enumerate the valid metric_type values in the description so the
            # model picks a real one up front instead of guessing (e.g.
            # "error_rate") and hitting an opaque VALIDATION_ERROR (TH-5406).
            # Values come from MonitorMetricTypeChoices.
            "description": (
                "Create an alert monitor on a project's telemetry. `metric_type` "
                "MUST be one of: count_of_errors, error_rates_for_function_calling, "
                "error_free_session_rates, service_provider_error_rates, "
                "llm_api_failure_rates, span_response_time, llm_response_time, "
                "token_usage, daily_tokens_spent, monthly_tokens_spent, "
                "evaluation_metrics. Pair it with a threshold + "
                "threshold_operator (e.g. greater_than) and the project."
            ),
        },
        "update": {"name": "update_alert_monitor"},
        "destroy": {"name": "delete_alert_monitor"},
    },
)(UserAlertMonitorView)

expose_to_mcp(
    category="tracing",
    tools={
        "list": {"name": "list_alert_monitor_logs"},
        "retrieve": {"name": "get_alert_monitor_log"},
    },
)(UserAlertMonitorLogView)

# Project versions (experiments use these)
expose_to_mcp(category="experiments")(ProjectVersionView)


# ---------------------------------------------------------------------------
# Phase 3A — destructive @actions (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py). execution_policy pinned explicitly for grep.
# ---------------------------------------------------------------------------


def _preview_bulk_delete_eval_tasks(params: dict, context) -> str:
    from tracer.models.eval_task import EvalTask, EvalTaskStatus

    ids = params.get("eval_task_ids") or []
    tasks = list(
        EvalTask.objects.filter(id__in=ids).values_list("name", "id", "status")
    )
    lines = [
        f"Will mark **{len(tasks)} eval task(s)** as deleted "
        f"(of {len(ids)} requested), together with their task/eval logs:"
    ]
    for name, tid, task_status in tasks[:10]:
        lines.append(f"- '{name}' (`{str(tid)[:8]}…`, status: {task_status})")
    if len(tasks) > 10:
        lines.append(f"- … and {len(tasks) - 10} more")
    running = [n for n, _, s in tasks if s == EvalTaskStatus.RUNNING]
    if running:
        lines.append(
            f"NOTE: {len(running)} task(s) are RUNNING and will be rejected "
            "by the API — pause them first."
        )
    lines.append("")
    lines.append("This cannot be undone.")
    return "\n".join(lines)


def _preview_delete_project_version_runs(params: dict, context) -> str:
    from tracer.models.project_version import ProjectVersion

    ids = params.get("ids") or []
    versions = list(
        ProjectVersion.objects.filter(id__in=ids).values_list(
            "name", "id", "project__name"
        )
    )
    lines = [
        f"Will delete **{len(versions)} project version run(s)** "
        f"(of {len(ids)} requested), including their traces/metrics tree:"
    ]
    for name, vid, project_name in versions[:10]:
        lines.append(
            f"- '{name}' (`{str(vid)[:8]}…`) in project '{project_name}'"
        )
    if len(versions) > 10:
        lines.append(f"- … and {len(versions) - 10} more")
    lines.append("")
    lines.append("This cannot be undone.")
    return "\n".join(lines)


# bulk_delete_eval_tasks -> EvalTaskView.mark_eval_tasks_deleted (POST,
# detail=False; EvalTaskDeleteRequestSerializer auto-resolves: eval_task_ids).
expose_to_mcp(
    category="tracing",
    tools={
        "mark_eval_tasks_deleted": {
            "name": "bulk_delete_eval_tasks",
            "entity": "eval task",
            "execution_policy": "destructive",
            "confirm_preview": _preview_bulk_delete_eval_tasks,
            "description": (
                "Bulk delete eval tasks by id (marks the tasks and their "
                "logs as deleted; running tasks are rejected — pause them "
                "first). DESTRUCTIVE: requires user confirmation (preview "
                "first, then re-call with confirm=true)."
            ),
        },
    },
)(EvalTaskView)

# delete_project_version_runs -> ProjectVersionView.delete_runs (POST,
# detail=False, raw request.data {"ids": [...]} — no serializer, so the
# input shape is declared via query_params (routed to the POST body).
expose_to_mcp(
    category="tracing",
    tools={
        "delete_runs": {
            "name": "delete_project_version_runs",
            "entity": "project version",
            "execution_policy": "destructive",
            "confirm_preview": _preview_delete_project_version_runs,
            "query_params": {
                "ids": {
                    "type": list[str],
                    "required": True,
                    "description": (
                        "List of project version (run) UUIDs to delete "
                        "(from `list_project_versions`)."
                    ),
                },
            },
            "description": (
                "Delete project version runs (prototype runs) by id, "
                "soft-deleting each version's run tree. DESTRUCTIVE: "
                "requires user confirmation (preview first, then re-call "
                "with confirm=true)."
            ),
        },
    },
)(ProjectVersionView)
