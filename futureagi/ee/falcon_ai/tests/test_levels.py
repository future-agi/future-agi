"""
Multi-level Falcon AI Test Suite.

Level 1: Single tool calls (all 237 tools)
Level 2: 2-10 tool calls within same feature (multi-step workflows)
Level 3: 2-10 tool calls across 2 features (cross-feature workflows)
Level 4: Up to 100 tool calls across all features (complex missions)

Run: python falcon_ai/tests/test_levels.py <level> [batch_start] [batch_size]
"""

# ─── LEVEL 1: Single tool call per test ─────────────────────────────────────
# Every tool should be callable by a natural user message

LEVEL_1 = [
    # ── Context / Identity ──
    ("L1", "whoami", "Who am I and what workspace am I in?"),
    ("L1", "search", "Search for anything related to customer support"),
    ("L1", "save_memory", "Remember that our main project is called 'production-api'"),
    ("L1", "list_memories", "What do you remember about my workspace?"),
    ("L1", "read_schema", "Show me the database schema"),
    ("L1", "read_taxonomy", "Show me the taxonomy"),
    # ── Datasets ──
    ("L1", "list_datasets", "List all my datasets"),
    (
        "L1",
        "create_dataset",
        "Create a dataset called 'test-level1' with columns: input, output",
    ),
    ("L1", "get_dataset", "Show me details of the test-level1 dataset"),
    ("L1", "update_dataset", "Rename the test-level1 dataset to 'test-level1-renamed'"),
    ("L1", "get_dataset_rows", "Show rows from my test-level1-renamed dataset"),
    ("L1", "add_columns", "Add a 'score' column to test-level1-renamed"),
    (
        "L1",
        "add_dataset_rows",
        "Add 3 sample rows to test-level1-renamed with input and output values",
    ),
    (
        "L1",
        "update_column",
        "Change the score column type to float in test-level1-renamed",
    ),
    (
        "L1",
        "update_cell_value",
        "Update the first row's output to 'updated value' in test-level1-renamed",
    ),
    ("L1", "duplicate_rows", "Duplicate the first row of test-level1-renamed"),
    ("L1", "clone_dataset", "Clone test-level1-renamed as 'test-level1-clone'"),
    ("L1", "duplicate_dataset", "Duplicate test-level1-renamed"),
    ("L1", "merge_datasets", "Merge test-level1-renamed and test-level1-clone"),
    (
        "L1",
        "add_rows_from_existing",
        "Copy rows from test-level1-clone to test-level1-renamed",
    ),
    ("L1", "delete_rows", "Delete the last row from test-level1-renamed"),
    ("L1", "delete_column", "Remove the score column from test-level1-renamed"),
    ("L1", "delete_dataset", "Delete the test-level1-clone dataset"),
    # ── Evaluations ──
    ("L1", "list_eval_templates", "Show all available eval templates"),
    ("L1", "get_eval_template", "Show me the faithfulness eval template details"),
    (
        "L1",
        "get_eval_template_by_name",
        "Get the eval template named 'hallucination_detection'",
    ),
    (
        "L1",
        "create_eval_template",
        "Create a custom eval template called 'test-politeness-l1' that checks if responses are polite",
    ),
    ("L1", "duplicate_eval_template", "Duplicate the test-politeness-l1 eval template"),
    (
        "L1",
        "test_eval_template",
        "Test the faithfulness eval template with sample input and output",
    ),
    ("L1", "list_eval_groups", "What eval groups exist?"),
    (
        "L1",
        "create_eval_group",
        "Create an eval group called 'test-suite-l1' with faithfulness",
    ),
    ("L1", "get_eval_group", "Show details of the test-suite-l1 eval group"),
    ("L1", "list_evaluations", "List all evaluation runs"),
    ("L1", "list_eval_tasks", "Show running eval tasks"),
    ("L1", "list_custom_eval_configs", "List custom eval configurations"),
    (
        "L1",
        "get_eval_code_snippet",
        "Show code snippet for running evaluations programmatically",
    ),
    ("L1", "list_eval_mapping_options", "Show eval mapping options for my datasets"),
    ("L1", "add_dataset_eval", "Add faithfulness eval to test-level1-renamed"),
    ("L1", "run_dataset_evals", "Run all evals on test-level1-renamed"),
    ("L1", "get_dataset_eval_stats", "Show eval results for test-level1-renamed"),
    # ── Tracing ──
    ("L1", "list_projects", "List all my tracing projects"),
    ("L1", "create_project", "Create a project called 'test-project-l1'"),
    ("L1", "get_project", "Show details of test-project-l1"),
    (
        "L1",
        "update_project",
        "Update test-project-l1 description to 'Level 1 test project'",
    ),
    ("L1", "search_traces", "Search for recent traces"),
    ("L1", "get_trace", "Show me the latest trace details"),
    ("L1", "get_trace_timeline", "Show timeline of the latest trace"),
    ("L1", "get_span_tree", "Show span tree of the latest trace"),
    ("L1", "list_spans", "List all spans from the latest trace"),
    ("L1", "get_span", "Show details of the first span in the latest trace"),
    ("L1", "analyze_errors", "Analyze error patterns in my traces"),
    (
        "L1",
        "get_trace_error_analysis",
        "What are the top errors in my production project?",
    ),
    ("L1", "get_trace_analytics", "Show trace analytics for the last week"),
    ("L1", "get_cost_breakdown", "Show my AI spending breakdown"),
    ("L1", "list_trace_tags", "What tags are on my traces?"),
    ("L1", "add_trace_tags", "Tag the latest trace with 'test-l1'"),
    ("L1", "remove_trace_tags", "Remove the 'test-l1' tag from the latest trace"),
    ("L1", "list_trace_scores", "Show scores for the latest trace"),
    ("L1", "create_score", "Create a quality score of 0.9 for the latest trace"),
    ("L1", "create_trace_annotation", "Annotate the latest trace as 'good quality'"),
    ("L1", "list_sessions", "Show user sessions"),
    ("L1", "get_session", "Show details of the latest session"),
    ("L1", "get_session_analytics", "Show analytics for the latest session"),
    # ── Prompts ──
    ("L1", "list_prompt_templates", "List all prompt templates"),
    (
        "L1",
        "create_prompt_template",
        "Create a prompt template called 'test-prompt-l1' for customer support",
    ),
    ("L1", "get_prompt_template", "Show details of test-prompt-l1"),
    ("L1", "update_prompt_template", "Update test-prompt-l1 description"),
    (
        "L1",
        "create_prompt_version",
        "Create a new version of test-prompt-l1 with few-shot examples",
    ),
    ("L1", "list_prompt_versions", "List all versions of test-prompt-l1"),
    ("L1", "get_prompt_version", "Show the latest version of test-prompt-l1"),
    ("L1", "compare_prompt_versions", "Compare versions of test-prompt-l1"),
    ("L1", "commit_prompt_version", "Commit the latest version of test-prompt-l1"),
    ("L1", "run_prompt", "Run test-prompt-l1 with input 'How do I reset my password?'"),
    ("L1", "run_prompt_evals", "Run evaluations on test-prompt-l1 outputs"),
    ("L1", "list_prompt_folders", "Show prompt folders"),
    ("L1", "list_prompt_labels", "Show prompt labels"),
    ("L1", "list_prompt_scenarios", "Show prompt scenarios"),
    (
        "L1",
        "set_eval_config_for_prompt",
        "Configure evaluation settings for test-prompt-l1",
    ),
    ("L1", "get_prompt_eval_configs", "Show eval configs for test-prompt-l1"),
    ("L1", "get_prompt_execution_results", "Show execution results for test-prompt-l1"),
    ("L1", "list_prompt_simulations", "List prompt simulations"),
    ("L1", "create_prompt_simulation", "Create a simulation for test-prompt-l1"),
    ("L1", "execute_prompt_simulation", "Run the simulation for test-prompt-l1"),
    ("L1", "get_prompt_simulation", "Show simulation results for test-prompt-l1"),
    ("L1", "create_optimization_run", "Start optimizing test-prompt-l1"),
    ("L1", "list_optimization_runs", "List optimization runs"),
    ("L1", "get_optimization_run", "Show latest optimization run details"),
    # ── Experiments ──
    ("L1", "list_experiments", "List all experiments"),
    (
        "L1",
        "create_experiment",
        "Create an experiment comparing models on test-level1-renamed",
    ),
    ("L1", "get_experiment_results", "Show results of my latest experiment"),
    ("L1", "get_experiment_comparison", "Compare variants in my latest experiment"),
    ("L1", "get_experiment_stats", "Show detailed stats for my latest experiment"),
    ("L1", "get_experiment_data", "Show raw data from my latest experiment"),
    ("L1", "rerun_experiment", "Rerun my latest experiment"),
    ("L1", "add_experiment_eval", "Add hallucination eval to my latest experiment"),
    ("L1", "run_experiment_evals", "Run all evals on my latest experiment"),
    ("L1", "compare_experiments", "Compare my two most recent experiments"),
    # ── Simulation / Agent Testing ──
    ("L1", "list_agents", "List all agent definitions"),
    ("L1", "create_agent_definition", "Create a customer support agent definition"),
    ("L1", "get_agent", "Show details of my agent"),
    ("L1", "update_agent_definition", "Update my agent's description"),
    ("L1", "create_agent_version", "Create a new version of my agent"),
    ("L1", "list_agent_versions", "List versions of my agent"),
    ("L1", "get_agent_version", "Show the latest agent version"),
    ("L1", "compare_agent_versions", "Compare agent versions"),
    ("L1", "list_scenarios", "List test scenarios"),
    ("L1", "create_scenario", "Create a test scenario for my agent"),
    ("L1", "get_scenario", "Show scenario details"),
    ("L1", "update_scenario", "Update the scenario name"),
    ("L1", "list_personas", "List all personas"),
    ("L1", "create_persona", "Create a persona for an angry customer"),
    ("L1", "get_persona", "Show the angry customer persona"),
    ("L1", "update_persona", "Update the persona description"),
    ("L1", "list_simulator_agents", "List simulator agents"),
    ("L1", "create_simulator_agent", "Create a simulator agent"),
    ("L1", "create_run_test", "Create a test run for my agent"),
    ("L1", "list_test_executions", "List test executions"),
    ("L1", "get_test_execution", "Show latest test execution"),
    ("L1", "get_test_execution_analytics", "Show test execution analytics"),
    ("L1", "get_run_test_analytics", "Show run test analytics"),
    ("L1", "run_agent_test", "Run a test on my agent"),
    ("L1", "evaluate_with_agent", "Evaluate traces using an agent"),
    # ── Annotations ──
    ("L1", "list_annotation_labels", "List annotation labels"),
    ("L1", "create_annotation_label", "Create a label called 'test-quality-l1'"),
    ("L1", "list_annotation_queues", "List annotation queues"),
    (
        "L1",
        "create_annotation_queue",
        "Create an annotation queue called 'test-queue-l1'",
    ),
    ("L1", "get_annotation_queue", "Show test-queue-l1 details"),
    ("L1", "annotation_summary", "Show annotation summary"),
    ("L1", "list_annotations", "List all annotations"),
    # ── Alerts ──
    ("L1", "list_alert_monitors", "Show my alerts"),
    ("L1", "create_alert_monitor", "Create an alert for error rate > 5%"),
    # ── Knowledge Base ──
    ("L1", "list_knowledge_bases", "List knowledge bases"),
    # ── Admin / Workspace ──
    ("L1", "get_organization", "Show my organization details"),
    ("L1", "list_organizations", "List my organizations"),
    ("L1", "list_workspaces", "List workspaces"),
    ("L1", "list_workspace_members", "Show workspace members"),
    ("L1", "list_org_members", "Show organization members"),
    ("L1", "list_users", "List all users"),
    ("L1", "get_user", "Show my user profile"),
    ("L1", "get_user_permissions", "What permissions do I have?"),
    ("L1", "list_api_keys", "Show my API keys"),
    # ── Docs ──
    ("L1", "search_docs", "Search docs for how to set up tracing"),
    ("L1", "ask_docs", "What evaluation metrics are available in Future AGI?"),
    ("L1", "get_docs_page", "Show the Python SDK documentation page"),
]

