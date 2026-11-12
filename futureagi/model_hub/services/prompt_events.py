"""The prompt app's webhook producer (XP-06; webhooks DESIGN.md sections 14.2 and 14.3).

Turns prompt rows into the inputs of the framework-free builders in ``webhooks.core.payloads`` and calls
``webhooks.publish()`` inside the writer's transaction. Only ``model_hub.services.prompt_label`` (the one writer)
calls this module.

- ``data`` (stored on the event, used for filters and the events list) is the thin body's ``data`` with people names
  off: no names, no content.
- ``builder_input`` holds what each endpoint's variant is built from at fan-out. It carries the prompt text (the full
  mode allowlist) only when the organization has an endpoint in full mode.
- People are ``{id, name}`` with the display name, or null when there is none: never an email, nor a name made from
  one (FA's invite flows store the part of the email before ``@`` as the name).
- ``source_key`` makes each event exactly once: one per version, and one per (prompt, label, revision).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.db import DatabaseError, transaction

from webhooks.core import payloads as core_payloads

log = logging.getLogger("model_hub.prompt_events")

PRODUCER = "model_hub.prompt_label"
_THIN_NO_NAMES = {"payload_mode": "thin", "include_people_names": False}
_PLACEHOLDER_EVENT = "evt_00000000000000000000000000"


def truncate_ms(dt: datetime) -> datetime:
    return dt.replace(microsecond=dt.microsecond // 1000 * 1000)


def iso_ms(dt: datetime) -> str:
    """RFC 3339 UTC with milliseconds, as every webhook timestamp is written."""
    dt = dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def person(user) -> dict | None:
    """``{id, name}``: the display name, or null. A name with an ``@`` in it, or equal to the part of the user's email
    before the ``@`` (what FA's invite flows store when the user never set a name), is treated as no name."""
    if user is None or getattr(user, "pk", None) is None:
        return None
    name = (getattr(user, "name", "") or "").strip() or None
    email = (getattr(user, "email", "") or "").strip().lower()
    local = email.split("@", 1)[0] if "@" in email else ""
    if name is not None and ("@" in name or (local and name.lower() == local)):
        name = None
    return {"id": str(user.id), "name": name}


def links(template) -> dict:
    """``app`` is the prompt's Workbench page on FA's UI host (``FRONTEND_BASE_URL``: ``FRONTEND_URL``, else
    ``APP_URL``, as FA's emails use). Only when neither is set, a single-host install where the API host also serves
    the UI, it is the API host (``BASE_URL``). ``api`` is the prompt's API route."""
    app = (
        getattr(settings, "FRONTEND_BASE_URL", "") or getattr(settings, "BASE_URL", "")
    ).rstrip("/")
    api = (getattr(settings, "BASE_URL", "") or app).rstrip("/")
    return {
        "app": f"{app}/dashboard/workbench/create/{template.id}",
        "api": f"{api}/model-hub/prompt-templates/{template.id}/",
    }


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, Mapping) and part.get("type", "text") == "text"
        )
    return ""


def content_of(version) -> dict:
    """The full-mode allowlist from a version's snapshot: roles and text parts, variable names, the model name."""
    snap = version.prompt_config_snapshot or {}
    if isinstance(snap, list):
        snap = snap[0] if snap else {}
    messages = [
        {"role": str(m.get("role", "")), "text": _text_of(m.get("content"))}
        for m in (snap.get("messages") or [])
        if isinstance(m, Mapping)
    ]
    variables = version.variable_names or {}
    names = list(variables) if isinstance(variables, Mapping | list) else []
    model = (snap.get("configuration") or {}).get("model")
    return {
        "messages": messages,
        "variables": [str(n) for n in names],
        "model": None if model is None else str(model),
    }


def _wants_content(organization_id) -> bool:
    from webhooks.models import Endpoint

    return Endpoint.objects.filter(
        organization_id=organization_id, deleted=False, payload_mode="full"
    ).exists()


def _label(label) -> dict:
    return {"id": str(label.id), "name": label.name, "type": label.type}


def _ref(version, with_message: bool) -> dict | None:
    if version is None:
        return None
    r = {"id": str(version.id), "version": version.template_version}
    if with_message:
        r["commit_message"] = version.commit_message or None
    return r


def _thin_data(type_: str, inp: Mapping[str, Any], org_id, ws_id) -> dict:
    full = {
        **inp,
        "event_id": _PLACEHOLDER_EVENT,
        "organization_id": org_id,
        "workspace_id": ws_id,
    }
    return core_payloads.BUILDERS[type_](full, _THIN_NO_NAMES)["data"]


# A producer bug (with ``webhooks.publish.SourceKeyConflict``): deterministic, so retrying the user's change would fail
# the same way. Never a database error.
PRODUCER_BUGS = (ValueError, TypeError, LookupError, AttributeError)


