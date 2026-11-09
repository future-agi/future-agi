"""``deployment.run.completed`` / ``deployment.run.failed`` from a B8 notice (B8-EVENT-CONTRACT.md section 7.1;
conformance ``payloads.json``, ``deployment.run`` build cases and ``deployment_name_header`` cases).

The intake validates the notice first; this builder only copies the named fields in the contract's order.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .ids import parse_iso_ms
from .payloads import envelope, es_key_order, has_secret_shape

RUN_EVENTS = ("run.completed", "run.failed")
_HEX2 = re.compile(rb"[0-9A-Fa-f]{2}")


class InvalidHeader(ValueError):
    """An X-Omega-* header FA cannot read: intake answers 400 ``invalid_header`` with the header name as field."""


def decode_deployment_name(value: str) -> str:
    """X-Omega-Deployment-Name: visible ASCII on the wire; each ``%XX`` is one byte, a ``%`` without two hex digits
    stays as it is, and the bytes must be strict UTF-8."""
    if any(not (" " <= c <= "~") for c in value):
        raise InvalidHeader("X-Omega-Deployment-Name")
    b = value.encode("ascii")
    raw = bytearray()
    i = 0
    while i < len(b):
        if b[i] == 0x25 and i + 2 < len(b) and _HEX2.fullmatch(b[i + 1 : i + 3]):
            raw.append(int(b[i + 1 : i + 3], 16))
            i += 3
        else:
            raw.append(b[i])
            i += 1
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise InvalidHeader("X-Omega-Deployment-Name") from None


def _usage(u: Mapping[str, Any], source: str) -> dict:
    out = {
        k: u[k]
        for k in ("input_tokens", "output_tokens", "total_tokens", "model_calls")
    }
    out["source"] = source
    return out


def build_deployment_run(
    notice: Mapping[str, Any],
    headers: Mapping[str, str],
    fa: Mapping[str, Any],
    dep_id: str,
    test: bool = False,
) -> dict:
    """``notice`` is the parsed notice (keys in received order), ``headers`` the forwarded X-Omega-* headers, ``fa``
    what FA adds: ``event_id``, ``organization_id``, ``workspace_id``, ``project_id``, ``verified_by_trace``,
    ``observe_usage``, ``cost`` and ``links``."""
    if notice["event"] not in RUN_EVENTS:
        raise ValueError(f"unknown run event {notice['event']!r}")
    run = notice["run"]
    started = run["started_at"]
    created_ms, ended_ms = (
        parse_iso_ms(run["created_at"]),
        parse_iso_ms(run["ended_at"]),
    )
    started_ms = parse_iso_ms(started) if started else None

    out = {
        k: run[k]
        for k in ("id", "thread_id", "assistant_id", "workflow_id", "status", "attempt")
    }
    out["trigger"] = {
        "kind": run["trigger"]["kind"],
        "cron_id": run["trigger"]["cron_id"],
    }
    out["created_at"] = run["created_at"]
    out["started_at"] = started
    out["ended_at"] = run["ended_at"]
    out["duration_ms"] = None if started_ms is None else ended_ms - started_ms
    out["queue_ms"] = None if started_ms is None else started_ms - created_ms
    out["supersteps"] = run["supersteps"]
    err = run["error"]
    out["error"] = (
        None
        if err is None
        else {k: err[k] for k in ("type", "code", "http_status", "host") if k in err}
    )
    ctx = run.get("webhook_context")
    if ctx is not None and has_secret_shape(ctx):
        out["context"] = None
        out["context_dropped"] = "secret_shape"
    else:
        out["context"] = None if ctx is None else es_key_order(ctx)
    out["verified_by_trace"] = fa["verified_by_trace"]

    if fa.get("observe_usage") is not None:
        usage = _usage(fa["observe_usage"], "observe")
    elif notice.get("usage") is not None:
        usage = _usage(notice["usage"], "runtime")
    else:
        usage = None
    cost = fa.get("cost")
    trace = (
        None
        if run["trace_id"] is None
        else {"id": run["trace_id"], "project_id": fa["project_id"]}
    )
    dep_url = headers["X-Omega-Deployment-Url"]
    data = {
        "deployment": {
            "id": dep_id,
            "name": decode_deployment_name(headers["X-Omega-Deployment-Name"]),
            "environment": run["environment"],
            "revision": run["revision"],
        },
        "run": out,
        "usage": usage,
        "cost": None
        if cost is None
        else {
            "usd_micros": cost["usd_micros"],
            "final": cost["final"],
            "basis": "observe_model_prices",
        },
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
        test=test,
    )
