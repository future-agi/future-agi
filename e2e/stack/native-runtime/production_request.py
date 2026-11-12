"""One live request served by the production application and traced (synthetic fixture).

The application team's tracing wrapper exports the identity of the context the application
actually loaded, to their retained tracing project:
  production_request.py --binding-id ID --context-id ID
Environment: CTXHUB_CONTEXT_CREDENTIAL (production, context-only), TRACE_URL, TRACE_API_KEY,
TRACE_SECRET_KEY, TRACE_PROJECT.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import measure
from app_cell import run_cell
from ctxhub_support.business import Business

REQUEST = "My ceramic mug arrived cracked. Please refund this damaged order."


def attr(key, value):
    return {"key": key, "value": {"stringValue": value}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--context-id", required=True)
    args = parser.parse_args()
    config = measure.configuration()
    business = Business(Path(os.environ.get("CTXHUB_BUSINESS_DB", "/fi_business/business.sqlite")))
    order = "synthetic-prod-" + uuid4().hex[:16]
    business.seed(order, age=10, amount=25, disputed=False)
    started = time.time_ns()
    result = run_cell(registry_url=os.environ["CTXHUB_REGISTRY_URL"], credential=os.environ["CTXHUB_CONTEXT_CREDENTIAL"],
        binding_id=args.binding_id, runtime_digest="sha256:" + hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        order_id=order, case_id="production-request", request_text=REQUEST, workdir=measure.STATE / "production", model=config["model"]["model_id"])
    ended = time.time_ns()
    trace, root, child = uuid4().hex, uuid4().hex[:16], uuid4().hex[:16]
    messages = json.dumps({"messages": [{"role": "user", "content": REQUEST}, {"role": "assistant", "content": result.get("output") or ""}]})
    span = lambda sid, parent, name, attrs: {"traceId": trace, "spanId": sid, **({"parentSpanId": parent} if parent else {}), "name": name, "kind": 1,
        "startTimeUnixNano": str(started), "endTimeUnixNano": str(ended), "attributes": attrs, "status": {"code": 1}}
    identity = [attr("fi.context.id", args.context_id), attr("fi.context.version_id", result["version_id"]), attr("fi.context.digest", result["context_digest"])]
    payload = {"resourceSpans": [{"resource": {"attributes": [attr("project_name", os.environ["TRACE_PROJECT"]), attr("service.name", os.environ["TRACE_PROJECT"])]},
        "scopeSpans": [{"scope": {"name": "support-app"}, "spans": [
            span(root, None, "Production refund request", [attr("input.value", messages), attr("output.value", result.get("output") or ""), *identity]),
            span(child, root, "support.llm-call", [attr("fi.span.kind", "llm")])]}]}]}
    response = httpx.post(os.environ["TRACE_URL"] + "/v1/traces", json=payload, timeout=30, trust_env=False,
        headers={"X-Api-Key": os.environ["TRACE_API_KEY"], "X-Secret-Key": os.environ["TRACE_SECRET_KEY"]})
    response.raise_for_status()
    dashed = "-".join([trace[:8], trace[8:12], trace[12:16], trace[16:20], trace[20:]])
    print(json.dumps({"trace_id": dashed, "version_id": result["version_id"], "context_digest": result["context_digest"], "status": result["status"]}))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
