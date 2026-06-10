"""Bridge registration for simulation detail APIViews.

Surfaces the FULL existing DRF responses through the bridge instead of
hand-written tools, so MCP and the UI share one source of truth:

- get_call_execution -> CallExecutionDetailView: full call detail including
  turn/latency/WPM/talk-ratio/interruption analytics (TH-5397).
- get_scenario -> ScenarioDetailView: full scenario detail including the
  scenario graph (nodes/edges) (TH-5375).
"""

from ai_tools.drf_bridge import expose_to_mcp
from simulate.views.agent_prompt_optimiser import AgentPromptOptimiserRunViewSet
from simulate.views.agent_version import (
    ActivateAgentVersionView,
    AgentVersionCallExecutionView,
    AgentVersionDetailView,
    AgentVersionEvalSummaryView,
    AgentVersionListView,
    CreateAgentVersionView,
    DeleteAgentVersionView,
    RestoreAgentVersionView,
)
from simulate.views.call_transcript import (
    CallBranchAnalysisView,
    CallTranscriptView,
    TestExecutionTranscriptsView,
)
from simulate.views.prompt_simulation import (
    ExecutePromptSimulationView,
    PromptSimulationDetailView,
    PromptSimulationScenariosView,
)
from simulate.views.run_test import (
    AddEvalConfigView,
    AllActiveTestsView,
    CallExecutionDetailView,
    CallExecutionErrorLocalizerTasksView,
    CallExecutionLogsView,
    CallExecutionRerunView,
    CreateRunTestView,
    CSVExportView,
    DeleteEvalConfigView,
    GetEvalConfigStructureView,
    PerformanceSummaryView,
    RunNewEvalsOnTestExecutionView,
    RunTestAnalyticsView,
    RunTestComponentsUpdateView,
    RunTestDeleteView,
    RunTestDetailView,
    RunTestEvalExplanationSummaryRefreshView,
    RunTestEvalExplanationSummaryView,
    RunTestEvalSummaryComparisonView,
    RunTestEvalSummaryView,
    RunTestExecutionsView,
    RunTestExecutionView,
    RunTestKPIsView,
    RunTestListView,
    RunTestScenariosView,
    TestExecutionAnalyticsView,
    TestExecutionBulkDeleteView,
    TestExecutionCancelView,
    TestExecutionDeleteView,
    TestExecutionDetailView,
    TestExecutionOptimiserAnalysisRefreshView,
    TestExecutionOptimiserAnalysisView,
    TestExecutionRerunView,
    TestExecutionStatusView,
    UpdateEvalConfigView,
)
from simulate.views.scenarios import (
    AddScenarioColumnsView,
    AddScenarioRowsView,
    CreateScenarioView,
    DeleteScenarioView,
    EditScenarioPromptsView,
    EditScenarioView,
    ScenarioDetailView,
    ScenariosListView,
)
from simulate.views.simulator_agent import (
    CreateSimulatorAgentView,
    DeleteSimulatorAgentView,
    EditSimulatorAgentView,
    SimulatorAgentDetailView,
    SimulatorAgentListView,
)

# get_eval_explanation_summary -> RunTestEvalExplanationSummaryView.get(request,
# test_execution_id): the AI-generated cluster analysis of a test execution's
# eval results (groups eval outcomes by metric with success/failure themes and
# root causes — the Analytics-tab summary). Had no MCP tool (part of TH-3726);
# bridge the existing read API. Auto-triggers generation if not yet run and
# returns status while pending.
expose_to_mcp(
    category="simulation",
    tools={
        "retrieve": {
            "name": "get_eval_explanation_summary",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Get the AI eval-explanation summary for a test execution — the "
                "cluster analysis of its eval results grouped by metric, with "
                "success/failure themes and root causes. Provide the "
                "test_execution_id (from list_test_executions). If not yet "
                "generated it is triggered and a pending status is returned; "
                "call again once ready."
            ),
        }
    },
)(RunTestEvalExplanationSummaryView)

# get_run_test_analytics -> RunTestAnalyticsView.get(request, run_test_id):
# aggregated analytics for a run test ACROSS its test executions (fail-rate
# trends, eval-category breakdowns, per-execution comparison) — the run-test
# level "how is this suite trending" view. Only get_test_execution_analytics
# (single execution) was reachable before; this adds the cross-execution
# rollup the Simulate analytics tab shows (TH-3726). Bridges the existing API.
expose_to_mcp(
    category="simulation",
    tools={
        "retrieve": {
            "name": "get_run_test_analytics",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Get aggregated analytics for a run test across ALL its test "
                "executions — fail-rate trends over runs, evaluation-category "
                "breakdowns, and per-execution comparison. Use this for "
                "'how is my agent trending across test runs'. Provide the "
                "run_test_id (from list_run_tests). For a single execution's "
                "analytics use get_test_execution_analytics instead."
            ),
        }
    },
)(RunTestAnalyticsView)

# list_run_tests / get_run_test -> RunTestListView.get / RunTestDetailView.get.
# A "run test" is a saved simulation suite (agent- or prompt-based). This is the
# KEYSTONE of the simulation MCP chain: the run_test_id it returns is required by
# list_test_executions, export_test_execution_csv and the analytics tools, which
# previously had NO way to obtain it via MCP — so the whole suite was unreachable
# (TH-5399, and the cascade that blocked TH-5386 / TH-5397 / TH-5385). The DRF
# views already exist; we only bridge them (no custom tool). "list" has no .list()
# on the APIView so the bridge falls back to .get(); "retrieve" routes run_test_id
# to the .get(request, run_test_id) kwarg via pk_kwarg.
expose_to_mcp(
    category="simulation",
    tools={
        "list": {
            "name": "list_run_tests",
            "entity": "run test",
            "description": (
                "List the run tests (saved simulation suites) in the workspace, "
                "newest first. THIS is where a run_test_id comes from — the id "
                "required by list_test_executions, export_test_execution_csv and "
                "the test-analytics tools. Filter by name (search) or source "
                "(simulation_type = 'agent_definition' | 'prompt')."
            ),
            "query_params": {
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Filter run tests by name.",
                },
                "simulation_type": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Filter by source type: 'agent_definition' or 'prompt'."
                    ),
                },
                "limit": {
                    "type": int,
                    "required": False,
                    "description": "Items per page (default 10).",
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "Page number (default 1).",
                },
            },
        }
    },
)(RunTestListView)

expose_to_mcp(
    category="simulation",
    tools={
        "retrieve": {
            "name": "get_run_test",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Get full detail for one run test (saved simulation suite) by id "
                "— its agent/prompt source, scenarios, attached eval configs and "
                "recent executions. Get the id from list_run_tests."
            ),
        }
    },
)(RunTestDetailView)

