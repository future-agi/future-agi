# ruff: noqa: E402
"""Packet C write round-trips for ai_tools/tests/verify_writes.py.

Entries follow the generalized ROUNDTRIPS contract (setup -> call ->
fresh-shell ORM assert -> compensate to net-zero). Only writes that can be
made net-zero WITHOUT triggering paid AI generation or Temporal workflows
run here:

- covered inline in verify_writes.py: create_persona/delete_persona.
- covered here: simulator-agent roundtrip, duplicate_persona,
  update_persona, create/delete_agent_version (seed-gated on an existing
  agent definition).
- deliberately NOT swept automatically (documented gaps, exercise manually):
  add_scenario_rows / add_scenario_columns (paid AI generation),
  rerun_test_execution / rerun_call_execution / execute_* (Temporal
  workflows + provider calls), refresh_* (paid AI regeneration),
  cancel_test_execution (needs a live running execution),
  fetch_assistant_from_provider (needs an external provider API key),
  create/update/delete_simulate_eval_config + update_run_test_components +
  update_scenario_prompts (need a dedicated run-test/scenario fixture; the
  delete/cancel conversions only ever run against rows a sweep creates).
"""

UNIQ = "bridge-writecheck-c"

# --- simulator agent: create -> ORM assert -> delete -----------------------
# (dict shape, not the legacy 3-tuple: the tuple runner deletes by {"id": ...}
# but delete_simulator_agent's pk input is simulator_agent_id.)
def _simagent_assert(ctx, result):
    from simulate.models.simulator_agent import SimulatorAgent

    return SimulatorAgent.objects.filter(
        organization=ctx.organization, name=f"{UNIQ}-simagent"
    ).exists()


def _simagent_compensate(ctx, result):
    from ai_tools.registry import registry
    from simulate.models.simulator_agent import SimulatorAgent

    for sa in SimulatorAgent.objects.filter(
        organization=ctx.organization, name=f"{UNIQ}-simagent"
    ):
        r = registry.get("delete_simulator_agent").run(
            {"simulator_agent_id": str(sa.id)}, ctx
        )
        if r.is_error:  # fall back to ORM so the sweep stays net-zero
            sa.delete()


ROUNDTRIPS: list = [
    {
        "tool": "create_simulator_agent",
        "args": {
            "name": f"{UNIQ}-simagent",
            "prompt": "You are a throwaway write-check customer. Say hi.",
            "voice_provider": "openai",
            "voice_name": "alloy",
            "model": "gpt-4o-mini",
        },
        "assert_orm": _simagent_assert,
        "compensate": _simagent_compensate,
    },
]


# --- duplicate_persona: ORM setup patches args, compensate deletes both ----
def _mk_persona_entry(
    tool: str,
    args_extra: dict,
    assert_field: str | None = None,
    id_field: str = "persona_id",
):
    """Build a dict entry that creates a source persona in setup, patches the
    shared args dict with its id, and hard-deletes every write-check persona
    in compensate (net-zero even on mid-entry failure).

    ``id_field`` is 'persona_id' for the custom @action (duplicate_persona)
    and 'id' for the CRUD update/destroy bridges.
    """
    args: dict = {id_field: "", **args_extra}

    def _setup(ctx):
        from simulate.models.persona import Persona

        p = Persona.objects.create(
            persona_type=Persona.PersonaType.WORKSPACE,
            organization=ctx.organization,
            workspace=ctx.workspace,
            name=f"{UNIQ}-src-{tool}",
            description="throwaway write-check persona (packet C)",
        )
        args[id_field] = str(p.id)

    def _assert(ctx, result):
        from simulate.models.persona import Persona

        qs = Persona.no_workspace_objects.filter(
            organization=ctx.organization, name__startswith=UNIQ
        )
        if assert_field == "duplicated":
            return qs.count() >= 2  # source + duplicate both in the DB
        return qs.filter(description__icontains="updated").exists()

    def _compensate(ctx, result):
        from simulate.models.persona import Persona

        Persona.no_workspace_objects.filter(
            organization=ctx.organization, name__startswith=UNIQ
        ).delete()

    return {
        "tool": tool,
        "args": args,
        "setup": _setup,
        "assert_orm": _assert,
        "compensate": _compensate,
    }


ROUNDTRIPS.append(
    _mk_persona_entry(
        "duplicate_persona",
        {"name": f"{UNIQ}-dup"},
        assert_field="duplicated",
    )
)
ROUNDTRIPS.append(
    _mk_persona_entry(
        "update_persona",
        {
            "name": f"{UNIQ}-upd",
            "description": "updated by packet C write check",
        },
        id_field="id",
    )
)


# --- agent versions: create -> ORM assert -> delete (seed-gated) -----------
def _agent_version_entry():
    from accounts.models.user import User
    from simulate.models.agent_definition import AgentDefinition

    # Scope to the sweep user's org (verify_writes builds ToolContext for it).
    org = (
        User.objects.select_related("organization")
        .get(email="kartik.nvj@futureagi.com")
        .organization
    )
    agent = AgentDefinition.objects.filter(organization=org).order_by("-created_at").first()
    if agent is None:
        return None
    agent_id = str(agent.id)
    state: dict = {}

    def _assert(ctx, result):
        from simulate.models.agent_version import AgentVersion

        av = (
            AgentVersion.objects.filter(agent_definition_id=agent_id)
            .order_by("-created_at")
            .first()
        )
        if av is None:
            return False
        state["version_id"] = str(av.id)
        return (av.commit_message or "") == f"{UNIQ} version"

    def _compensate(ctx, result):
        from simulate.models.agent_version import AgentVersion

        if state.get("version_id"):
            AgentVersion.objects.filter(id=state["version_id"]).delete()

    return {
        "tool": "create_agent_version",
        "args": {"agent_id": agent_id, "commit_message": f"{UNIQ} version"},
        "assert_orm": _assert,
        "compensate": _compensate,
    }


try:
    _entry = _agent_version_entry()
    if _entry is not None:
        ROUNDTRIPS.append(_entry)
except Exception as _e:  # pragma: no cover — never break the sweep on import
    print(f"[WARN] writes_c agent-version entry skipped: {_e}")
