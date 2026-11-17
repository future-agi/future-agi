"""The prompt writer's one lock order (webhooks DESIGN.md section 14.3 item 2).

Every transaction that changes a prompt label, or soft-deletes prompts or versions through the writer, takes its row
locks in this order, and only the levels it needs:

1. prompt rows (``PromptTemplate``), by id;
2. version rows (``PromptVersion``), by id;
3. label heads (``PromptLabelHead``), by ``label_id`` (``prompt_label._lock_heads``).

So no two writers wait on each other in a cycle. Prompt and version rows are locked ``FOR NO KEY UPDATE``. That
serializes the writers among themselves, but it never blocks the ``FOR KEY SHARE`` lock that a foreign-key check takes
on a referenced row (a new attachment or head pointing at the version or the prompt). The one exception is a prompt's
own soft delete (``prompt_label.template_saving``): it locks its prompt row ``FOR UPDATE``, so a writer about to insert
the first head of a label on that prompt waits for the delete in that foreign-key check, and then sees it.

Before this order, ``assign_labels_to_version`` and a version soft delete locked the version and then the heads, while
``move_label`` locked a head and then waited on the version for its foreign-key checks. Postgres detected the deadlock
and aborted one of the two requests.
"""

from __future__ import annotations

from collections.abc import Iterable

from django.db import connection

from model_hub.models.prompt_label import PromptLabelHead
from model_hub.models.run_prompt import PromptTemplate, PromptVersion


class DeletedTarget(ValueError):
    """A label cannot be put on a deleted version, or on a version of a deleted prompt."""


def _in_transaction() -> None:
    if not connection.in_atomic_block:
        raise RuntimeError("prompt writer locks are only held inside a transaction")


def lock_prompts(ids: Iterable) -> list[PromptTemplate]:
    """Level 1: the prompt rows, by id."""
    _in_transaction()
    return list(
        PromptTemplate._base_manager.select_for_update(no_key=True, of=("self",))
        .filter(pk__in=list(ids))
        .order_by("pk")
    )


def lock_versions(ids: Iterable) -> list:
    """Level 2: the version rows, by id. Returns the ids it locked."""
    _in_transaction()
    return list(
        PromptVersion._base_manager.select_for_update(no_key=True, of=("self",))
        .filter(pk__in=list(ids))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def lock_versions_of(prompt_ids: Iterable) -> list:
    """Level 2 for whole prompts: every version row of these prompts, by id."""
    _in_transaction()
    return list(
        PromptVersion._base_manager.select_for_update(no_key=True, of=("self",))
        .filter(original_template_id__in=list(prompt_ids))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def locked_version_flags(pk, *fields: str) -> dict | None:
    """Level 2 for one version, returning ``fields`` as they are under the lock (None when the row is gone)."""
    _in_transaction()
    return (
        PromptVersion._base_manager.select_for_update(no_key=True, of=("self",))
        .filter(pk=pk)
        .values(*fields)
        .first()
    )


def locked_prompt_deleted(pk) -> bool | None:
    """Level 1 for a prompt's own soft delete, ``FOR UPDATE`` (the exception named above): the stored ``deleted``
    flag under the lock, or None when the row is gone."""
    _in_transaction()
    row = (
        PromptTemplate._base_manager.select_for_update(of=("self",))
        .filter(pk=pk)
        .values("deleted")
        .first()
    )
    return None if row is None else row["deleted"]


def real_holders(template_id, heads: dict) -> dict:
    """The version that really holds each locked head's label (``{label_id: version_id or None}``), read under the
    head locks from the attachment table: the head's own version while it still holds the label,
    else the most recently updated live version holding it (as the migration's backfill seeds a head), else none.

    It differs from ``head.version_id`` only when a pod still on code from before migration 0123 (a rolling deploy)
    moved or removed a label without moving its head."""
    held: dict[str, list] = {}
    for lid, vid, deleted in (
        PromptVersion.labels.through.objects.filter(
            promptlabel_id__in=list(heads),
            promptversion__original_template_id=template_id,
        )
        .order_by("promptversion__updated_at", "promptversion_id")
        .values_list("promptlabel_id", "promptversion_id", "promptversion__deleted")
    ):
        held.setdefault(str(lid), []).append((vid, deleted))
    out = {}
    for lid, head in heads.items():
        rows = held.get(lid, [])
        if head.version_id is not None and any(v == head.version_id for v, _ in rows):
            out[lid] = head.version_id
        else:
            live = [v for v, deleted in rows if not deleted]
            out[lid] = live[-1] if live else None
    return out


def ever_on(template: PromptTemplate, lid: str) -> bool:
    """The label has a head on this prompt, or sits on one of its versions (from before the heads)."""
    return (
        PromptLabelHead.objects.filter(template=template, label_id=lid).exists()
        or PromptVersion.labels.through.objects.filter(
            promptlabel_id=lid, promptversion__original_template=template
        ).exists()
    )


def loaded_settled(version: PromptVersion, deletes: bool) -> bool:
    """Whether a save needs no level-2 lock at all (the run paths full-save committed versions
    repeatedly). A version loaded from the database already committed (and already deleted, if it is being deleted)
    can neither publish (one event per version id, D-X3) nor release a label on this save. ``PromptVersion.from_db``
    records the loaded values; a new, re-keyed or refreshed instance, or one loaded without both fields, locks."""
    loaded = getattr(version, "_loaded_flags", None)
    if version._state.adding or not loaded or loaded.get("pk") != version.pk:
        return False
    if loaded.get("is_draft") is not False:
        return False
    return not deletes or loaded.get("deleted") is True


def refuse_deleted_target(version: PromptVersion) -> None:
    """Refuse a label for a deleted target. Called with the version (level 2) and the heads (level 3) locked:

    - a version soft delete holds the version row, so it has committed (and is seen here) or waits for this writer;
    - a bulk prompt delete (``prompt_label.release_prompts``) holds every version row of its prompts first: the same;
    - a prompt's own soft delete holds the prompt row ``FOR UPDATE`` and then the heads. A writer holding a head it
      already had commits first, and the delete then releases that head. A writer waiting on a head, or inserting a
      new one (whose foreign-key check waits on the prompt row), sees the delete here.

    So no label is left on a deleted version.
    """
    row = (
        PromptVersion._base_manager.filter(pk=version.pk)
        .values("deleted", "original_template__deleted")
        .first()
    )
    if row is None or row["deleted"] or row["original_template__deleted"]:
        raise DeletedTarget(
            "a deleted prompt version, or a version of a deleted prompt, cannot hold a label"
        )
