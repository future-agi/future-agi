# Import all tool modules to trigger @register_tool decorators.
# This is called from AiToolsConfig.ready().

# Agent tools (8)
# DRF Bridge tools — auto-registered via @expose_to_mcp on ViewSet classes.
# Importing the views module triggers registration.
# list_workspaces, list_users — @expose_to_mcp lives directly on
# WorkspaceListAPIView / UserListAPIView in accounts/views/workspace_management.py
import accounts.views.workspace_management  # noqa: F401
import tracer.views.project  # noqa: F401  — registers tracing_* bridge tools
from ai_tools.tools.agents import (
    get_test_execution,  # noqa: F401
    list_scenarios,  # noqa: F401
    list_test_executions,  # noqa: F401
)

# get_call_execution -> DRF bridge on CallExecutionDetailView (see _simulate.py)

# list_agents, list_agent_versions -> DRF bridge (see _agents.py)
# Annotation Queue tools (8)
from ai_tools.tools.annotation_queues import (
    add_queue_items,  # noqa: F401
    get_queue_progress,  # noqa: F401
    submit_queue_annotations,  # noqa: F401
)

# list/get/create/update/delete_annotation_queue -> DRF bridge (see _annotation_queues.py)
# Annotation tools — all migrated to DRF bridge (see _annotations.py)
# list_annotation_labels, get_annotation_label, create/update/delete_annotation_label,
# list_annotations, get_annotation
# DRF bridges programmatically applied for legacy lint-debt files:
from ai_tools.tools.bridge import (
    _agentcc,  # noqa: F401  17 agentcc ViewSets (analytics, api_key, blocklist, etc.)
    _agents,  # noqa: F401  list/get/create/update/delete_agent
    _annotation_queues,  # noqa: F401  list/get/create/update/delete_annotation_queue
    _annotations,  # noqa: F401  list/get/create/update/delete_annotation_label + list/get_annotation
    _dashboards,  # noqa: F401  dashboard query engine bridges (Packet D)
    _datasets,  # noqa: F401  list/get/create/update/delete_dataset
    # eval_group is NOT bridged — EvalGroupView.create/update read non-serializer
    # fields (eval_template_ids), so they use purpose-built hand-written tools.
    _evaluations,  # noqa: F401  separate_evals bridges (Packet E)
    _experiments,  # noqa: F401  experiment V2 view bridges (Packet B)
    _knowledge_bases,  # noqa: F401  list/get/create/update/delete_knowledge_base
    _misc_viewsets,  # noqa: F401  15 misc ViewSets (dashboards, scores, secrets, observability, etc.)
    _optimization,  # noqa: F401  DatasetOptimizationViewSet @actions (Packet E)
    _personas,  # noqa: F401  list/get/create/update/delete_persona
    _prompt_folders,  # noqa: F401  list/get/create/update/delete_prompt_folder
    _prompt_labels,  # noqa: F401  list/get/create/update/delete_prompt_label
    _prompt_templates,  # noqa: F401  list/get/create/update/delete_prompt_template
    _simulate,  # noqa: F401  get_call_execution (analytics), get_scenario (graph)
    _tracing,  # noqa: F401  list/get_trace, list/get_span, list/get_session, list/get/update_eval_task, list/get/create/update/delete_alert_monitor, project versions
)

# Context tools (5) — memory tools (save/list/delete) are EE and registered
# via ee.falcon_ai.apps.FalconAIConfig.ready().
from ai_tools.tools.context import (
    # list_workspaces — replaced by DRF bridge on WorkspaceListAPIView
    read_schema,  # noqa: F401
    read_taxonomy,  # noqa: F401
    search,  # noqa: F401
    search_tools,  # noqa: F401  smart tool/capability discovery over the full registry
    whoami,  # noqa: F401
)

# Dataset tools (29)
from ai_tools.tools.datasets import (
    add_columns,  # noqa: F401
    add_dataset_rows,  # noqa: F401
    add_rows_from_existing,  # noqa: F401
    clone_dataset,  # noqa: F401
    delete_column,  # noqa: F401
    delete_rows,  # noqa: F401
    duplicate_dataset,  # noqa: F401
    duplicate_rows,  # noqa: F401
    list_dataset_evals,  # noqa: F401
    # list_knowledge_bases -> DRF bridge (see _knowledge_bases.py)
    merge_datasets,  # noqa: F401
    run_prompt_for_rows,  # noqa: F401
    update_cell_value,  # noqa: F401
)