# ─── LEVEL 2: Multi-step within same feature (2-10 tool calls) ──────────────

LEVEL_2 = [
    # Dataset workflows
    (
        "L2",
        "dataset_full_crud",
        "Create a dataset called 'l2-crud-test' with columns input and output, add 5 sample QA rows, show me the rows, then update the first row's output",
    ),
    (
        "L2",
        "dataset_eval_pipeline",
        "Find my largest dataset, run faithfulness and hallucination evals on it, then show me the results",
    ),
    (
        "L2",
        "dataset_column_ops",
        "Take my first dataset, add columns 'category' and 'priority', then show the updated schema",
    ),
    (
        "L2",
        "dataset_clone_modify",
        "Clone my first dataset, add 10 new rows to the clone, then compare row counts",
    ),
    (
        "L2",
        "dataset_bulk_eval",
        "List all my datasets, pick the one with most rows, add 3 different evals, run them all",
    ),
    # Tracing workflows
    (
        "L2",
        "trace_debug_flow",
        "Find error traces from the last 24 hours, analyze the error patterns, and show me the timeline of the worst one",
    ),
    (
        "L2",
        "trace_cost_analysis",
        "Show my cost breakdown, find the most expensive model, then search traces using that model",
    ),
    (
        "L2",
        "trace_tag_annotate",
        "Find my latest trace, tag it with 'reviewed', annotate it as 'good', and show its scores",
    ),
    (
        "L2",
        "trace_session_deep",
        "List user sessions, pick the latest one, show its analytics and all traces in it",
    ),
    (
        "L2",
        "trace_project_setup",
        "Create a new project 'l2-test', list all projects, show details of the new one",
    ),
    # Prompt workflows
    (
        "L2",
        "prompt_iterate",
        "Create a prompt called 'l2-prompt', create 2 versions with different system messages, compare them",
    ),
    (
        "L2",
        "prompt_test_run",
        "Find my first prompt template, run it with 3 different inputs, show the results",
    ),
    (
        "L2",
        "prompt_eval_cycle",
        "List prompts, pick one, configure evals on it, run the evals, show results",
    ),
    (
        "L2",
        "prompt_optimize_flow",
        "Find my best prompt, start an optimization run, check the status",
    ),
    (
        "L2",
        "prompt_sim_flow",
        "Create a prompt, set up a simulation with 5 scenarios, execute it, show results",
    ),
    # Eval workflows
    (
        "L2",
        "eval_template_suite",
        "List all eval templates, show details of the top 3, create a group combining them",
    ),
    (
        "L2",
        "eval_custom_create",
        "Create a custom eval template for 'response tone', test it with sample data, then add it to a dataset",
    ),
    ("L2", "eval_compare_datasets", "Compare eval results across my top 2 datasets"),
    (
        "L2",
        "eval_group_apply",
        "Create an eval group with faithfulness and hallucination, apply it to all my datasets",
    ),
    # Experiment workflows
    (
        "L2",
        "experiment_full",
        "Create an experiment on my first dataset comparing 2 model configs, wait for results, show comparison",
    ),
    (
        "L2",
        "experiment_eval_add",
        "Find my latest experiment, add 3 eval templates to it, run the evals, show stats",
    ),
    # Annotation workflows
    (
        "L2",
        "annotation_setup",
        "Create a label 'quality-l2' with options good/bad/neutral, create a queue for it, show the queue",
    ),
    (
        "L2",
        "annotation_review",
        "List annotation queues, show progress on the first one, list recent annotations",
    ),
    # Agent/Simulation workflows
    (
        "L2",
        "agent_test_cycle",
        "Create an agent definition, create a test scenario, create a persona, run a test",
    ),
    (
        "L2",
        "agent_version_compare",
        "List my agents, show versions of the first one, compare the latest two versions",
    ),
]

