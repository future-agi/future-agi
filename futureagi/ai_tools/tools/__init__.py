# Import all tool modules to trigger @register_tool decorators.
# This is called from AiToolsConfig.ready().

# Agent tools (8)
from ai_tools.tools.agents import get_agent  # noqa: F401
from ai_tools.tools.agents import get_call_execution  # noqa: F401
from ai_tools.tools.agents import get_test_execution  # noqa: F401
from ai_tools.tools.agents import list_agent_versions  # noqa: F401
from ai_tools.tools.agents import list_agents  # noqa: F401
from ai_tools.tools.agents import list_scenarios  # noqa: F401
from ai_tools.tools.agents import list_test_executions  # noqa: F401
from ai_tools.tools.agents import run_agent_test  # noqa: F401

# Annotation Queue tools (8)
from ai_tools.tools.annotation_queues import add_queue_items  # noqa: F401
from ai_tools.tools.annotation_queues import create_annotation_queue  # noqa: F401
from ai_tools.tools.annotation_queues import delete_annotation_queue  # noqa: F401
from ai_tools.tools.annotation_queues import get_annotation_queue  # noqa: F401
from ai_tools.tools.annotation_queues import get_queue_progress  # noqa: F401
from ai_tools.tools.annotation_queues import list_annotation_queues  # noqa: F401
from ai_tools.tools.annotation_queues import submit_queue_annotations  # noqa: F401
from ai_tools.tools.annotation_queues import update_annotation_queue  # noqa: F401

# Annotation tools (13)
from ai_tools.tools.annotations import annotation_summary  # noqa: F401
from ai_tools.tools.annotations import create_annotation  # noqa: F401
from ai_tools.tools.annotations import create_annotation_label  # noqa: F401
from ai_tools.tools.annotations import delete_annotation  # noqa: F401
from ai_tools.tools.annotations import delete_label  # noqa: F401
from ai_tools.tools.annotations import get_annotate_row  # noqa: F401
from ai_tools.tools.annotations import get_annotation  # noqa: F401
from ai_tools.tools.annotations import list_annotation_labels  # noqa: F401
from ai_tools.tools.annotations import list_annotations  # noqa: F401
from ai_tools.tools.annotations import reset_annotations  # noqa: F401
from ai_tools.tools.annotations import submit_annotation  # noqa: F401
from ai_tools.tools.annotations import update_annotation  # noqa: F401
from ai_tools.tools.annotations import update_label  # noqa: F401

# Context tools (5) — memory tools (save/list/delete) are EE and registered
# via ee.falcon_ai.apps.FalconAIConfig.ready().
from ai_tools.tools.context import list_workspaces  # noqa: F401
from ai_tools.tools.context import read_schema  # noqa: F401
from ai_tools.tools.context import read_taxonomy  # noqa: F401
from ai_tools.tools.context import search  # noqa: F401
from ai_tools.tools.context import whoami  # noqa: F401

# Dataset tools (29)
from ai_tools.tools.datasets import add_columns  # noqa: F401
from ai_tools.tools.datasets import add_dataset_eval  # noqa: F401
from ai_tools.tools.datasets import add_dataset_rows  # noqa: F401
from ai_tools.tools.datasets import add_rows_from_existing  # noqa: F401
from ai_tools.tools.datasets import add_run_prompt_column  # noqa: F401
from ai_tools.tools.datasets import clone_dataset  # noqa: F401
from ai_tools.tools.datasets import create_dataset  # noqa: F401
from ai_tools.tools.datasets import delete_column  # noqa: F401
from ai_tools.tools.datasets import delete_dataset  # noqa: F401
from ai_tools.tools.datasets import delete_dataset_eval  # noqa: F401
from ai_tools.tools.datasets import delete_rows  # noqa: F401
from ai_tools.tools.datasets import duplicate_dataset  # noqa: F401
from ai_tools.tools.datasets import duplicate_rows  # noqa: F401
from ai_tools.tools.datasets import edit_dataset_eval  # noqa: F401
from ai_tools.tools.datasets import edit_run_prompt_column  # noqa: F401
from ai_tools.tools.datasets import get_dataset  # noqa: F401
from ai_tools.tools.datasets import get_dataset_eval_stats  # noqa: F401
from ai_tools.tools.datasets import get_dataset_rows  # noqa: F401
from ai_tools.tools.datasets import get_run_prompt_column_config  # noqa: F401
from ai_tools.tools.datasets import list_dataset_evals  # noqa: F401
from ai_tools.tools.datasets import list_datasets  # noqa: F401
from ai_tools.tools.datasets import list_knowledge_bases  # noqa: F401
from ai_tools.tools.datasets import merge_datasets  # noqa: F401
from ai_tools.tools.datasets import preview_run_prompt_column  # noqa: F401
from ai_tools.tools.datasets import run_dataset_evals  # noqa: F401
from ai_tools.tools.datasets import run_prompt_for_rows  # noqa: F401
from ai_tools.tools.datasets import update_cell_value  # noqa: F401
from ai_tools.tools.datasets import update_column  # noqa: F401
from ai_tools.tools.datasets import update_dataset  # noqa: F401

