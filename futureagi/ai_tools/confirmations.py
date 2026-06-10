"""Execution-policy classification + destructive-action confirmation store.

Phase 3A (PHASES.md): destructive tools must be ENFORCED-safe, not
prompt-safe. Two pieces live here, both OSS-side (ee imports OSS, never the
reverse):

1. ``classify()`` / ``classify_name_only()`` — tag every tool
   read | mutate | destructive. Bridge tools are classified at registration
   (ai_tools/drf_bridge.py) from action/method/name; hand-written tools are
   backfilled name-only by ``registry.register``.

2. The Redis-backed pending-confirmation store. A destructive call without
   an approved record is intercepted in ``BaseTool.run`` (the gate) BEFORE
   ``execute()`` — zero side effects — and returns a preview plus a
   short-lived (15-min), exact-args-bound confirmation record. Execution
   requires a server-held approval: the Confirm button (Falcon WS) flips the
   record to ``approved``; the phase-2 call (identical args + confirm=true)
   finds the record via a deterministic lookup key (the LLM never carries
   the token), consumes it (single-use), and only then executes.

Security boundary: the lookup key embeds ``user_id`` (+ conversation), so
one user's approval can never authorize another user's call, and approvals
don't bleed across chats. TTL is fixed at creation and never refreshed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

POLICIES = ("read", "mutate", "destructive")

# Kept in sync with ai_tools/drf_bridge.py WRITE_METHODS (duplicated here so
# this module imports nothing from ai_tools — base/registry/drf_bridge all
# import from it).
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

DESTRUCTIVE_NAME_PREFIXES = (
    "delete_",
    "remove_",
    "revoke_",
    "bulk_",
    "hard_",
    "purge_",
    "reset_",
)

_MARK_DELETED_RE = re.compile(r"^mark_.+_deleted$")

# Phase 5A exemption (PHASES.md 5A — "memory writes are mutations" under the
# 3A policy): Falcon workspace-memory writes are a trust feature governed by
# attribution + the management UX, NOT the confirm gate. ``delete_memory``
# removes a single KV row the user can recreate in one message, so despite
# its destructive-shaped name it is classified mutate. Keep this list to
# memory tools only — anything else delete-shaped stays gated.
NON_DESTRUCTIVE_NAME_EXEMPTIONS = frozenset({"delete_memory"})

# Name-only fallback for hand-written tools (no HTTP method to inspect).
# Only feeds the frontend write badge for non-destructive tools; the gate
# cares solely about "destructive".
MUTATE_NAME_PREFIXES = (
    "create_",
    "update_",
    "add_",
    "submit_",
    "set_",
    "save_",
    "assign_",
    "import_",
    "upload_",
    "move_",
    "duplicate_",
    "restore_",
    "complete_",
    "skip_",
    "review_",
    "rerun_",
    "run_",
    "execute_",
    "cancel_",
    "stop_",
    "pause_",
    "unpause_",
    "activate_",
    "commit_",
    "trigger_",
    "generate_",
    "improve_",
    "apply_",
    "mark_",
)


def _name_is_destructive(name: str) -> bool:
    name = name or ""
    if name in NON_DESTRUCTIVE_NAME_EXEMPTIONS:
        return False
    return (
        name.startswith(DESTRUCTIVE_NAME_PREFIXES)
        or "_bulk_" in name
        or bool(_MARK_DELETED_RE.match(name))
    )


def classify(
    name: str,
    action: str | None = None,
    method: str | None = None,
    override: str | None = None,
) -> str:
    """Classify a bridge tool. Binding rules, first match wins:

    1. Explicit ``execution_policy`` config override.
    2. ``action == "destroy"`` or ``method == "DELETE"`` -> destructive.
    3. Write method AND destructive-shaped name -> destructive.
    4. Write method -> mutate.
    5. Else -> read.
    """
    if override:
        if override not in POLICIES:
            raise ValueError(
                f"Invalid execution_policy {override!r} for tool {name!r}; "
                f"expected one of {POLICIES}."
            )
        return override
    method_u = (method or "").upper()
    if action == "destroy" or method_u == "DELETE":
        return "destructive"
    if method_u in WRITE_METHODS and _name_is_destructive(name):
        return "destructive"
    if method_u in WRITE_METHODS:
        return "mutate"
    return "read"


def classify_name_only(name: str) -> str:
    """Backfill classifier for hand-written tools (no method metadata)."""
    if _name_is_destructive(name):
        return "destructive"
    if (name or "").startswith(MUTATE_NAME_PREFIXES):
        return "mutate"
    return "read"


# ---------------------------------------------------------------------------
# Preview ("summary") builder
# ---------------------------------------------------------------------------

# Per-tool preview builders (design §1.9). Populated at registration via the
# `confirm_preview` tool-config key in @expose_to_mcp (a callable defined in
# the bridge module). Contract (binding): signature
# ``preview(params: dict, context: ToolContext) -> str`` (markdown,
# <=1200 chars); read-only; MUST use workspace/org-scoped managers (it runs
# inside workspace_context); must resolve human names + counts for targeted
# objects; must state permanence when no undo exists. Exceptions inside a
# builder fall back to the default builder (never block the gate).
PREVIEW_BUILDERS: dict[str, Any] = {}


def register_preview_builder(tool_name: str, builder) -> None:
    if not callable(builder):
        raise ValueError(
            f"confirm_preview for tool {tool_name!r} must be callable, "
            f"got {type(builder).__name__}"
        )
    PREVIEW_BUILDERS[tool_name] = builder

# Delete tools with a cheap compensating create (the 1B ROUNDTRIPS pairs).
# Used for the nullable ``undo_note`` in the confirmation payload; tools/
# bridge configs can override via the ``undo_note`` class attr / config key.
UNDO_NOTES = {
    "delete_prompt_folder": (
        "Undo: re-create the folder with `create_prompt_folder` "
        "(folder contents are not restored)."
    ),
    "delete_persona": "Undo: re-create the persona with `create_persona`.",
    "delete_eval_group": "Undo: re-create the group with `create_eval_group`.",
    "delete_prompt_label": "Undo: re-create the label with `create_prompt_label`.",
    "delete_knowledge_base": (
        "Undo: re-create the knowledge base with `create_knowledge_base` "
        "(uploaded files are not restored)."
    ),
}


def undo_note_for(tool_name: str) -> str | None:
    return UNDO_NOTES.get(tool_name)


def build_preview(tool: Any, args: dict, undo_note: str | None = None) -> str:
    """Human preview of a destructive call: tool, intent, exact arguments.

    Rendered in the chat confirmation card AND relayed by the LLM, so it
    must name precisely what will happen — never editorialize args away.
    """
    lines = [f"Falcon wants to run: `{getattr(tool, 'name', tool)}`"]
    desc = (getattr(tool, "description", "") or "").strip().splitlines()
    if desc and desc[0]:
        lines.append(desc[0].strip())
    lines.append("")
    if args:
        lines.append("Arguments:")
        for key in sorted(args):
            try:
                rendered = json.dumps(args[key], default=str)
            except Exception:
                rendered = str(args[key])
            if len(rendered) > 300:
                rendered = rendered[:300] + "…"
            lines.append(f"- {key}: {rendered}")
    else:
        lines.append("Arguments: (none)")
    # "N item(s) targeted" — N = max length of any list-valued param, else 1.
    n_targeted = max(
        (len(v) for v in args.values() if isinstance(v, (list, tuple))),
        default=0,
    ) or 1
    lines.append(f"{n_targeted} item(s) targeted.")
    lines.append("")
    lines.append(undo_note or "This cannot be undone.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Args hashing
# ---------------------------------------------------------------------------


def compute_args_hash(args: dict) -> str:
    """Deterministic hash of the VALIDATED params dump (exact-args binding)."""
    payload = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Redis store
# ---------------------------------------------------------------------------

TTL_SECONDS = 900  # 15 minutes, fixed at creation, never refreshed
REC_PREFIX = "falcon:confirm:"
LOOKUP_PREFIX = "falcon:confirm:lookup:"

_pool = None


def _get_redis():
    """OSS-side Redis client (settings.REDIS_URL; no ee import)."""
    global _pool
    import redis

    if _pool is None:
        url = None
        try:
            from django.conf import settings

            url = getattr(settings, "REDIS_URL", None)
        except Exception:
            url = None
        url = url or os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _pool = redis.ConnectionPool.from_url(url, decode_responses=True)
    return redis.Redis(connection_pool=_pool)


def _lookup_key(context: Any, tool_name: str, args_hash: str) -> str:
    conv = getattr(context, "conversation_id", None) or "noconv"
    return f"{LOOKUP_PREFIX}{context.user_id}:{conv}:{tool_name}:{args_hash}"


def _write_rec(r, rec: dict, ttl: int) -> None:
    r.setex(f"{REC_PREFIX}{rec['token']}", ttl, json.dumps(rec, default=str))


def create_pending(
    context: Any,
    tool_name: str,
    args_hash: str,
    args: dict,
    preview: str,
    undo_note: str | None = None,
) -> tuple[str, str]:
    """Write a pending confirmation record. Returns (token, expires_at ISO)."""
    token = uuid.uuid4().hex
    now = datetime.now(UTC)
    expires_at = (now + timedelta(seconds=TTL_SECONDS)).isoformat()
    rec = {
        "token": token,
        "tool_name": tool_name,
        "args_hash": args_hash,
        "args": args,
        "preview": preview,
        "undo_note": undo_note,
        "user_id": str(context.user_id),
        "workspace_id": str(getattr(context, "workspace_id", "") or ""),
        "conversation_id": getattr(context, "conversation_id", None),
        "transport": getattr(context, "transport", "falcon") or "falcon",
        "status": "pending",
        "message_id": None,
        "call_id": None,
        "created_at": now.isoformat(),
        "expires_at": expires_at,
    }
    r = _get_redis()
    _write_rec(r, rec, TTL_SECONDS)
    r.setex(_lookup_key(context, tool_name, args_hash), TTL_SECONDS, token)
    return token, expires_at


def get(token: str) -> dict | None:
    if not token:
        return None
    raw = _get_redis().get(f"{REC_PREFIX}{token}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def lookup(context: Any, tool_name: str, args_hash: str) -> dict | None:
    """Find the record for (user, conversation, tool, exact args) — this is
    how the phase-2 call resolves its approval without the LLM ever carrying
    the token."""
    token = _get_redis().get(_lookup_key(context, tool_name, args_hash))
    if not token:
        return None
    rec = get(token)
    if rec is None:
        return None
    # Defense in depth — the key already binds these.
    if rec.get("tool_name") != tool_name or rec.get("args_hash") != args_hash:
        return None
    return rec


def _update_rec(token: str, mutate) -> dict | None:
    """Load-mutate-store preserving the remaining TTL (never refreshed)."""
    r = _get_redis()
    key = f"{REC_PREFIX}{token}"
    raw = r.get(key)
    if not raw:
        return None
    try:
        rec = json.loads(raw)
    except (TypeError, ValueError):
        return None
    mutate(rec)
    ttl = r.ttl(key)
    if not isinstance(ttl, int) or ttl <= 0:
        # Key expired between get and ttl — treat as gone.
        return None
    _write_rec(r, rec, ttl)
    return rec


def set_status(token: str, status: str) -> dict | None:
    if status not in ("pending", "approved", "cancelled", "consumed"):
        raise ValueError(f"Invalid confirmation status {status!r}")

    def _mutate(rec):
        rec["status"] = status

    return _update_rec(token, _mutate)


def consume(rec: dict) -> dict | None:
    """Mark a record consumed (single-use). Called by the gate right before
    it lets ``execute()`` run."""
    return set_status(rec["token"], "consumed")


def attach_ui_ref(
    token: str, message_id: str | None, call_id: str | None
) -> dict | None:
    """Bind the record to the chat message / tool-call card that displays it
    (set by the agent loop after the tool_call_result event is persisted)."""

    def _mutate(rec):
        rec["message_id"] = str(message_id) if message_id else None
        rec["call_id"] = str(call_id) if call_id else None

    return _update_rec(token, _mutate)
