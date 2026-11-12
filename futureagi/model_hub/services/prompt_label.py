"""The one writer of prompt label attachments and version commits (webhooks DESIGN.md section 14.3).

Every change of where a label sits within a prompt goes through :func:`_apply`, in one transaction:
1. lock in the one order of :mod:`model_hub.services.prompt_locks` (prompt rows, version rows, then label heads by
   ``label_id``, a missing head inserted first with ``ON CONFLICT DO NOTHING``), only the levels it needs, so writers
   serialize per (prompt, label) and cannot deadlock against each other;
2. read from and to **under the lock** (from is the version that really holds the label in the attachment table,
   :func:`prompt_locks.real_holders`); a label is never put on a deleted version or a version of a deleted prompt;
3. write the attachment, bump ``revision`` and move ``version`` on each head that actually changed;
4. publish one ``prompt.label.changed`` per changed (prompt, label). A request that changes nothing publishes nothing.

Version commits go through :func:`commit_version`, and ``PromptVersion.save()`` reports every insert or draft-to-
committed transition and every soft delete here (:func:`version_saving`), so ``.create()``, ``.save()`` and this
module are the only ways a version becomes committed. A guard test fails CI on any label or draft write elsewhere.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel, PromptLabelHead
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.services import prompt_events
from model_hub.services.prompt_actor import (
    acting_as,
    current_actor,
    events_suppressed,
    publishing,
)
from model_hub.services.prompt_locks import (
    DeletedTarget,
    ever_on,
    loaded_settled,
    lock_prompts,
    lock_versions,
    lock_versions_of,
    locked_prompt_deleted,
    locked_version_flags,
    real_holders,
    refuse_deleted_target,
)

# Reads and locks here use each model's plain base manager: the default managers filter by the request's workspace
# (which hides the global system labels, and joins the workspace table, which FOR UPDATE refuses on its nullable side).

CAUSE_CHANGE, CAUSE_REPLACED, CAUSE_DELETED = (
    "label_change",
    "replaced",
    "version_deleted",
)


@dataclass(frozen=True)
class LabelChange:
    label: PromptLabel
    revision: int
    from_version: PromptVersion | None
    to_version: PromptVersion | None
    cause: str

    @property
    def action(self) -> str:
        if self.from_version is None:
            return "assigned"
        return "removed" if self.to_version is None else "moved"


# ---- the serialization point ------------------------------------------------------------------------------------


def _through():
    return PromptVersion.labels.through


def _ensure_heads(template: PromptTemplate, label_ids: Iterable) -> None:
    """Insert a head for each label that has none, seeded from today's attachment (the most recently updated live
    version holding it), as the migration's backfill does."""
    wanted = sorted({str(x) for x in label_ids})
    if not wanted:
        return
    have = {
        str(x)
        for x in PromptLabelHead.objects.filter(
            template=template, label_id__in=wanted
        ).values_list("label_id", flat=True)
    }
    missing = [x for x in wanted if x not in have]
    if not missing:
        return
    seeds = {}
    for row in (
        _through()
        .objects.filter(
            promptlabel_id__in=missing,
            promptversion__original_template=template,
            promptversion__deleted=False,
        )
        .order_by("promptversion__updated_at", "promptversion_id")
        .values("promptlabel_id", "promptversion_id")
    ):
        seeds[str(row["promptlabel_id"])] = row["promptversion_id"]
    PromptLabelHead.objects.bulk_create(
        [
            PromptLabelHead(template=template, label_id=x, version_id=seeds.get(x))
            for x in missing
        ],
        ignore_conflicts=True,
    )


def _lock_heads(template: PromptTemplate, *, label_ids=(), all_of_template=False):
    """Level 3 of the lock order: lock heads ``FOR UPDATE`` in ``label_id`` order and return them by label id. With
    ``all_of_template``, every head of the prompt (a set-replace or a version deletion needs the whole map). The
    caller already holds the version rows it writes to."""
    _ensure_heads(template, label_ids)
    qs = PromptLabelHead.objects.select_for_update().filter(template=template)
    if not all_of_template:
        qs = qs.filter(label_id__in=list(label_ids))
    heads = {str(h.label_id): h for h in qs.order_by("label_id")}
    for lid, real in real_holders(template.pk, heads).items():
        if heads[lid].version_id != real:
            # Left stale by a pod on older code: repaired before it is read; no revision, no event.
            heads[lid].version_id = real
            heads[lid].save(update_fields=["version", "updated_at"])
    return heads


