"""Clean evaluator settings that grading leaked into simulate eval attachments.

Grading used to save the evaluator's own settings into ``config["config"]``, and a
stored grading text then won over the template's on every later grade. The rule here
removes only what grading wrote, keeps a copy of what may have been the user's, and
``restore`` puts that copy back.

Migration 0114 carries its own frozen copy of the strip rule and imports none of this;
keep the two in step on purpose, and let the golden tests tell you when they differ.
"""

import uuid

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
        "reverse_output",
        "check_internet",
        "agent_mode",
        "tools",
        "knowledge_bases",
        "data_injection",
        "summary",
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


def keys_to_remove(
    nested: dict, template_config, protected: frozenset | set = frozenset()
) -> list[str]:
    """What the clean-up takes out of one nested dict.

    ``api_key`` always goes. Everything else goes only when the dict shows the leak.
    ``protected`` is the keys a restore handled on this row; they stay.
    """
    removed = set()
    if "api_key" in nested:
        removed.add("api_key")
    if has_leak_signal(nested):
        removed.update(removable_keys(nested, template_config))
    removed -= set(protected)
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


def _restored_ids(removed_model, using) -> dict:
    """``{eval_config_id: keys a restore handled}``, the union over that row's copies."""
    protected: dict = {}
    for eval_config_id, restored_keys in (
        removed_model.objects.using(using)
        .filter(restored_keys__isnull=False)
        .values_list("eval_config_id", "restored_keys")
    ):
        if restored_keys:
            protected.setdefault(eval_config_id, set()).update(restored_keys)
    return protected


def strip_rows(
    model, removed_model, *, using: str = "default", dry_run: bool = False
) -> tuple[int, int]:
    """Remove the evaluator-written keys from every attachment's nested ``config`` dict.

    ``model`` is ``SimulateEvalConfig`` and ``removed_model`` the copy table's model.
    Rows are read in primary-key pages, soft-deleted ones included, so no long
    transaction or server-side cursor is held. A changed row is written in its own short
    transaction: a conditional update of ``config`` only when the row still holds what was
    read (so ``updated_at`` is not bumped and an edit saved meanwhile is never
    overwritten; the next run gets it), then the copy row. On a row with a restored copy
    the keys its restore handled stay (the union of its copies' ``restored_keys``), so a
    restore sticks; everything else the rule removes still goes, ``api_key`` always. With ``dry_run`` nothing is written and the second count
    is the rows that would change.

    Returns ``(scanned, changed)``: rows holding a nested dict, and rows rewritten.
    """
    protected_by_row = _restored_ids(removed_model, using)
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
            removed = keys_to_remove(
                nested,
                row.eval_template.config,
                protected=protected_by_row.get(row.pk, frozenset()),
            )
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


class _Raced(Exception):
    """A row or a copy changed between the read and the write of one restore."""


