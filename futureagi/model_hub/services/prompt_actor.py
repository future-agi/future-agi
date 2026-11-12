"""Who a prompt change is made by, and whether it publishes, for the one prompt writer (``prompt_label``; webhooks
DESIGN.md sections 14.1-14.3).

- ``acting_as(user)`` names the person events carry as ``created_by`` / ``changed_by``; without it the request's user
  (bound by FA's authentication) is named. ``acting_as(NO_PERSON)`` names nobody.
- ``request_actor(request)`` is who a request acts as: its user, or nobody for a ``system`` API key.
- ``events_suppressed(reason)`` makes writes publish nothing, explicitly and with a named reason.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from tfc.middleware.workspace_context import get_current_user

_suppressed: ContextVar[str | None] = ContextVar(
    "prompt_events_suppressed", default=None
)
_actor: ContextVar[object] = ContextVar("prompt_events_actor", default=None)
NO_PERSON = object()  # ``acting_as(NO_PERSON)``: the change names no person


@contextmanager
def events_suppressed(reason: str) -> Iterator[None]:
    """Writes inside this block publish no webhook event (onboarding seeds, data migrations). Explicit, never
    implied: ``reason`` names the caller."""
    token = _suppressed.set(reason)
    try:
        yield
    finally:
        _suppressed.reset(token)


@contextmanager
def acting_as(user) -> Iterator[None]:
    """The person events name when it is not the request's user (``NO_PERSON``: nobody)."""
    token = _actor.set(user)
    try:
        yield
    finally:
        _actor.reset(token)


def request_actor(request):
    """Who a request acts as. A ``system`` API key acts as no person: FA's authentication resolves it to the
    organization's oldest user so that permissions work, but that user did not make the change."""
    key = getattr(request, "org_api_key", None)
    if key is not None and getattr(key, "type", None) == "system":
        return NO_PERSON
    return request.user


def current_actor():
    actor = _actor.get()
    if actor is NO_PERSON:
        return None
    return actor or get_current_user()


def publishing(publish: bool) -> bool:
    return publish and _suppressed.get() is None
