"""The deployment.run.* builder: FA's customer event from a B8 notice (B8-EVENT-CONTRACT.md section 7.1)."""

from __future__ import annotations

import base64
import json
import re

from .ref_ids import parse_iso_ms
from .ref_payloads import envelope, es_key_order, has_secret_shape


class InvalidHeader(ValueError):
    """An X-Omega-* header FA cannot read: intake answers 400 invalid_header with the header name as field."""


def _pct_decode(s: str) -> str:
    """X-Omega-Deployment-Name: visible ASCII on the wire; each %XX is one byte, a '%' without two hex digits stays as
    it is, and the bytes must be strict UTF-8. Anything else raises InvalidHeader (400 invalid_header)."""
    if any(not (" " <= ch <= "~") for ch in s):
        raise InvalidHeader("X-Omega-Deployment-Name")
    raw = bytearray()
    i = 0
    b = s.encode("ascii")
    while i < len(b):
        if (
            b[i] == 0x25
            and i + 2 < len(b)
            and re.fullmatch(rb"[0-9A-Fa-f]{2}", b[i + 1 : i + 3])
        ):
            raw.append(int(b[i + 1 : i + 3], 16))
            i += 3
        else:
            raw.append(b[i])
            i += 1
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidHeader("X-Omega-Deployment-Name") from None


def build_deployment_run(inp: dict) -> dict:
    """B8-EVENT-CONTRACT.md section 7.1. ``inp`` holds the raw notice bytes, the forwarded headers and FA's facts."""
    notice = json.loads(base64.b64decode(inp["notice_b64"]))
    run = notice["run"]
    hdr = inp["headers"]
    fa = inp["fa"]
    started = run["started_at"]
    created_ms, ended_ms = (
        parse_iso_ms(run["created_at"]),
        parse_iso_ms(run["ended_at"]),
    )
    started_ms = parse_iso_ms(started) if started else None
    ctx = run.get("webhook_context")
    run_out = {
        k: run[k]
        for k in ("id", "thread_id", "assistant_id", "workflow_id", "status", "attempt")
    }
    run_out["trigger"] = {
        "kind": run["trigger"]["kind"],
        "cron_id": run["trigger"]["cron_id"],
    }
    run_out.update(
        created_at=run["created_at"],
        started_at=started,
        ended_at=run["ended_at"],
        duration_ms=None if started_ms is None else ended_ms - started_ms,
        queue_ms=None if started_ms is None else started_ms - created_ms,
        supersteps=run["supersteps"],
    )
    err = run["error"]
    run_out["error"] = (
        None
        if err is None
        else {k: err[k] for k in ("type", "code", "http_status", "host") if k in err}
    )
    if ctx is not None and has_secret_shape(ctx):
        run_out["context"] = None
        run_out["context_dropped"] = "secret_shape"
    else:
        run_out["context"] = None if ctx is None else es_key_order(ctx)
    run_out["verified_by_trace"] = fa["verified_by_trace"]
    if fa.get("observe_usage") is not None:
        u = fa["observe_usage"]
        usage = {
            k: u[k]
            for k in ("input_tokens", "output_tokens", "total_tokens", "model_calls")
        }
        usage["source"] = "observe"
    elif notice["usage"] is not None:
        u = notice["usage"]
        usage = {
            k: u[k]
            for k in ("input_tokens", "output_tokens", "total_tokens", "model_calls")
        }
        usage["source"] = "runtime"
    else:
        usage = None
    cost = (
        None
        if fa.get("cost") is None
        else {
            "usd_micros": fa["cost"]["usd_micros"],
            "final": fa["cost"]["final"],
            "basis": "observe_model_prices",
        }
    )
    trace = (
        None
        if run["trace_id"] is None
        else {"id": run["trace_id"], "project_id": fa["project_id"]}
    )
    dep_url = hdr["X-Omega-Deployment-Url"]
    data = {
        "deployment": {
            "id": inp["dep_id"],
            "name": _pct_decode(hdr["X-Omega-Deployment-Name"]),
            "environment": run["environment"],
            "revision": run["revision"],
        },
        "run": run_out,
        "usage": usage,
        "cost": cost,
        "trace": trace,
        "links": {
            "deployment": fa["links"]["deployment"],
            "trace": None if trace is None else fa["links"]["trace"],
            "run_api": None
            if dep_url == "-"
            else f"{dep_url}/studio/v1/runs/{run['id']}",
        },
    }
    return envelope(
        event_id=fa["event_id"],
        type_="deployment." + notice["event"],
        timestamp=run["ended_at"],
        organization_id=fa["organization_id"],
        workspace_id=fa["workspace_id"],
        data=data,
        test=inp.get("test", False),
    )