# ─── LEVEL 3: Cross-feature (2-10 tool calls, max 2 features) ───────────────

LEVEL_3 = [
    # Dataset + Eval
    (
        "L3",
        "data_to_eval",
        "Create a QA dataset with 10 rows, then run faithfulness and hallucination evals on it and show results",
    ),
    (
        "L3",
        "eval_on_traces",
        "Find traces with errors, create a dataset from them, then run evals on that dataset",
    ),
    # Prompt + Experiment
    (
        "L3",
        "prompt_experiment",
        "Create a prompt template, set up an experiment comparing GPT-4 and Claude on my dataset, show results",
    ),
    (
        "L3",
        "prompt_to_eval",
        "Find my best prompt, run it on sample data, then evaluate the outputs for quality",
    ),
    # Tracing + Dataset
    (
        "L3",
        "traces_to_data",
        "Search for recent traces, extract key metrics, create a dataset with those metrics",
    ),
    (
        "L3",
        "cost_to_action",
        "Analyze my costs, find the most expensive traces, create a report dataset",
    ),
    # Eval + Experiment
    (
        "L3",
        "eval_experiment",
        "Create an experiment, add multiple eval templates, run them, compare results across variants",
    ),
    (
        "L3",
        "eval_group_experiment",
        "Create an eval group, apply it to my experiment, show comparison",
    ),
    # Docs + Action
    (
        "L3",
        "learn_then_do",
        "Search docs for how to create evaluations, then create a custom eval template based on what you found",
    ),
    (
        "L3",
        "docs_to_setup",
        "Search docs for tracing setup, then create a project configured as described",
    ),
    # Admin + Tracing
    (
        "L3",
        "workspace_health",
        "Show my workspace members, check our API usage, show cost breakdown",
    ),
    (
        "L3",
        "audit_traces",
        "List workspace members, then show traces from each user's recent activity",
    ),
    # Dataset + Prompt
    (
        "L3",
        "data_prompt_flow",
        "Find my best dataset, create a prompt template that uses it, run the prompt with sample inputs",
    ),
    (
        "L3",
        "prompt_data_gen",
        "Create a prompt for generating synthetic data, run it 5 times, create a dataset from the outputs",
    ),
    # Annotation + Eval
    (
        "L3",
        "annotation_eval",
        "Set up annotation labels and a queue, then configure evals that match the annotation criteria",
    ),
    # Agent + Tracing
    (
        "L3",
        "agent_trace_debug",
        "Find my agent's latest test results, trace the failing scenarios, analyze errors",
    ),
]