# Detail GET handlers take the id as a named URL kwarg (call_execution_id /
# scenario_id), so the bridge needs pk_kwarg to route the id correctly.
# Use the "retrieve" action: it's a DETAIL action (so the bridge builds an `id`
# input param and routes it), and for an APIView it resolves to the `.get()`
# handler. pk_kwarg names the handler's URL kwarg (call_execution_id /
# scenario_id) so the id reaches `get(request, <kwarg>=id)`.
expose_to_mcp(
    category="agents",
    tools={
        "retrieve": {
            "name": "get_call_execution",
            "pk_kwarg": "call_execution_id",
            # APIViews have no auto-discovered list tool, so tell the agent where
            # the id comes from (so search_tools surfaces the prerequisite call).
            "id_source": "get_test_execution",
        }
    },
)(CallExecutionDetailView)

expose_to_mcp(
    category="simulation",
    tools={
        "retrieve": {
            "name": "get_scenario",
            "pk_kwarg": "scenario_id",
            "id_source": "list_scenarios",
        }
    },
)(ScenarioDetailView)

# export_test_execution_csv -> CSVExportView.get(request, item_id): the same
# "Export Data" CSV the Simulate UI offers (TH-5386). It's a detail GET keyed by
# item_id with a required `type` (runtest|testexecution) plus optional
# search/status. The bridge collects the id + those query params (detail +
# query_params), routes item_id to the URL kwarg, and the CSV body is returned
# as text via _unwrap_response.
expose_to_mcp(
    category="agents",
    tools={
        "retrieve": {
            "name": "export_test_execution_csv",
            "pk_kwarg": "item_id",
            "id_source": "list_test_executions",
            "entity": "run test or test execution",
            "description": (
                "Export a run test's or test execution's call data as CSV "
                "(the same export the Simulate UI offers). Provide `id` (the "
                "run test id or test execution id) and `type` to say which it "
                "is. Returns CSV text."
            ),
            "query_params": {
                "type": {
                    "type": str,
                    "required": True,
                    "description": (
                        "Export source type: 'runtest' (id is a run test id) "
                        "or 'testexecution' (id is a test execution id)."
                    ),
                },
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Optional call-execution search term.",
                },
                "status": {
                    "type": str,
                    "required": False,
                    "description": "Optional call-execution status filter.",
                },
            },
        }
    },
)(CSVExportView)

# get_fix_my_agent_analysis -> TestExecutionOptimiserAnalysisView.get(request,
# test_execution_id): the "Fix My Agent" optimiser analysis the UI shows for a
# test execution — the AI-generated summary of what went wrong + prioritized
# suggestions. It had no MCP tool (TH-5385); bridge the existing read API. The
# view auto-triggers/creates the analysis if not yet run and returns status
# info while it is pending.
expose_to_mcp(
    category="agents",
    tools={
        "retrieve": {
            "name": "get_fix_my_agent_analysis",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Get the 'Fix My Agent' optimiser analysis for a test execution "
                "— the AI-generated summary of what went wrong and the "
                "prioritized, actionable suggestions to improve the agent. "
                "Provide the test execution id (from get_test_execution / "
                "list_test_executions). If the analysis hasn't run yet it is "
                "triggered and status info is returned; call again once ready."
            ),
        }
    },
)(TestExecutionOptimiserAnalysisView)

# ---------------------------------------------------------------------------
# Simulation WRITE lifecycle — previously you could LIST/cancel a test
# execution but had no way to CREATE or RUN one via MCP, so the whole simulate
# happy-path (build a scenario -> create a run test -> run it -> poll) was
# unreachable. These bridge the existing DRF write APIViews (no custom tools);
# the request body is auto-derived from each view's @validated_request
# request_serializer, so the agent gets the real field contract.
# ---------------------------------------------------------------------------

# create_scenario -> CreateScenarioView.post (ScenarioCreateRequestSerializer):
# create a scenario (a set of simulation test cases) of kind dataset/script/
# graph. Creation runs async (returns 202 + the new scenario id with PROCESSING
# status). The scenario id feeds create_run_test's scenario_ids.
expose_to_mcp(
    category="simulation",
    tools={
        "create": {
            "name": "create_scenario",
            "entity": "scenario",
            "description": (
                "Create a scenario (a set of simulation test cases) for an agent. "
                "'kind' is 'dataset' (copy cases from a dataset), 'script', or "
                "'graph'. Creation runs asynchronously and returns the new scenario "
                "id with a PROCESSING status — poll get_scenario until it is ready. "
                "Use the scenario id in create_run_test's scenario_ids."
            ),
        }
    },
)(CreateScenarioView)

# create_run_test -> CreateRunTestView.post (CreateRunTestSerializer): create a
# run test (saved simulation suite) for an agent. Requires name +
# agent_definition_id + scenario_ids; optionally attach eval configs. Returns
# the new run_test_id (the keystone id for execute_run_test / the analytics
# tools).
expose_to_mcp(
    category="simulation",
    tools={
        "create": {
            "name": "create_run_test",
            "entity": "run test",
            "description": (
                "Create a run test (a saved simulation suite) for an agent. Provide "
                "name, agent_definition_id (from list_agents) and scenario_ids "
                "(from list_scenarios / create_scenario). Optionally attach "
                "evaluations via eval_config_ids or evaluations_config. Returns the "
                "new run_test_id — then start it with execute_run_test."
            ),
        }
    },
)(CreateRunTestView)

# execute_run_test -> RunTestExecutionView.post(request, run_test_id)
# (ExecuteRunTestSerializer): start running a run test's scenarios against the
# agent, creating a test execution. Detail POST — the id routes to run_test_id;
# optional scenario_ids body limits it to a subset. Returns execution_id +
# status; poll with get_test_execution_status.
expose_to_mcp(
    category="simulation",
    tools={
        "execute": {
            "name": "execute_run_test",
            "method": "POST",
            "detail": True,
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Start executing a run test — runs its scenarios against the agent "
                "and creates a test execution. Provide the run_test_id (from "
                "list_run_tests / create_run_test). Optionally pass scenario_ids to "
                "run only a subset. Returns the execution_id and initial status; "
                "poll progress with get_test_execution_status."
            ),
        }
    },
)(RunTestExecutionView)

# get_test_execution_status -> TestExecutionStatusView.get(request, run_test_id):
# live status/progress of a run test's current execution (overall status +
# per-scenario/call progress). Detail GET keyed by run_test_id. Use to poll
# after execute_run_test.
expose_to_mcp(
    category="simulation",
    tools={
        "retrieve": {
            "name": "get_test_execution_status",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Get the live execution status and progress of a run test's current "
                "test execution — overall status plus per-scenario/call progress. "
                "Provide the run_test_id (from list_run_tests). Use this to poll "
                "after calling execute_run_test."
            ),
        }
    },
)(TestExecutionStatusView)

# ===========================================================================
# Phase 2A Packet C — simulation tail (cluster 3) + simulate-side optimiser
# @actions (cluster 6 part). Same-name HW conversions register the bridge
# under the legacy tool name; the legacy module is deleted in the same
# commit (see ai_tools/tools/__init__.py).
#
# Phase 3A: TestExecutionBulkDeleteView bridged as bulk_delete_test_executions
# (confirmation-gated; end of this module).
# Kept as hand-written (NO DRF endpoint exists — §6.3-style endpoint work):
#   compare_agent_versions, list_simulate_eval_configs,
#   list_eval_mapping_options, duplicate_agent_definition.
# Adjudicated: delete_agent_definition (DeleteAgentDefinitionView) RETIRED —
#   exact duplicate of the bridged delete_agent (ViewSet destroy); one
#   surface only, per spec.
# ===========================================================================

