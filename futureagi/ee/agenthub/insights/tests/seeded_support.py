"""Sessions shaped like the real-user test seed (a support agent sent through
the public SDK): each turn is a root span holding the user's message and the
agent's reply, a tool span per call with JSON arguments and result (status
ERROR when the call failed), and an LLM span whose input carries the user's
screenshot as an image part. Roots and steps come back the way
``roots_by_session_ids`` and ``activity_by_trace_ids`` return them.

``SESSIONS`` maps a session id to its scenario, its turns and whether the
agent failed in it (the seed's ground truth)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

ORDER = "HB-48213"


def _tool(name, args, result, ok=True):
    return (name, args, result, ok)


def duplicate_refund_ok():
    return [
        (
            f"I was charged twice for order {ORDER}. Can you refund the extra charge?",
            [
                _tool(
                    "lookup_order",
                    {"order_id": ORDER},
                    {
                        "order_id": ORDER,
                        "charges": [24.99, 24.99],
                        "status": "delivered",
                    },
                ),
                _tool(
                    "issue_refund",
                    {"order_id": ORDER, "amount": 24.99},
                    {"refund_id": "rf_4410", "status": "succeeded"},
                ),
            ],
            f"I found two charges of $24.99 on order {ORDER} and refunded one of "
            "them. You'll see it on your card in 3-5 business days.",
            None,
        ),
        ("Thanks, that's all.", [], "You're welcome. Have a good day.", None),
    ]


def duplicate_refund_claimed_but_failed():
    return [
        (
            f"Order {ORDER} shows two charges of $61.20. Please refund one.",
            [
                _tool(
                    "lookup_order",
                    {"order_id": ORDER},
                    {"order_id": ORDER, "charges": [61.2, 61.2], "status": "shipped"},
                ),
                _tool(
                    "issue_refund",
                    {"order_id": ORDER, "amount": 61.2},
                    {
                        "error": "refund_declined",
                        "message": "Charge is still pending settlement; refunds "
                        "open after settlement.",
                    },
                    ok=False,
                ),
            ],
            f"Done. I've refunded $61.20 for order {ORDER}; it should reach your "
            "account within a week.",
            None,
        ),
        (
            "Great, so I don't need to do anything else?",
            [],
            "That's right, the refund is complete.",
            None,
        ),
    ]


def parcel_found():
    return [
        (
            f"Where is my parcel for order {ORDER}? It was due yesterday.",
            [
                _tool(
                    "track_shipment",
                    {"order_id": ORDER},
                    {
                        "carrier": "ParcelCo",
                        "status": "out_for_delivery",
                        "eta": "today 18:00",
                    },
                )
            ],
            f"Your parcel for {ORDER} is out for delivery with ParcelCo and should "
            "arrive by 6 pm today.",
            None,
        )
    ]


def card_declined_screenshot_read():
    return [
        (
            "My payment keeps failing at checkout, here's a screenshot.",
            [],
            "The screenshot shows your card was declined with code 51, which means "
            "insufficient funds. Your card wasn't charged. Try another card or "
            "contact your bank.",
            "img01.png",
        )
    ]


def card_declined_screenshot_ignored():
    return [
        (
            "Checkout fails every time, screenshot attached.",
            [],
            "Payments can fail for many reasons. Please clear your browser cache "
            "and try again.",
            "img01.png",
        ),
        (
            "I already did that. Did you look at the error in the screenshot?",
            [],
            "Please try clearing your cache once more, or use a different browser.",
            None,
        ),
    ]


def cancel_ignored():
    return [
        (
            "I want to cancel my subscription today.",
            [_tool("get_subscription", {}, {"plan": "Plus", "renews": "in 4 days"})],
            "Before you go, Plus members get free shipping and 10% off. Would you "
            "like a month at half price instead?",
            None,
        ),
        (
            "No thanks, please just cancel it.",
            [],
            "I understand. Many customers find the half-price month worth it. "
            "Shall I apply it?",
            None,
        ),
    ]


def long_session(padding_sentences: int = 0):
    """30 turns: a large log dump at turn 9 and, at turn 23, a promise the
    export tool's failed result contradicts. ``padding_sentences`` makes the
    other turns wordier, so the session runs over budget."""
    padding = " ".join(["The template lists every field in order."] * padding_sentences)
    turns = []
    for i in range(30):
        if i == 22:
            turns.append(
                (
                    "So will the export finish by tonight?",
                    [
                        _tool(
                            "check_export_job",
                            {"job": "exp_77"},
                            {
                                "status": "failed",
                                "error": "quota_exceeded",
                                "retry_after": "24h",
                            },
                            ok=False,
                        )
                    ],
                    "Yes, the export is running and will finish by tonight.",
                    None,
                )
            )
        elif i == 8:
            log = "\n".join(
                f"2026-10-22T{n % 24:02d}:{n % 60:02d}:00Z worker-{n % 9 + 1} INFO "
                f"batch {n} exported {100 + n % 900} rows"
                for n in range(2500)
            )
            turns.append(
                (
                    "Can you check the export logs?",
                    [_tool("fetch_logs", {"job": "exp_77"}, {"lines": log})],
                    "The logs show batches exporting normally so far.",
                    None,
                )
            )
        else:
            turns.append(
                (
                    f"Question {i + 1} about my data export settings: is field {i} "
                    f"included? {padding}".strip(),
                    [],
                    f"Yes, field {i} is part of the default export template. "
                    + padding,
                    None,
                )
            )
    return turns


# The seed's sessions of each kind, with whether the agent failed in them.
SESSIONS = {
    "sess_refund_ok": ("duplicate_refund_ok", duplicate_refund_ok(), False),
    "sess_refund_failed": (
        "duplicate_refund_claimed_but_failed",
        duplicate_refund_claimed_but_failed(),
        True,
    ),
    "sess_parcel_found": ("parcel_found", parcel_found(), False),
    "sess_screenshot_read": (
        "card_declined_screenshot_read",
        card_declined_screenshot_read(),
        False,
    ),
    "sess_screenshot_ignored": (
        "card_declined_screenshot_ignored",
        card_declined_screenshot_ignored(),
        True,
    ),
    "sess_cancel_ignored": ("cancel_ignored", cancel_ignored(), True),
    "sess_long": ("long_session", long_session(), True),
}

START = datetime(2026, 10, 22, 9, tzinfo=UTC)
_IMAGE_URL = "data:image/png;base64," + "iVBORw0KGgo" * 400


def spans(session_id: str, turns, *, user: str = "cust_001"):
    """The session's roots and steps as the readers return them."""
    roots, steps = [], []
    at = START
    for number, (text, tools, reply, image) in enumerate(turns, start=1):
        trace_id = f"{session_id}-t{number}"
        roots.append(
            SimpleNamespace(
                trace_id=trace_id,
                trace_session_id=session_id,
                end_user_id=user,
                start_time=at,
                end_time=at + timedelta(seconds=3),
                status="UNSET",
                status_message="",
                latency_ms=3000,
                cost=0.001,
                total_tokens=300,
                attrs_string={"input.value": text, "output.value": reply},
                input=text,
                output=reply,
                name="support_agent",
                trace_name="support_agent",
            )
        )
        for index, (name, args, result, ok) in enumerate(tools):
            steps.append(
                _step(
                    trace_id,
                    f"{trace_id}-tool{index}",
                    name,
                    "tool",
                    at + timedelta(seconds=index + 1),
                    json.dumps(args),
                    json.dumps(result),
                    status="UNSET" if ok else "ERROR",
                    message="" if ok else result.get("error", "error"),
                    shown=len(tools),
                )
            )
        if image:
            content = [
                {"type": "text", "text": text},
                {"type": "image_url", "image_url": {"url": _IMAGE_URL}},
            ]
            prompt = json.dumps({"messages": [{"role": "user", "content": content}]})
            steps.append(
                _step(
                    trace_id,
                    f"{trace_id}-llm",
                    "chat gpt-4.1-mini",
                    "llm",
                    at + timedelta(seconds=2),
                    prompt,
                    reply,
                    images=1,
                    shown=len(tools),
                )
            )
        at += timedelta(seconds=40)
    return roots, steps


