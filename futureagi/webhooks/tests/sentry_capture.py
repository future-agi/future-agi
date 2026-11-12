"""What Sentry would send, captured in-process, for the K2 tests (DESIGN.md section 5.3).

The client uses the options of FA's own ``tfc.logging.sentry.init_sentry`` that decide what an event carries: the
variables of every frame (``include_local_variables``), a stack attached to every message event (``attach_stacktrace``)
and the logging integration. Three things differ, on purpose:

- **No scrubbing** (an empty denylist and no ``before_send``): a test then proves that a secret is not in the event at
  all, not that a name-based scrubber happened to match the variable that held it;
- **``event_level``** is the caller's: WARNING models a lowered ``SENTRY_LOG_LEVEL``, so warnings become events too;
- **no source lines**: they are code, not runtime data, and would read back a test's own marker constants. The option
  covers exception frames only (the SDK reads source for an attached stack regardless), so :func:`leaked` also drops
  the source lines of every frame before it searches.

The transport keeps the events in a list; there is no DSN, so nothing leaves the process. Only explicit integrations
are set up (no Django or other auto-enabled patching), and the client is bound to an isolation scope that ends with
the ``with`` block.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import traceback
from collections.abc import Iterator

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.scrubber import EventScrubber
from sentry_sdk.transport import Transport


class _Keep(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def capture_envelope(self, envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(event)


@contextlib.contextmanager
def sentry_events(event_level: int = logging.ERROR) -> Iterator[list[dict]]:
    keep = _Keep()
    client = sentry_sdk.Client(
        transport=keep,
        default_integrations=False,
        auto_enabling_integrations=False,
        integrations=[LoggingIntegration(level=logging.INFO, event_level=event_level)],
        include_local_variables=True,
        attach_stacktrace=True,
        include_source_context=False,  # see the module docstring
        send_default_pii=True,
        event_scrubber=EventScrubber(denylist=[], send_default_pii=True),
        max_value_length=100_000,
    )
    try:
        with sentry_sdk.isolation_scope() as scope:
            scope.set_client(client)
            yield keep.events
    finally:
        client.close()


def frames(event: dict) -> list[dict]:
    """Every stack frame in the event: its exceptions' and the stack attached to a message."""
    out: list[dict] = []
    for value in (event.get("exception") or {}).get("values") or []:
        out += (value.get("stacktrace") or {}).get("frames") or []
    for thread in (event.get("threads") or {}).get("values") or []:
        out += (thread.get("stacktrace") or {}).get("frames") or []
    return out


def frame_vars(event: dict, module: str, function: str) -> list[dict]:
    """The captured variables of each frame of ``module.function`` in the event."""
    return [
        f.get("vars") or {}
        for f in frames(event)
        if (f.get("module"), f.get("function")) == (module, function)
    ]


SOURCE_KEYS = ("pre_context", "context_line", "post_context")


def leaked(events: list[dict], needles) -> list[str]:
    """The needles found anywhere in the events as sent (message, extra, breadcrumbs, frames and their variables),
    source lines aside."""
    events = copy.deepcopy(events)
    for event in events:
        for frame in frames(event):
            for key in SOURCE_KEYS:
                frame.pop(key, None)
    text = json.dumps(events, default=str)
    return [n for n in needles if n in text]


def record_text(record: logging.LogRecord) -> str:
    """Everything a log handler or a reporter could take from ``record``, source lines aside: its message, every
    attribute (extras included) and, with exc_info, each chained error's message and its frames' variables."""
    parts = [
        record.getMessage(),
        repr({k: v for k, v in vars(record).items() if k != "exc_info"}),
    ]
    exc = record.exc_info[1] if record.exc_info else None
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        parts += traceback.format_exception_only(type(exc), exc)
        tb = exc.__traceback__
        while tb is not None:
            parts.append(repr(dict(tb.tb_frame.f_locals)))
            tb = tb.tb_next
        exc = exc.__cause__ or exc.__context__
    return "\n".join(parts)