# --- Scenarios (simulate/views/scenarios.py) -------------------------------

# list_scenarios (conversion) -> ScenariosListView.get. The view validates
# query params with ScenarioFilterSerializer(reject_unknown_fields=True), so
# the input declares EXACTLY those fields (a stray page_size would 400).
expose_to_mcp(
    category="agents",
    tools={
        "list": {
            "name": "list_scenarios",
            "entity": "scenario",
            "description": (
                "List scenarios (sets of simulation test cases) in the "
                "workspace, newest first. THIS is where a scenario_id comes "
                "from — required by get_scenario, update_scenario, "
                "add_scenario_rows/columns and create_run_test.scenario_ids."
            ),
            "query_params": {
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Filter scenarios by name/source.",
                },
                "agent_definition_id": {
                    "type": str,
                    "required": False,
                    "description": "Only scenarios for this agent definition.",
                },
                "agent_type": {
                    "type": str,
                    "required": False,
                    "description": "Filter by agent type (e.g. 'voice', 'chat').",
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "1-indexed page number (default 1).",
                },
                "limit": {
                    "type": int,
                    "required": False,
                    "description": "Items per page (default 10).",
                },
            },
        }
    },
)(ScenariosListView)

# update_scenario (conversion) -> EditScenarioView.put(scenario_id)
# (ScenarioEditRequestSerializer: name/description/graph/prompt).
expose_to_mcp(
    category="simulation",
    tools={
        "edit_scenario": {
            "name": "update_scenario",
            "method": "PUT",
            "detail": True,
            "pk_field": "scenario_id",
            "pk_kwarg": "scenario_id",
            "id_source": "list_scenarios",
            "entity": "scenario",
            "description": (
                "Update a scenario's name, description, graph (nodes/edges "
                "JSON) or prompt. Provide the scenario_id (from "
                "list_scenarios); only the fields you pass are changed."
            ),
        }
    },
)(EditScenarioView)

# delete_scenario (conversion) -> DeleteScenarioView.delete(scenario_id).
# query_params={} = deliberately body-less write (the A2 escape hatch).
expose_to_mcp(
    category="simulation",
    tools={
        "delete_scenario_by_id": {
            "name": "delete_scenario",
            "method": "DELETE",
            "detail": True,
            "pk_field": "scenario_id",
            "pk_kwarg": "scenario_id",
            "id_source": "list_scenarios",
            "entity": "scenario",
            "query_params": {},
            "description": (
                "Delete (soft-delete) a scenario by id. Get the scenario_id "
                "from list_scenarios."
            ),
        }
    },
)(DeleteScenarioView)

# add_scenario_rows -> AddScenarioRowsView.post(scenario_id)
# (ScenarioAddRowsRequestSerializer: num_rows 10-20000 + description).
expose_to_mcp(
    category="simulation",
    tools={
        "add_rows": {
            "name": "add_scenario_rows",
            "method": "POST",
            "detail": True,
            "pk_field": "scenario_id",
            "pk_kwarg": "scenario_id",
            "id_source": "list_scenarios",
            "entity": "scenario",
            "description": (
                "Generate and add more test-case rows to a scenario "
                "(AI-generated from the scenario's description). num_rows "
                "must be between 10 and 20000. Runs asynchronously — poll "
                "get_scenario for the row count."
            ),
        }
    },
)(AddScenarioRowsView)

# add_scenario_columns -> AddScenarioColumnsView.post(scenario_id)
# (ScenarioAddColumnsRequestSerializer: columns=[{name, description}, ...]).
expose_to_mcp(
    category="simulation",
    tools={
        "add_columns": {
            "name": "add_scenario_columns",
            "method": "POST",
            "detail": True,
            "pk_field": "scenario_id",
            "pk_kwarg": "scenario_id",
            "id_source": "list_scenarios",
            "entity": "scenario",
            "description": (
                "Add up to 10 new columns to a scenario; each column is "
                "{name, description} and values are AI-generated for "
                "existing rows. Provide the scenario_id from list_scenarios."
            ),
        }
    },
)(AddScenarioColumnsView)

# update_scenario_prompts -> EditScenarioPromptsView.put(scenario_id)
# (ScenarioEditPromptsRequestSerializer: prompts).
expose_to_mcp(
    category="simulation",
    tools={
        "edit_prompts": {
            "name": "update_scenario_prompts",
            "method": "PUT",
            "detail": True,
            "pk_field": "scenario_id",
            "pk_kwarg": "scenario_id",
            "id_source": "list_scenarios",
            "entity": "scenario",
            "description": (
                "Replace the prompts text of a scenario (the instruction "
                "block its simulated test cases are driven by). Provide the "
                "scenario_id (from list_scenarios) and the new prompts text."
            ),
        }
    },
)(EditScenarioPromptsView)

# --- Run tests / test executions (simulate/views/run_test.py) --------------

# list_active_tests -> AllActiveTestsView.get: all currently-running test
# executions in the org (live progress across run tests).
expose_to_mcp(
    category="simulation",
    tools={
        "list": {
            "name": "list_active_tests",
            "entity": "active test",
            "description": (
                "List all currently active (running) test executions in the "
                "organization with their live progress. No parameters "
                "required. Use cancel_test_execution to stop one."
            ),
        }
    },
)(AllActiveTestsView)

# get_run_test_kpis -> RunTestKPIsView.get(test_execution_id): combined KPIs
# (total calls, success rate, avg score/response/accuracy/sentiment).
expose_to_mcp(
    category="simulation",
    tools={
        "kpis": {
            "name": "get_run_test_kpis",
            "method": "GET",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Get the combined KPI card values for a test execution — "
                "Total Calls, Success Rate, Avg Score, Avg Response, Avg "
                "Accuracy, Avg Sentiment. Provide the test_execution_id "
                "(from list_test_executions)."
            ),
        }
    },
)(RunTestKPIsView)

# get_performance_summary -> PerformanceSummaryView.get(test_execution_id):
# pass-rate metrics + top performing scenarios.
expose_to_mcp(
    category="simulation",
    tools={
        "performance_summary": {
            "name": "get_performance_summary",
            "method": "GET",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Get the performance summary for a test execution — test-run "
                "performance metrics (pass rate, total test runs, latest "
                "fail rate) and the top performing scenarios with scores. "
                "Provide the test_execution_id (from list_test_executions)."
            ),
        }
    },
)(PerformanceSummaryView)