def _publish(type_, template, inp, *, occurred_at, source_key, digest):
    """``webhooks.publish()`` in a savepoint of the writer's transaction.

    - A producer bug (``PRODUCER_BUGS``: a builder error, a source-key conflict) rolls back the event only and is
      logged with ``webhooks.producer.failed`` (ops alert WA11); the user's prompt change still commits. Only the error's type is
      logged: no frame, no prompt text.
    - A database error (a timeout, a serialization failure, a lost connection) is not caught: the change fails with
      its event, as the transactional outbox requires, and the caller can retry both.
    """
    from webhooks.publish import SourceKeyConflict, publish

    org_id = str(template.organization_id)
    ws_id = None if template.workspace_id is None else str(template.workspace_id)
    failure = None
    try:
        with transaction.atomic():
            return publish(
                type=type_,
                organization_id=template.organization_id,
                workspace_id=template.workspace_id,
                data=_thin_data(type_, inp, org_id, ws_id),
                builder_input=inp,
                source_key=source_key,
                source_hash=hashlib.sha256(digest.encode()).hexdigest(),
                producer=PRODUCER,
                occurred_at=occurred_at,
                subject_kind="prompt",
                subject_id=str(template.id),
            )
    except DatabaseError:
        raise
    except (SourceKeyConflict, *PRODUCER_BUGS) as exc:
        failure = type(exc).__name__
    log.error(
        "prompt webhook event not published",
        extra={
            "metric": "webhooks.producer.failed",
            # The ops alert (webhooks.core.alerts, WA11): an error line with its id reaches the error pipeline and
            # the critical channel, as WA6's decrypt failure does. No row is left that a gauge could count.
            "alert": "WA11",
            "type": type_,
            "prompt_id": str(template.id),
            "error_type": failure,
        },
    )
    return None


def version_created_input(
    version, template, *, actor, created_at: datetime, labels, previous, content
) -> dict:
    inp = {
        "prompt": {"id": str(template.id), "name": template.name},
        "version": {
            "id": str(version.id),
            "version": version.template_version,
            "commit_message": version.commit_message or None,
            "is_default": bool(version.is_default),
            "labels": [_label(lbl) for lbl in labels],
            "created_at": iso_ms(created_at),
            "created_by": person(actor),
        },
        "previous_version": None
        if previous is None
        else {"id": str(previous.id), "version": previous.template_version},
        "links": links(template),
    }
    if content:
        inp["content"] = content_of(version)
    return inp


def publish_version_created(version, *, actor, created_at: datetime):
    """One ``prompt.version.created`` for a version that was just committed."""
    from model_hub.models.prompt_label import PromptLabel
    from model_hub.models.run_prompt import PromptVersion

    template = version.original_template
    if template is None or template.organization_id is None:
        return None
    created_at = truncate_ms(created_at)
    labels = PromptLabel._base_manager.filter(heads__version=version).order_by(
        "name", "id"
    )
    # Read without a lock: two commits of one prompt at the same moment can both name the same previous version.
    # It is informational; receivers order versions by created_at.
    previous = (
        PromptVersion._base_manager.filter(
            original_template=template, is_draft=False, deleted=False
        )
        .exclude(id=version.id)
        .order_by("-created_at", "-id")
        .first()
    )
    inp = version_created_input(
        version,
        template,
        actor=actor,
        created_at=created_at,
        labels=list(labels),
        previous=previous,
        content=_wants_content(template.organization_id),
    )
    return _publish(
        "prompt.version.created",
        template,
        inp,
        occurred_at=created_at,
        source_key=f"prompt.version.created:{version.id}",
        digest=str(version.id),
    )


def label_changed_input(change, template, *, actor, changed_at: datetime, content):
    inp = {
        "prompt": {"id": str(template.id), "name": template.name},
        "label": _label(change.label),
        "revision": change.revision,
        "cause": change.cause,
        "from_version": _ref(change.from_version, False),
        "to_version": _ref(change.to_version, True),
        "changed_at": iso_ms(changed_at),
        "changed_by": person(actor),
        "links": links(template),
    }
    if content and change.to_version is not None:
        inp["content"] = content_of(change.to_version)
    return inp


def publish_label_changes(template, changes: Iterable, *, actor, changed_at: datetime):
    """One ``prompt.label.changed`` per changed (prompt, label)."""
    if template.organization_id is None:
        return []
    changes = list(changes)
    if not changes:
        return []
    changed_at = truncate_ms(changed_at)
    content = _wants_content(template.organization_id)
    out = []
    for ch in changes:
        inp = label_changed_input(
            ch, template, actor=actor, changed_at=changed_at, content=content
        )
        frm = ch.from_version.id if ch.from_version else None
        to = ch.to_version.id if ch.to_version else None
        out.append(
            _publish(
                "prompt.label.changed",
                template,
                inp,
                occurred_at=changed_at,
                source_key=f"prompt.label.changed:{template.id}:{ch.label.id}:{ch.revision}",
                digest=f"{frm}>{to}:{ch.cause}",
            )
        )
    return out