def _apply(
    template: PromptTemplate,
    heads: dict,
    targets: dict,
    *,
    publish: bool,
) -> list[LabelChange]:
    """``targets`` maps a label id to ``(to_version or None, cause)``. Heads must already be locked."""
    changes: list[LabelChange] = []
    versions: dict = {}

    def version(vid):
        if vid is None:
            return None
        if vid not in versions:
            versions[vid] = PromptVersion._base_manager.get(pk=vid)
        return versions[vid]

    for label_id in sorted(targets):
        head = heads[label_id]
        to, cause = targets[label_id]
        to_id = None if to is None else to.id
        if to is not None:
            versions[to.id] = to
        attached = _through().objects.filter(
            promptlabel_id=label_id, promptversion__original_template=template
        )
        # The label is on one version at most: this also repairs a pair the O1 race left on two versions.
        attached.exclude(promptversion_id=to_id).delete()
        if to is not None:
            _through().objects.get_or_create(
                promptversion_id=to.id, promptlabel_id=label_id
            )
        if head.version_id == to_id:
            continue  # nothing changed: no revision, no event (O5)
        frm = version(head.version_id)
        head.version_id = to_id
        head.revision += 1
        head.save(update_fields=["version", "revision", "updated_at"])
        changes.append(
            LabelChange(
                label=head.label,
                revision=head.revision,
                from_version=frm,
                to_version=to,
                cause=cause,
            )
        )
    if changes and publishing(publish):
        prompt_events.publish_label_changes(
            template, changes, actor=current_actor(), changed_at=timezone.now()
        )
    return changes


def _template_of(version: PromptVersion) -> PromptTemplate:
    template = version.original_template
    if template is None:
        raise ValueError("a prompt version without a prompt cannot hold labels")
    return template


def assign_labels(
    version: PromptVersion, labels, *, publish: bool = True
) -> list[LabelChange]:
    """Set-replace: ``version`` holds exactly ``labels`` afterwards. Each label moves to it from wherever it was, and
    each label it held that is not in ``labels`` is removed with ``cause: replaced`` (O2)."""
    template = _template_of(version)
    wanted = {str(lbl.id) for lbl in labels}
    with transaction.atomic():
        lock_versions([version.pk])
        heads = _lock_heads(template, label_ids=wanted, all_of_template=True)
        if wanted:
            refuse_deleted_target(version)
        targets = dict.fromkeys(wanted, (version, CAUSE_CHANGE))
        for lid, head in heads.items():
            if head.version_id == version.id and lid not in wanted:
                targets[lid] = (None, CAUSE_REPLACED)
        return _apply(template, heads, targets, publish=publish)


def move_label(
    version: PromptVersion, label, *, publish: bool = True
) -> list[LabelChange]:
    """Put ``label`` on ``version`` (assigned, or moved from the version that held it). Other labels are untouched."""
    template = _template_of(version)
    with transaction.atomic():
        lock_versions([version.pk])
        heads = _lock_heads(template, label_ids=[str(label.id)])
        refuse_deleted_target(version)
        return _apply(
            template, heads, {str(label.id): (version, CAUSE_CHANGE)}, publish=publish
        )


def remove_label(
    version: PromptVersion, label, *, publish: bool = True
) -> list[LabelChange]:
    """Take ``label`` off ``version``. Nothing happens (and nothing is published) when it is not there; a label that
    was never on the prompt gets no head row either."""
    template = _template_of(version)
    lid = str(label.id)
    with transaction.atomic():
        lock_versions([version.pk])
        if not ever_on(template, lid):
            return []
        heads = _lock_heads(template, label_ids=[lid])
        if heads[lid].version_id != version.id:
            _through().objects.filter(
                promptversion_id=version.id, promptlabel_id=lid
            ).delete()
            return []
        return _apply(template, heads, {lid: (None, CAUSE_CHANGE)}, publish=publish)