# cancel_test_execution (conversion) -> TestExecutionCancelView.post via the
# /test-executions/{test_execution_id}/cancel/ route. NOTE: the legacy HW
# tool also accepted run_test_id (cancel latest); the bridged surface is the
# public by-execution route — get the latest execution id from
# list_test_executions first.
expose_to_mcp(
    category="simulation",
    tools={
        "cancel": {
            "name": "cancel_test_execution",
            "method": "POST",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Cancel a running test execution (sends cancellation to the "
                "Temporal/Celery workflow and stops active calls). Provide "
                "the test_execution_id — for a run test's latest execution "
                "get it from list_test_executions first."
            ),
        }
    },
)(TestExecutionCancelView)

# get_test_execution (conversion) -> TestExecutionDetailView.get
# (ExecutionDetailQuerySerializer: search/page/limit; filters/row_groups/
# group_keys are JSON-array query fields not expressible as flat inputs).
expose_to_mcp(
    category="agents",
    tools={
        "execution_detail": {
            "name": "get_test_execution",
            "method": "GET",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Get a test execution's full detail: status, metrics and its "
                "paginated call executions (call ids here feed "
                "get_call_transcript / get_call_logs / get_call_execution). "
                "Provide the test_execution_id (from list_test_executions). "
                "Optional search/page/limit page through the calls."
            ),
        }
    },
)(TestExecutionDetailView)

# list_test_executions (conversion) -> RunTestExecutionsView.get(run_test_id).
# NOTE: now keyed by run_test_id (the real API shape) — this is where a
# test_execution_id comes from.
expose_to_mcp(
    category="agents",
    tools={
        "executions": {
            "name": "list_test_executions",
            "method": "GET",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "List the test executions of a run test, newest first — THIS "
                "is where a test_execution_id comes from. Provide the "
                "run_test_id (from list_run_tests). Optional search/status "
                "filters and limit/page pagination."
            ),
            "query_params": {
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Filter by status or scenario name.",
                },
                "status": {
                    "type": str,
                    "required": False,
                    "description": "Filter by execution status.",
                },
                "limit": {
                    "type": int,
                    "required": False,
                    "description": "Items per page (default 10).",
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "1-indexed page number (default 1).",
                },
            },
        }
    },
)(RunTestExecutionsView)

# get_test_execution_analytics (conversion) -> TestExecutionAnalyticsView.get.
expose_to_mcp(
    category="simulation",
    tools={
        "analytics": {
            "name": "get_test_execution_analytics",
            "method": "GET",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Get analytics for a single test execution — fail rate over "
                "test runs and evaluation categories over test runs (the "
                "Analytics tab charts). Provide the test_execution_id (from "
                "list_test_executions). For the cross-execution rollup use "
                "get_run_test_analytics."
            ),
        }
    },
)(TestExecutionAnalyticsView)

# delete_test_execution (conversion) -> TestExecutionDeleteView.delete.
# The legacy HW tool's bulk mode (run_test_id + select_all) maps to
# TestExecutionBulkDeleteView, bridged in Phase 3A (end of this module).
expose_to_mcp(
    category="simulation",
    tools={
        "delete_execution": {
            "name": "delete_test_execution",
            "method": "DELETE",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "query_params": {},
            "description": (
                "Delete (soft-delete) a single test execution by id. Get the "
                "test_execution_id from list_test_executions."
            ),
        }
    },
)(TestExecutionDeleteView)

# delete_run_test (conversion) -> RunTestDeleteView.delete(run_test_id).
expose_to_mcp(
    category="simulation",
    tools={
        "delete_run_test_by_id": {
            "name": "delete_run_test",
            "method": "DELETE",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "query_params": {},
            "description": (
                "Delete (soft-delete) a run test (saved simulation suite) by "
                "id. Get the run_test_id from list_run_tests."
            ),
        }
    },
)(RunTestDeleteView)

# rerun_test_execution -> TestExecutionRerunView.post(run_test_id)
# (TestExecutionRerunSerializer: rerun_type + test_execution_ids|select_all).
expose_to_mcp(
    category="simulation",
    tools={
        "rerun_executions": {
            "name": "rerun_test_execution",
            "method": "POST",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Rerun test executions of a run test. rerun_type is "
                "'eval_only' (re-evaluate existing calls) or 'call_and_eval' "
                "(re-place calls and evaluate). Target specific executions "
                "via test_execution_ids or all via select_all=true. Provide "
                "the run_test_id (from list_run_tests)."
            ),
        }
    },
)(TestExecutionRerunView)

# rerun_call_execution (conversion) -> CallExecutionRerunView.post
# (test_execution_id route + CallExecutionRerunSerializer). NOTE: legacy HW
# took a single call_execution_id; the real API is keyed by
# test_execution_id with call_execution_ids[]|select_all in the body.
expose_to_mcp(
    category="simulation",
    tools={
        "rerun_calls": {
            "name": "rerun_call_execution",
            "method": "POST",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Rerun call executions inside a test execution. rerun_type "
                "is 'eval_only' or 'call_and_eval'; pass call_execution_ids "
                "(from get_test_execution) or select_all=true. Provide the "
                "test_execution_id."
            ),
        }
    },
)(CallExecutionRerunView)

# run_new_evals_on_test_execution -> RunNewEvalsOnTestExecutionView.post
# (run_test_id + RunNewEvalsOnTestExecutionSerializer).
expose_to_mcp(
    category="simulation",
    tools={
        "run_new_evals": {
            "name": "run_new_evals_on_test_execution",
            "method": "POST",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Run additional evaluations on EXISTING test executions "
                "without re-placing calls. Provide the run_test_id, the "
                "eval_config_ids to run (from get_run_test / "
                "create_simulate_eval_config), and test_execution_ids or "
                "select_all=true."
            ),
        }
    },
)(RunNewEvalsOnTestExecutionView)

# update_run_test_components -> RunTestComponentsUpdateView.patch
# (RunTestComponentsUpdateSerializer: agent_definition_id/version/
# simulator_agent_id/scenarios/enable_tool_evaluation).
expose_to_mcp(
    category="simulation",
    tools={
        "components_update": {
            "name": "update_run_test_components",
            "method": "PATCH",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Update a run test's component references — the agent "
                "definition, agent version, simulator agent, scenario set, "
                "or enable_tool_evaluation flag. Provide the run_test_id "
                "(from list_run_tests); only passed fields change."
            ),
        }
    },
)(RunTestComponentsUpdateView)

# list_run_test_scenarios -> RunTestScenariosView.get(run_test_id).
expose_to_mcp(
    category="simulation",
    tools={
        "scenarios": {
            "name": "list_run_test_scenarios",
            "method": "GET",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "List the scenarios attached to a specific run test "
                "(paginated). Provide the run_test_id (from list_run_tests). "
                "For all workspace scenarios use list_scenarios instead."
            ),
            "query_params": {
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Filter scenarios by name.",
                },
                "limit": {
                    "type": int,
                    "required": False,
                    "description": "Items per page (default 10).",
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "1-indexed page number (default 1).",
                },
            },
        }
    },
)(RunTestScenariosView)

