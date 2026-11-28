"""The builder chat's turn and question events, as the producer emits them.

Transcribed from agent-learning-kit commit 7e69ae59,
``src/fi/alk/harness/hosted_chat_entrypoint.py`` (``HostedChatRuntime``). Each
sequence is a list of ``(kind, payload[, message_id])`` tuples for the driver tests'
``_feed``; ``fill`` puts real ids in the ``{command}``, ``{answer}``, ``{question}``
and ``{control}`` placeholders. ``checkpoint_committed``, ``invocation_id`` and a
rebuild's ``usage`` are left out. ``assistant_message`` is too, though the driver
reads a turn's last one as the reason it made no change: it and ``turn_completed``
carry the turn's own ``invocation_id`` (L1618-1645), while ``assistant_delta`` may
carry the backend's. The comment on each names what its batch acknowledges through;
tests pass that as ``acked``.
"""

from typing import Any

Events = list[tuple[Any, ...]]

# A turn starts (``_handle_command``, L1447-1455). Acks through: the command.
STARTED: Events = [
    ("turn_started", {"command_message_id": "{command}", "resumed": False}),
]

# A Vertex turn resumed after its sandbox restarted (L1447-1455, with
# ``resume_invocation_id`` set). Acks through: the command.
RESUMED: Events = [
    ("turn_started", {"command_message_id": "{command}", "resumed": True}),
]

# Any other backend after a restart: the turn is answered once more (L1397-1407,
# L1447-1455). Acks through: the command.
RETRIED: Events = [
    ("turn_started", {"command_message_id": "{command}", "resumed": False}),
]

# The retried turn's sandbox stopped too (L1409-1425): no turn starts. Acks through:
# the command.
CUT_OFF_AGAIN: Events = [
    (
        "turn_interrupted",
        {
            "command_message_id": "{command}",
            "reason": "previous sandbox stopped before the turn committed",
        },
    ),
    ("turn_completed", {"command_message_id": "{command}", "interrupted": True}),
]

# The person stopped the running turn (L1527-1558, ``_turn_until_interrupted``
# L1702-1705). Acks through: the control.
STOPPED: Events = [
    (
        "turn_interrupted",
        {"command_message_id": "{command}", "interrupt_message_id": "{control}"},
    ),
    ("turn_completed", {"command_message_id": "{command}", "outcome": "interrupted"}),
]

# A control with no turn running names itself (L1360-1366). Acks through: the
# control.
CONTROL_ALONE: Events = [
    ("turn_interrupted", {"command_message_id": "{control}", "kind": "interrupt"}),
]

# The running turn asks (``_ask``, L1862-1892); asked again after a restart, it keeps
# its message id. Acks through: nothing new.
ASKED: Events = [
    (
        "question_requested",
        {"prompt": "Which table holds the orders?", "options": []},
        "{question}",
    ),
]

# An answer with no suspended question (L1349-1358). Acks through: the answer.
IGNORED_ANSWER: Events = [
    (
        "turn_completed",
        {
            "command_message_id": "{answer}",
            "ignored": "no matching suspended question",
        },
    ),
]

# The turn succeeded (L1613-1623). Acks through: the command, or an answer the turn
# took. A question the turn asked and nobody answered ends with it (its stage timed
# out waiting, and the turn was answered without it).
COMPLETED: Events = [
    ("turn_completed", {"command_message_id": "{command}", "outcome": "success"}),
]


def _put(value: Any, ids: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    for name, one in ids.items():
        value = value.replace("{" + name + "}", one)
    return value


def fill(sequence: Events, **ids: Any) -> Events:
    """``sequence`` with the placeholders replaced by ``ids``."""
    text = {name: str(one) for name, one in ids.items()}
    return [
        (
            kind,
            {key: _put(value, text) for key, value in payload.items()},
            *(_put(one, text) for one in rest),
        )
        for kind, payload, *rest in sequence
    ]
