"""B8 notice fixtures, their expected intake answers, and the delivered event (contract sections 6, 12, 14)."""

from __future__ import annotations

import copy

from . import reference as R
from .cases_b8_more import b8_more_fixtures
from .cases_b8_notices import notice_12_1, notice_12_4, variant_notice
from .common import (
    DOC_FACTS,
    WS,
    check,
    sha,
)


def default_step(expect: dict) -> str:
    """The intake step that gives an answer, where the answer alone says which (see b8/fixtures.json rules)."""
    if expect["status"] == 202:
        return "accepted"
    if expect["code"] == "protocol_unsupported":
        return "protocol"
    if expect["code"] == "identity_not_allowed":
        return "identity"
    if expect["code"] == "event_status_mismatch":
        return "semantic"
    if expect.get("field") == "":
        return "parse"
    raise ValueError(f"name the step for {expect}")


def b8_fixtures() -> tuple[dict[str, bytes], list[dict]]:
    files: dict[str, bytes] = {}
    index: list[dict] = []
    c = R.canonical

    def add(kind, name, obj_or_bytes, expect, description, step=None):
        raw = obj_or_bytes if isinstance(obj_or_bytes, bytes) else c(obj_or_bytes)
        files[f"b8/{kind}/{name}.json"] = raw
        index.append(
            {
                "file": f"b8/{kind}/{name}.json",
                "kind": kind,
                "bytes": len(raw),
                "sha256": sha(raw),
                "description": description,
                "expect": expect,
                "step": step or default_step(expect),
            }
        )

    n1, n4 = notice_12_1(), notice_12_4()
    raw1, raw4 = c(n1), c(n4)
    check(
        len(raw1) == DOC_FACTS["b8_12_1"]["bytes"]
        and sha(raw1) == DOC_FACTS["b8_12_1"]["sha256"],
        "b8 12.1",
    )
    check(
        len(raw4) == DOC_FACTS["b8_12_4"]["bytes"]
        and sha(raw4) == DOC_FACTS["b8_12_4"]["sha256"],
        "b8 12.4",
    )
    check(
        R.parse_iso_ms(n1["run"]["ended_at"]) + 12
        == int(n1["notice_id"].replace("-", "")[:12], 16),
        "12.1 notice_id time = ended_at + 12 ms",
    )
    accepted = {"status": 202}
    add(
        "valid",
        "run-completed-api-whep",
        raw1,
        accepted,
        "Contract section 12.1, byte for byte",
    )
    add(
        "valid",
        "run-cancelled-queued-cron",
        raw4,
        accepted,
        "Contract section 12.4, byte for byte",
    )
    base_start = {
        "created_at": "2026-10-22T11:00:00.000Z",
        "started_at": "2026-10-22T11:00:00.250Z",
    }
    add(
        "valid",
        "run-failed-timeout-studio-url-webhook",
        variant_notice(
            n1,
            "timeout",
            "2026-10-22T11:05:00.250Z",
            **base_start,
            status="failed",
            trigger={"kind": "studio", "cron_id": None},
            error={"type": "TimeoutError", "code": "timeout"},
            webhook="https://hooks.example.dev/fi?job=1",
            webhook_context=None,
        ),
        accepted,
        "status failed (timeout), trigger studio, webhook in URL form with a query",
    )
    add(
        "valid",
        "run-failed-tool-error-batch",
        variant_notice(
            n1,
            "tool",
            "2026-10-22T11:01:10.000Z",
            **base_start,
            status="failed",
            trigger={"kind": "batch", "cron_id": None},
            error={
                "type": "HTTPError",
                "code": "tool_error",
                "http_status": 502,
                "host": "api.vendor.example",
            },
            webhook=None,
            webhook_context={"batch": "b-7", "row": 12, "dry_run": False, "note": None},
        ),
        accepted,
        "status failed (tool_error with http_status and host), trigger batch, every context value type",
    )
    add(
        "valid",
        "run-interrupted-api",
        variant_notice(
            n1,
            "interrupted",
            "2026-10-22T11:02:00.000Z",
            **base_start,
            status="interrupted",
            error={"type": "ProcessExit", "code": "interrupted"},
            webhook=None,
        ),
        accepted,
        "status interrupted (crash)",
    )
    add(
        "valid",
        "run-interrupted-shutdown-attempt-2",
        variant_notice(
            n1,
            "shutdown",
            "2026-10-22T11:03:00.000Z",
            **base_start,
            status="interrupted",
            attempt=2,
            error={"type": "Shutdown", "code": "shutdown"},
        ),
        accepted,
        "status interrupted (shutdown), attempt 2",
    )
    preview = variant_notice(
        n1,
        "preview",
        "2026-10-22T11:04:00.000Z",
        **base_start,
        status="failed",
        environment="preview",
        error={"type": "ModelError", "code": "model_error"},
    )
    add(
        "valid",
        "run-failed-model-error-preview",
        preview,
        accepted,
        "environment preview, model_error",
    )
    extra = copy.deepcopy(n1)
    extra["sdk"] = {"name": "omega-server", "version": "0.9.1"}
    extra["run"]["priority"] = "high"
    extra["run"]["trigger"]["schedule"] = "@hourly"
    extra["usage"]["cached_input_tokens"] = 0
    add(
        "valid",
        "run-completed-unknown-fields",
        extra,
        accepted,
        "unknown fields at the top level, in run, trigger and usage: accepted and ignored (contract section 10)",
    )
    newkind = variant_notice(
        n1,
        "newvals",
        "2026-10-22T11:06:00.000Z",
        **base_start,
        environment="staging",
        trigger={"kind": "webhook_in", "cron_id": None},
    )
    add(
        "valid",
        "run-completed-new-enum-values",
        newkind,
        accepted,
        "an environment and a trigger kind v1 does not list: additive values pass the patterns",
    )
    ctx1024 = copy.deepcopy(n1)
    ctx1024["run"]["webhook_context"] = {
        "a": "x" * 256,
        "b": "x" * 256,
        "c": "x" * 256,
        "d": "x" * 227,
    }
    check(
        len(R.canonical(ctx1024["run"]["webhook_context"])) == 1024, "1024-byte context"
    )
    add(
        "valid",
        "run-completed-context-1024-bytes",
        ctx1024,
        accepted,
        "webhook_context of exactly 1,024 bytes",
    )

    def bad(field):
        return {"status": 400, "code": "invalid_field", "field": field}

    t = copy.deepcopy(n1)
    t["deployment"] = {"id": "dep-3f9a0c1d2e4b5a69"}
    add(
        "invalid",
        "identity-top-level",
        t,
        {"status": 400, "code": "identity_not_allowed", "field": "deployment"},
        "an identity field at the top level",
    )
    t = copy.deepcopy(n1)
    t["run"]["workspace_id"] = WS
    add(
        "invalid",
        "identity-in-run",
        t,
        {"status": 400, "code": "identity_not_allowed", "field": "run.workspace_id"},
        "an identity field inside run",
    )
    t = copy.deepcopy(n1)
    t["run"]["status"] = "failed"
    t["run"]["error"] = {"type": "TimeoutError", "code": "timeout"}
    add(
        "invalid",
        "event-status-mismatch",
        t,
        {"status": 400, "code": "event_status_mismatch", "field": "event"},
        "event run.completed with run.status failed",
    )
    t = copy.deepcopy(n1)
    t["run"]["started_at"] = "2026-10-22T10:20:08.000Z"
    add(
        "invalid",
        "started-after-ended",
        t,
        bad("run.ended_at"),
        "started_at later than ended_at",
        "semantic",
    )
    t = copy.deepcopy(n1)
    t["run"]["webhook_context"] = {
        "a": "x" * 256,
        "b": "x" * 256,
        "c": "x" * 256,
        "d": "x" * 228,
    }
    check(len(R.canonical(t["run"]["webhook_context"])) == 1025, "1025-byte context")
    add(
        "invalid",
        "webhook-context-1025-bytes",
        t,
        bad("run.webhook_context"),
        "webhook_context of 1,025 bytes",
        "semantic",
    )
    t = copy.deepcopy(n1)
    t["run"]["webhook"] = "https://user:pw@hooks.example.dev/fi"
    add(
        "invalid",
        "webhook-userinfo",
        t,
        bad("run.webhook"),
        "a webhook URL with userinfo (the schema pattern already refuses '@')",
        "schema",
    )
    t = copy.deepcopy(n1)
    t["run"]["trace_id"] = "0" * 32
    add(
        "invalid",
        "trace-id-all-zeros",
        t,
        bad("run.trace_id"),
        "trace_id of 32 zeros",
        "schema",
    )
    t = copy.deepcopy(n1)
    t["notice_id"] = "3f0e8c1a-5b2d-4c7e-9a1f-2b3c4d5e6f70"
    add(
        "invalid",
        "notice-id-not-v7",
        t,
        bad("notice_id"),
        "a UUIDv4 notice_id",
        "schema",
    )
    t = copy.deepcopy(n1)
    t["run"]["error"] = {"type": "Warning", "code": "internal"}
    add(
        "invalid",
        "error-on-succeeded",
        t,
        bad("run.error"),
        "run.error set on a succeeded run",
        "semantic",
    )
    t = copy.deepcopy(n1)
    del t["usage"]
    add(
        "invalid",
        "usage-missing",
        t,
        bad("usage"),
        "the required usage key is absent",
        "schema",
    )
    t = copy.deepcopy(n1)
    t["run"]["created_at"] = "2026-10-22T10:20:01.200Z"
    add(
        "invalid",
        "created-after-started",
        t,
        bad("run.started_at"),
        "created_at later than started_at",
        "semantic",
    )
    add(
        "invalid",
        "not-an-object",
        b'["run.completed"]',
        bad(""),
        "JSON that is not an object",
    )
    add("invalid", "not-json", b'{"protocol":', bad(""), "bytes that are not JSON")
    t = copy.deepcopy(n1)
    t["protocol"] = "omega-deploy-run-event/v2"
    add(
        "invalid",
        "protocol-v2",
        t,
        {"status": 503, "code": "protocol_unsupported"},
        "an unknown protocol major: retried, never refused (contract sections 4.2 step 5, 5.1 step 5, 10)",
    )
    t = copy.deepcopy(n1)
    t["event"] = "run.waiting"
    add(
        "invalid",
        "event-unknown",
        t,
        {"status": 503, "code": "protocol_unsupported"},
        "an event value v1 does not know: retried, never refused",
    )
    b8_more_fixtures(add, n1)
    return files, index