# get_call_error_localizer_tasks -> CallExecutionErrorLocalizerTasksView.get
# (call_execution_id + optional eval_config_id query param).
expose_to_mcp(
    category="simulation",
    tools={
        "error_localizer_tasks": {
            "name": "get_call_error_localizer_tasks",
            "method": "GET",
            "detail": True,
            "pk_field": "call_execution_id",
            "pk_kwarg": "call_execution_id",
            "id_source": "get_test_execution",
            "entity": "call execution",
            "description": (
                "Get the error-localizer tasks for a call execution — the "
                "granular per-turn error localization runs behind its eval "
                "results. Provide the call_execution_id (from "
                "get_test_execution's call list); optionally scope to one "
                "eval_config_id."
            ),
            "query_params": {
                "eval_config_id": {
                    "type": str,
                    "required": False,
                    "description": "Only tasks for this eval config (UUID).",
                },
            },
        }
    },
)(CallExecutionErrorLocalizerTasksView)

# --- Simulate eval configs (run-test attached evaluations) -----------------

# create_simulate_eval_config -> AddEvalConfigView.post(run_test_id)
# (AddEvalConfigsRequestSerializer: evaluations_config=[...]).
expose_to_mcp(
    category="simulation",
    tools={
        "add_eval_configs": {
            "name": "create_simulate_eval_config",
            "method": "POST",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Attach one or more evaluation configs to a run test. "
                "evaluations_config is an array of objects like "
                "{eval_template_id, name, config, mapping}; use "
                "get_simulate_eval_config_structure / list_eval_mapping_options "
                "to discover the expected shape. Provide the run_test_id."
            ),
        }
    },
)(AddEvalConfigView)

# delete_simulate_eval_config (conversion) -> DeleteEvalConfigView.delete
# (run_test_id + eval_config_id — dual URL kwargs via path_kwargs).
expose_to_mcp(
    category="simulation",
    tools={
        "delete_eval_config": {
            "name": "delete_simulate_eval_config",
            "method": "DELETE",
            "detail": True,
            "pk_field": "eval_config_id",
            "pk_kwarg": "eval_config_id",
            "id_source": "get_run_test",
            "entity": "simulate eval config",
            "query_params": {},
            "path_kwargs": {
                "run_test_id": {
                    "description": "UUID of the run test the eval config belongs to.",
                    "id_source": "list_run_tests",
                },
            },
            "description": (
                "Detach/delete an evaluation config from a run test. Provide "
                "the eval_config_id (from get_run_test's eval configs) and "
                "the run_test_id."
            ),
        }
    },
)(DeleteEvalConfigView)

# update_simulate_eval_config (conversion) -> UpdateEvalConfigView.post
# (EvalConfigUpdateRequestSerializer; dual URL kwargs).
expose_to_mcp(
    category="simulation",
    tools={
        "update_eval_config": {
            "name": "update_simulate_eval_config",
            "method": "POST",
            "detail": True,
            "pk_field": "eval_config_id",
            "pk_kwarg": "eval_config_id",
            "id_source": "get_run_test",
            "entity": "simulate eval config",
            "path_kwargs": {
                "run_test_id": {
                    "description": "UUID of the run test the eval config belongs to.",
                    "id_source": "list_run_tests",
                },
            },
            "description": (
                "Update an evaluation config attached to a run test — name, "
                "config parameters, field mapping, model, error_localizer or "
                "kb_id. Set run=true with a test_execution_id to immediately "
                "re-evaluate. Provide eval_config_id and run_test_id."
            ),
        }
    },
)(UpdateEvalConfigView)

# get_simulate_eval_config_structure -> GetEvalConfigStructureView.get
# (dual URL kwargs).
expose_to_mcp(
    category="simulation",
    tools={
        "eval_config_structure": {
            "name": "get_simulate_eval_config_structure",
            "method": "GET",
            "detail": True,
            "pk_field": "eval_config_id",
            "pk_kwarg": "eval_config_id",
            "id_source": "get_run_test",
            "entity": "simulate eval config",
            "path_kwargs": {
                "run_test_id": {
                    "description": "UUID of the run test the eval config belongs to.",
                    "id_source": "list_run_tests",
                },
            },
            "description": (
                "Get the structure of a run test's evaluation config — its "
                "required inputs, config parameters and current mapping (use "
                "before update_simulate_eval_config). Provide eval_config_id "
                "(from get_run_test) and run_test_id."
            ),
        }
    },
)(GetEvalConfigStructureView)

# get_run_test_eval_summary -> RunTestEvalSummaryView.get(run_test_id)
# (EvalSummaryFilterSerializer: optional execution_id).
expose_to_mcp(
    category="simulation",
    tools={
        "eval_summary": {
            "name": "get_run_test_eval_summary",
            "method": "GET",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Get per-evaluation summary statistics for a run test "
                "(pass/fail counts and average scores per eval template), "
                "optionally scoped to one execution via execution_id. "
                "Provide the run_test_id (from list_run_tests)."
            ),
        }
    },
)(RunTestEvalSummaryView)

# compare_run_test_eval_summaries -> RunTestEvalSummaryComparisonView.get
# (EvalSummaryComparisonFilterSerializer: execution_ids JSON array string).
expose_to_mcp(
    category="simulation",
    tools={
        "eval_summary_comparison": {
            "name": "compare_run_test_eval_summaries",
            "method": "GET",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "description": (
                "Compare evaluation summary statistics across multiple test "
                "executions of one run test. execution_ids is a JSON-encoded "
                "array string of test execution UUIDs, e.g. "
                '\'["uuid1","uuid2"]\' (from list_test_executions). Provide '
                "the run_test_id."
            ),
        }
    },
)(RunTestEvalSummaryComparisonView)

# refresh_eval_explanation_summary -> RunTestEvalExplanationSummaryRefreshView
# .post(test_execution_id) (EmptyRequestSerializer — body-less POST).
expose_to_mcp(
    category="simulation",
    tools={
        "refresh_summary": {
            "name": "refresh_eval_explanation_summary",
            "method": "POST",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Force-regenerate the AI eval-explanation summary of a test "
                "execution (the cluster analysis get_eval_explanation_summary "
                "returns). Provide the test_execution_id; poll "
                "get_eval_explanation_summary for the result."
            ),
        }
    },
)(RunTestEvalExplanationSummaryRefreshView)

# refresh_optimiser_analysis -> TestExecutionOptimiserAnalysisRefreshView
# .post(test_execution_id) (EmptyRequestSerializer).
expose_to_mcp(
    category="agents",
    tools={
        "refresh_analysis": {
            "name": "refresh_optimiser_analysis",
            "method": "POST",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Force-regenerate the 'Fix My Agent' optimiser analysis for "
                "a test execution. Provide the test_execution_id; poll "
                "get_fix_my_agent_analysis for the result."
            ),
        }
    },
)(TestExecutionOptimiserAnalysisRefreshView)

# --- Call transcripts / logs (simulate/views/call_transcript.py + run_test) -

