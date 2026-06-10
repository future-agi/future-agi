"""Packet B write round-trips (verify_writes.py ROUNDTRIPS, generalized dicts).

Pattern: every entry is self-contained and net-zero — `setup` get_or_creates a
dedicated throwaway prompt template under a FIXED UUID (so static args can
reference it), `assert_orm` checks the DB row (never the formatted reply), and
`compensate` reverses the side effect via ORM or the inverse tool.

Covered writes:
  create_prompt_draft, create_prompt_version, commit_prompt_version,
  set_default_prompt_version, save_prompt_name, update_prompt_eval_configs
  (+ delete_prompt_eval_config exercised as its compensation),
  compare_experiments (idempotent recompute of stored rankings).

Deliberately NOT auto-swept (documented; verify manually):
  - run_prompt / run_prompt_evals / generate_prompt / improve_prompt /
    analyze_prompt / generate_prompt_variables — real LLM spend (background
    workflows); not safe for an unattended harness.
  - create_experiment / rerun_experiment / rerun_experiment_cells /
    run_experiment_evaluations / add_experiment_evaluation — start Temporal
    workflows that consume LLM credits and overwrite shared dev-DB results;
    NOT net-zero.
  - stop_experiment — needs a RUNNING experiment to stop (cannot be staged
    deterministically without first burning a run).
  - move_prompt_to_folder — args would need a dynamically created folder id;
    static-args harness can't express it (folder CRUD itself is covered by
    the inline create_prompt_folder ROUNDTRIP in verify_writes.py).
  - create_experiment_feedback / submit_experiment_feedback — need a live
    eval column + row + metric triple from a finished V2 experiment; replay
    manually with ids from list_dataset_experiments.

Run: docker exec ws1-backend python -m ai_tools.tests.verify_writes
"""

from uuid import UUID

# Fixed throwaway-template UUID so static args can reference setup's row.
WRITECHECK_TEMPLATE_ID = "a04a7731-31cf-4b56-b6d5-1fa554c77c48"
WRITECHECK_TEMPLATE_NAME = "bridge-writecheck-template-b"
WRITECHECK_DRAFT_NAME = "bridge-writecheck-prompt-draft-b"
WRITECHECK_EVALCFG_NAME = "bridge-writecheck-evalcfg-b"

# Stable dev-DB ids (same harvest as seed_ids_b.py, 2026-06-10).
SEED_EXPERIMENT_ID = "649afe76-7e86-4eec-8c72-195398c46132"  # Completed V2

_PROMPT_CONFIG = {
    "messages": [
        {"role": "user", "content": [{"text": "Say hi to {{name}}.", "type": "text"}]}
    ],
    "configuration": {
        "model": "gpt-4o-mini",
        "temperature": 0.5,
        "max_tokens": 100,
        "response_format": "text",
    },
    "placeholders": [],
}


def _ensure_template(ctx):
    """get_or_create the fixed-UUID throwaway template with a v1 draft."""
    from model_hub.models.run_prompt import PromptTemplate, PromptVersion

    template, _ = PromptTemplate.objects.get_or_create(
        id=UUID(WRITECHECK_TEMPLATE_ID),
        defaults={
            "name": WRITECHECK_TEMPLATE_NAME,
            "organization": ctx.organization,
            "description": "Packet B write-check throwaway template",
        },
    )
    # Undo any rename/soft-delete left by an aborted previous sweep.
    if template.name != WRITECHECK_TEMPLATE_NAME or template.deleted:
        template.name = WRITECHECK_TEMPLATE_NAME
        template.deleted = False
        template.save()
    if not PromptVersion.objects.filter(
        original_template=template, template_version="v1", deleted=False
    ).exists():
        PromptVersion.objects.create(
            original_template=template,
            template_version="v1",
            is_draft=True,
            prompt_config_snapshot=[_PROMPT_CONFIG],
            variable_names={"name": ["world"]},
        )