# list/get/create/update/delete_dataset -> DRF bridge (see _datasets.py)
# Docs tools (3)
from ai_tools.tools.docs import (
    ask_docs,  # noqa: F401
    get_page,  # noqa: F401
    search_docs,  # noqa: F401
)

# Evaluation tools
from ai_tools.tools.evaluations import (
    apply_eval_group_to_dataset,  # noqa: F401
    compare_evaluations,  # noqa: F401
    create_composite_eval,  # noqa: F401
    create_eval_group,  # noqa: F401  — hand-written (EvalGroupView not clean CRUD)
    create_eval_template,  # noqa: F401  — eval creation from Falcon (restored)
    delete_eval_group,  # noqa: F401
    delete_eval_logs,  # noqa: F401
    delete_eval_template,  # noqa: F401  — eval creation from Falcon (restored)
    duplicate_eval_template,  # noqa: F401
    edit_eval_group_templates,  # noqa: F401
    evaluate_with_agent,  # noqa: F401
    execute_composite_eval,  # noqa: F401
    get_eval_code_snippet,  # noqa: F401
    get_eval_group,  # noqa: F401
    get_eval_log_detail,  # noqa: F401
    get_eval_logs,  # noqa: F401
    get_eval_playground,  # noqa: F401
    get_eval_template,  # noqa: F401
    get_evaluation,  # noqa: F401
    list_eval_groups,  # noqa: F401
    list_eval_templates,  # noqa: F401
    list_evaluations,  # noqa: F401
    test_eval_template,  # noqa: F401
    update_eval_group,  # noqa: F401
    update_eval_template,  # noqa: F401  — eval creation from Falcon (restored)
)

# Experiment tools (11)
from ai_tools.tools.experiments import (
    compare_experiments,  # noqa: F401
    create_experiment,  # noqa: F401
    delete_experiment,  # noqa: F401
    get_experiment_comparison,  # noqa: F401
    get_experiment_results,  # noqa: F401
    get_experiment_stats,  # noqa: F401
    list_experiments,  # noqa: F401
    rerun_experiment,  # noqa: F401
)

# Optimization tools (10)
from ai_tools.tools.optimization import (
    create_optimization_run,  # noqa: F401
    get_optimization_graph,  # noqa: F401
    get_optimization_steps,  # noqa: F401
    get_optimization_trial,  # noqa: F401
    get_trial_evaluations,  # noqa: F401
    get_trial_prompt,  # noqa: F401
    get_trial_scenarios,  # noqa: F401
    list_optimization_runs,  # noqa: F401
    stop_optimization_run,  # noqa: F401
)

# Prompt Workbench tools (26)
# create_prompt_template — replaced by DRF bridge on PromptTemplateViewSet
# delete_prompt_template — replaced by DRF bridge on PromptTemplateViewSet
# get_prompt_template — replaced by DRF bridge on PromptTemplateViewSet
from ai_tools.tools.prompts import (
    commit_prompt_version,  # noqa: F401
    compare_prompt_versions,  # noqa: F401
    create_prompt_version,  # noqa: F401
    delete_prompt_simulation,  # noqa: F401
    execute_prompt_simulation,  # noqa: F401
    get_prompt_eval_configs,  # noqa: F401
    get_prompt_execution_results,  # noqa: F401
    get_prompt_version,  # noqa: F401
    # list_prompt_folders -> DRF bridge (see _prompt_folders.py)
    # list_prompt_labels -> DRF bridge (see _prompt_labels.py)
    list_prompt_scenarios,  # noqa: F401
    list_prompt_versions,  # noqa: F401
    run_prompt,  # noqa: F401
    run_prompt_evals,  # noqa: F401
    update_prompt_simulation,  # noqa: F401
)

# update_prompt_template — replaced by DRF bridge on PromptTemplateViewSet
# Simulation tools (38)
from ai_tools.tools.simulation import (
    activate_agent_version,  # noqa: F401
    cancel_test_execution,  # noqa: F401
    compare_agent_versions,  # noqa: F401
    create_simulator_agent,  # noqa: F401
    delete_agent_definition,  # noqa: F401
    delete_run_test,  # noqa: F401
    delete_scenario,  # noqa: F401
    delete_simulate_eval_config,  # noqa: F401
    delete_test_execution,  # noqa: F401
    duplicate_agent_definition,  # noqa: F401
    get_agent_version,  # noqa: F401
    get_call_logs,  # noqa: F401
    get_call_transcript,  # noqa: F401
    # get_scenario -> DRF bridge on ScenarioDetailView (see _simulate.py)
    get_test_execution_analytics,  # noqa: F401
    list_eval_mapping_options,  # noqa: F401
    list_simulate_eval_configs,  # noqa: F401
    list_simulator_agents,  # noqa: F401
    rerun_call_execution,  # noqa: F401
    update_scenario,  # noqa: F401
    update_simulate_eval_config,  # noqa: F401
    update_simulator_agent,  # noqa: F401
)

