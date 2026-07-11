"""Bridge registration for the prompt-template cluster (Phase 2A Packet B).

Covers PromptTemplateViewSet CRUD + its custom @actions, plus the sibling
views PromptExecutionViewSet, PromptHistoryExecutionViewSet and
ColumnValuesAPIView. Method/detail for @actions are auto-derived from the
DRF @action decorator (drf_bridge A1); pk routing uses the standard
ViewSet lookup (kwargs["pk"]) with the input field named ``template_id``
to match the legacy hand-written tools.

Same-name conversions (legacy hand-written modules in ai_tools/tools/prompts/
deleted in the same change):
  compare_prompt_versions  -> compare_versions @action
  create_prompt_version    -> add_new_draft @action (adjudication: the legacy
      tool created a committed version via prompt_service; add_new_draft
      creates DRAFT versions — same capability, the draft is then committed
      with commit_prompt_version, which matches the product flow)
  run_prompt               -> run_template @action
  get_prompt_eval_configs  -> get_evaluation_configs @action
  run_prompt_evals         -> run_evals_on_multiple_versions @action
  list_prompt_versions     -> versions @action
  commit_prompt_version    -> commit @action
  get_prompt_execution_results -> PromptExecutionViewSet.list (the legacy
      tool read EE APICallLog rows ORM-direct; the DRF surface for prompt
      execution state is this viewset — templates with their latest
      version/output prefetched)

Deliberately NOT bridged:
  - PromptTemplateViewSet.bulk_delete — bridged in Phase 3A as
    bulk_delete_prompt_templates (confirmation-gated; end of this module).
  - PromptTemplateViewSet.stop_streaming — UI websocket streaming control.

TODO: when PromptTemplateViewSet.list grows a @validated_request(
query_serializer=PromptTemplateListRequestSerializer), the bridge will
auto-discover the search/page/page_size/ordering params and this file can
remove the list query_params block too.
"""

from ai_tools.drf_bridge import expose_to_mcp
from model_hub.views.prompt_template import (
    ColumnValuesAPIView,
    PromptExecutionViewSet,
    PromptHistoryExecutionViewSet,
    PromptTemplateViewSet,
)

_PROMPT_CONFIG_ITEM_DOC = (
    "Each item: {'messages': [{'role': 'system'|'user'|'assistant', 'content': "
    "[{'text': '...', 'type': 'text'}]}], 'configuration': {'model': '<model>', "
    "'temperature': 0.7, 'max_tokens': 1000, 'response_format': 'text'}, "
    "'placeholders': []}."
)

