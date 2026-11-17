"""Clean evaluator settings that grading leaked into simulate eval attachments.

Grading used to save the evaluator's own settings into ``config["config"]``, and a
stored grading text then won over the template's on every later grade. The rule here
removes only what grading wrote, keeps a copy of what may have been the user's, and
``restore`` puts that copy back.

Migration 0114 carries its own frozen copy of the strip rule and imports none of this;
keep the two in step on purpose, and let the golden tests tell you when they differ.
"""

import structlog
from django.db import transaction
from django.utils import timezone

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

# A row was graded with the leak when its nested dict holds both keys of any pair. No
# screen or template schema puts these pairs there, and each pair is written by every
# leaking grade of some evaluator type: Agent writes `pass_threshold` + `model`,
# CustomPrompt `model` + `output_type`, Deterministic and Ranking `model` + `provider`.
LEAK_SIGNAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("pass_threshold", "model"),
    ("model", "output_type"),
    ("model", "provider"),
)

# The keys a restore may bring back: the ones grading honours from a stored value.
# The signal keys, and the keys grading derives again, never come back.
RESTORABLE_KEYS: frozenset[str] = frozenset(
    {
        "rule_prompt",
        "system_prompt",
        "template_format",
        "messages",
        "few_shot_examples",
        "choices",
        "multi_choice",
        "custom_eval",
        "knowledge_base_id",
    }
)

PAGE_SIZE = 500


def has_leak_signal(nested: dict) -> bool:
    """Whether grading wrote into this nested dict, judged on the dict as read."""
    return any(
        first in nested and second in nested for first, second in LEAK_SIGNAL_PAIRS
    )


def _template_parameters(template_config) -> frozenset[str]:
    """Names of the template's own parameters, whose values in the nested dict are the user's."""
    if not isinstance(template_config, dict):
        return frozenset()
    parameters = template_config.get("config")
    if not isinstance(parameters, dict):
        return frozenset()
    return frozenset(parameters)


def removable_keys(nested: dict, template_config) -> list[str]:
    """Sorted evaluator-written keys present in ``nested`` that are not the template's parameters."""
    kept = _template_parameters(template_config)
    return sorted(
        key for key in nested if key in EVALUATOR_WRITTEN_KEYS and key not in kept
    )


def keys_to_remove(nested: dict, template_config) -> list[str]:
    """What the clean-up takes out of one nested dict.

    ``api_key`` always goes. Everything else goes only when the dict shows the leak.
    """
    removed = set()
    if "api_key" in nested:
        removed.add("api_key")
    if has_leak_signal(nested):
        removed.update(removable_keys(nested, template_config))
    return sorted(removed)


def copyable(removed: dict) -> dict:
    """The removed pairs worth keeping: never ``api_key``, never a ``None`` value."""
    return {
        key: value
        for key, value in removed.items()
        if key != "api_key" and value is not None
    }


def _read_page(model, after_pk, using):
    rows = model._base_manager.using(using).filter(config__has_key="config")
    if after_pk is not None:
        rows = rows.filter(pk__gt=after_pk)
    return list(
        rows.order_by("pk")
        .select_related("eval_template")
        .only("id", "config", "eval_template__config")[:PAGE_SIZE]
    )


def _restored_ids(removed_model, using):
    return set(
        removed_model.objects.using(using)
        .filter(restored_at__isnull=False)
        .values_list("eval_config_id", flat=True)
    )


