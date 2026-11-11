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


def run(request, *, store, registry, provider, business):
    if request.get("schema") != 1 or set(request) - {
        "schema",
        "thread",
        "case",
        "expected_lock",
    }:
        raise ValueError("invalid_application_request")
    return Application(
        store=store, registry=registry, provider=provider, business=business
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


def main():
    parser = argparse.ArgumentParser(
        description="Synthetic support application; no qualification claim"
    )
    parser.add_argument("request", type=Path)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
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
    request = json.loads(args.request.read_text())
    with ExitStack() as stack:
        sdk = Client(
            base_url=os.environ["CTXHUB_REGISTRY_URL"],
            auth=ContextAuth(os.environ["CTXHUB_CONTEXT_CREDENTIAL"]),
            max_retries=0,
        )
        stack.callback(sdk.close)
        provider = stack.enter_context(
            httpx.Client(
                base_url=fixture_url(os.environ["CTXHUB_PROVIDER_URL"]),
                trust_env=False,
                follow_redirects=False,
                timeout=15,
            )
        )
        business = stack.enter_context(
            httpx.Client(
                base_url=fixture_url(os.environ["CTXHUB_BUSINESS_URL"]),
                trust_env=False,
                follow_redirects=False,
                timeout=15,
            )
        )
        store = Store(args.state)
        stack.callback(store.close)
        result = run(
            request,
            store=store,
            registry=Registry(
                sdk,
                os.environ["CTXHUB_BINDING_ID"],
                os.environ["CTXHUB_RUNTIME_DIGEST"],
            ),
            provider=provider,
            business=business,
        )
        result["events"] = store.events(request["thread"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # Raw transcript remains in the private state DB, not stdout or ingestion receipts.
        with args.output.open("w", encoding="utf-8") as output:
            args.output.chmod(0o600)
            output.write(canonical(result) + "\n")
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
