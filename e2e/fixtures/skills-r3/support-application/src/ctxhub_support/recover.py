"""Batch thread recovery for the packaged application: one reviewed plan, one verified apply.

    python -m ctxhub_support.recover plan  --registry FILE --request TEXT
        [--order KEY=ORDER ...] [--recheck KEY=ORDER ... --recheck-request TEXT] --output PLAN
    python -m ctxhub_support.recover apply --registry FILE --plan PLAN
        --reviewed-plan-sha256 DIGEST --phase threads|recheck --output RESULT

The registry file is the application's own thread registry: for every durable
thread, its state database, binding and the NAMES of the environment variables
that hold its credentials. It never contains a secret.

``plan`` inspects every registered thread (the live access check only; it never
resolves a label, opens a session, calls a model or repeats a tool) and writes
what each one needs: resume under its original identity, close and reconstruct
as an explicitly clean thread, reconstruct when the mapping is lost, or close and
recheck a superseded incident thread. It executes nothing. The operator reviews
that plan once.

``apply`` executes one phase of exactly that reviewed plan. It refuses a plan
whose digest is not the reviewed digest, a registry that changed, and any thread
whose inspected state differs from the plan (a stale plan is never applied).
Reconstruction never copies messages, session ids, locks or business receipts and
never replays a completed business action; the result carries the application's
durable receipts next to the independent business ledger so they can be reconciled.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from contextlib import ExitStack
from pathlib import Path

import httpx
from fi.platform import Client, ContextAuth

from .entrypoint import (
    execute,
    fixture_url,
    refuse_broad_credentials,
    reset,
    transport_settings,
)
from .lifecycle import close_thread, inspect
from .store import Store, canonical, digest

KEY = re.compile(r"[a-z][a-z0-9_-]{0,31}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]{1,63}")
REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PHASES = ("threads", "recheck")
EVIDENCE = "synthetic fixture"
# Reasons after which a thread needs nothing: it already finished or was closed on purpose.
SETTLED = {"completed", "thread_closed"}


def _secret(name):
    if (
        not isinstance(name, str)
        or not ENV_NAME.fullmatch(name)
        or not os.environ.get(name)
    ):
        raise ValueError("credential_unavailable")
    return os.environ[name]


def _client(name):
    return Client(
        base_url=fixture_url(os.environ["CTXHUB_REGISTRY_URL"]).rstrip("/"),
        auth=ContextAuth(_secret(name)),
        max_retries=0,
    )


def load_registry(path):
    registry = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = registry.get("threads") if isinstance(registry, dict) else None
    if registry["schema"] != 1 or not isinstance(rows, list) or not rows:
        raise ValueError("invalid_recovery_registry")
    seen = set()
    for row in rows:
        allowed = {
            "key",
            "thread",
            "state",
            "binding_id",
            "report_env",
            "consume_env",
            "replacement_env",
        }
        if (
            set(row) - allowed
            or not {"key", "thread", "state", "binding_id", "report_env"} <= set(row)
            or not KEY.fullmatch(row["key"])
            or row["key"] in seen
            or not UUID.fullmatch(row["binding_id"])
            or not isinstance(row["thread"], str)
            or not row["thread"]
            or not isinstance(row["state"], str)
            or any(
                name in row and not ENV_NAME.fullmatch(row[name])
                for name in ("report_env", "consume_env", "replacement_env")
            )
        ):
            raise ValueError("invalid_recovery_registry")
        seen.add(row["key"])
    return registry


def _facts(inspected):
    """The part of an inspection a plan depends on: any difference makes the plan stale."""
    return {
        name: inspected.get(name)
        for name in (
            "thread",
            "known",
            "phase",
            "checkpoint",
            "session_id",
            "version_id",
            "reason",
            "resumable",
            "durable_receipts",
            "pending_tool_calls",
            "case_digest",
        )
    }


def _inspect(row, role, stack):
    """Read-only facts for one registered thread: (store, inspection, durable state)."""
    store = Store(row["state"])
    stack.callback(store.close)
    state = store.load(row["thread"])
    if role == "recover":
        client = _client(row.get("consume_env"))
        stack.callback(client.close)
        return inspect(store, row["thread"], client, row["binding_id"]), state
    # A superseded incident thread is never probed for authority; its durable state is the fact.
    if state is None:
        return {
            "thread": row["thread"],
            "known": False,
            "resumable": False,
            "reason": "thread_state_missing",
        }, None
    return {
        "thread": row["thread"],
        "known": True,
        "phase": state["phase"],
        "checkpoint": state["checkpoint"],
        "session_id": (state.get("session") or {}).get("session_id"),
        "version_id": (state.get("session") or {}).get("version_id"),
        "resumable": False,
        "reason": "thread_closed"
        if state["phase"] == "closed"
        else "incident_superseded",
        "durable_receipts": [],
        "pending_tool_calls": [],
        "case_digest": digest(state["case"]),
    }, state


def observe(row, *, orders, rechecks, request, recheck_request):
    """Inspect one registered thread and decide what it needs; read-only."""
    with ExitStack() as stack:
        role = "recheck" if row["key"] in rechecks else "recover"
        inspected, state = _inspect(row, role, stack)
        reason = inspected["reason"]
        entry = {
            "key": row["key"],
            "thread": row["thread"],
            "role": role,
            "known": inspected.get("known", False),
            "reason": reason,
            "checkpoint": inspected.get("checkpoint"),
            "session_id": inspected.get("session_id"),
            "version_id": inspected.get("version_id"),
            "pending_tool_calls": inspected.get("pending_tool_calls", []),
            "durable_receipts": inspected.get("durable_receipts", []),
            "case_digest": inspected.get("case_digest"),
        }
        closable = (
            state is not None
            and bool(state.get("session"))
            and state["phase"] != "closed"
        )
        if role == "recheck":
            if state is None or row["key"] not in rechecks or not recheck_request:
                raise ValueError("recheck_requires_thread_order_and_request")
            entry.update(
                phase="recheck",
                action="close_and_recheck" if closable else "recheck",
                order_id=rechecks[row["key"]],
                case_id=f"recheck-{row['key']}",
                request=recheck_request,
                close_reason="incident_superseded",
            )
        else:
            entry["phase"] = "threads"
            if inspected.get("resumable") is True:
                entry.update(action="resume", order_id=state["case"]["order_id"])
            elif reason in SETTLED:
                entry.update(
                    action="none", order_id=state["case"]["order_id"] if state else None
                )
            else:
                order = state["case"]["order_id"] if state else orders.get(row["key"])
                if not order or not request:
                    raise ValueError("reconstruction_requires_order_and_request")
                entry.update(
                    action="close_and_reconstruct" if closable else "reconstruct",
                    order_id=order,
                    case_id=f"reconstructed-{row['key']}",
                    request=request,
                    close_reason=reason,
                )
        entry["precondition_sha256"] = digest(_facts(inspected))
        return entry


def plan_digest(plan):
    return digest(
        {name: plan[name] for name in ("schema", "registry_sha256", "entries")}
    )


def build_plan(registry, *, orders, rechecks, request, recheck_request):
    entries = [
        observe(
            row,
            orders=orders,
            rechecks=rechecks,
            request=request,
            recheck_request=recheck_request,
        )
        for row in registry["threads"]
    ]
    plan = {
        "schema": 1,
        "evidence_class": EVIDENCE,
        "registry_sha256": digest(registry),
        "entries": entries,
    }
    plan["plan_sha256"] = plan_digest(plan)
    return plan


def _business_ledger():
    with httpx.Client(
        base_url=fixture_url(os.environ["CTXHUB_BUSINESS_URL"]),
        trust_env=False,
        follow_redirects=False,
        timeout=15,
    ) as client:
        response = client.get("ledger")
        response.raise_for_status()
        return response.json()


def reconcile(entries):
    """The application's durable receipts beside the business backend's own ledger, per order."""
    ledger = _business_ledger()
    orders = {}
    for entry in entries:
        if entry.get("order_id"):
            orders.setdefault(entry["order_id"], 0)
            orders[entry["order_id"]] += len(entry["durable_receipts"])
    return {
        "request_count": ledger["request_count"],
        "operation_count": len(ledger["operations"]),
        "orders": [
            {
                "order_id": order,
                "durable_receipts_before": receipts,
                "operations": sum(
                    1 for op in ledger["operations"] if op["order_id"] == order
                ),
            }
            for order, receipts in sorted(orders.items())
        ],
    }


def _run(request, state, row, consume_env, report_env):
    return execute(
        request,
        state,
        registry_url=os.environ["CTXHUB_REGISTRY_URL"],
        consume=_secret(consume_env),
        report=_secret(report_env),
        binding_id=row["binding_id"],
        runtime_digest=os.environ["CTXHUB_RUNTIME_DIGEST"],
        provider_url=os.environ["CTXHUB_PROVIDER_URL"],
        business_url=os.environ["CTXHUB_BUSINESS_URL"],
        **transport_settings(),
    )


def apply_plan(registry, plan, *, phase, reviewed):
    if phase not in PHASES:
        raise ValueError("invalid_phase")
    if not (plan.get("plan_sha256") == plan_digest(plan) == reviewed):
        raise ValueError("plan_not_reviewed")
    if plan["registry_sha256"] != digest(registry):
        raise ValueError("registry_changed")
    rows = {row["key"]: row for row in registry["threads"]}
    chosen = [entry for entry in plan["entries"] if entry["phase"] == phase]
    # Every precondition is checked before the first effect: a stale plan applies nothing.
    for entry in chosen:
        current = observe(
            rows[entry["key"]],
            orders={entry["key"]: entry.get("order_id")},
            rechecks={entry["key"]: entry["order_id"]}
            if entry["phase"] == "recheck"
            else {},
            request=entry.get("request"),
            recheck_request=entry.get("request"),
        )
        if (
            current["precondition_sha256"] != entry["precondition_sha256"]
            or current["action"] != entry["action"]
        ):
            raise ValueError("plan_stale")
    done = []
    for entry in chosen:
        row = rows[entry["key"]]
        started = time.perf_counter()
        outcome = {
            "key": entry["key"],
            "action": entry["action"],
            "thread": entry["thread"],
        }
        if entry["action"] == "none":
            done.append({**outcome, "status": "unchanged", "reason": entry["reason"]})
            continue
        if entry["action"] == "resume":
            with ExitStack() as stack:
                store = Store(row["state"])
                stack.callback(store.close)
                case = store.load(entry["thread"])["case"]
            result = _run(
                {"schema": 1, "thread": entry["thread"], "case": case},
                row["state"],
                row,
                row["consume_env"],
                row["report_env"],
            )
            outcome.update(
                status=result["status"],
                session_id=result.get("session_id"),
                version_id=result.get("version_id"),
                checkpoint=result["checkpoint"],
                output=result,
            )
        else:
            if entry["action"] in {"close_and_reconstruct", "close_and_recheck"}:
                reason = entry["close_reason"]
                if not REASON.fullmatch(reason):
                    raise ValueError("invalid_reason_code")
                with ExitStack() as stack:
                    store = Store(row["state"])
                    stack.callback(store.close)
                    client = _client(row["report_env"])
                    stack.callback(client.close)
                    outcome["closed"] = close_thread(
                        store, entry["thread"], client, row["binding_id"], reason
                    )
            clean = reset(
                {
                    "schema": 1,
                    "case": {
                        "case_id": entry["case_id"],
                        "order_id": entry["order_id"],
                        "request": entry["request"],
                    },
                    "reconstruction_reviewed": True,
                }
            )
            state = Path(row["state"]).with_name(f"clean-{entry['key']}.sqlite")
            result = _run(
                clean,
                state,
                row,
                row.get("replacement_env", row.get("consume_env")),
                row["report_env"],
            )
            outcome.update(
                status=result["status"],
                clean_thread=clean["thread"],
                session_id=result.get("session_id"),
                version_id=result.get("version_id"),
                checkpoint=result["checkpoint"],
                output=result,
            )
        # Each entry's own cost, so reconstruction is measured separately from a resume.
        outcome["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        done.append(outcome)
    return {
        "schema": 1,
        "evidence_class": EVIDENCE,
        "plan_sha256": plan["plan_sha256"],
        "phase": phase,
        "threads": done,
        "reconciliation": reconcile(chosen),
    }


def _pairs(values):
    pairs = {}
    for value in values or []:
        key, _, item = value.partition("=")
        if not KEY.fullmatch(key) or not item.strip() or key in pairs:
            raise ValueError("invalid_key_value")
        pairs[key] = item
    return pairs


def _write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plan and apply a reviewed thread recovery"
    )
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--request")
    parser.add_argument("--order", action="append")
    parser.add_argument("--recheck", action="append")
    parser.add_argument("--recheck-request")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--reviewed-plan-sha256")
    parser.add_argument("--phase", choices=PHASES)
    args = parser.parse_args(argv)
    refuse_broad_credentials()
    registry = load_registry(args.registry)
    if args.command == "plan":
        plan = build_plan(
            registry,
            orders=_pairs(args.order),
            rechecks=_pairs(args.recheck),
            request=args.request,
            recheck_request=args.recheck_request,
        )
        _write_new(args.output, plan)
        print(
            canonical(
                {
                    "status": "planned",
                    "executed": False,
                    "plan_sha256": plan["plan_sha256"],
                    # What the operator reviews: each thread, what it needs and why. No secret, message or tool result.
                    "threads": [
                        {
                            name: entry[name]
                            for name in (
                                "key",
                                "action",
                                "reason",
                                "phase",
                                "known",
                                "checkpoint",
                                "session_id",
                                "order_id",
                            )
                        }
                        | {
                            "pending_tool_calls": entry["pending_tool_calls"],
                            "durable_receipt_calls": [
                                row["call_id"] for row in entry["durable_receipts"]
                            ],
                        }
                        for entry in plan["entries"]
                    ],
                    "evidence_class": EVIDENCE,
                }
            )
        )
        return 0
    if not (args.plan and args.reviewed_plan_sha256 and args.phase):
        parser.error("apply needs --plan, --reviewed-plan-sha256 and --phase")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    result = apply_plan(
        registry, plan, phase=args.phase, reviewed=args.reviewed_plan_sha256
    )
    _write_new(args.output, result)
    print(
        canonical(
            {
                "status": "applied",
                "phase": args.phase,
                "plan_sha256": result["plan_sha256"],
                "threads": {row["key"]: row["status"] for row in result["threads"]},
                "reconciliation": result["reconciliation"],
                "evidence_class": EVIDENCE,
            }
        )
    )
    return (
        0
        if all(row["status"] in {"completed", "unchanged"} for row in result["threads"])
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