# get_call_transcript (conversion) -> CallTranscriptView.get(call_execution_id).
expose_to_mcp(
    category="simulation",
    tools={
        "transcript": {
            "name": "get_call_transcript",
            "method": "GET",
            "detail": True,
            "pk_field": "call_execution_id",
            "pk_kwarg": "call_execution_id",
            "id_source": "get_test_execution",
            "entity": "call execution",
            "description": (
                "Get the ordered conversation transcript of one simulated "
                "call (speaker turns with timings). Provide the "
                "call_execution_id (from get_test_execution's call list). "
                "For every call in an execution at once use "
                "get_test_execution_transcripts."
            ),
        }
    },
)(CallTranscriptView)

# get_test_execution_transcripts -> TestExecutionTranscriptsView.get.
expose_to_mcp(
    category="simulation",
    tools={
        "transcripts": {
            "name": "get_test_execution_transcripts",
            "method": "GET",
            "detail": True,
            "pk_field": "test_execution_id",
            "pk_kwarg": "test_execution_id",
            "id_source": "list_test_executions",
            "entity": "test execution",
            "description": (
                "Get the transcripts of ALL calls in a test execution in one "
                "shot (per-call speaker turns, grouped by call/scenario). "
                "Provide the test_execution_id (from list_test_executions)."
            ),
        }
    },
)(TestExecutionTranscriptsView)

# get_call_branch_analysis -> CallBranchAnalysisView.get(call_execution_id):
# analyzes a call against its scenario graph branches.
expose_to_mcp(
    category="simulation",
    tools={
        "branch_analysis": {
            "name": "get_call_branch_analysis",
            "method": "GET",
            "detail": True,
            "pk_field": "call_execution_id",
            "pk_kwarg": "call_execution_id",
            "id_source": "get_test_execution",
            "entity": "call execution",
            "description": (
                "Analyze a call execution against its scenario's graph "
                "branches — which path the conversation took and where it "
                "deviated from the expected flow. Provide the "
                "call_execution_id (from get_test_execution's call list)."
            ),
        }
    },
)(CallBranchAnalysisView)

# get_call_logs (conversion) -> CallExecutionLogsView.get(call_execution_id).
expose_to_mcp(
    category="simulation",
    tools={
        "logs": {
            "name": "get_call_logs",
            "method": "GET",
            "detail": True,
            "pk_field": "call_execution_id",
            "pk_kwarg": "call_execution_id",
            "id_source": "get_test_execution",
            "entity": "call execution",
            "description": (
                "Get the customer-side runtime log entries of a call "
                "execution (errors, warnings, info emitted during the call). "
                "Provide the call_execution_id (from get_test_execution's "
                "call list); optionally filter by severity_text."
            ),
            "query_params": {
                "severity_text": {
                    "type": str,
                    "required": False,
                    "description": "Filter by severity (e.g. ERROR, WARN, INFO).",
                },
                "customer_call_id": {
                    "type": str,
                    "required": False,
                    "description": (
                        "Look up by provider call id instead of the "
                        "call_execution_id."
                    ),
                },
            },
        }
    },
)(CallExecutionLogsView)

# --- Simulator agents (simulate/views/simulator_agent.py) ------------------

# list_simulator_agents (conversion) -> SimulatorAgentListView.get.
expose_to_mcp(
    category="simulation",
    tools={
        "list": {
            "name": "list_simulator_agents",
            "entity": "simulator agent",
            "description": (
                "List the simulator agents (the synthetic 'customer' agents "
                "that drive simulated calls), paginated, newest first. THIS "
                "is where a simulator_agent_id comes from."
            ),
            "query_params": {
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Search name/prompt/model/voice provider.",
                },
                "limit": {
                    "type": int,
                    "required": False,
                    "description": "Items per page (default 10).",
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "1-indexed page number (default 1).",
                },
            },
        }
    },
)(SimulatorAgentListView)

# create_simulator_agent (conversion) -> CreateSimulatorAgentView.post
# (SimulatorAgentSerializer via @validated_request).
expose_to_mcp(
    category="simulation",
    tools={
        "create": {
            "name": "create_simulator_agent",
            "entity": "simulator agent",
            "description": (
                "Create a simulator agent — the synthetic 'customer' agent "
                "that drives simulated calls (name, system prompt, model, "
                "optional voice settings). Returns the new simulator agent."
            ),
        }
    },
)(CreateSimulatorAgentView)

# get_simulator_agent -> SimulatorAgentDetailView.get(agent_id).
expose_to_mcp(
    category="simulation",
    tools={
        "detail": {
            "name": "get_simulator_agent",
            "method": "GET",
            "detail": True,
            "pk_field": "simulator_agent_id",
            "pk_kwarg": "agent_id",
            "id_source": "list_simulator_agents",
            "entity": "simulator agent",
            "description": (
                "Get one simulator agent's full configuration by id (from "
                "list_simulator_agents)."
            ),
        }
    },
)(SimulatorAgentDetailView)

# update_simulator_agent (conversion) -> EditSimulatorAgentView.put
# (SimulatorAgentSerializer — full PUT contract).
expose_to_mcp(
    category="simulation",
    tools={
        "edit": {
            "name": "update_simulator_agent",
            "method": "PUT",
            "detail": True,
            "pk_field": "simulator_agent_id",
            "pk_kwarg": "agent_id",
            "id_source": "list_simulator_agents",
            "entity": "simulator agent",
            "description": (
                "Update a simulator agent (full replace — PUT): name, "
                "prompt, model and voice settings. Provide the "
                "simulator_agent_id (from list_simulator_agents)."
            ),
        }
    },
)(EditSimulatorAgentView)

# delete_simulator_agent -> DeleteSimulatorAgentView.delete(agent_id).
expose_to_mcp(
    category="simulation",
    tools={
        "remove": {
            "name": "delete_simulator_agent",
            "method": "DELETE",
            "detail": True,
            "pk_field": "simulator_agent_id",
            "pk_kwarg": "agent_id",
            "id_source": "list_simulator_agents",
            "entity": "simulator agent",
            "query_params": {},
            "description": (
                "Delete a simulator agent by id (from "
                "list_simulator_agents)."
            ),
        }
    },
)(DeleteSimulatorAgentView)

# --- Agent versions (simulate/views/agent_version.py) ----------------------
# All these handlers take agent_id (+ version_id) URL kwargs: agent_id rides
# pk routing for the list/create, and version-scoped tools put version_id on
# pk + agent_id on path_kwargs (A5 dual-kwarg support).

# list_agent_versions -> AgentVersionListView.get(agent_id).
expose_to_mcp(
    category="simulation",
    tools={
        "versions": {
            "name": "list_agent_versions",
            "method": "GET",
            "detail": True,
            "pk_field": "agent_id",
            "pk_kwarg": "agent_id",
            "id_source": "list_agents",
            "entity": "agent definition",
            "description": (
                "List all versions of an agent definition (version ids, "
                "numbers, status, scores) — THIS is where a version_id comes "
                "from. Provide the agent_id (from list_agents)."
            ),
        }
    },
)(AgentVersionListView)