# ─── LEVEL 4: Complex missions (up to 100 tool calls, all features) ─────────

LEVEL_4 = [
    (
        "L4",
        "full_qa_pipeline",
        "Build a complete QA pipeline from scratch: "
        "1) Create a QA dataset with 20 rows of questions and expected answers "
        "2) Create a prompt template for answering questions "
        "3) Run the prompt on all dataset rows "
        "4) Add faithfulness, hallucination, and relevance evals "
        "5) Run all evals and show results "
        "6) Create an experiment comparing GPT-4 vs Claude on this dataset "
        "7) Show the experiment comparison and recommend the best model",
    ),
    (
        "L4",
        "workspace_audit",
        "Do a complete workspace health audit: "
        "1) Show who I am and my permissions "
        "2) List all datasets with their row counts and eval status "
        "3) List all prompt templates with version counts "
        "4) Show all experiments and their results "
        "5) Analyze trace error patterns across all projects "
        "6) Show cost breakdown by model and project "
        "7) Check alert configurations "
        "8) List annotation queues and progress "
        "9) Summarize everything with recommendations",
    ),
    (
        "L4",
        "eval_everything",
        "Evaluate everything in my workspace: "
        "1) List all datasets "
        "2) For each dataset, check what evals are configured "
        "3) Add faithfulness eval to any dataset that doesn't have it "
        "4) Run evals on all datasets "
        "5) Compare results across datasets "
        "6) Create an eval group with the most useful templates "
        "7) Show a summary of quality across the workspace",
    ),
    (
        "L4",
        "prompt_engineering_cycle",
        "Do a full prompt engineering cycle: "
        "1) Create a customer support prompt template "
        "2) Create version 1 with basic instructions "
        "3) Run it on sample inputs and evaluate "
        "4) Create version 2 with improved instructions based on eval results "
        "5) Run version 2 and evaluate "
        "6) Compare v1 and v2 "
        "7) Start an optimization run "
        "8) Set up a simulation with 10 scenarios "
        "9) Execute the simulation "
        "10) Show final recommendations",
    ),
    (
        "L4",
        "agent_testing_suite",
        "Set up a complete agent testing suite: "
        "1) Create an agent definition for customer support "
        "2) Create 5 different personas (happy, angry, confused, technical, non-technical) "
        "3) Create test scenarios for each persona "
        "4) Run tests across all scenarios "
        "5) Analyze test results "
        "6) Find traces from the test runs "
        "7) Evaluate trace quality "
        "8) Show a summary with pass/fail rates per persona",
    ),
    (
        "L4",
        "debug_production",
        "Debug a production issue end-to-end: "
        "1) Search for error traces in the last 24 hours "
        "2) Analyze error patterns "
        "3) Find the most common error "
        "4) Get the trace timeline for a failing request "
        "5) Show the span tree "
        "6) Check if any alerts were triggered "
        "7) Search docs for the error type "
        "8) Create a dataset from the error traces "
        "9) Set up evals to monitor for this error pattern "
        "10) Summarize findings and suggested fixes",
    ),
]


