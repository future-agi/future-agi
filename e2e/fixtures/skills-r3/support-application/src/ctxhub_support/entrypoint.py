"""Public packaged application contract shared by J2 and J6.

`run` executes one task using the caller's exact binding/session. `reset` creates
an explicitly clean thread; it does NOT erase business receipts or copy history.
The console/module path is a VM smoke entrypoint, not proof of E2B qualification.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fi.platform import Client, ContextAuth

from .application import Application
from .registry import Registry
from .store import Store, canonical


def run(request, *, store, registry, provider, business, model="fixture-model"):
    if request.get("schema") != 1 or set(request) - {
        "schema",
        "thread",
        "case",
        "expected_lock",
    }:
        raise ValueError("invalid_application_request")
    return Application(
        store=store,
        registry=registry,
        provider=provider,
        business=business,
        model=model,
    ).run(
        thread=request["thread"],
        case=request["case"],
        expected_lock=request.get("expected_lock"),
    )


def reset(request):
    """Operator-reviewed task only; return a new request without any inherited state."""
    if (
        set(request) != {"schema", "case", "reconstruction_reviewed"}
        or request["schema"] != 1
        or request["reconstruction_reviewed"] is not True
    ):
        raise ValueError("clean_thread_requires_reconstruction_review")
    case = request["case"]
    if (
        not isinstance(case, dict)
        or set(case) != {"case_id", "order_id", "request"}
        or not all(isinstance(value, str) and value.strip() for value in case.values())
    ):
        raise ValueError("invalid_reconstructed_case")
    return {"schema": 1, "thread": str(uuid4()), "case": dict(case)}


def fixture_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "skills-fakes"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("only_owned_fixture_transports_allowed")
    return value.rstrip("/") + "/"


def execute(
    request,
    state,
    *,
    registry_url,
    consume,
    binding_id,
    runtime_digest,
    provider_url,
    business_url,
    context_id=None,
    report=None,
    provider_key=None,
    model="fixture-model",
):
    """Run one request under one binding's credentials and return the result.

    Shared by the single-thread entrypoint and the batch ``recover`` command, so
    a resumed or clean thread is the same application run either way.
    """
    with ExitStack() as stack:
        sdk = Client(base_url=registry_url, auth=ContextAuth(consume), max_retries=0)
        stack.callback(sdk.close)
        provider = stack.enter_context(
            httpx.Client(
                base_url=fixture_url(provider_url),
                trust_env=False,
                follow_redirects=False,
                timeout=15,
                # A scenario-scoped synthetic transport credential, never a broad key.
                headers=(
                    {"Authorization": "Bearer " + provider_key} if provider_key else {}
                ),
            )
        )
        business = stack.enter_context(
            httpx.Client(
                base_url=fixture_url(business_url),
                trust_env=False,
                follow_redirects=False,
                timeout=15,
            )
        )
        store = Store(state)
        stack.callback(store.close)
        result = run(
            request,
            store=store,
            registry=Registry(sdk, binding_id, runtime_digest, context_id=context_id),
            provider=provider,
            business=business,
            model=model,
        )
        # J6: optional authenticated delivery receipts (report credential only).
        if report:
            from .reporting import report_deliveries

            reporter = Client(
                base_url=registry_url, auth=ContextAuth(report), max_retries=0
            )
            stack.callback(reporter.close)
            try:
                result["reported"] = report_deliveries(
                    store, request["thread"], reporter, binding_id
                )
            except Exception as exc:  # never claim delivery reporting on failure
                result["reported"] = 0
                result["report_error"] = type(exc).__name__
        result["events"] = store.events(request["thread"])
        return result


def transport_settings():
    """The scenario-scoped provider credential and model, shared by every entrypoint."""
    return {
        "provider_key": os.environ.get("CTXHUB_PROVIDER_KEY") or None,
        "model": os.environ.get("CTXHUB_MODEL", "fixture-model"),
    }


def refuse_broad_credentials():
    # Broad keys are never passed through or used as a fallback credential mode.
    if any(
        os.environ.get(name)
        for name in (
            "FI_API_KEY",
            "FI_SECRET_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
        )
    ):
        raise ValueError("broad_credentials_must_be_absent")


def write_private(path, value):
    """Exclusive-or-overwrite private JSON output; the raw transcript stays in the state DB."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        path.chmod(0o600)
        output.write(canonical(value) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Synthetic support application; no qualification claim"
    )
    parser.add_argument("request", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    refuse_broad_credentials()
    request = json.loads(args.request.read_text())
    result = execute(
        request,
        args.state,
        registry_url=os.environ["CTXHUB_REGISTRY_URL"],
        consume=os.environ["CTXHUB_CONTEXT_CREDENTIAL"],
        binding_id=os.environ["CTXHUB_BINDING_ID"],
        runtime_digest=os.environ["CTXHUB_RUNTIME_DIGEST"],
        provider_url=os.environ["CTXHUB_PROVIDER_URL"],
        business_url=os.environ["CTXHUB_BUSINESS_URL"],
        report=os.environ.get("CTXHUB_REPORT_CREDENTIAL") or None,
        **transport_settings(),
    )
    write_private(args.output, result)
    print(
        canonical(
            {
                "status": result["status"],
                "checkpoint": result["checkpoint"],
                "evidence_class": "synthetic fixture",
            }
        )
    )
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