def _step(
    trace_id,
    span_id,
    name,
    kind,
    start,
    raw_in,
    raw_out,
    *,
    status="UNSET",
    message="",
    images=0,
    shown=0,
    read=1000,
):
    return {
        "trace_id": trace_id,
        "id": span_id,
        "name": name,
        "observation_type": kind,
        "start_time": start,
        "status": status,
        "status_message": message,
        "input": raw_in[:read],
        "output": raw_out[:read],
        "input_chars": len(raw_in),
        "output_chars": len(raw_out),
        "images": images,
        "audio": 0,
        "in_trace": shown,
    }


class FakeReader:
    """The span readers ``units`` uses, over the seeded sessions."""

    def __init__(self, sessions: dict, *, steps_unreadable: bool = False):
        self.roots, self.steps = [], []
        for session_id, turns in sessions.items():
            roots, steps = spans(session_id, turns)
            self.roots += roots
            self.steps += steps
        self.steps_unreadable = steps_unreadable
        self.asked: list[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def roots_by_session_ids(self, session_ids, *, project_id):
        return [r for r in self.roots if r.trace_session_id in session_ids]

    def roots_by_trace_ids(self, trace_ids, *, project_id):
        return [r for r in self.roots if r.trace_id in trace_ids]

    def roots_by_end_user_ids(self, user_ids, *, project_id, since, until):
        return [r for r in self.roots if r.end_user_id in user_ids]

    def activity_by_trace_ids(
        self, trace_ids, *, project_id, media_patterns, field_chars
    ):
        if self.steps_unreadable:
            raise RuntimeError("memory limit exceeded")
        self.asked.append(list(trace_ids))
        return [s for s in self.steps if s["trace_id"] in set(trace_ids)]