# ─── LEVEL 1 EXTENSION: Remaining 94 tools ──────────────────────────────────

LEVEL_1_EXT = [
    # ── Dataset extended ──
    ("L1", "list_dataset_evals", "List all evaluations configured on my first dataset"),
    ("L1", "edit_dataset_eval", "Edit the eval configuration on my first dataset"),
    ("L1", "delete_dataset_eval", "Remove the faithfulness eval from my first dataset"),
    ("L1", "create_dataset_from_file", "How do I create a dataset from a CSV file?"),
    ("L1", "create_dataset_from_huggingface", "Import a dataset from HuggingFace"),
    ("L1", "add_run_prompt_column", "Add a prompt run column to my dataset"),
    ("L1", "apply_eval_group_to_dataset", "Apply my eval group to my first dataset"),
    # ── Eval extended ──
    ("L1", "run_evaluation", "Run a single evaluation on my latest trace"),
    ("L1", "get_evaluation", "Show details of my latest evaluation run"),
    ("L1", "compare_evaluations", "Compare evaluation results between two datasets"),
    ("L1", "create_eval_task", "Set up a recurring eval task"),
    ("L1", "get_eval_task", "Show my latest eval task details"),
    ("L1", "get_eval_task_logs", "Show logs from my latest eval task"),
    ("L1", "pause_eval_task", "Pause my running eval task"),
    ("L1", "unpause_eval_task", "Resume my paused eval task"),
    ("L1", "delete_eval_tasks", "Delete completed eval tasks"),
    ("L1", "delete_eval_template", "Delete the test-politeness eval template"),
    ("L1", "delete_eval_group", "Delete the test-suite eval group"),
    ("L1", "delete_eval_logs", "Clear eval logs for my dataset"),
    ("L1", "update_eval_template", "Update the description of my custom eval template"),
    ("L1", "update_eval_group", "Update my eval group name"),
    ("L1", "edit_eval_group_templates", "Change which templates are in my eval group"),
    (
        "L1",
        "create_custom_eval_config",
        "Create a custom eval configuration for my project",
    ),
    ("L1", "list_simulate_eval_configs", "List simulation eval configs"),
    ("L1", "create_simulate_eval_config", "Create a simulation eval config"),
    ("L1", "delete_simulate_eval_config", "Delete a simulation eval config"),
    ("L1", "update_simulate_eval_config", "Update a simulation eval config"),
    ("L1", "check_eval_config_exists", "Check if an eval config exists on my project"),
    ("L1", "get_eval_log_detail", "Show detailed eval log for the latest evaluation"),
    ("L1", "get_eval_logs", "Show all eval logs"),
    ("L1", "get_eval_playground", "Open the eval playground"),
    ("L1", "submit_eval_feedback", "Submit feedback on an eval result"),
    ("L1", "get_project_eval_attributes", "Show eval attributes for my project"),
    # ── Tracing extended ──
    ("L1", "create_annotation", "Create an annotation on my latest trace"),
    ("L1", "get_annotation", "Show my latest annotation"),
    ("L1", "update_annotation", "Update my annotation text"),
    ("L1", "delete_annotation", "Delete my latest annotation"),
    ("L1", "update_trace_annotation", "Update a trace annotation"),
    ("L1", "delete_trace_annotation", "Delete a trace annotation"),
    ("L1", "delete_project", "Delete the test-project project"),
    ("L1", "update_alert_monitor", "Update my alert monitor threshold"),
    ("L1", "delete_alert_monitor", "Delete my test alert"),
    ("L1", "get_call_execution", "Show the latest call execution details"),
    ("L1", "get_call_logs", "Show call logs"),
    ("L1", "get_call_transcript", "Show the transcript of the latest call"),
    ("L1", "rerun_call_execution", "Rerun the latest call execution"),
    # ── Prompt extended ──
    ("L1", "update_prompt_simulation", "Update my prompt simulation settings"),
    ("L1", "delete_prompt_simulation", "Delete my test prompt simulation"),
    ("L1", "delete_prompt_template", "Delete the test-prompt template"),
    ("L1", "run_new_evals_on_simulation", "Run new evaluations on my simulation"),
    ("L1", "stop_optimization_run", "Stop my running optimization"),
    ("L1", "get_optimization_graph", "Show the optimization progress graph"),
    ("L1", "get_optimization_steps", "Show optimization steps"),
    ("L1", "get_optimization_trial", "Show the latest optimization trial"),
    ("L1", "get_trial_evaluations", "Show evaluations for the latest trial"),
    ("L1", "get_trial_prompt", "Show the prompt from the latest trial"),
    ("L1", "get_trial_scenarios", "Show scenarios from the latest trial"),
    # ── Experiment extended ──
    ("L1", "compare_experiments", "Compare my two most recent experiments"),
    ("L1", "run_experiment_evals", "Run evaluations on my latest experiment"),
    ("L1", "delete_experiment", "Delete my oldest experiment"),
    # ── Simulation / Agent extended ──
    ("L1", "activate_agent_version", "Activate the latest version of my agent"),
    ("L1", "duplicate_agent_definition", "Duplicate my agent definition"),
    ("L1", "delete_agent_definition", "Delete my test agent"),
    ("L1", "add_scenario_columns", "Add columns to my test scenario"),
    ("L1", "add_scenario_rows", "Add rows to my test scenario"),
    ("L1", "delete_scenario", "Delete my test scenario"),
    ("L1", "update_simulator_agent", "Update my simulator agent settings"),
    ("L1", "duplicate_persona", "Duplicate the angry customer persona"),
    ("L1", "delete_persona", "Delete my test persona"),
    ("L1", "update_run_test", "Update my test run name"),
    ("L1", "delete_run_test", "Delete my test run"),
    ("L1", "rerun_test_execution", "Rerun my latest test execution"),
    ("L1", "cancel_test_execution", "Cancel my running test"),
    ("L1", "delete_test_execution", "Delete my latest test execution"),
    # ── Annotation extended ──
    ("L1", "get_annotate_row", "Show the next row to annotate"),
    ("L1", "submit_annotation", "Submit an annotation for the current row"),
    ("L1", "add_queue_items", "Add items to my annotation queue"),
    ("L1", "get_queue_progress", "Show progress on my annotation queue"),
    ("L1", "submit_queue_annotations", "Submit batch annotations"),
    ("L1", "reset_annotations", "Reset annotations for my queue"),
    ("L1", "update_annotation_label", "Update my annotation label"),
    ("L1", "update_annotation_queue", "Update my annotation queue settings"),
    ("L1", "delete_annotation_label", "Delete my test annotation label"),
    ("L1", "delete_annotation_queue", "Delete my test annotation queue"),
    # ── Admin extended ──
    ("L1", "create_workspace", "Create a new workspace called 'test-ws'"),
    ("L1", "update_workspace", "Update workspace description"),
    ("L1", "add_workspace_member", "Add a member to my workspace"),
    ("L1", "invite_users", "Invite a new user to my organization"),
    ("L1", "update_user_role", "Update a user's role to editor"),
    ("L1", "remove_user", "Remove a test user from the workspace"),
    ("L1", "deactivate_user", "Deactivate a test user"),
    ("L1", "create_api_key", "Create a new API key"),
    ("L1", "revoke_api_key", "Revoke my test API key"),
    ("L1", "delete_memory", "Delete a saved memory"),
]


