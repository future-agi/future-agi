"""Read-only experiment source support for an authenticated workspace.

This describes installed create/execute paths, not destination reachability,
worker health, or permission to use a particular dataset, prompt or graph.
Those checks remain with creation and execution. Nothing here dispatches work.

`agent` means an Agent Playground GraphVersion, not an unmapped Console target.
At head-73, TX1-B admits those graphs under either Console-agent setting; see
experiment_target_admission and test_experiment_target_kind. Do not invent a
setting gate here that the real creation/execution path does not enforce.
"""

_SOURCE_LABELS = {
    "prompt": "Prompt",
    "agent": "Agent graph",
    "http": "HTTP endpoint",
    "a2a": "A2A",
    "mcp_tool": "MCP tool",
}

# These are execution dependencies, not a second set of availability booleans.
# Resolve them against the same registries used to construct Temporal workers.
# A new source needs both a creation serializer and a wired execution path;
# adding its label or a protocol client alone must never advertise support.
_EXECUTION_PATHS = {
    "prompt": (
        "ProcessPromptV2Workflow",
        {"setup_prompt_v2_activity", "process_row_activity"},
    ),
    "agent": (
        "ProcessAgentWorkflow",
        {
            "setup_agent_activity",
            "prepare_agent_row_activity",
            "execute_native_graph_activity",
        },
    ),
    "http": (
        "ProcessHttpSourceWorkflow",
        {
            "setup_http_source_activity",
            "process_http_row_activity",
            "settle_http_row_activity",
            "finish_http_source_activity",
        },
    ),
}
# An A2A agent is saved as an HTTP source variant (http.protocol = "a2a") and
# runs on the same workflow and activities; only the reply reader differs.
_EXECUTION_PATHS["a2a"] = _EXECUTION_PATHS["http"]


def _executable_kinds():
    try:
        from tfc.temporal import experiments

        workflows = {
            runner.__name__
            for runner in experiments.get_workflows()
            if callable(getattr(runner, "run", None))
        }
        activities = {
            runner.__name__
            for runner in experiments.get_activities()
            if callable(runner)
        }
    except ImportError:
        # A missing worker dependency is not permission to enable a source.
        return set()

    if (
        "RunExperimentV2Workflow" not in workflows
        or "setup_experiment_activity" not in activities
    ):
        return set()
    return {
        kind
        for kind, (workflow, required_activities) in _EXECUTION_PATHS.items()
        if workflow in workflows and required_activities <= activities
    }


def _http_path_installed():
    try:
        from model_hub.services import http_experiment_source
    except ImportError:
        return False

    return all(
        callable(getattr(http_experiment_source, name, None))
        for name in (
            "persist_http_source",
            "run_http_snapshot_row",
            "execute_http_row",
        )
    ) and callable(
        getattr(
            getattr(http_experiment_source, "outbound_destination", None),
            "request",
            None,
        )
    )


def _a2a_path_installed():
    if not _http_path_installed():
        return False
    from model_hub.services import http_experiment_source

    return callable(getattr(http_experiment_source, "a2a_reply_text", None))


def get_experiment_run_sources(request):
    """Use only the workspace already admitted by the existing authentication."""
    organization = getattr(request, "organization", None)
    workspace = getattr(request, "workspace", None)
    organization_id = getattr(organization, "id", None)
    workspace_id = getattr(workspace, "id", None)
    if (
        organization_id is None
        or workspace_id is None
        or not getattr(workspace, "is_active", False)
        or str(getattr(workspace, "organization_id", None)) != str(organization_id)
    ):
        return {
            "workspace_id": None,
            "sources": {
                kind: {
                    "available": False,
                    "reason": "Select a workspace to create an experiment.",
                }
                for kind in _SOURCE_LABELS
            },
        }

    from model_hub.serializers.experiments import PromptConfigEntrySerializer

    creatable = PromptConfigEntrySerializer().fields["target_type"].choices
    executable = _executable_kinds()
    if "http" in executable and not _http_path_installed():
        executable.remove("http")
    if "a2a" in executable and not _a2a_path_installed():
        executable.remove("a2a")

    sources = {}
    for kind, label in _SOURCE_LABELS.items():
        if kind not in creatable or kind not in _EXECUTION_PATHS:
            reason = f"{label} experiments are not built in this release."
        elif kind not in executable:
            reason = (
                f"{label} experiments are unavailable on this server. "
                "Contact your administrator."
            )
        else:
            reason = None
        sources[kind] = {"available": reason is None, "reason": reason}
    return {"workspace_id": str(workspace_id), "sources": sources}