# create_agent_version -> CreateAgentVersionView.post(agent_id)
# (AgentVersionCreateRequestSerializer: agent fields to change + commit info).
expose_to_mcp(
    category="simulation",
    tools={
        "create_version": {
            "name": "create_agent_version",
            "method": "POST",
            "detail": True,
            "pk_field": "agent_id",
            "pk_kwarg": "agent_id",
            "id_source": "list_agents",
            "entity": "agent definition",
            "description": (
                "Create a new version of an agent definition — pass only the "
                "agent fields you want changed (prompt/model/provider/etc.); "
                "a version snapshot is committed. Provide the agent_id (from "
                "list_agents)."
            ),
        }
    },
)(CreateAgentVersionView)

# get_agent_version (conversion) -> AgentVersionDetailView.get(agent_id,
# version_id).
expose_to_mcp(
    category="simulation",
    tools={
        "version_detail": {
            "name": "get_agent_version",
            "method": "GET",
            "detail": True,
            "pk_field": "version_id",
            "pk_kwarg": "version_id",
            "id_source": "list_agent_versions",
            "entity": "agent version",
            "path_kwargs": {
                "agent_id": {
                    "description": "UUID of the agent definition the version belongs to.",
                    "id_source": "list_agents",
                },
            },
            "description": (
                "Get one agent version's full snapshot (prompt, model, "
                "config, score). Provide version_id (from "
                "list_agent_versions) and agent_id (from list_agents)."
            ),
        }
    },
)(AgentVersionDetailView)

# activate_agent_version (conversion) -> ActivateAgentVersionView.post
# (EmptyRequestSerializer — body-less).
expose_to_mcp(
    category="simulation",
    tools={
        "activate": {
            "name": "activate_agent_version",
            "method": "POST",
            "detail": True,
            "pk_field": "version_id",
            "pk_kwarg": "version_id",
            "id_source": "list_agent_versions",
            "entity": "agent version",
            "path_kwargs": {
                "agent_id": {
                    "description": "UUID of the agent definition the version belongs to.",
                    "id_source": "list_agents",
                },
            },
            "description": (
                "Activate an agent version — make it the version the agent "
                "runs with. Provide version_id (from list_agent_versions) "
                "and agent_id (from list_agents)."
            ),
        }
    },
)(ActivateAgentVersionView)

# delete_agent_version -> DeleteAgentVersionView.delete(agent_id, version_id).
expose_to_mcp(
    category="simulation",
    tools={
        "delete_version": {
            "name": "delete_agent_version",
            "method": "DELETE",
            "detail": True,
            "pk_field": "version_id",
            "pk_kwarg": "version_id",
            "id_source": "list_agent_versions",
            "entity": "agent version",
            "query_params": {},
            "path_kwargs": {
                "agent_id": {
                    "description": "UUID of the agent definition the version belongs to.",
                    "id_source": "list_agents",
                },
            },
            "description": (
                "Delete (soft-delete) an agent version. Provide version_id "
                "(from list_agent_versions) and agent_id (from list_agents). "
                "Restore later with restore_agent_version."
            ),
        }
    },
)(DeleteAgentVersionView)

# restore_agent_version -> RestoreAgentVersionView.post (EmptyRequestSerializer).
expose_to_mcp(
    category="simulation",
    tools={
        "restore": {
            "name": "restore_agent_version",
            "method": "POST",
            "detail": True,
            "pk_field": "version_id",
            "pk_kwarg": "version_id",
            "id_source": "list_agent_versions",
            "entity": "agent version",
            "path_kwargs": {
                "agent_id": {
                    "description": "UUID of the agent definition the version belongs to.",
                    "id_source": "list_agents",
                },
            },
            "description": (
                "Restore a previously deleted agent version. Provide "
                "version_id and agent_id."
            ),
        }
    },
)(RestoreAgentVersionView)

# get_agent_version_eval_summary -> AgentVersionEvalSummaryView.get.
expose_to_mcp(
    category="simulation",
    tools={
        "version_eval_summary": {
            "name": "get_agent_version_eval_summary",
            "method": "GET",
            "detail": True,
            "pk_field": "version_id",
            "pk_kwarg": "version_id",
            "id_source": "list_agent_versions",
            "entity": "agent version",
            "path_kwargs": {
                "agent_id": {
                    "description": "UUID of the agent definition the version belongs to.",
                    "id_source": "list_agents",
                },
            },
            "description": (
                "Get the evaluation summary for one agent version — how its "
                "simulated calls scored per evaluation. Provide version_id "
                "(from list_agent_versions) and agent_id."
            ),
        }
    },
)(AgentVersionEvalSummaryView)

# list_agent_version_call_executions -> AgentVersionCallExecutionView.get.
expose_to_mcp(
    category="simulation",
    tools={
        "version_calls": {
            "name": "list_agent_version_call_executions",
            "method": "GET",
            "detail": True,
            "pk_field": "version_id",
            "pk_kwarg": "version_id",
            "id_source": "list_agent_versions",
            "entity": "agent version",
            "path_kwargs": {
                "agent_id": {
                    "description": "UUID of the agent definition the version belongs to.",
                    "id_source": "list_agents",
                },
            },
            "description": (
                "List the call executions made with one agent version "
                "(across its test runs). Provide version_id (from "
                "list_agent_versions) and agent_id."
            ),
        }
    },
)(AgentVersionCallExecutionView)

# --- Prompt-based simulations (simulate/views/prompt_simulation.py) --------
# A 'prompt simulation' is a run test whose source is a prompt template
# version (instead of an agent definition). Legacy HW tools called the
# run-test id 'simulation_id'; the bridged tools use the real URL kwarg
# names (prompt_template_id + run_test_id).

# execute_prompt_simulation (conversion) -> ExecutePromptSimulationView.post
# (ExecutePromptSimulationRequestSerializer: scenario_ids|select_all).
expose_to_mcp(
    category="prompts",
    tools={
        "execute": {
            "name": "execute_prompt_simulation",
            "method": "POST",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "prompt simulation",
            "path_kwargs": {
                "prompt_template_id": {
                    "description": (
                        "UUID of the prompt template the simulation belongs "
                        "to (from list_prompt_templates)."
                    ),
                    "id_source": "list_prompt_templates",
                },
            },
            "description": (
                "Start executing a prompt-based simulation (a run test whose "
                "agent source is a prompt version). Provide run_test_id (the "
                "simulation's run test, from list_run_tests with "
                "simulation_type='prompt') and prompt_template_id. Optional "
                "scenario_ids or select_all=true. Poll "
                "get_test_execution_status."
            ),
        }
    },
)(ExecutePromptSimulationView)