expose_to_mcp(
    category="prompts",
    tools={
        "list": {
            "query_params": {
                "search": {
                    "type": str,
                    "description": (
                        "Filter by template name (case-insensitive substring "
                        "match). Example: 'summari' matches 'summarization-v3'."
                    ),
                    "required": False,
                },
                "page": {
                    "type": int,
                    "default": 1,
                    "description": "Page number, 1-indexed.",
                    "required": False,
                },
                "page_size": {
                    "type": int,
                    "default": 20,
                    "description": "Number of templates per page. Range 1-100.",
                    "required": False,
                    # TH-4667: the paginator reads `limit`; without this
                    # remap page_size was silently ignored (always 10 rows).
                    "actual": "limit",
                },
                "ordering": {
                    "type": str,
                    "description": (
                        "Sort order. One of: 'name', '-name', 'created_at', "
                        "'-created_at'. Prefix with '-' for descending."
                    ),
                    "required": False,
                },
            },
        },
        "retrieve": {},
        "create": {},
        "update": {},
        "destroy": {},
        # ------------------------------------------------------------------
        # Custom @actions — net-new (Packet B)
        # ------------------------------------------------------------------
        "get_template_by_name": {
            "name": "get_prompt_template_by_name",
            "description": (
                "Get a prompt template by its exact name (not UUID). Returns "
                "the template plus the requested version's prompt_config, "
                "variables and output — the default version if no version is "
                "given. Use list_prompt_templates to discover names."
            ),
            "query_params": {
                "name": {
                    "type": str,
                    "required": True,
                    "description": "Exact template name (case-sensitive).",
                },
                "version": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional version label like 'v2'. Omit for the "
                        "default version."
                    ),
                },
            },
        },
        "get_all_variables": {
            "name": "get_prompt_template_variables",
            "pk_field": "template_id",
            "id_source": "list_prompt_templates",
            "description": (
                "Get the {{variable}} names (and their stored sample values) "
                "defined on a prompt template. Call before run_prompt to know "
                "which variable_names to supply."
            ),
        },
        "get_next_version": {
            "name": "get_prompt_next_version",
            "pk_field": "template_id",
            "id_source": "list_prompt_templates",
            "description": (
                "Get the next version label (e.g. 'v4') that a new draft of "
                "this prompt template would receive."
            ),
        },
        "create_draft": {
            "name": "create_prompt_draft",
            "description": (
                "Create a brand-new prompt template with an initial v1 draft "
                "version. Use this to start a new prompt from scratch; use "
                "create_prompt_version to add a draft to an EXISTING template. "
                "Returns the new template id and v1 details."
            ),
            "query_params": {
                "prompt_config": {
                    "type": list,
                    "required": True,
                    "description": (
                        "Prompt configuration array (the first item is used). "
                        + _PROMPT_CONFIG_ITEM_DOC
                    ),
                },
                "name": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Template name. Omitted -> auto-named 'Untitled-N'."
                    ),
                },
                "description": {
                    "type": str,
                    "required": False,
                    "description": "Optional template description.",
                },
                "variable_names": {
                    "type": dict,
                    "required": False,
                    "description": (
                        "Variable map {'var': ['sample value', ...]} for "
                        "{{var}} placeholders in the messages."
                    ),
                },
                "is_draft": {
                    "type": bool,
                    "required": False,
                    "default": True,
                    "description": "Create v1 as a draft (default true).",
                },
                "metadata": {
                    "type": dict,
                    "required": False,
                    "description": "Optional metadata object for v1.",
                },
                "prompt_base_template": {
                    "type": str,
                    "required": False,
                    "description": "Optional base-template UUID to start from.",
                },
                "prompt_folder": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional folder UUID (from list_prompt_folders) to "
                        "create the template in."
                    ),
                },
            },
        },
        "retrieve_evaluations": {
            "name": "get_prompt_evaluations",
            "pk_field": "template_id",
            "description": (
                "Get evaluation results for one or more versions of a prompt "
                "template (scores per eval config, optionally with variables "
                "and prompts). Pass versions as a JSON array string."
            ),
            "query_params": {
                "versions": {
                    "type": list,
                    "required": True,
                    "description": (
                        "Version labels to fetch, e.g. ['v1'] or "
                        "['v1', 'v2'] (set compare=true for more than one)."
                    ),
                },
                "compare": {
                    "type": bool,
                    "required": False,
                    "description": "Set true when passing multiple versions.",
                },
                "show_var": {
                    "type": bool,
                    "required": False,
                    "description": "Include the templates' variables.",
                },
                "show_prompts": {
                    "type": bool,
                    "required": False,
                    "description": "Include the prompt configs.",
                },
            },
        },
        "get_run_status": {
            "name": "get_prompt_run_status",
            "pk_field": "template_id",
            "description": (
                "Get the current run status and outputs of a prompt template "
                "run (per version). Poll this after run_prompt with "
                "is_run=true."
            ),
            "query_params": {
                "template_version": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Version label like 'v2'. Omit for all versions."
                    ),
                },
            },
        },
        "update_evaluation_configs": {
            "name": "update_prompt_eval_configs",
            "pk_field": "template_id",
            "description": (
                "Attach an evaluation configuration to a prompt template "
                "(creates a PromptEvalConfig). Optionally run it immediately "
                "on given versions with is_run=true. Use list_eval_templates "
                "to find the eval template id."
            ),
            "query_params": {
                "id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the EVAL TEMPLATE to attach (from "
                        "list_eval_templates) — not a prompt id."
                    ),
                },
                "name": {
                    "type": str,
                    "required": True,
                    "description": (
                        "Unique name for this eval config on the template."
                    ),
                },
                "mapping": {
                    "type": dict,
                    "required": False,
                    "description": (
                        "Maps the eval template's required keys to prompt "
                        "fields, e.g. {'output': 'output', 'input': "
                        "'{{question}}'}."
                    ),
                },
                "config": {
                    "type": dict,
                    "required": False,
                    "description": "Eval runtime config overrides (optional).",
                },
                "params": {
                    "type": dict,
                    "required": False,
                    "description": "Eval parameter values (optional).",
                },
                "is_run": {
                    "type": bool,
                    "required": False,
                    "description": (
                        "Run the eval immediately after attaching it."
                    ),
                },
                "version_to_run": {
                    "type": list,
                    "required": False,
                    "description": (
                        "Version labels to run on, e.g. ['v1']. Defaults to "
                        "the latest version when is_run=true."
                    ),
                },
            },
        },
        "delete_evaluation_config": {
            "name": "delete_prompt_eval_config",
            "pk_field": "template_id",
            "description": (
                "Remove (soft-delete) an evaluation configuration from a "
                "prompt template. Get config ids from get_prompt_eval_configs."
            ),
            "query_params": {
                # the handler reads request.query_params["id"] on DELETE
                "id": {
                    "type": str,
                    "required": True,
                    "in": "query",
                    "description": (
                        "UUID of the PromptEvalConfig to delete (the 'id' "
                        "field from get_prompt_eval_configs)."
                    ),
                },
            },
        },
        "set_default": {
            "name": "set_default_prompt_version",
            "pk_field": "template_id",
            "serializer": "VersionDefaultSerializer",
            "description": (
                "Set a specific version (e.g. 'v2') of a prompt template as "
                "the default version."
            ),
        },
        "generate_prompt": {
            "name": "generate_prompt",
            "description": (
                "Generate a new prompt from a natural-language description "
                "of the task (AI prompt authoring). Returns a generation_id; "
                "the generation runs in the background."
            ),
            "query_params": {
                "statement": {
                    "type": str,
                    "required": True,
                    "description": (
                        "Description of what the prompt should do, e.g. "
                        "'summarize support tickets into one sentence'."
                    ),
                },
            },
        },
        "improve_prompt": {
            "name": "improve_prompt",
            "description": (
                "Improve an existing prompt while keeping its {{variables}} "
                "intact (AI prompt optimization; EE feature). Returns an "
                "improve_id; the improvement runs in the background."
            ),
            "query_params": {
                "existing_prompt": {
                    "type": str,
                    "required": True,
                    "description": "The current prompt text to improve.",
                },
                "improvement_requirements": {
                    "type": str,
                    "required": True,
                    "description": (
                        "What to improve, e.g. 'reduce hallucinations, keep "
                        "it under 100 words'."
                    ),
                },
            },
        },
        "analyze_prompt": {
            "name": "analyze_prompt",
            "description": (
                "Analyze a prompt against feedback/explanation and return "
                "concrete improvement suggestions (synchronous)."
            ),
            "query_params": {
                "prompt": {
                    "type": str,
                    "required": True,
                    "description": "The prompt text to analyze.",
                },
                "explanation": {
                    "type": str,
                    "required": True,
                    "description": (
                        "What is wrong / what to improve about the prompt."
                    ),
                },
                "example": {
                    "type": dict,
                    "required": False,
                    "description": (
                        "Optional example {'input': ..., 'output': ...} "
                        "illustrating the problem."
                    ),
                },
            },
        },
        "generate_variables": {
            "name": "generate_prompt_variables",
            "description": (
                "Generate synthetic sample values for prompt variables "
                "(returns {'variables': {var: [values...]}})."
            ),
            "query_params": {
                "prompt_name": {
                    "type": str,
                    "required": True,
                    "description": "Name/topic of the prompt.",
                },
                "variable_names": {
                    "type": list,
                    "required": True,
                    "description": (
                        "Variable names to generate values for, e.g. "
                        "['question', 'context']."
                    ),
                },
                "variable_count": {
                    "type": int,
                    "required": False,
                    "default": 1,
                    "description": "Number of values per variable (default 1).",
                },
                "prompt_instructions": {
                    "type": str,
                    "required": False,
                    "description": "Optional prompt instructions for context.",
                },
            },
        },
        "get_sdk_code": {
            "name": "get_prompt_sdk_code",
            "pk_field": "template_id",
            "id_source": "list_prompt_templates",
            "path_kwargs": {
                "language": {
                    "description": (
                        "Code language to generate. One of: 'python', "
                        "'typescript', 'curl', 'langchain', 'nodejs', 'go'."
                    ),
                },
            },
            "description": (
                "Get ready-to-use SDK/API code for executing a prompt "
                "template in the requested language."
            ),
        },
        "save_name": {
            "name": "save_prompt_name",
            "pk_field": "template_id",
            "description": (
                "Rename a prompt template. Fails if another template already "
                "uses the name."
            ),
            "query_params": {
                "name": {
                    "type": str,
                    "required": True,
                    "description": "New template name (must be unique).",
                },
            },
        },
        "save_prompt_folder": {
            "name": "move_prompt_to_folder",
            "pk_field": "template_id",
            "description": (
                "Move a prompt template into a prompt folder. Use "
                "list_prompt_folders to find folder ids."
            ),
            "query_params": {
                "prompt_folder_id": {
                    "type": str,
                    "required": True,
                    "description": (
                        "UUID of the destination folder (from "
                        "list_prompt_folders)."
                    ),
                },
            },
        },
        # ------------------------------------------------------------------
        # Custom @actions — same-name conversions of legacy hand-written tools
        # ------------------------------------------------------------------
        "compare_versions": {
            "name": "compare_prompt_versions",
            "pk_field": "template_id",
            "serializer": "CompareVersionsSerializer",
            "description": (
                "Compare up to 3 versions of a prompt template side by side "
                "(messages, config, outputs, eval results). Pass version "
                "labels like ['v1', 'v2']."
            ),
        },
        "add_new_draft": {
            "name": "create_prompt_version",
            "pk_field": "template_id",
            "serializer": "MultipleDraftSerializer",
            "description": (
                "Create one or more new DRAFT versions of an existing prompt "
                "template (auto-numbered v2, v3, ...). Commit a draft with "
                "commit_prompt_version; set it default with "
                "set_default_prompt_version. For a brand-new template use "
                "create_prompt_draft."
            ),
        },
        "run_template": {
            "name": "run_prompt",
            "pk_field": "template_id",
            "description": (
                "Run a prompt template version: substitutes variables, sends "
                "it to the configured LLM in the background, and updates the "
                "version's stored output. Set is_run=true to execute; poll "
                "get_prompt_run_status for results. With is_run=false it only "
                "saves config changes to a draft version."
            ),
            "query_params": {
                "version": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Version label to run, e.g. 'v2'. Omit to create/"
                        "update the working draft."
                    ),
                },
                "prompt_config": {
                    "type": list,
                    "required": False,
                    "description": (
                        "Updated prompt configuration array (first item "
                        "used). " + _PROMPT_CONFIG_ITEM_DOC
                    ),
                },
                "variable_names": {
                    "type": dict,
                    "required": False,
                    "description": (
                        "Variable values map {'var': ['value1', ...]} — each "
                        "index is one run row."
                    ),
                },
                "evaluation_configs": {
                    "type": list,
                    "required": False,
                    "description": "Evaluation configs to store on the version.",
                },
                "is_run": {
                    "type": bool,
                    "required": False,
                    "description": (
                        "true = actually execute the LLM run; false = just "
                        "save changes."
                    ),
                },
                "run_index": {
                    "type": int,
                    "required": False,
                    "description": (
                        "Run only this 0-based variable index. Omit to run "
                        "all."
                    ),
                },
                "placeholders": {
                    "type": dict,
                    "required": False,
                    "description": "Optional placeholder values.",
                },
                "source": {
                    "type": str,
                    "required": False,
                    "description": "Source tag (default 'prompt').",
                },
                "name": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Optional template rename as part of the run."
                    ),
                },
            },
        },
        "get_evaluation_configs": {
            "name": "get_prompt_eval_configs",
            "pk_field": "template_id",
            "id_source": "list_prompt_templates",
            "description": (
                "List the evaluation configurations attached to a prompt "
                "template (config id, eval template, mapping, params). The "
                "returned ids feed run_prompt_evals and "
                "delete_prompt_eval_config."
            ),
        },
        "run_evals_on_multiple_versions": {
            "name": "run_prompt_evals",
            "pk_field": "template_id",
            "description": (
                "Run configured evaluations (PromptEvalConfigs) against one "
                "or more versions of a prompt template in the background. "
                "Configure evals first with update_prompt_eval_configs, run "
                "the prompt with run_prompt, then run evals on the output."
            ),
            "query_params": {
                "prompt_eval_config_ids": {
                    "type": list,
                    "required": True,
                    "description": (
                        "PromptEvalConfig UUIDs to run (from "
                        "get_prompt_eval_configs)."
                    ),
                },
                "version_to_run": {
                    "type": list,
                    "required": False,
                    "description": (
                        "Version labels to run on, e.g. ['v1', 'v2']."
                    ),
                },
                "run_index": {
                    "type": int,
                    "required": False,
                    "description": (
                        "Run evals only for this 0-based variable index."
                    ),
                },
            },
        },
        "versions": {
            "name": "list_prompt_versions",
            "pk_field": "template_id",
            "id_source": "list_prompt_templates",
            "description": (
                "List all versions of a prompt template (newest first): "
                "version label, draft/committed state, default flag, commit "
                "message and config snapshot."
            ),
            "query_params": {
                "page": {
                    "type": int,
                    "required": False,
                    "description": "Page number, 1-indexed.",
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Versions per page.",
                    # TH-4667: the paginator reads `limit`; without this
                    # remap page_size was silently ignored.
                    "actual": "limit",
                },
            },
        },
        "commit": {
            "name": "commit_prompt_version",
            "pk_field": "template_id",
            "serializer": "CommitSerializer",
            "description": (
                "Commit a draft prompt version: adds a commit message, marks "
                "it non-draft, and optionally sets it as the default version."
            ),
        },
    },
)(PromptTemplateViewSet)

