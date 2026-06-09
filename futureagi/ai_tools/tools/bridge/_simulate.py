"""Bridge registration for simulation detail APIViews.

Surfaces the FULL existing DRF responses through the bridge instead of
hand-written tools, so MCP and the UI share one source of truth:

- get_call_execution -> CallExecutionDetailView: full call detail including
  turn/latency/WPM/talk-ratio/interruption analytics (TH-5397).
- get_scenario -> ScenarioDetailView: full scenario detail including the
  scenario graph (nodes/edges) (TH-5375).
"""

from ai_tools.drf_bridge import expose_to_mcp
from simulate.views.run_test import (
    CallExecutionDetailView,
    CreateRunTestView,
    CSVExportView,
    RunTestAnalyticsView,
    RunTestDetailView,
    RunTestEvalExplanationSummaryView,
    RunTestExecutionView,
    RunTestListView,
    TestExecutionOptimiserAnalysisView,
    TestExecutionStatusView,
)
from simulate.views.scenarios import CreateScenarioView, ScenarioDetailView

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