# Docs tools (3)
from ai_tools.tools.docs import ask_docs  # noqa: F401
from ai_tools.tools.docs import get_page  # noqa: F401
from ai_tools.tools.docs import search_docs  # noqa: F401

# Evaluation tools (25)
from ai_tools.tools.evaluations import apply_eval_group_to_dataset  # noqa: F401
from ai_tools.tools.evaluations import compare_evaluations  # noqa: F401
from ai_tools.tools.evaluations import create_eval_group  # noqa: F401
from ai_tools.tools.evaluations import create_eval_template  # noqa: F401
from ai_tools.tools.evaluations import delete_eval_group  # noqa: F401
from ai_tools.tools.evaluations import delete_eval_logs  # noqa: F401
from ai_tools.tools.evaluations import delete_eval_template  # noqa: F401
from ai_tools.tools.evaluations import duplicate_eval_template  # noqa: F401
from ai_tools.tools.evaluations import edit_eval_group_templates  # noqa: F401
from ai_tools.tools.evaluations import evaluate_with_agent  # noqa: F401
from ai_tools.tools.evaluations import get_eval_code_snippet  # noqa: F401
from ai_tools.tools.evaluations import get_eval_group  # noqa: F401
from ai_tools.tools.evaluations import get_eval_log_detail  # noqa: F401
from ai_tools.tools.evaluations import get_eval_logs  # noqa: F401
from ai_tools.tools.evaluations import get_eval_playground  # noqa: F401
from ai_tools.tools.evaluations import get_eval_template  # noqa: F401
from ai_tools.tools.evaluations import get_evaluation  # noqa: F401
from ai_tools.tools.evaluations import list_eval_groups  # noqa: F401
from ai_tools.tools.evaluations import list_eval_templates  # noqa: F401
from ai_tools.tools.evaluations import list_evaluations  # noqa: F401
from ai_tools.tools.evaluations import run_evaluation  # noqa: F401
from ai_tools.tools.evaluations import submit_eval_feedback  # noqa: F401
from ai_tools.tools.evaluations import test_eval_template  # noqa: F401
from ai_tools.tools.evaluations import update_eval_group  # noqa: F401
from ai_tools.tools.evaluations import update_eval_template  # noqa: F401

# Experiment tools (11)
from ai_tools.tools.experiments import add_experiment_eval  # noqa: F401
from ai_tools.tools.experiments import compare_experiments  # noqa: F401
from ai_tools.tools.experiments import create_experiment  # noqa: F401
from ai_tools.tools.experiments import delete_experiment  # noqa: F401
from ai_tools.tools.experiments import get_experiment_comparison  # noqa: F401
from ai_tools.tools.experiments import get_experiment_data  # noqa: F401
from ai_tools.tools.experiments import get_experiment_results  # noqa: F401
from ai_tools.tools.experiments import get_experiment_stats  # noqa: F401
from ai_tools.tools.experiments import list_experiments  # noqa: F401
from ai_tools.tools.experiments import rerun_experiment  # noqa: F401
from ai_tools.tools.experiments import run_experiment_evals  # noqa: F401