# update_prompt_simulation (conversion) -> PromptSimulationDetailView.patch
# (PromptSimulationUpdateRequestSerializer); delete_prompt_simulation
# (conversion) -> .delete on the same view.
expose_to_mcp(
    category="prompts",
    tools={
        "update_simulation": {
            "name": "update_prompt_simulation",
            "method": "PATCH",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "prompt simulation",
            "path_kwargs": {
                "prompt_template_id": {
                    "description": (
                        "UUID of the prompt template the simulation belongs "
                        "to (from list_prompt_templates)."
                    ),
                    "id_source": "list_prompt_templates",
                },
            },
            "description": (
                "Update a prompt-based simulation — name, description, "
                "prompt_version_id, scenario_ids or enable_tool_evaluation. "
                "Provide run_test_id and prompt_template_id."
            ),
        },
        "delete_simulation": {
            "name": "delete_prompt_simulation",
            "method": "DELETE",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "prompt simulation",
            "query_params": {},
            "path_kwargs": {
                "prompt_template_id": {
                    "description": (
                        "UUID of the prompt template the simulation belongs "
                        "to (from list_prompt_templates)."
                    ),
                    "id_source": "list_prompt_templates",
                },
            },
            "description": (
                "Delete (soft-delete) a prompt-based simulation run. Provide "
                "run_test_id and prompt_template_id."
            ),
        },
    },
)(PromptSimulationDetailView)

# list_prompt_scenarios (conversion) -> PromptSimulationScenariosView.get.
expose_to_mcp(
    category="prompts",
    tools={
        "list": {
            "name": "list_prompt_scenarios",
            "entity": "prompt scenario",
            "description": (
                "List the scenarios available for prompt-based simulations "
                "(id, name, type, dataset), paginated. Use the ids in "
                "execute_prompt_simulation / update_prompt_simulation."
            ),
            "query_params": {
                "search": {
                    "type": str,
                    "required": False,
                    "description": "Filter scenarios by name.",
                },
                "limit": {
                    "type": int,
                    "required": False,
                    "description": "Items per page (default 20).",
                },
                "page": {
                    "type": int,
                    "required": False,
                    "description": "1-indexed page number (default 1).",
                },
            },
        }
    },
)(PromptSimulationScenariosView)

# --- Agent prompt optimiser run @actions (cluster 6, simulate side) --------
# AgentPromptOptimiserRunViewSet CRUD is already bridged in _misc_viewsets.py
# (*_agent_prompt_optimiser_run); per A10 the @action additions register
# additively HERE. method/detail auto-derive from the DRF @action decorator;
# trial-scoped actions carry the extra trial_id URL kwarg via path_kwargs.
expose_to_mcp(
    category="optimization",
    tools={
        "steps": {
            "name": "get_optimiser_run_steps",
            "pk_field": "run_id",
            "id_source": "list_agent_prompt_optimiser_runs",
            "entity": "agent prompt optimiser run",
            "description": (
                "Get the step timeline of an agent prompt optimiser run "
                "(each optimisation step with status/results). Provide the "
                "run_id (from list_agent_prompt_optimiser_runs)."
            ),
        },
        "graph": {
            "name": "get_optimiser_run_graph",
            "pk_field": "run_id",
            "id_source": "list_agent_prompt_optimiser_runs",
            "entity": "agent prompt optimiser run",
            "description": (
                "Get the score-progression graph data of an agent prompt "
                "optimiser run (trial scores over iterations). Provide the "
                "run_id (from list_agent_prompt_optimiser_runs)."
            ),
        },
        "trial_prompt": {
            "name": "get_optimiser_trial_prompt",
            "pk_field": "run_id",
            "id_source": "list_agent_prompt_optimiser_runs",
            "entity": "agent prompt optimiser run",
            "path_kwargs": {
                "trial_id": {
                    "description": (
                        "UUID of the prompt trial within the run (from "
                        "get_optimiser_run_steps / get_optimiser_run_graph)."
                    ),
                },
            },
            "description": (
                "Get one optimiser trial's candidate prompt next to the "
                "baseline prompt. Provide run_id and trial_id."
            ),
        },
        "trial_evaluations": {
            "name": "get_optimiser_trial_evaluations",
            "pk_field": "run_id",
            "id_source": "list_agent_prompt_optimiser_runs",
            "entity": "agent prompt optimiser run",
            "path_kwargs": {
                "trial_id": {
                    "description": (
                        "UUID of the prompt trial within the run (from "
                        "get_optimiser_run_steps / get_optimiser_run_graph)."
                    ),
                },
            },
            "description": (
                "Get one optimiser trial's evaluation scores grouped by eval "
                "config, with change vs the baseline trial. Provide run_id "
                "and trial_id."
            ),
        },
        "trial_scenarios": {
            "name": "get_optimiser_trial_scenarios",
            "pk_field": "run_id",
            "id_source": "list_agent_prompt_optimiser_runs",
            "entity": "agent prompt optimiser run",
            "path_kwargs": {
                "trial_id": {
                    "description": (
                        "UUID of the prompt trial within the run (from "
                        "get_optimiser_run_steps / get_optimiser_run_graph)."
                    ),
                },
            },
            "description": (
                "Get one optimiser trial's per-scenario results (inputs, "
                "outputs and per-eval scores). Provide run_id and trial_id."
            ),
        },
    },
)(AgentPromptOptimiserRunViewSet)


# ---------------------------------------------------------------------------
# Phase 3A — destructive views (confirmation-gated; see PHASES.md 3A and
# ai_tools/confirmations.py). execution_policy pinned explicitly for grep.
# ---------------------------------------------------------------------------


def _preview_bulk_delete_test_executions(params: dict, context) -> str:
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import TestExecution

    run_test_id = params.get("run_test_id")
    run_test = (
        RunTest.objects.filter(id=run_test_id, organization=context.organization)
        .only("id", "name")
        .first()
    )
    rt_label = f"'{run_test.name}'" if run_test else f"`{run_test_id}` (not found)"
    select_all = bool(params.get("select_all"))
    ids = params.get("test_execution_ids") or []
    qs = TestExecution.objects.filter(run_test_id=run_test_id)
    if select_all:
        if ids:
            qs = qs.exclude(id__in=ids)
        scope = "ALL test executions" + (f" except {len(ids)} excluded" if ids else "")
    else:
        qs = qs.filter(id__in=ids)
        scope = f"{len(ids)} selected test execution(s)"
    count = qs.count()
    return (
        f"Will delete **{count} test execution(s)** ({scope}) from run test "
        f"{rt_label}. Active executions (running/pending/cancelling) are "
        "rejected by the API.\n\nThis cannot be undone."
    )


# bulk_delete_test_executions -> TestExecutionBulkDeleteView.post(run_test_id)
# (APIView verb handler; TestExecutionBulkDeleteSerializer auto-resolves:
# select_all + test_execution_ids). run_test_id is a URL kwarg -> pk routing.
expose_to_mcp(
    category="simulation",
    tools={
        "post": {
            "name": "bulk_delete_test_executions",
            "method": "POST",
            "detail": True,
            "pk_field": "run_test_id",
            "pk_kwarg": "run_test_id",
            "id_source": "list_run_tests",
            "entity": "run test",
            "execution_policy": "destructive",
            "confirm_preview": _preview_bulk_delete_test_executions,
            "description": (
                "Bulk delete test executions within a run test — target "
                "specific ids via test_execution_ids, or everything via "
                "select_all=true (test_execution_ids then acts as an "
                "exclusion list). DESTRUCTIVE: requires user confirmation "
                "(preview first, then re-call with confirm=true)."
            ),
        },
    },
)(TestExecutionBulkDeleteView)
