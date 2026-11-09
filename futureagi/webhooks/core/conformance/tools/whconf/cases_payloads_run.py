"""payloads.json: deployment.run.* bodies built from B8 notices (B8-EVENT-CONTRACT.md sections 7.1 and 12.5)."""

from __future__ import annotations

import copy

from . import reference as R
from .cases_b8_notices import b8_build_input, notice_12_1, notice_12_4, variant_notice


def run_cases(add, run_schema: dict) -> dict:
    """Adds the deployment.run cases in their 1.0.0 order and returns the section 12.1 notice."""
    n1, n4 = notice_12_1(), notice_12_4()
    add(
        "b8-12-5",
        "deployment.run",
        b8_build_input(n1, "12.5"),
        None,
        run_schema["completed"],
        "B8-EVENT-CONTRACT.md section 12.1 -> section 12.5, byte for byte",
        expect_doc="b8_12_5",
    )
    add(
        "b8-12-4-cancelled-queued",
        "deployment.run",
        b8_build_input(
            n4,
            "12.4",
            headers={
                "X-Omega-Deployment-Name": "nightly-digest",
                "X-Omega-Deployment-Url": "https://nightly-digest.example.test",
            },
            fa={
                "event_id": "evt_" + R.ulid(1792664730010, "evt:12.4"),
                "verified_by_trace": False,
                "observe_usage": None,
                "cost": None,
            },
        ),
        None,
        run_schema["failed"],
        "section 12.4: started_at null, so duration_ms, queue_ms, trace, cost, "
        "links.trace are null",
    )
    sk = copy.deepcopy(n1)
    sk["run"]["webhook_context"] = {
        "ticket": "48213",
        "token": "sk-live-canary0123456789",
    }
    add(
        "b8-context-secret-shape",
        "deployment.run",
        b8_build_input(sk, "sk"),
        None,
        run_schema["completed"],
        "B8-T13: a secret-shaped value drops the whole context",
    )
    dash = copy.deepcopy(n1)
    add(
        "b8-no-public-url-utf8-name",
        "deployment.run",
        b8_build_input(
            dash,
            "dash",
            headers={
                "X-Omega-Deployment-Name": "support%20agent%20%E2%9C%93",
                "X-Omega-Deployment-Url": "-",
            },
        ),
        None,
        run_schema["completed"],
        "X-Omega-Deployment-Url '-' gives run_api null; the name is percent-decoded "
        "UTF-8, sent as raw UTF-8",
    )
    rt = copy.deepcopy(n1)
    add(
        "b8-runtime-usage-partial-cost",
        "deployment.run",
        b8_build_input(
            rt,
            "rt",
            fa={
                "observe_usage": None,
                "cost": {"usd_micros": 1200, "final": False},
                "verified_by_trace": False,
            },
        ),
        None,
        run_schema["completed"],
        "no root span yet: usage from the notice (source runtime), cost not final",
    )
    fl = copy.deepcopy(n1)
    fl["run"]["webhook_context"] = {
        "score": 1.0,
        "ratio": 2.5,
        "n": -3,
        "ok": True,
        "tiny": 0.00001,
        "tinier": 1.5e-7,
        "big": 1e21,
        "pow60": 2.0**60,
        "bigint": 12345678901234567890,
        "negzero": -0.0,
        "sum": 0.1 + 0.2,
    }
    add(
        "b8-context-numbers",
        "deployment.run",
        b8_build_input(fl, "fl"),
        None,
        run_schema["completed"],
        "numbers are IEEE-754 doubles written as ECMAScript Number::toString (JSON.stringify): 1.0 -> 1, "
        "0.00001 (not 1e-05), 1.5e-7, 1e+21, 2^60 -> 1152921504606847000, -0.0 -> 0",
    )
    tool = variant_notice(
        n1,
        "tool",
        "2026-10-22T11:01:10.000Z",
        created_at="2026-10-22T11:00:00.000Z",
        started_at="2026-10-22T11:00:00.250Z",
        status="failed",
        trigger={"kind": "batch", "cron_id": None},
        error={
            "type": "HTTPError",
            "code": "tool_error",
            "http_status": 502,
            "host": "api.vendor.example",
        },
        webhook=None,
        webhook_context=None,
    )
    tool["sdk"] = {"ignored": True}
    tool["run"]["priority"] = "high"
    add(
        "b8-failed-tool-error-unknown-fields",
        "deployment.run",
        b8_build_input(
            tool, "tool", fa={"event_id": "evt_" + R.ulid(1792667000000, "evt:tool")}
        ),
        None,
        run_schema["failed"],
        "a failed run; unknown notice fields are never copied",
    )
    return n1