def strip_rows(
    model, removed_model, *, using: str = "default", dry_run: bool = False
) -> tuple[int, int]:
    """Remove the evaluator-written keys from every attachment's nested ``config`` dict.

    ``model`` is ``SimulateEvalConfig`` and ``removed_model`` the copy table's model.
    Rows are read in primary-key pages, soft-deleted ones included, so no long
    transaction or server-side cursor is held. A changed row is written in its own short
    transaction: a conditional update of ``config`` only when the row still holds what was
    read (so ``updated_at`` is not bumped and an edit saved meanwhile is never
    overwritten; the next run gets it), then the copy row. Rows with a restored copy are
    skipped, so a restore sticks. With ``dry_run`` nothing is written and the second count
    is the rows that would change.

    Returns ``(scanned, changed)``: rows holding a nested dict, and rows rewritten.
    """
    restored_ids = _restored_ids(removed_model, using)
    scanned = 0
    changed = 0
    last_pk = None
    while True:
        page = _read_page(model, last_pk, using)
        for row in page:
            # The database `?` operator also matches a JSON string or array holding
            # "config", so the shape is checked again here.
            if not isinstance(row.config, dict):
                continue
            nested = row.config.get("config")
            if not isinstance(nested, dict):
                continue
            scanned += 1
            if row.pk in restored_ids:
                continue
            removed = keys_to_remove(nested, row.eval_template.config)
            if not removed:
                continue
            if dry_run:
                changed += 1
                continue
            cleaned = {k: v for k, v in nested.items() if k not in removed}
            kept_copy = copyable({k: nested[k] for k in removed})
            with transaction.atomic(using=using):
                written = (
                    model._base_manager.using(using)
                    .filter(pk=row.pk, config=row.config)
                    .update(config={**row.config, "config": cleaned})
                )
                if written and kept_copy:
                    removed_model.objects.using(using).create(
                        eval_config_id=row.pk, removed=kept_copy
                    )
            changed += written
        if len(page) < PAGE_SIZE:
            break
        last_pk = page[-1].pk

    logger.info(
        "simulate_eval_config_evaluator_keys_stripped",
        scanned=scanned,
        changed=changed,
        dry_run=dry_run,
    )
    return scanned, changed


def restore(
    model,
    removed_model,
    *,
    ids=None,
    keys=RESTORABLE_KEYS,
    using: str = "default",
) -> dict:
    """Put the copied keys back into the attachments they were removed from.

    ``ids`` limits it to those eval config ids; ``None`` means every unrestored copy.
    Only keys in ``keys`` that are also in ``RESTORABLE_KEYS`` come back. A row's copies
    are applied oldest first and a key the row holds now is never overwritten. Rows are
    read through ``_base_manager``, so soft-deleted ones are found. The write is a
    conditional update inside one transaction that also marks the copies restored; a lost
    race is reported and can be retried. A copy whose row is gone is reported and skipped.

    Returns ``{"restored": [...ids], "missing": [...ids], "raced": [...ids]}``.
    """
    allowed = frozenset(keys) & RESTORABLE_KEYS
    copies = removed_model.objects.using(using).filter(restored_at__isnull=True)
    if ids is not None:
        copies = copies.filter(eval_config_id__in=list(ids))
    by_row: dict = {}
    for copy_row in copies.order_by("created_at", "pk"):
        by_row.setdefault(copy_row.eval_config_id, []).append(copy_row)

    report: dict = {"restored": [], "missing": [], "raced": []}
    for eval_config_id, row_copies in by_row.items():
        row = (
            model._base_manager.using(using)
            .filter(pk=eval_config_id)
            .only("id", "config")
            .first()
        )
        if row is None:
            report["missing"].append(eval_config_id)
            continue
        config = dict(row.config) if isinstance(row.config, dict) else {}
        nested = (
            dict(config["config"]) if isinstance(config.get("config"), dict) else {}
        )
        applied_by_copy = []
        for copy_row in row_copies:
            applied = []
            for key, value in copy_row.removed.items():
                if key in allowed and key not in nested:
                    nested[key] = value
                    applied.append(key)
            applied_by_copy.append((copy_row, sorted(applied)))
        with transaction.atomic(using=using):
            written = (
                model._base_manager.using(using)
                .filter(pk=row.pk, config=row.config)
                .update(config={**config, "config": nested})
            )
            if not written:
                report["raced"].append(eval_config_id)
                continue
            now = timezone.now()
            for copy_row, applied in applied_by_copy:
                removed_model.objects.using(using).filter(pk=copy_row.pk).update(
                    restored_at=now, restored_keys=applied
                )
        report["restored"].append(eval_config_id)
    return report