def restore(
    model,
    removed_model,
    *,
    ids=None,
    keys=RESTORABLE_KEYS,
    using: str = "default",
) -> dict:
    """Put the copied keys back into the attachments they were removed from.

    ``ids`` limits it to those eval config ids (UUIDs or their strings); ``None`` means
    every unrestored copy. ``keys`` must be a non-empty subset of ``RESTORABLE_KEYS``, or
    ``ValueError`` is raised. A copy's restorable keys are the ones it holds that are in
    ``RESTORABLE_KEYS``; a copy with none is never marked and its row is never reported.
    A row's copies are applied oldest first and a key the row holds now is never
    overwritten. In each run a key is handled when it was applied or the row already held
    it; ``restored_keys`` accumulates every handled key (the strip keeps exactly those on
    that row), and ``restored_at`` is set only when every restorable key of the copy is
    handled, so a narrowed restore leaves the copy open for a later one. Rows are read
    through ``_base_manager``, so soft-deleted ones are found.

    A row whose nested dict shows the leak signal now is skipped (``leaked``): strip it
    first. A row whose config or nested ``config`` is present but not a dict is skipped
    (``skipped``), never replaced. Whatever has to be written (the row's config, even when
    only the copies are closed, and each copy's mark) is a conditional update inside one
    transaction: the row must still hold the config as read and each copy must still be
    unmarked with the ``restored_keys`` as read. If any of them changed, the whole row is
    rolled back and reported ``raced``; it can be retried.

    Returns a dict of eval config id lists: ``restored`` (a key was applied), ``missing``,
    ``raced``, ``skipped``, ``leaked``, ``nothing_to_apply`` (none of the requested keys is
    left in any open copy of the row), ``closed_without_applying`` (a copy was closed
    because every key was already there), ``none`` (requested ids with no unrestored copy
    that has a restorable key); and ``applied`` and ``present``, each ``{id: sorted keys}``
    for the keys written and the keys the row already held.
    """
    keys = frozenset(keys or ())
    if not keys:
        raise ValueError("restore needs at least one key")
    unknown = keys - RESTORABLE_KEYS
    if unknown:
        raise ValueError(f"not restorable keys: {sorted(unknown)}")

    copies = removed_model.objects.using(using).filter(restored_at__isnull=True)
    if ids is not None:
        ids = [uuid.UUID(str(i)) for i in ids]
        copies = copies.filter(eval_config_id__in=ids)
    by_row: dict = {}
    for copy_row in copies.order_by("created_at", "pk"):
        if isinstance(copy_row.removed, dict) and (
            copy_row.removed.keys() & RESTORABLE_KEYS
        ):
            by_row.setdefault(copy_row.eval_config_id, []).append(copy_row)

    report: dict = {
        "restored": [],
        "missing": [],
        "raced": [],
        "skipped": [],
        "leaked": [],
        "nothing_to_apply": [],
        "closed_without_applying": [],
        "none": [i for i in (ids or []) if i not in by_row],
        "applied": {},
        "present": {},
    }
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
        if row.config is None:
            config: dict = {}
            row_filter: dict = {"config__isnull": True}
        elif isinstance(row.config, dict):
            config = dict(row.config)
            row_filter = {"config": row.config}
        else:
            report["skipped"].append(eval_config_id)
            continue
        if "config" in config and not isinstance(config["config"], dict):
            report["skipped"].append(eval_config_id)
            continue
        nested = dict(config.get("config") or {})
        if has_leak_signal(nested):
            report["leaked"].append(eval_config_id)
            continue
        if not any(
            (copy_row.removed.keys() & keys) - set(copy_row.restored_keys or ())
            for copy_row in row_copies
        ):
            report["nothing_to_apply"].append(eval_config_id)
            continue

        applied_keys: set = set()
        present_keys: set = set()
        closed_without_applying = False
        marks = []
        for copy_row in row_copies:
            restorable = copy_row.removed.keys() & RESTORABLE_KEYS
            earlier = set(copy_row.restored_keys or [])
            applied = set()
            handled = set(earlier)
            for key in sorted(restorable - earlier):
                if key in nested:
                    present_keys.add(key)
                    handled.add(key)
                elif key in keys:
                    nested[key] = copy_row.removed[key]
                    applied.add(key)
                    handled.add(key)
            applied_keys |= applied
            done = restorable <= handled
            if handled != earlier or done:
                marks.append((copy_row, sorted(handled), done))
            closed_without_applying = closed_without_applying or (done and not applied)
        if not marks:
            continue

        new_config = {**config, "config": nested} if applied_keys else row.config
        try:
            with transaction.atomic(using=using):
                # Run even when nothing is applied: the copies are closed on what the row
                # held when it was read, so the row must still hold it.
                written = (
                    model._base_manager.using(using)
                    .filter(pk=row.pk, **row_filter)
                    .update(config=new_config)
                )
                if not written:
                    raise _Raced
                now = timezone.now()
                for copy_row, handled_keys, done in marks:
                    as_read = (
                        {"restored_keys__isnull": True}
                        if copy_row.restored_keys is None
                        else {"restored_keys": copy_row.restored_keys}
                    )
                    marked = (
                        removed_model.objects.using(using)
                        .filter(pk=copy_row.pk, restored_at__isnull=True, **as_read)
                        .update(
                            restored_at=now if done else None,
                            restored_keys=handled_keys,
                        )
                    )
                    if not marked:
                        raise _Raced
        except _Raced:
            report["raced"].append(eval_config_id)
            continue
        if applied_keys:
            report["restored"].append(eval_config_id)
            report["applied"][eval_config_id] = sorted(applied_keys)
        if present_keys:
            report["present"][eval_config_id] = sorted(present_keys)
        if closed_without_applying:
            report["closed_without_applying"].append(eval_config_id)
    return report