# Optimization tools (10)
from ai_tools.tools.optimization import create_optimization_run  # noqa: F401
from ai_tools.tools.optimization import get_optimization_graph  # noqa: F401
from ai_tools.tools.optimization import get_optimization_run  # noqa: F401
from ai_tools.tools.optimization import get_optimization_steps  # noqa: F401
from ai_tools.tools.optimization import get_optimization_trial  # noqa: F401
from ai_tools.tools.optimization import get_trial_evaluations  # noqa: F401
from ai_tools.tools.optimization import get_trial_prompt  # noqa: F401
from ai_tools.tools.optimization import get_trial_scenarios  # noqa: F401
from ai_tools.tools.optimization import list_optimization_runs  # noqa: F401
from ai_tools.tools.optimization import stop_optimization_run  # noqa: F401

# Prompt Workbench tools (26)
from ai_tools.tools.prompts import commit_prompt_version  # noqa: F401
from ai_tools.tools.prompts import compare_prompt_versions  # noqa: F401
from ai_tools.tools.prompts import create_prompt_simulation  # noqa: F401
from ai_tools.tools.prompts import create_prompt_template  # noqa: F401
from ai_tools.tools.prompts import create_prompt_version  # noqa: F401
from ai_tools.tools.prompts import delete_prompt_simulation  # noqa: F401
from ai_tools.tools.prompts import delete_prompt_template  # noqa: F401
from ai_tools.tools.prompts import execute_prompt_simulation  # noqa: F401
from ai_tools.tools.prompts import get_prompt_eval_configs  # noqa: F401
from ai_tools.tools.prompts import get_prompt_execution_results  # noqa: F401
from ai_tools.tools.prompts import get_prompt_simulation  # noqa: F401
from ai_tools.tools.prompts import get_prompt_template  # noqa: F401
from ai_tools.tools.prompts import get_prompt_version  # noqa: F401
from ai_tools.tools.prompts import list_prompt_folders  # noqa: F401
from ai_tools.tools.prompts import list_prompt_labels  # noqa: F401
from ai_tools.tools.prompts import list_prompt_scenarios  # noqa: F401
from ai_tools.tools.prompts import list_prompt_simulations  # noqa: F401
from ai_tools.tools.prompts import list_prompt_templates  # noqa: F401
from ai_tools.tools.prompts import list_prompt_versions  # noqa: F401
from ai_tools.tools.prompts import run_prompt  # noqa: F401
from ai_tools.tools.prompts import run_prompt_evals  # noqa: F401
from ai_tools.tools.prompts import set_eval_config_for_prompt  # noqa: F401
from ai_tools.tools.prompts import update_prompt_simulation  # noqa: F401
from ai_tools.tools.prompts import update_prompt_template  # noqa: F401