def release_deleted_versions(
    template: PromptTemplate, version_ids, *, publish: bool = True
):
    """Every label held by a version being soft-deleted is removed with ``cause: version_deleted``. The caller
    (``version_saving``) holds the version rows."""
    ids = {str(v) for v in version_ids}
    with transaction.atomic():
        heads = _lock_heads(template, all_of_template=True)
        targets = {
            lid: (None, CAUSE_DELETED)
            for lid, h in heads.items()
            if h.version_id is not None and str(h.version_id) in ids
        }
        return _apply(template, heads, targets, publish=publish)


@contextmanager
def template_saving(template: PromptTemplate, update_fields) -> Iterator[None]:
    """Wraps ``PromptTemplate.save()``. A save that soft-deletes a prompt releases every label it holds, with
    ``cause: version_deleted``, in the same transaction: deleting a prompt soft-deletes all its versions (the
    template destroy action does it right after this save)."""
    fields = None if update_fields is None else set(update_fields)
    deletes = bool(template.deleted) and (fields is None or "deleted" in fields)
    if not deletes or template._state.adding:
        yield
        return
    with transaction.atomic():
        was_deleted = locked_prompt_deleted(template.pk)
        yield
        if was_deleted is False:
            release_prompt_labels(template)


def release_prompt_labels(template: PromptTemplate, *, publish: bool = True):
    """A prompt is being deleted with all its versions: every label it holds is removed with ``cause:
    version_deleted``. It takes only the heads: the caller holds the prompt row (``template_saving``) or every version
    row too (:func:`release_prompts`)."""
    with transaction.atomic():
        heads = _lock_heads(template, all_of_template=True)
        targets = {
            lid: (None, CAUSE_DELETED)
            for lid, h in heads.items()
            if h.version_id is not None
        }
        return _apply(template, heads, targets, publish=publish)


def release_prompts(template_ids, *, publish: bool = True) -> list[PromptTemplate]:
    """For a bulk prompt delete (a folder): lock the prompt rows and all their version rows (levels 1 and 2), then
    release every label of each live prompt (``version_deleted``). The caller's ``.update(deleted=True)`` that follows,
    in the same transaction, then finds its rows locked already: it cannot wait on a writer that waits for a head."""
    templates = lock_prompts(template_ids)
    lock_versions_of([t.pk for t in templates])
    for template in templates:
        if not template.deleted:
            release_prompt_labels(template, publish=publish)
    return templates


