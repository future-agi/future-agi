"""
Registry manifest gate (Phase 1B).

Bridge registration failures are logged as ``bridge_registration_failed``
and swallowed (ai_tools/drf_bridge.py:1005-1010), so a broken bridge
ships silently: the tool just vanishes from the registry. This module is
the deterministic merge gate that catches that:

  (a) total tool count never drops below the floor,
  (b) a pinned sentinel set of critical tool names is present,
  (c) no tool name is registered twice,
  (d) a fresh interpreter import of the registry emits zero
      bridge_registration_failed events.

Sentinel names were derived from the live registry on 2026-06-10
(count=395). If a sentinel is renamed deliberately, update the pin in
the same PR — that review moment is the point of the gate.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.apps import apps

import ai_tools
import ai_tools.tools  # noqa: F401  — triggers @register_tool / bridge registration
from ai_tools.registry import registry

# Floor, not exact count: new tools may land in parallel branches, but a
# swallowed registration failure of a whole ViewSet drops dozens at once.
MINIMUM_TOOL_COUNT = 390

# Critical tools across every cluster: discovery keepers, tracing
# bridges, simulate lifecycle, annotations, evals, feedback, datasets,
# prompts, docs/web, workspace.
SENTINEL_TOOLS = frozenset(
    {
        # Context / discovery keepers
        "search_tools",
        "whoami",
        "search",
        "read_schema",
        "read_taxonomy",
        "get_cost_breakdown",
        "render_widget",
        # Docs / web
        "ask_docs",
        "web_search",
        # Tracing bridges
        "list_trace_projects",
        "list_traces",
        "list_spans",
        # Simulate lifecycle (TH-5467)
        "create_scenario",
        "create_run_test",
        "execute_run_test",
        "get_test_execution_status",
        # Annotations
        "create_annotation_label",
        "create_annotation_queue",
        # Evaluations
        "create_eval_template",
        "list_eval_templates",
        "apply_eval_group_to_dataset",
        # Feedback
        "create_feedback",
        # Datasets
        "create_dataset",
        "add_dataset_rows",
        "list_datasets",
        # Prompts
        "create_prompt_template",
        "commit_prompt_version",
        # Workspace
        "list_workspaces",
    }
)

# EE-only: registered by ee.falcon_ai.apps.FalconAIConfig.ready(), not
# by `import ai_tools.tools`.
EE_MEMORY_SENTINEL_TOOLS = frozenset(
    {"save_memory", "list_memories", "delete_memory"}
)


def _registered_names():
    return [tool.name for tool in registry.list_all()]


class TestRegistryManifest:
    def test_tool_count_floor(self):
        count = registry.count()
        assert count >= MINIMUM_TOOL_COUNT, (
            f"Registry has {count} tools, expected >= {MINIMUM_TOOL_COUNT}. "
            "A bridge registration likely failed and was swallowed — check "
            "logs for 'bridge_registration_failed'."
        )

    def test_sentinel_tools_present(self):
        names = set(_registered_names())
        missing = sorted(SENTINEL_TOOLS - names)
        assert not missing, (
            f"Critical tools missing from registry: {missing}. "
            "If a rename was intentional, update SENTINEL_TOOLS in this file."
        )

    def test_ee_memory_tools_present_when_ee_installed(self):
        if not apps.is_installed("ee.falcon_ai"):
            pytest.skip("ee.falcon_ai not installed — memory tools are EE-only")
        names = set(_registered_names())
        missing = sorted(EE_MEMORY_SENTINEL_TOOLS - names)
        assert not missing, f"EE falcon memory tools missing: {missing}"

    def test_no_duplicate_tool_names(self):
        names = _registered_names()
        assert len(names) == len(set(names)), (
            f"Duplicate tool names in registry: "
            f"{sorted(n for n in set(names) if names.count(n) > 1)}"
        )
        # list_all() must agree with count() (both come from the same dict)
        assert len(names) == registry.count()

    def test_fresh_import_has_no_bridge_registration_failures(self):
        """Boot a fresh interpreter and assert zero swallowed failures.

        Registration happens at Django app-ready time, so by the time
        pytest runs, any bridge_registration_failed log has already
        fired and caplog can't see it. A subprocess re-runs the full
        registration with output captured; structlog renders the
        exception log to stdout/stderr (verified in this container).
        """
        script = (
            "import django\n"
            "django.setup()\n"
            "import ai_tools.tools  # noqa: F401\n"
            "from ai_tools.registry import registry\n"
            'print("REGISTRY_COUNT=%d" % registry.count())\n'
        )
        backend_root = Path(ai_tools.__file__).resolve().parents[1]
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": os.environ.get(
                "DJANGO_SETTINGS_MODULE", "tfc.settings.test"
            ),
        }
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(backend_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = proc.stdout + proc.stderr
        assert proc.returncode == 0, f"fresh registry import failed:\n{output[-3000:]}"
        assert "bridge_registration_failed" not in output, (
            "bridge_registration_failed fired during registry import — a "
            "bridge is silently broken:\n"
            + "\n".join(
                line
                for line in output.splitlines()
                if "bridge_registration_failed" in line
            )[:3000]
        )
        count_lines = [
            line for line in proc.stdout.splitlines() if line.startswith("REGISTRY_COUNT=")
        ]
        assert count_lines, f"no REGISTRY_COUNT in output:\n{output[-2000:]}"
        fresh_count = int(count_lines[-1].split("=", 1)[1])
        assert fresh_count >= MINIMUM_TOOL_COUNT