def _template_qs():
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.objects.filter(id=UUID(WRITECHECK_TEMPLATE_ID))


def _versions_qs():
    from model_hub.models.run_prompt import PromptVersion

    return PromptVersion.objects.filter(
        original_template_id=UUID(WRITECHECK_TEMPLATE_ID), deleted=False
    )


# --- create_prompt_draft -----------------------------------------------------
def _assert_draft_created(ctx, result):
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.objects.filter(
        name=WRITECHECK_DRAFT_NAME, organization=ctx.organization, deleted=False
    ).exists()


def _compensate_draft_created(ctx, result):
    from django.utils import timezone

    from model_hub.models.run_prompt import PromptTemplate

    PromptTemplate.objects.filter(
        name=WRITECHECK_DRAFT_NAME, organization=ctx.organization
    ).update(deleted=True, deleted_at=timezone.now())


# --- create_prompt_version ---------------------------------------------------
def _assert_new_draft_version(ctx, result):
    return _versions_qs().filter(is_draft=True).exclude(
        template_version="v1"
    ).exists()


def _compensate_new_draft_version(ctx, result):
    from django.utils import timezone

    _versions_qs().exclude(template_version="v1").update(
        deleted=True, deleted_at=timezone.now()
    )


# --- commit_prompt_version ---------------------------------------------------
def _setup_commit(ctx):
    _ensure_template(ctx)
    _versions_qs().filter(template_version="v1").update(is_draft=True)


def _assert_v1_committed(ctx, result):
    v1 = _versions_qs().filter(template_version="v1").first()
    return v1 is not None and v1.is_draft is False


def _compensate_v1_committed(ctx, result):
    _versions_qs().filter(template_version="v1").update(
        is_draft=True, commit_message=""
    )


# --- set_default_prompt_version ----------------------------------------------
def _assert_v1_default(ctx, result):
    v1 = _versions_qs().filter(template_version="v1").first()
    return v1 is not None and v1.is_default is True


# --- save_prompt_name ----------------------------------------------------------
def _assert_renamed(ctx, result):
    t = _template_qs().first()
    return t is not None and t.name == WRITECHECK_TEMPLATE_NAME + "-renamed"


# --- update/delete_prompt_eval_configs ----------------------------------------
def _eval_template_id(ctx):
    """First system eval template (no_workspace manager — org-agnostic)."""
    from model_hub.models.evals_metric import EvalTemplate

    et = (
        EvalTemplate.no_workspace_objects.filter(deleted=False)
        .order_by("created_at")
        .first()
    )
    return str(et.id) if et else None


def _setup_eval_config(ctx):
    _ensure_template(ctx)
    # Drop leftovers from an aborted sweep so the unique-name check passes.
    from model_hub.models.run_prompt import PromptEvalConfig

    PromptEvalConfig.objects.filter(
        name=WRITECHECK_EVALCFG_NAME,
        prompt_template_id=UUID(WRITECHECK_TEMPLATE_ID),
        deleted=False,
    ).update(deleted=True)
    # Late-bind the eval template id into the entry's static args (the eval
    # template UUID is DB-dependent, so it can't be hardcoded portably).
    eval_id = _eval_template_id(ctx)
    if eval_id:
        _UPDATE_EVAL_CONFIG_ENTRY["args"]["id"] = eval_id
    else:
        raise RuntimeError("no EvalTemplate available for the write check")


def _assert_eval_config(ctx, result):
    from model_hub.models.run_prompt import PromptEvalConfig

    return PromptEvalConfig.objects.filter(
        name=WRITECHECK_EVALCFG_NAME,
        prompt_template_id=UUID(WRITECHECK_TEMPLATE_ID),
        deleted=False,
    ).exists()