# get_prompt_execution_results -> PromptExecutionViewSet.list: templates with
# their latest execution/version state prefetched (same-name conversion; the
# legacy tool read EE APICallLog rows ORM-direct, which has no OSS endpoint).
expose_to_mcp(
    category="prompts",
    tools={
        "list": {
            "name": "get_prompt_execution_results",
            "entity": "prompt execution",
            "description": (
                "List prompt templates with their latest execution state "
                "(model, latest version/output, folder, collaborators). Use "
                "get_prompt_run_status for a single template's run detail and "
                "get_prompt_evaluations for eval scores."
            ),
            "query_params": {
                "name": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Filter by template name (case-insensitive contains)."
                    ),
                },
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Search by template name.",
                },
                "prompt_folder": {
                    "type": str,
                    "required": False,
                    "description": "Filter by prompt folder UUID.",
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "Page number, 1-indexed.",
                },
                "page_size": {
                    "type": int,
                    "required": False,
                    "description": "Items per page.",
                },
            },
        },
    },
)(PromptExecutionViewSet)

# get_prompt_execution_details -> PromptHistoryExecutionViewSet.
# get_execution_details(request, execution_id): single PromptVersion detail.
# DRF @action is detail=False with the id in the url_path regex, so the
# bridge config overrides detail and routes execution_id via pk_kwarg
# (assign_items precedent).
expose_to_mcp(
    category="prompts",
    tools={
        "get_execution_details": {
            "name": "get_prompt_execution_details",
            "detail": True,
            "pk_field": "execution_id",
            "pk_kwarg": "execution_id",
            "entity": "prompt version",
            "description": (
                "Get full detail of a single prompt version/execution by its "
                "UUID (the 'id' of a row from list_prompt_versions): config "
                "snapshot, variables, output and eval results."
            ),
        },
    },
)(PromptHistoryExecutionViewSet)

