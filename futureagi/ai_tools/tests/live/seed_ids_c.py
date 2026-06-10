# ruff: noqa: E402
"""Packet C seed ids for ai_tools/tests/verify_bridges.py (simulate app).

Why this file exists: many Packet C detail-GET tools are keyed by ids whose
``id_source`` tool is ITSELF a detail tool (e.g. ``get_call_transcript``'s
id_source is ``get_test_execution``, which needs a test_execution_id) — the
harvester can't run those with ``{}``, so it falls back to SEED_IDS.

Instead of hand-pinned, box-specific UUIDs, seeds are harvested from the
live DB via the ORM at import time (this module is only imported inside the
container by verify_bridges/verify_writes, after django.setup()). Values are
either a bare id string (passed as the tool's pk_field) or a full params
dict (for tools that also need path_kwargs / extra query params). Tools
whose rows don't exist on the box are simply absent (NODATA in the sweep).
"""

SEED_IDS: dict = {}

# The sweep's ToolContext is built for this user (see verify_bridges.py) —
# rows are scoped to their organization so the views' in-method org checks
# pass (a globally-latest row from another tenant would 404).
USER_EMAIL = "kartik.nvj@futureagi.com"


def _harvest() -> None:
    from accounts.models.user import User
    from simulate.models.agent_prompt_optimiser_run import AgentPromptOptimiserRun
    from simulate.models.agent_version import AgentVersion
    from simulate.models.eval_config import SimulateEvalConfig
    from simulate.models.prompt_trial import PromptTrial
    from simulate.models.test_execution import CallExecution, TestExecution

    org = User.objects.select_related("organization").get(email=USER_EMAIL).organization

    te = (
        TestExecution.objects.filter(run_test__organization=org)
        .order_by("-created_at")
        .first()
    )
    if te:
        te_id = str(te.id)
        for tool in (
            "get_test_execution",
            "get_test_execution_analytics",
            "get_run_test_kpis",
            "get_performance_summary",
            "get_eval_explanation_summary",
            "get_fix_my_agent_analysis",
            "get_test_execution_transcripts",
        ):
            SEED_IDS[tool] = te_id
        SEED_IDS["export_test_execution_csv"] = {
            "item_id": te_id,
            "type": "testexecution",
        }

    ce = (
        CallExecution.objects.filter(test_execution__run_test__organization=org)
        .order_by("-created_at")
        .first()
    )
    if ce:
        ce_id = str(ce.id)
        for tool in (
            "get_call_execution",
            "get_call_transcript",
            "get_call_logs",
            "get_call_branch_analysis",
            "get_call_error_localizer_tasks",
        ):
            SEED_IDS[tool] = ce_id

    av = (
        AgentVersion.objects.filter(organization=org)
        .select_related("agent_definition")
        .order_by("-created_at")
        .first()
    )
    if av:
        params = {
            "version_id": str(av.id),
            "agent_id": str(av.agent_definition_id),
        }
        for tool in (
            "get_agent_version",
            "get_agent_version_eval_summary",
            "list_agent_version_call_executions",
        ):
            SEED_IDS[tool] = dict(params)
        SEED_IDS["list_agent_versions"] = str(av.agent_definition_id)

    ec = (
        SimulateEvalConfig.objects.filter(run_test__organization=org)
        .order_by("-created_at")
        .first()
    )
    if ec:
        SEED_IDS["get_simulate_eval_config_structure"] = {
            "eval_config_id": str(ec.id),
            "run_test_id": str(ec.run_test_id),
        }

    trial = (
        PromptTrial.objects.filter(
            agent_prompt_optimiser_run__test_execution__run_test__organization=org
        )
        .select_related("agent_prompt_optimiser_run")
        .order_by("-created_at")
        .first()
    )
    if trial:
        params = {
            "run_id": str(trial.agent_prompt_optimiser_run_id),
            "trial_id": str(trial.id),
        }
        for tool in (
            "get_optimiser_trial_prompt",
            "get_optimiser_trial_evaluations",
            "get_optimiser_trial_scenarios",
        ):
            SEED_IDS[tool] = dict(params)
    run = (
        AgentPromptOptimiserRun.objects.filter(
            test_execution__run_test__organization=org
        )
        .order_by("-created_at")
        .first()
    )
    if run:
        SEED_IDS.setdefault("get_optimiser_run_steps", str(run.id))
        SEED_IDS.setdefault("get_optimiser_run_graph", str(run.id))


try:
    _harvest()
except Exception as _e:  # pragma: no cover — never break the sweep on import
    print(f"[WARN] seed_ids_c harvest failed: {_e}")