# Visualization tools (1)
# Tracing tools (42) + Error Feed tools (7 — tagged category="error_feed")
# create_project — replaced by DRF bridge on ProjectView (registers same name)
# Legacy ``create_trace_annotation`` / ``update_trace_annotation`` /
# ``delete_trace_annotation`` tools were unregistered as part of the
# unified-Score migration. Their write paths only synced Score for span-level
# annotations, leaving trace-level Scores stale relative to the legacy
# TraceAnnotation row — a silent-drift surface that production Score-only
# readers would expose. Use ``create_score`` / ``submit_trace_scores`` /
# ``list_trace_scores`` instead. Tool files remain on disk pending Phase 4
# deletion of the model itself.
# tracing/explore_trace.py now registers as ``explore_trace_legacy`` (the
# Chauffeur read-all-spans + Haiku summary). The short name ``explore_trace``
# belongs to the eval-context navigator in web/trace_explorer.py.
# get_project — replaced by DRF bridge on ProjectView (registers same name)
# list_projects — replaced by DRF bridge on ProjectView (registers same name)
from ai_tools.tools.tracing import (
    add_trace_tags,  # noqa: F401
    analyze_error_cluster,  # noqa: F401
    check_eval_config_exists,  # noqa: F401
    explore_trace,  # noqa: F401
    get_error_cluster_detail,  # noqa: F401
    get_eval_task_logs,  # noqa: F401
    get_eval_template_by_name,  # noqa: F401
    get_project_eval_attributes,  # noqa: F401
    get_span_tree,  # noqa: F401
    get_trace_analytics,  # noqa: F401
    get_trace_error_analysis,  # noqa: F401
    get_trace_span_children,  # noqa: F401
    get_trace_spans_by_type,  # noqa: F401
    get_trace_timeline,  # noqa: F401
    # list_custom_eval_configs -> DRF bridge (see _misc_viewsets.py)
    list_error_clusters,  # noqa: F401
    list_trace_scores,  # noqa: F401
    list_trace_tags,  # noqa: F401
    pause_eval_task,  # noqa: F401
    read_trace_span,  # noqa: F401
    remove_trace_tags,  # noqa: F401
    render_widget,  # noqa: F401
    search_trace_spans,  # noqa: F401
    search_traces,  # noqa: F401
    submit_trace_finding,  # noqa: F401
)

# DRF bridges (see _tracing.py): list/get_trace, list/get_span, list/get_session,
# list/get/create/update/delete_eval_task,
# list/get/create/update/delete_alert_monitor, project_versions
# update_project — replaced by DRF bridge on ProjectView (registers same name)
# update_trace_annotation: unregistered (see comment above on
# create_trace_annotation). Use create_score / submit_trace_scores instead.
# Usage tools (1)
from ai_tools.tools.usage import get_cost_breakdown  # noqa: F401

# User & Workspace tools (17)
from ai_tools.tools.users import (
    add_workspace_member,  # noqa: F401
    # create_api_key -> DRF bridge (see _misc_viewsets.py via ApiKeyViewSet)
    deactivate_user,  # noqa: F401
    get_organization,  # noqa: F401
    get_user_permissions,  # noqa: F401
    invite_users,  # noqa: F401
    # list_api_keys -> DRF bridge (see _misc_viewsets.py via ApiKeyViewSet)
    # list_org_members — removed, duplicate of list_users bridge tool
    list_organizations,  # noqa: F401
    # list_users — replaced by DRF bridge on UserListAPIView
    list_workspace_members,  # noqa: F401
    # revoke_api_key -> DRF bridge via ApiKeyViewSet destroy action (delete_api_key)
    update_user_role,  # noqa: F401
    update_workspace,  # noqa: F401
)

# Web tools (4)
from ai_tools.tools.web import (
    brave_search,  # noqa: F401
    ground_truth_search,  # noqa: F401
    kb_search,  # noqa: F401
    trace_explorer,  # noqa: F401
)