# ─── TEST RUNNER (reuse from test_matrix.py) ─────────────────────────────────

import asyncio
import json
import random
import subprocess
import sys
import time


def get_token():
    r = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            "http://localhost:8001/accounts/token/",
            "-H",
            "Content-Type: application/json",
            "-d",
            '{"email":"nikhil@futureagi.com","password":"test@123"}',
        ],
        capture_output=True,
        text=True,
    )
    return json.loads(r.stdout)["access"]


def create_conv(token, title):
    r = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            "http://localhost:8001/falcon-ai/conversations/",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps({"title": title}),
        ],
        capture_output=True,
        text=True,
    )
    d = json.loads(r.stdout)
    return d.get("result", {}).get("id", d.get("id", ""))


async def run_test(ws_uri, conv_id, message, timeout=120):
    import websockets

    tools_called = []
    response = ""
    errors = []

    try:
        async with websockets.connect(
            ws_uri, max_size=10_000_000, ping_interval=30, ping_timeout=60
        ) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "chat",
                        "message": message,
                        "conversation_id": conv_id,
                        "context": {"page": "falcon-ai"},
                        "file_ids": [],
                    }
                )
            )

            start = time.time()
            while time.time() - start < timeout:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=20)
                    d = json.loads(raw)
                    t = d.get("type", "")
                    if t == "text_delta":
                        response += d.get("data", {}).get("delta", "")
                    elif t == "tool_call_start":
                        tools_called.append(d.get("data", {}).get("tool_name", ""))
                    elif t == "tool_call_result":
                        if d.get("data", {}).get("status") == "error":
                            err = d.get("data", {}).get("result_summary", "")
                            if not any(
                                x in err.lower()
                                for x in [
                                    "already exists",
                                    "use this id",
                                    "documentation search",
                                ]
                            ):
                                errors.append(err)
                    elif t == "error":
                        errors.append(d.get("message", ""))
                        break
                    elif t == "done":
                        break
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        errors.append(f"Connection: {e}")

    elapsed = time.time() - start if "start" in dir() else 0
    has_response = len(response) > 20
    has_tools = len(tools_called) > 0

    status = "PASS"
    if not has_response and not has_tools:
        status = "FAIL_NO_RESPONSE"
    elif len(errors) > 0 and not has_response:
        status = "FAIL_ERROR"
    elif has_response or has_tools:
        status = "PASS"

    return {
        "status": status,
        "tools_called": tools_called,
        "tool_count": len(tools_called),
        "response_len": len(response),
        "errors": errors,
        "elapsed": round(elapsed, 1),
    }


