"""The section 12 notices, their variants and the delivered event (B8-EVENT-CONTRACT.md sections 7.1, 12.1-12.5)."""

from __future__ import annotations

import copy

from . import reference as R
from .common import (
    CONFORMANCE_VERSION,
    DOC_FACTS,
    ORG,
    PROJECT,
    SECRET_A,
    WS,
    b64,
    body_fields,
    check,
    sha,
)


def notice_12_1() -> dict:
    return {
        "protocol": "omega-deploy-run-event/v1",
        "notice_id": "01a16320-0f9a-7a3b-9c55-2f1e8a0b6d13",
        "event": "run.completed",
        "run": {
            "id": "run_01M5HHZX3JB8F0RFCVFWQ4MV41",
            "thread_id": "th_01M5HHXJ6C3W72TF2BB9MQH1WP",
            "assistant_id": None,
            "workflow_id": "support-agent",
            "revision": 4,
            "environment": "production",
            "status": "succeeded",
            "attempt": 1,
            "trigger": {"kind": "api", "cron_id": None},
            "created_at": "2026-10-22T10:20:01.010Z",
            "started_at": "2026-10-22T10:20:01.120Z",
            "ended_at": "2026-10-22T10:20:07.950Z",
            "supersteps": 9,
            "error": None,
            "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
            "webhook": "whep_01M5FFQV9R5CY4TQKFE20S58XM",
            "webhook_context": {"ticket": "48213", "queue": "refunds"},
        },
        "usage": {
            "input_tokens": 1834,
            "output_tokens": 402,
            "total_tokens": 2236,
            "model_calls": 3,
        },
    }


def notice_12_4() -> dict:
    return {
        "protocol": "omega-deploy-run-event/v1",
        "notice_id": "01a16324-f999-70b2-bd4e-5f60718293a4",
        "event": "run.failed",
        "run": {
            "id": "run_01M5HJ90KCDSEMRESA344FFSPN",
            "thread_id": None,
            "assistant_id": None,
            "workflow_id": "nightly-digest",
            "revision": 4,
            "environment": "production",
            "status": "cancelled",
            "attempt": 1,
            "trigger": {"kind": "cron", "cron_id": "cron_nightly-digest"},
            "created_at": "2026-10-22T10:24:59.500Z",
            "started_at": None,
            "ended_at": "2026-10-22T10:25:30.004Z",
            "supersteps": 0,
            "error": {"type": "RunCancelled", "code": "cancelled"},
            "trace_id": None,
            "webhook": None,
            "webhook_context": None,
        },
        "usage": None,
    }


def variant_notice(base: dict, seed: str, ended: str, **run_changes) -> dict:
    n = copy.deepcopy(base)
    n["run"].update(run_changes)
    n["run"]["ended_at"] = ended
    ms = R.parse_iso_ms(ended) + 7
    n["notice_id"] = R.uuid7(ms, "notice:" + seed)
    n["run"]["id"] = "run_" + R.ulid(
        R.parse_iso_ms(n["run"]["created_at"]), "run:" + seed
    )
    n["event"] = "run.completed" if n["run"]["status"] == "succeeded" else "run.failed"
    return n


def delivered_fixture() -> dict:
    n1 = notice_12_1()
    body = R.canonical(R.build_deployment_run(b8_build_input(n1, "12.5")))
    f = DOC_FACTS["b8_12_5"]
    check(len(body) == f["bytes"] and sha(body) == f["sha256"], "b8 12.5 body")
    sig = R.sign(SECRET_A, "evt_01M5HJ03YMFHNNMJ9R4WB0BX73", f["t"], body)
    check(sig == f["sig_a"], "b8 12.5 signature")
    headers = R.attempt_headers(
        msg_id="evt_01M5HJ03YMFHNNMJ9R4WB0BX73",
        timestamp=f["t"],
        signature="v1," + sig,
        event_type="deployment.run.completed",
        delivery_id="dlv_01M5HJ08XP18DJRFAEBXG730MK",
        attempt=1,
        sequence=214,
        test=False,
    )
    return {
        "conformance_version": CONFORMANCE_VERSION,
        "source": "B8-EVENT-CONTRACT.md section 12.5",
        "secret": SECRET_A,
        "msg_id": "evt_01M5HJ03YMFHNNMJ9R4WB0BX73",
        "timestamp": f["t"],
        **body_fields(body),
        "webhook_signature": "v1," + sig,
        "headers": headers,
        "notes": "links hosts are placeholders and the two app paths are illustrative until B8c pins them; the bytes, "
        "sha256 and signature are exact for this text.",
    }


def b8_build_input(notice: dict, which: str, **over) -> dict:
    inp = {
        "dep_id": "dep-3f9a0c1d2e4b5a69",
        "notice_b64": b64(R.canonical(notice)),
        "headers": {
            "X-Omega-Deployment-Name": "support-agent",
            "X-Omega-Deployment-Environment": "production",
            "X-Omega-Deployment-Url": "https://support-agent.example.test",
        },
        "fa": {
            "event_id": "evt_01M5HJ03YMFHNNMJ9R4WB0BX73",
            "organization_id": ORG,
            "workspace_id": WS,
            "project_id": PROJECT,
            "verified_by_trace": True,
            "observe_usage": {
                "input_tokens": 1834,
                "output_tokens": 402,
                "total_tokens": 2236,
                "model_calls": 3,
            },
            "cost": {"usd_micros": 4127, "final": True},
            "links": {
                "deployment": "https://app.example.test/dashboard/deployments/dep-3f9a0c1d2e4b5a69",
                "trace": "https://app.example.test/dashboard/observe/8d3e5f10-2a4b-4c6d-8e9f-0a1b2c3d4e5f/trace/"
                "4bf92f3577b34da6a3ce929d0e0e4736",
            },
        },
    }
    for k, v in over.items():
        if k in ("headers", "fa"):
            inp[k].update(v)
        else:
            inp[k] = v
    return inp