# Simulation tools (38)
from ai_tools.tools.simulation import activate_agent_version  # noqa: F401
from ai_tools.tools.simulation import add_scenario_columns  # noqa: F401
from ai_tools.tools.simulation import add_scenario_rows  # noqa: F401
from ai_tools.tools.simulation import cancel_test_execution  # noqa: F401
from ai_tools.tools.simulation import compare_agent_versions  # noqa: F401
from ai_tools.tools.simulation import create_agent_definition  # noqa: F401
from ai_tools.tools.simulation import create_agent_version  # noqa: F401
from ai_tools.tools.simulation import create_persona  # noqa: F401
from ai_tools.tools.simulation import create_run_test  # noqa: F401
from ai_tools.tools.simulation import create_scenario  # noqa: F401
from ai_tools.tools.simulation import create_simulate_eval_config  # noqa: F401
from ai_tools.tools.simulation import create_simulator_agent  # noqa: F401
from ai_tools.tools.simulation import delete_agent_definition  # noqa: F401
from ai_tools.tools.simulation import delete_persona  # noqa: F401
from ai_tools.tools.simulation import delete_run_test  # noqa: F401
from ai_tools.tools.simulation import delete_scenario  # noqa: F401
from ai_tools.tools.simulation import delete_simulate_eval_config  # noqa: F401
from ai_tools.tools.simulation import delete_test_execution  # noqa: F401
from ai_tools.tools.simulation import duplicate_agent_definition  # noqa: F401
from ai_tools.tools.simulation import duplicate_persona  # noqa: F401
from ai_tools.tools.simulation import get_agent_version  # noqa: F401
from ai_tools.tools.simulation import get_call_logs  # noqa: F401
from ai_tools.tools.simulation import get_call_transcript  # noqa: F401
from ai_tools.tools.simulation import get_persona  # noqa: F401
from ai_tools.tools.simulation import get_run_test_analytics  # noqa: F401
from ai_tools.tools.simulation import get_scenario  # noqa: F401
from ai_tools.tools.simulation import get_test_execution_analytics  # noqa: F401
from ai_tools.tools.simulation import list_eval_mapping_options  # noqa: F401
from ai_tools.tools.simulation import list_personas  # noqa: F401
from ai_tools.tools.simulation import list_simulate_eval_configs  # noqa: F401
from ai_tools.tools.simulation import list_simulator_agents  # noqa: F401
from ai_tools.tools.simulation import rerun_call_execution  # noqa: F401
from ai_tools.tools.simulation import rerun_test_execution  # noqa: F401
from ai_tools.tools.simulation import run_new_evals  # noqa: F401
from ai_tools.tools.simulation import update_agent_definition  # noqa: F401
from ai_tools.tools.simulation import update_persona  # noqa: F401
from ai_tools.tools.simulation import update_run_test  # noqa: F401
from ai_tools.tools.simulation import update_scenario  # noqa: F401
from ai_tools.tools.simulation import update_simulate_eval_config  # noqa: F401
from ai_tools.tools.simulation import update_simulator_agent  # noqa: F401