async def run_level(level_tests, token, level_name):
    results = []
    suffix = f"-t{random.randint(1000,9999)}"

    for i, (level, test_id, msg) in enumerate(level_tests):
        # Uniquify resource names
        msg = msg.replace("test-level1", f"test-level1{suffix}")
        msg = msg.replace("l2-", f"l2{suffix}-")
        msg = msg.replace("l1-", f"l1{suffix}-")
        msg = msg.replace("test-quality-l1", f"test-quality-l1{suffix}")
        msg = msg.replace("test-queue-l1", f"test-queue-l1{suffix}")
        msg = msg.replace("test-prompt-l1", f"test-prompt-l1{suffix}")
        msg = msg.replace("test-project-l1", f"test-project-l1{suffix}")
        msg = msg.replace("test-politeness-l1", f"test-politeness-l1{suffix}")
        msg = msg.replace("test-suite-l1", f"test-suite-l1{suffix}")
        msg = msg.replace("quality-l2", f"quality-l2{suffix}")

        print(f"  [{i+1}/{len(level_tests)}] {test_id}: {msg[:70]}...", flush=True)

        conv_id = create_conv(token, f"{level_name}-{test_id}")
        ws_uri = f"ws://localhost:8001/ws/falcon-ai/?token={token}"

        timeout = 120 if level == "L1" else 180 if level in ("L2", "L3") else 600
        result = await run_test(ws_uri, conv_id, msg, timeout=timeout)
        result["test_id"] = f"{level}/{test_id}"
        result["message"] = msg
        results.append(result)

        emoji = "✅" if result["status"] == "PASS" else "❌"
        print(
            f"    {emoji} {result['status']} | {result['tool_count']} tools | {result['elapsed']}s",
            flush=True,
        )

        await asyncio.sleep(0.5)

    return results


def print_summary(results, level_name):
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if "FAIL" in r["status"])
    avg_tools = sum(r["tool_count"] for r in results) / total if total else 0

    print(f"\n{'='*60}")
    print(
        f"{level_name}: {passed}/{total} passed ({passed*100//total}%) | Avg tools/test: {avg_tools:.1f}"
    )
    print(f"{'='*60}")

    if failed:
        print("\n❌ FAILURES:")
        for r in results:
            if "FAIL" in r["status"]:
                print(f"  {r['test_id']}: {r['status']} | errors={r['errors'][:2]}")

    with open(f"falcon_test_{level_name.lower()}.json", "w") as f:
        json.dump(results, f, indent=2)


async def main():
    level = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 999

    levels = {1: LEVEL_1 + LEVEL_1_EXT, 2: LEVEL_2, 3: LEVEL_3, 4: LEVEL_4}
    tests = levels.get(level, LEVEL_1)
    batch = tests[start : start + size]

    name = f"Level-{level}"
    print(f"Running {name}: {len(batch)} tests (from {len(tests)} total)")

    token = get_token()
    results = await run_level(batch, token, name)
    print_summary(results, name)


if __name__ == "__main__":
    asyncio.run(main())