def assign_labels_to_version(
    template_version_id, label_ids, user, workspace, *, actor=None
):
    """Authorize the live canonical parent before any writer side effect.

    ``workspace`` is the explicitly selected request/service workspace, not an
    ambient or account-primary fallback. Its admitted organization is retained
    when reloading it. Labels are independently authorized, including shared
    SYSTEM labels; neither label access nor the event actor grants parent access.
    """
    from uuid import UUID

    from rest_framework.exceptions import PermissionDenied

    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tfc.utils.tenant_scope import (
        acts_in_organization,
        scope_to_accessible_workspace,
    )

    def deny():
        # Preserve the action's opaque not-found error text for absent and denied
        # parents alike; the view retains its existing typed 400 envelope.
        raise PermissionDenied("Template version does not exist")

    try:
        version_id = UUID(str(template_version_id))
        user_id = UUID(str(getattr(user, "pk", None)))
        workspace_id = UUID(str(getattr(workspace, "pk", None)))
        organization_id = UUID(str(getattr(workspace, "organization_id", None)))
    except (ValueError, TypeError, AttributeError):
        deny()

    def authorized_parents():
        live_user = User.objects.filter(pk=user_id, is_active=True).first()
        live_workspace = (
            Workspace.no_workspace_objects.filter(
                pk=workspace_id,
                organization_id=organization_id,
                deleted=False,
                is_active=True,
            )
            .select_related("organization")
            .first()
        )
        if (
            live_user is None
            or live_workspace is None
            or not acts_in_organization(live_user, live_workspace.organization)
            or not live_user.can_write_to_workspace(live_workspace)
        ):
            deny()
        parents = scope_to_accessible_workspace(
            PromptTemplate.no_workspace_objects.filter(deleted=False),
            live_workspace.organization,
            live_workspace,
            user=live_user,
        ).filter(
            Q(workspace__isnull=True)
            | Q(
                workspace__deleted=False,
                workspace__is_active=True,
                workspace__organization_id=organization_id,
            )
        )
        return parents

    with transaction.atomic():
        template_version = PromptVersion._base_manager.filter(
            pk=version_id,
            original_template__in=authorized_parents(),
            deleted=False,
        ).first()
        if template_version is None:
            deny()
        template_id = template_version.original_template_id
        # The established order is parent -> version -> heads. Never add a
        # parent lock after taking a version/head lock. No writer hooks change.
        if not lock_prompts([template_id]) or not lock_versions([version_id]):
            deny()
        # Waiting for those real locks can outlive admission. Re-read the actor,
        # workspace, memberships, parent binding and version under the locks.
        template_version = (
            PromptVersion._base_manager.filter(
                pk=version_id,
                original_template_id=template_id,
                original_template__in=authorized_parents(),
                deleted=False,
            )
            .select_related("original_template")
            .first()
        )
        if template_version is None:
            deny()
        labels = list(
            PromptLabel.no_workspace_objects.filter(
                Q(organization_id=organization_id, workspace_id=workspace_id)
                | Q(organization__isnull=True, type=LabelTypeChoices.SYSTEM.value),
                id__in=label_ids,
                deleted=False,
            )
        )
        if len(labels) != len(label_ids):
            missing = {str(x) for x in label_ids} - {str(lbl.id) for lbl in labels}
            raise Exception(
                f"Some labels not found or are not allowed to be assigned: {sorted(missing)}"
            )
        # Saved before the heads are locked: a default version's save() also updates the other versions (level 2).
        template_version.updated_at = timezone.now()
        template_version.save(update_fields=["updated_at"])
        try:
            with acting_as(actor or user):
                assign_labels(template_version, labels)
        except DeletedTarget:
            deny()


# ---- version commits --------------------------------------------------------------------------------------------


def commit_version(version: PromptVersion, *, publish: bool = True) -> bool:
    """Commit a draft: ``is_draft`` becomes false and one ``prompt.version.created`` is published (unless
    ``publish=False``). False, and nothing published, when it was already committed."""
    with transaction.atomic():
        row = locked_version_flags(version.pk, "is_draft")
        if row is None or not row["is_draft"]:
            return False
        version.is_draft = False
        if publishing(publish):
            version.save(update_fields=["is_draft", "updated_at"])
        else:
            with events_suppressed("commit_version(publish=False)"):
                version.save(update_fields=["is_draft", "updated_at"])
        return True


def commit_versions(versions: Iterable[PromptVersion], *, publish: bool = True) -> int:
    """Commit several drafts in one transaction: their rows are locked by id first (level 2), then each one is
    committed in the order given, through :func:`commit_version`. Returns how many were drafts."""
    versions = list(versions)
    with transaction.atomic():
        lock_versions([v.pk for v in versions])
        return sum(1 for v in versions if commit_version(v, publish=publish))


@contextmanager
def version_saving(version: PromptVersion, update_fields) -> Iterator[None]:
    """Wraps ``PromptVersion.save()``. A committed insert, or a save that moves ``is_draft`` from true to false,
    publishes one ``prompt.version.created``; a save that soft-deletes a version holding labels releases them. The
    old values are read under a row lock, so two concurrent commits of one draft publish once."""
    fields = None if update_fields is None else set(update_fields)
    commits = not version.is_draft and (fields is None or "is_draft" in fields)
    deletes = bool(version.deleted) and (fields is None or "deleted" in fields)
    skip = not (commits or deletes) or loaded_settled(version, deletes)
    if skip or version.original_template_id is None:
        yield
        return
    with transaction.atomic():
        before = None
        if not version._state.adding:
            before = locked_version_flags(version.pk, "is_draft", "deleted")
        yield
        if commits and not version.deleted and (before is None or before["is_draft"]):
            if publishing(True):
                prompt_events.publish_version_created(
                    version,
                    actor=current_actor(),
                    created_at=version.created_at if before is None else timezone.now(),
                )
        if deletes and before is not None and not before["deleted"]:
            release_deleted_versions(version.original_template, [version.id])