def _compensate_eval_config(ctx, result):
    """Compensate THROUGH the inverse bridge tool — also covers
    delete_prompt_eval_config in the same round-trip."""
    from ai_tools.registry import registry

    from model_hub.models.run_prompt import PromptEvalConfig

    cfg = PromptEvalConfig.objects.filter(
        name=WRITECHECK_EVALCFG_NAME,
        prompt_template_id=UUID(WRITECHECK_TEMPLATE_ID),
        deleted=False,
    ).first()
    if cfg is None:
        return
    tool = registry.get("delete_prompt_eval_config")
    if tool is not None:
        r = tool.run(
            {"template_id": WRITECHECK_TEMPLATE_ID, "id": str(cfg.id)}, ctx
        )
        if not r.is_error:
            return
    cfg.deleted = True
    cfg.save(update_fields=["deleted"])


# --- compare_experiments (idempotent recompute over seed experiment) ----------
def _assert_comparisons(ctx, result):
    from model_hub.models.experiments import ExperimentComparison

    return ExperimentComparison.objects.filter(
        experiment_id=UUID(SEED_EXPERIMENT_ID), deleted=False
    ).exists()


_UPDATE_EVAL_CONFIG_ENTRY = {
    "tool": "update_prompt_eval_configs",
    "args": {
        "template_id": WRITECHECK_TEMPLATE_ID,
        # "id" (eval template UUID) is late-bound by _setup_eval_config.
        "name": WRITECHECK_EVALCFG_NAME,
        "mapping": {},
    },
    "setup": _setup_eval_config,
    "assert_orm": _assert_eval_config,
    "compensate": _compensate_eval_config,
}

ROUNDTRIPS = [
    {
        "tool": "create_prompt_draft",
        "args": {
            "name": WRITECHECK_DRAFT_NAME,
            "prompt_config": [_PROMPT_CONFIG],
            "is_draft": True,
        },
        "assert_orm": _assert_draft_created,
        "compensate": _compensate_draft_created,
    },
    {
        "tool": "create_prompt_version",
        "args": {
            "template_id": WRITECHECK_TEMPLATE_ID,
            "new_prompts": [
                {
                    "prompt_config": [_PROMPT_CONFIG],
                    "variable_names": {"name": ["world"]},
                    "evaluation_configs": [],
                }
            ],
        },
        "setup": _ensure_template,
        "assert_orm": _assert_new_draft_version,
        "compensate": _compensate_new_draft_version,
    },
    {
        "tool": "commit_prompt_version",
        "args": {
            "template_id": WRITECHECK_TEMPLATE_ID,
            "version_name": "v1",
            "message": "bridge writecheck commit",
        },
        "setup": _setup_commit,
        "assert_orm": _assert_v1_committed,
        "compensate": _compensate_v1_committed,
    },
    {
        "tool": "set_default_prompt_version",
        "args": {
            "template_id": WRITECHECK_TEMPLATE_ID,
            "version_name": "v1",
        },
        "setup": _ensure_template,
        "assert_orm": _assert_v1_default,
        # No compensation needed: single-version throwaway template; the
        # default flag is idempotent and scoped to it.
    },
    {
        "tool": "save_prompt_name",
        "args": {
            "template_id": WRITECHECK_TEMPLATE_ID,
            "name": WRITECHECK_TEMPLATE_NAME + "-renamed",
        },
        "setup": _ensure_template,
        "assert_orm": _assert_renamed,
        "compensate": (
            "save_prompt_name",
            {
                "template_id": WRITECHECK_TEMPLATE_ID,
                "name": WRITECHECK_TEMPLATE_NAME,
            },
        ),
    },
    _UPDATE_EVAL_CONFIG_ENTRY,
    {
        "tool": "compare_experiments",
        "args": {"experiment_id": SEED_EXPERIMENT_ID, "weights": {}},
        "assert_orm": _assert_comparisons,
        # Recompute is idempotent over the same completed experiment — the
        # stored comparison is refreshed in place; nothing to compensate.
    },
]