# get_prompt_column_values -> ColumnValuesAPIView.post: resolves dataset
# column placeholders ({{column_id}}) to sample values from the first rows.
# Read-shaped POST; serializer auto-recovered from @validated_request
# (ColumnValuesRequestSerializer).
expose_to_mcp(
    category="prompts",
    tools={
        "post": {
            "name": "get_prompt_column_values",
            "method": "POST",
            "description": (
                "Get sample values for dataset columns referenced by a "
                "prompt's column placeholders. Pass the dataset UUID and a "
                "mapping of placeholder key -> column UUID; returns up to 10 "
                "row values per column."
            ),
        },
    },
)(ColumnValuesAPIView)


# ---------------------------------------------------------------------------
# Phase 3A — destructive @actions (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py). execution_policy is auto-derived but pinned
# explicitly for greppability.
# ---------------------------------------------------------------------------


def _preview_bulk_delete_prompt_templates(params: dict, context) -> str:
    """Confirmation preview: resolve template names (workspace/org-scoped)."""
    from model_hub.models.run_prompt import PromptTemplate

    ids = params.get("ids") or []
    templates = list(
        PromptTemplate.objects.filter(id__in=ids).values_list("name", "id")
    )
    lines = [
        f"Will permanently soft-delete **{len(templates)} prompt template(s)** "
        f"(of {len(ids)} requested):"
    ]
    for name, tid in templates[:10]:
        lines.append(f"- '{name}' (`{str(tid)[:8]}…`)")
    if len(templates) > 10:
        lines.append(f"- … and {len(templates) - 10} more")
    missing = len(ids) - len(templates)
    if missing > 0:
        lines.append(
            f"({missing} requested id(s) do not match any template in this "
            "workspace and will be ignored.)"
        )
    lines.append("")
    lines.append("This cannot be undone.")
    return "\n".join(lines)


expose_to_mcp(
    category="prompts",
    tools={
        "bulk_delete": {
            "name": "bulk_delete_prompt_templates",
            "entity": "prompt template",
            "execution_policy": "destructive",
            "confirm_preview": _preview_bulk_delete_prompt_templates,
            "query_params": {
                "ids": {
                    "type": list[str],
                    "required": True,
                    "description": (
                        "List of prompt template UUIDs to delete (from "
                        "`list_prompt_templates`)."
                    ),
                },
            },
            "description": (
                "Bulk delete prompt templates by id. DESTRUCTIVE: requires "
                "user confirmation (first call returns a preview; re-call "
                "with confirm=true after the user approves)."
            ),
        },
    },
)(PromptTemplateViewSet)