# Visualization tools (1)
# Tracing tools (42) + Error Feed tools (7 — tagged category="error_feed")
from ai_tools.tools.tracing import add_trace_tags  # noqa: F401
from ai_tools.tools.tracing import analyze_error_cluster  # noqa: F401
from ai_tools.tools.tracing import analyze_errors  # noqa: F401
from ai_tools.tools.tracing import analyze_project_traces  # noqa: F401
from ai_tools.tools.tracing import check_eval_config_exists  # noqa: F401
from ai_tools.tools.tracing import create_alert_monitor  # noqa: F401
from ai_tools.tools.tracing import create_custom_eval_config  # noqa: F401
from ai_tools.tools.tracing import create_eval_task  # noqa: F401
from ai_tools.tools.tracing import create_project  # noqa: F401
from ai_tools.tools.tracing import create_score  # noqa: F401
# Legacy ``create_trace_annotation`` / ``update_trace_annotation`` /
# ``delete_trace_annotation`` tools were unregistered as part of the
# unified-Score migration. Their write paths only synced Score for span-level
# annotations, leaving trace-level Scores stale relative to the legacy
# TraceAnnotation row — a silent-drift surface that production Score-only
# readers would expose. Use ``create_score`` / ``submit_trace_scores`` /
# ``list_trace_scores`` instead. Tool files remain on disk pending Phase 4
# deletion of the model itself.
from ai_tools.tools.tracing import delete_alert_monitor  # noqa: F401
from ai_tools.tools.tracing import delete_eval_tasks  # noqa: F401
from ai_tools.tools.tracing import delete_project  # noqa: F401
# tracing/explore_trace.py now registers as ``explore_trace_legacy`` (the
# Chauffeur read-all-spans + Haiku summary). The short name ``explore_trace``
# belongs to the eval-context navigator in web/trace_explorer.py.
from ai_tools.tools.tracing import explore_trace  # noqa: F401
from ai_tools.tools.tracing import get_error_cluster_detail  # noqa: F401
from ai_tools.tools.tracing import get_eval_task  # noqa: F401
from ai_tools.tools.tracing import get_eval_task_logs  # noqa: F401
from ai_tools.tools.tracing import get_eval_template_by_name  # noqa: F401
from ai_tools.tools.tracing import get_project  # noqa: F401
from ai_tools.tools.tracing import get_project_eval_attributes  # noqa: F401
from ai_tools.tools.tracing import get_session  # noqa: F401
from ai_tools.tools.tracing import get_session_analytics  # noqa: F401
from ai_tools.tools.tracing import get_span  # noqa: F401
from ai_tools.tools.tracing import get_span_tree  # noqa: F401
from ai_tools.tools.tracing import get_trace  # noqa: F401
from ai_tools.tools.tracing import get_trace_analytics  # noqa: F401
from ai_tools.tools.tracing import get_trace_error_analysis  # noqa: F401
from ai_tools.tools.tracing import get_trace_span_children  # noqa: F401
from ai_tools.tools.tracing import get_trace_spans_by_type  # noqa: F401
from ai_tools.tools.tracing import get_trace_timeline  # noqa: F401
from ai_tools.tools.tracing import list_alert_monitors  # noqa: F401
from ai_tools.tools.tracing import list_custom_eval_configs  # noqa: F401
from ai_tools.tools.tracing import list_error_clusters  # noqa: F401
from ai_tools.tools.tracing import list_eval_tasks  # noqa: F401
from ai_tools.tools.tracing import list_projects  # noqa: F401
from ai_tools.tools.tracing import list_sessions  # noqa: F401
from ai_tools.tools.tracing import list_spans  # noqa: F401
from ai_tools.tools.tracing import list_trace_scores  # noqa: F401
from ai_tools.tools.tracing import list_trace_tags  # noqa: F401
from ai_tools.tools.tracing import pause_eval_task  # noqa: F401
from ai_tools.tools.tracing import read_trace_span  # noqa: F401
from ai_tools.tools.tracing import remove_trace_tags  # noqa: F401
from ai_tools.tools.tracing import render_widget  # noqa: F401
from ai_tools.tools.tracing import search_trace_spans  # noqa: F401
from ai_tools.tools.tracing import search_traces  # noqa: F401
from ai_tools.tools.tracing import submit_trace_finding  # noqa: F401
from ai_tools.tools.tracing import submit_trace_scores  # noqa: F401
from ai_tools.tools.tracing import unpause_eval_task  # noqa: F401
from ai_tools.tools.tracing import update_alert_monitor  # noqa: F401
from ai_tools.tools.tracing import update_eval_task  # noqa: F401
from ai_tools.tools.tracing import update_project  # noqa: F401
# update_trace_annotation: unregistered (see comment above on
# create_trace_annotation). Use create_score / submit_trace_scores instead.

# Usage tools (1)
from ai_tools.tools.usage import get_cost_breakdown  # noqa: F401

# User & Workspace tools (17)
from ai_tools.tools.users import add_workspace_member  # noqa: F401
from ai_tools.tools.users import create_api_key  # noqa: F401
from ai_tools.tools.users import create_workspace  # noqa: F401
from ai_tools.tools.users import deactivate_user  # noqa: F401
from ai_tools.tools.users import get_organization  # noqa: F401
from ai_tools.tools.users import get_user  # noqa: F401
from ai_tools.tools.users import get_user_permissions  # noqa: F401
from ai_tools.tools.users import invite_users  # noqa: F401
from ai_tools.tools.users import list_api_keys  # noqa: F401
from ai_tools.tools.users import list_org_members  # noqa: F401
from ai_tools.tools.users import list_organizations  # noqa: F401
from ai_tools.tools.users import list_users  # noqa: F401
from ai_tools.tools.users import list_workspace_members  # noqa: F401
from ai_tools.tools.users import remove_user  # noqa: F401
from ai_tools.tools.users import revoke_api_key  # noqa: F401
from ai_tools.tools.users import update_user_role  # noqa: F401
from ai_tools.tools.users import update_workspace  # noqa: F401

# Web tools (4)
from ai_tools.tools.web import brave_search  # noqa: F401
from ai_tools.tools.web import ground_truth_search  # noqa: F401
from ai_tools.tools.web import kb_search  # noqa: F401
from ai_tools.tools.web import trace_explorer  # noqa: F401
