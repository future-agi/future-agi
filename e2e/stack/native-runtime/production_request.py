"""Serve one production fixture request. Only the public SDK records skill loads."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import measure  # mounts the packaged support application
from ctxhub_support.business import Business
from ctxhub_support.entrypoint import execute, transport_settings
from fi.telemetry import agent_scope
from fi_instrumentation.fi_types import ProjectType
from fi_instrumentation.otel import HTTPSpanExporter, register
from opentelemetry.trace import Status, StatusCode

REQUEST = "My ceramic mug arrived cracked. Please refund this damaged order."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--context-id", required=True)
    args = parser.parse_args()
    order = "synthetic-prod-" + uuid4().hex[:16]
    business = Business(Path(os.environ.get("CTXHUB_BUSINESS_DB", "/fi_business/business.sqlite")))
    business.seed(order, age=10, amount=25, disputed=False)
    request = {"schema": 1, "thread": str(uuid4()), "case": {
        "case_id": "production-request", "order_id": order, "request": REQUEST,
    }}
    # The standalone collector exposes standard OTLP, not the hosted FI ingress path.
    exporter = HTTPSpanExporter(endpoint=os.environ["TRACE_URL"].rstrip("/") + "/v1/traces",
                                headers={"X-Api-Key": os.environ["TRACE_API_KEY"],
                                         "X-Secret-Key": os.environ["TRACE_SECRET_KEY"]})
    provider = register(project_name=os.environ["TRACE_PROJECT"], project_type=ProjectType.OBSERVE,
                        span_exporter=exporter, verbose=False)
    try:
        with provider.get_tracer("support-app").start_as_current_span("Production refund request") as root:
            trace_id = str(UUID(int=root.get_span_context().trace_id))
            with agent_scope(agent="support-app"):
                result = execute(request, measure.STATE / f"{order}.sqlite",
                                 registry_url=os.environ["CTXHUB_REGISTRY_URL"],
                                 consume=os.environ["CTXHUB_CONTEXT_CREDENTIAL"], binding_id=args.binding_id,
                                 context_id=args.context_id, runtime_digest="sha256:" + hashlib.sha256(
                                     json.dumps(measure.configuration(), sort_keys=True).encode()).hexdigest(),
                                 provider_url=measure.PROVIDER_URL, business_url="http://127.0.0.1:8099",
                                 **transport_settings())
                if result["status"] != "completed":
                    root.set_status(Status(StatusCode.ERROR))
        if not provider.force_flush():
            raise RuntimeError("production trace flush timed out")
    finally:
        provider.shutdown()
    print(json.dumps({"trace_id": trace_id, **{k: result[k] for k in ("version_id", "context_digest", "status")}}))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
