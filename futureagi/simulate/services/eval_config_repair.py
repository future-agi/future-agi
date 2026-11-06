"""Clean evaluator settings that grading leaked into simulate eval attachments."""

import structlog

logger = structlog.get_logger(__name__)

# Every key the evaluator set-up writes; `code` is a real fallback input, so it stays out.
EVALUATOR_WRITTEN_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "provider",
        "model",
        "model_type",
        "rule_prompt",
        "system_prompt",
        "output_type",
        "messages",
        "few_shot_examples",
        "check_internet",
        "multi_choice",
        "choices",
        "choice_scores",
        "pass_threshold",
        "reverse_output",
        "knowledge_base_id",
        "agent_mode",
        "tools",
        "knowledge_bases",
        "data_injection",
        "summary",
        "organization_id",
        "workspace_id",
        "custom_eval",
        "param_modalities",
        "required_keys",
        "template_format",
    }
)


def strip_evaluator_keys(model) -> tuple[int, int]:
    """Remove the evaluator-written keys from every attachment's nested ``config`` dict.

    Grading used to save the evaluator's own settings into ``config["config"]``, and a
    stored grading text then won over the template's on every later grade; removing
    them makes the next grade take them from the template again.

    Only the nested dict is cleaned, because that is where grading wrote. Top-level
    keys, the harness's snapshot of the template config taken when the eval was
    attached among them, are left alone on purpose, even though some of them share a
    name with a key in the set. Keys outside the set, ``code`` included, stay. Rows
    without a nested dict are not saved.

    ``model`` is the ``SimulateEvalConfig`` class, real or the migration's historical
    one, so only what both provide is used. ``_base_manager`` is a plain manager in both
    and includes soft-deleted rows, which carry the same leaked keys. Rows are saved with
    a conditional update that writes only ``config`` and only when the row still holds
    what was read, so ``updated_at`` is not bumped (a repair is not an edit) and an edit
    saved while the strip runs is never overwritten. Running it again changes nothing.

    Returns ``(scanned, changed)``: rows holding a nested dict, and rows rewritten.
    """
    scanned = 0
    changed = 0
    rows = (
        model._base_manager.filter(config__has_key="config")
        .only("id", "config")
        .iterator()
    )
    for row in rows:
        # The database `?` operator also matches a JSON string or array holding
        # "config", so the shape is checked again here.
        if not isinstance(row.config, dict):
            continue
        nested = row.config.get("config")
        if not isinstance(nested, dict):
            continue
        scanned += 1
        cleaned = {
            key: value
            for key, value in nested.items()
            if key not in EVALUATOR_WRITTEN_KEYS
        }
        if len(cleaned) == len(nested):
            continue
        # Only if nobody saved the row meanwhile; a later run picks it up.
        changed += model._base_manager.filter(pk=row.pk, config=row.config).update(
            config={**row.config, "config": cleaned}
        )

    logger.info(
        "simulate_eval_config_evaluator_keys_stripped",
        scanned=scanned,
        changed=changed,
    )
    return scanned, changed
