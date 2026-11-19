"""Standalone host-side sample for independent SDK clients in one traced request.

Read a synthetic scenario on stdin, never credentials on argv. In no-OTel mode
run this file with a separate interpreter that has only the plain SDK wheel.
"""

import json
import sys
from contextlib import nullcontext
from importlib.util import find_spec
from uuid import UUID

from fi.context import ContextClient, SnapshotIntegrityError
from fi.context.source import ConsumptionSource
from fi.platform import Client, ContextAuth
from fi.skills.client import SkillsClient
from fi.telemetry import agent_scope


class DamagedChunk(ConsumptionSource):
    """Fault only the synthetic response body; leave the manifest and checksum intact."""

    def chunk(self, *args, **kwargs):
        chunk = super().chunk(*args, **kwargs)
        return {**chunk, "text": chunk["text"] + "!"}


def main():
    cfg = json.load(sys.stdin)
    no_otel = cfg.get("noOtel", False)
    provider = None
    if no_otel:
        if find_spec("opentelemetry") is not None:
            raise RuntimeError("no-OTel flow requires an interpreter without OpenTelemetry installed")
    else:
        from fi_instrumentation.fi_types import ProjectType
        from fi_instrumentation.otel import HTTPSpanExporter, register
        from opentelemetry.trace import Status, StatusCode

        provider = register(project_name=cfg["project"], project_type=ProjectType.OBSERVE, verbose=False,
                            span_exporter=HTTPSpanExporter(endpoint="http://localhost:24318/v1/traces", headers={
                                "X-Api-Key": cfg["apiKey"], "X-Secret-Key": cfg["secretKey"],
                            }))
    tracer = provider.get_tracer("support-py") if provider else None
    trace_id = None
    try:
        with Client(base_url="http://localhost:8100", auth=ContextAuth(cfg["credential"]), max_retries=0,
                    default_headers={"User-Agent": "futureagi-python/0.7.0"} if cfg.get("oldVersion") else None) as client:
            for _ in range(cfg.get("requests", 1)):
                with tracer.start_as_current_span("Python support request") if tracer else nullcontext() as root:
                    if root:
                        trace_id = str(UUID(int=root.get_span_context().trace_id))
                        root.set_attribute("output.value", "Refund reviewed")
                    for agent in cfg["agents"]:
                        with agent_scope(agent=agent["name"]):
                            if "context" in agent:
                                ctx = agent["context"]
                                mounted = ContextClient(client).resolve(ctx["name"], **ctx["selector"])
                                mounted.read_all()
                                for path in ctx.get("reads", ()):
                                    mounted.read_file(path)
                            for skill in agent["skills"]:
                                damaged = cfg.get("damaged")
                                source = DamagedChunk(client, kind="skill") if damaged else client
                                leaf = SkillsClient(source).resolve(skill["name"], **skill["selector"])
                                if cfg.get("fetchOnly"):
                                    continue
                                try:
                                    leaf.read_file("SKILL.md")
                                except SnapshotIntegrityError:
                                    if not damaged:
                                        raise
                                else:
                                    if damaged:
                                        raise AssertionError("damaged skill was accepted")
                            if tracer:
                                tracer.start_span("prepare answer").end()
                    if root and cfg.get("failedRequest"):
                        root.set_status(Status(StatusCode.ERROR, "fixture request failed"))
    finally:
        if provider:
            provider.shutdown()
    print(json.dumps({"trace_id": trace_id}))


if __name__ == "__main__":
    main()
