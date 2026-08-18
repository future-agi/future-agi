"""
Tests for Temporal simulate activities.

Run with: pytest tfc/temporal/simulate/tests/test_activities.py -v
"""

import ast
import asyncio
import builtins
import pathlib
import symtable
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

SIMULATE_PKG = pathlib.Path(__file__).resolve().parents[1]


def run_async(coro):
    """Helper to run async functions in sync tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestGenerateColumnDataActivity:
    """Tests for generate_column_data_activity."""

    def test_activity_uses_await_not_asyncio_run(self):
        """
        Test that generate_column_data_activity uses await instead of asyncio.run().

        This is a regression test for a critical bug where asyncio.run() was used
        inside an async function, which would cause a RuntimeError:
        "asyncio.run() cannot be called from a running event loop"
        """
        from tfc.temporal.simulate.types import GenerateColumnDataInput

        # Create mock DataFrame result
        mock_df = pd.DataFrame(
            {"new_column": ["value1", "value2"]}, index=["row-1", "row-2"]
        )

        # Create async mock for generate_column_data
        mock_agent = MagicMock()
        mock_agent.generate_column_data = AsyncMock(return_value=mock_df)

        with (
            patch("django.db.close_old_connections"),
            patch(
                "tfc.temporal.common.heartbeat.Heartbeater.__aenter__",
                return_value=MagicMock(details=None),
            ),
            patch("tfc.temporal.common.heartbeat.Heartbeater.__aexit__"),
            patch(
                "tfc.temporal.common.shutdown.ShutdownMonitor.__aenter__",
                return_value=MagicMock(raise_if_is_worker_shutdown=MagicMock()),
            ),
            patch("tfc.temporal.common.shutdown.ShutdownMonitor.__aexit__"),
            patch(
                "ee.agenthub.synthetic_data_agent.synthetic_data_agent.SyntheticDataAgent",
                return_value=mock_agent,
            ),
            patch("temporalio.activity.logger", MagicMock()),
        ):
            from tfc.temporal.simulate.activities import generate_column_data_activity

            input_data = GenerateColumnDataInput(generation_payload={"test": "payload"})

            # This should NOT raise "asyncio.run() cannot be called from a running event loop"
            # If the bug exists (using asyncio.run instead of await), this will fail
            result = run_async(generate_column_data_activity(input_data))

            assert result.status == "COMPLETED"
            assert result.data is not None
            assert "row-1" in result.data
            assert "row-2" in result.data
            assert result.data["row-1"]["new_column"] == "value1"
            assert result.data["row-2"]["new_column"] == "value2"

            # Verify that generate_column_data was awaited (not called with asyncio.run)
            mock_agent.generate_column_data.assert_awaited_once_with(
                {"test": "payload"}
            )

    def test_activity_handles_exception(self):
        """Test that activity returns FAILED status on exception."""
        from tfc.temporal.simulate.types import GenerateColumnDataInput

        mock_agent = MagicMock()
        mock_agent.generate_column_data = AsyncMock(
            side_effect=ValueError("Generation failed")
        )

        with (
            patch("django.db.close_old_connections"),
            patch(
                "tfc.temporal.common.heartbeat.Heartbeater.__aenter__",
                return_value=MagicMock(details=None),
            ),
            patch("tfc.temporal.common.heartbeat.Heartbeater.__aexit__"),
            patch(
                "tfc.temporal.common.shutdown.ShutdownMonitor.__aenter__",
                return_value=MagicMock(raise_if_is_worker_shutdown=MagicMock()),
            ),
            patch("tfc.temporal.common.shutdown.ShutdownMonitor.__aexit__"),
            patch(
                "ee.agenthub.synthetic_data_agent.synthetic_data_agent.SyntheticDataAgent",
                return_value=mock_agent,
            ),
            patch("temporalio.activity.logger", MagicMock()),
        ):
            from tfc.temporal.simulate.activities import generate_column_data_activity

            input_data = GenerateColumnDataInput(generation_payload={})

            result = run_async(generate_column_data_activity(input_data))

            assert result.status == "FAILED"
            assert result.error is not None  # Error message captured

    def test_activity_converts_dataframe_to_dict(self):
        """Test that DataFrame is correctly converted to nested dict."""
        from tfc.temporal.simulate.types import GenerateColumnDataInput

        # Create multi-column DataFrame
        mock_df = pd.DataFrame(
            {
                "col_a": ["a1", "a2", "a3"],
                "col_b": ["b1", "b2", "b3"],
            },
            index=["row-1", "row-2", "row-3"],
        )

        mock_agent = MagicMock()
        mock_agent.generate_column_data = AsyncMock(return_value=mock_df)

        with (
            patch("django.db.close_old_connections"),
            patch(
                "tfc.temporal.common.heartbeat.Heartbeater.__aenter__",
                return_value=MagicMock(details=None),
            ),
            patch("tfc.temporal.common.heartbeat.Heartbeater.__aexit__"),
            patch(
                "tfc.temporal.common.shutdown.ShutdownMonitor.__aenter__",
                return_value=MagicMock(raise_if_is_worker_shutdown=MagicMock()),
            ),
            patch("tfc.temporal.common.shutdown.ShutdownMonitor.__aexit__"),
            patch(
                "ee.agenthub.synthetic_data_agent.synthetic_data_agent.SyntheticDataAgent",
                return_value=mock_agent,
            ),
            patch("temporalio.activity.logger", MagicMock()),
        ):
            from tfc.temporal.simulate.activities import generate_column_data_activity

            input_data = GenerateColumnDataInput(generation_payload={})

            result = run_async(generate_column_data_activity(input_data))

            assert result.status == "COMPLETED"
            # Verify structure: row_id -> column_name -> value
            assert result.data["row-1"]["col_a"] == "a1"
            assert result.data["row-1"]["col_b"] == "b1"
            assert result.data["row-2"]["col_a"] == "a2"
            assert result.data["row-3"]["col_b"] == "b3"


class TestAsyncioRunBugRegression:
    """
    Regression tests to ensure asyncio.run() is not used in async contexts.

    The bug: Using asyncio.run() inside an async function causes:
    RuntimeError: asyncio.run() cannot be called from a running event loop

    This is because asyncio.run() tries to create a new event loop, but one
    is already running when inside an async function.
    """

    def test_asyncio_run_in_async_context_raises_error(self):
        """Demonstrate that asyncio.run() in async context raises RuntimeError."""

        async def buggy_async_function():
            # This simulates the bug that was in generate_column_data_activity
            async def inner_async():
                return "result"

            # This will raise RuntimeError
            return asyncio.run(inner_async())

        with pytest.raises(RuntimeError, match="cannot be called from a running"):
            asyncio.run(buggy_async_function())

    def test_await_in_async_context_works(self):
        """Demonstrate that await in async context works correctly."""

        async def correct_async_function():
            async def inner_async():
                return "result"

            # This is the correct approach
            return await inner_async()

        result = asyncio.run(correct_async_function())
        assert result == "result"


class TestResolveScenarioAgent:
    """Tests for ``_resolve_scenario_agent`` (TH-4891).

    Dataset-kind scenarios with ``source_type=prompt`` were silently
    failing in the Temporal activity because the activity hard-required
    ``scenario.agent_definition``. This helper centralises the prompt-
    adapter resolution that the graph/script activities already use.
    """

    def _make_scenario(self, *, agent_definition=None, prompt_template=None,
                       prompt_version=None):
        from types import SimpleNamespace
        org = SimpleNamespace(id="org-1")
        ws = SimpleNamespace(id="ws-1")
        return SimpleNamespace(
            agent_definition=agent_definition,
            prompt_template=prompt_template,
            prompt_version=prompt_version,
            organization=org,
            workspace=ws,
        )

    def test_agent_definition_source_returns_real_object(self):
        from types import SimpleNamespace
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        real_ad = SimpleNamespace(id="ad-1", agent_name="Real Agent")
        scenario = self._make_scenario(agent_definition=real_ad)

        result = _resolve_scenario_agent(scenario, "agent_definition")
        assert result is real_ad

    def test_prompt_source_returns_adapter_with_prompt_metadata(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        prompt_version = SimpleNamespace(
            prompt_config_snapshot={
                "messages": [
                    {"role": "system", "content": "You are a helpdesk bot."},
                    {"role": "user", "content": "Greet the customer."},
                ]
            }
        )
        prompt_template = MagicMock()
        prompt_template.id = "pt-42"
        prompt_template.name = "Helpdesk Prompt"
        prompt_template.description = "fallback desc"

        scenario = self._make_scenario(
            prompt_template=prompt_template, prompt_version=prompt_version
        )

        result = _resolve_scenario_agent(scenario, "prompt")
        assert result.id == "pt-42"
        assert result.agent_name == "Helpdesk Prompt"
        assert "You are a helpdesk bot." in result.description
        assert "Greet the customer." in result.description
        assert result.agent_type == "text"
        assert result.languages == ["en"]
        assert result.inbound is True

    def test_prompt_source_falls_back_to_default_version(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        default_version = SimpleNamespace(
            prompt_config_snapshot={
                "messages": [{"role": "system", "content": "Default prompt."}]
            }
        )
        prompt_template = MagicMock()
        prompt_template.id = "pt-1"
        prompt_template.name = "Default Lookup"
        prompt_template.description = ""
        prompt_template.all_executions.filter.return_value.first.return_value = (
            default_version
        )

        scenario = self._make_scenario(
            prompt_template=prompt_template, prompt_version=None
        )

        result = _resolve_scenario_agent(scenario, "prompt")
        assert "Default prompt." in result.description

    def test_prompt_source_without_template_falls_back_to_agent_definition(self):
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        scenario = self._make_scenario(
            agent_definition=None, prompt_template=None
        )
        result = _resolve_scenario_agent(scenario, "prompt")
        assert result is None

    def test_empty_prompt_messages_uses_template_description(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from tfc.temporal.simulate.activities import _resolve_scenario_agent

        prompt_version = SimpleNamespace(
            prompt_config_snapshot={"messages": []}
        )
        prompt_template = MagicMock()
        prompt_template.id = "pt-2"
        prompt_template.name = "Empty"
        prompt_template.description = "Template-level description"

        scenario = self._make_scenario(
            prompt_template=prompt_template, prompt_version=prompt_version
        )
        result = _resolve_scenario_agent(scenario, "prompt")
        assert result.description == "Template-level description"


class TestGraphScenarioNameBinding:
    """Regression tests for two `NameError`s in the graph-scenario path.

    Both bugs are of the same shape: a name is read but never bound, so the
    code path dies with `NameError` the first time it runs. Neither is
    reachable from a new workflow — `CreateGraphScenarioWorkflow.run` sends
    new executions down the v3 branch — but both are live for in-flight and
    replaying workflows that were started on the older branches.

    These checks are static because the alternative is a Temporal worker.
    This module's own docstring already records why that is avoided:
    "running actual Temporal activities requires a Temporal worker
    environment". A missing local and a missing import are fully decidable
    from the source, so a static check loses nothing here.
    """

    GRAPH_ROUTING_KEYS = {
        "source_type",
        "agent_definition_id",
        "generate_graph",
        "graph_data",
    }

    @staticmethod
    def _function_table(module_table, name):
        """Depth-first lookup of a function's symbol table by name."""
        for child in module_table.get_children():
            if child.get_name() == name and child.get_type() == "function":
                return child
            found = TestGraphScenarioNameBinding._function_table(child, name)
            if found is not None:
                return found
        return None

    @staticmethod
    def _unbound_names(source, filename, func_name):
        """Names a function reads that resolve to neither a local, a module
        global, nor a builtin — i.e. guaranteed `NameError` at runtime."""
        module_table = symtable.symtable(source, filename, "exec")
        module_names = {s.get_name() for s in module_table.get_symbols()}
        func_table = TestGraphScenarioNameBinding._function_table(
            module_table, func_name
        )
        assert func_table is not None, f"{func_name} not found in {filename}"
        return sorted(
            s.get_name()
            for s in func_table.get_symbols()
            if s.is_global()
            and s.get_name() not in module_names
            and not hasattr(builtins, s.get_name())
        )

    def test_create_graph_scenario_sync_binds_graph_routing_keys(self):
        """`_create_graph_scenario_sync` must read its four routing keys.

        A refactor extracted the v3 setup activity and took these four
        `validated_data.get(...)` assignments with it, leaving the v1 body
        reading names that no longer existed. The very next statement,
        `if source_type == "prompt"`, raised `NameError` on every call.
        """
        source = (SIMULATE_PKG / "activities.py").read_text()
        tree = ast.parse(source)
        func = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_create_graph_scenario_sync"
        )
        assigned = {
            target.id
            for node in ast.walk(func)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        missing = sorted(self.GRAPH_ROUTING_KEYS - assigned)
        msg = f"_create_graph_scenario_sync reads {missing} without binding them"
        assert not missing, msg

    @pytest.mark.parametrize(
        "func_name",
        ["_create_graph_scenario_sync", "_setup_graph_scenario_sync"],
    )
    def test_graph_scenario_helpers_have_no_unbound_names(self, func_name):
        """Neither the v1 nor the v3 graph-scenario helper may read a name
        that is not a local, a module global, or a builtin."""
        source = (SIMULATE_PKG / "activities.py").read_text()
        unbound = self._unbound_names(source, "activities.py", func_name)
        assert not unbound, f"{func_name} reads unbound names: {unbound}"

    def test_workflows_import_every_activity_input_type(self):
        """Every `*Input` / `*Output` dataclass the workflows construct must
        be imported.

        `ExtractIntentsInput` was used in `_run_v2_multi_activity` but was
        missing from the otherwise-alphabetical import block, so the v2
        branch raised `NameError` at step 2 — after setup had already run
        and written a graph.
        """
        tree = ast.parse((SIMULATE_PKG / "workflows.py").read_text())
        available = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        available |= {
            node.name
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        constructed = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id.endswith(("Input", "Output"))
        }
        missing = sorted(constructed - available)
        assert not missing, f"workflows.py constructs but never imports: {missing}"
