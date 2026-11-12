"""Cloud-only public CLI recovery against the real product registry.

The HTTP proxy injects an outage when the independent business server receives
its refund. No application state, receipt, checkpoint or product row is seeded
or edited. This is a synthetic GCP fixture, NOT a Simulation/E2B qualification.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from ctxhub_support.business import Business, server


@contextmanager
def serving(httpd):
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("fixture_destruction_unconfirmed")


def main():
    if sys.platform != "linux":
        raise RuntimeError("Use the assigned labelled cloud VM")
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    config = json.loads(args.input.read_text())
    origin = urlsplit(config["api_url"])
    if origin.scheme != "http" or origin.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("owned_registry_required")
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    available = threading.Event()
    available.set()
    observations = []

    class RegistryProxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def dispatch(self):
            started = time.perf_counter_ns()
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if not self.path.startswith("/model-hub/skills/contexts/"):
                self.send_error(404)
                return
            if not available.is_set():
                status, payload = 503, b'{"error":"synthetic_registry_outage"}'
            else:
                with httpx.Client(
                    trust_env=False, follow_redirects=False, timeout=30
                ) as client:
                    result = client.request(
                        self.command,
                        config["api_url"].rstrip("/") + self.path,
                        content=body,
                        headers={
                            key: value
                            for key, value in self.headers.items()
                            if key.lower()
                            in {
                                "authorization",
                                "content-type",
                                "idempotency-key",
                                "x-context-resolution",
                            }
                        },
                    )
                    status, payload = result.status_code, result.content
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            # No token, request body or returned file bytes in the relay ledger.
            observations.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "status": status,
                    "elapsed_ns": time.perf_counter_ns() - started,
                }
            )

        do_GET = dispatch
        do_POST = dispatch

    business = Business(args.output / "business.sqlite")
    business.seed(config["request"]["case"]["order_id"])
    business_http = server(business)
    original_handler = business_http.RequestHandlerClass

    class InterruptAfterBusinessRequest(original_handler):
        def do_POST(self):
            # The business server still commits its receipt and returns it. The
            # next registry boundary fails, before another model call can occur.
            available.clear()
            super().do_POST()

    business_http.RequestHandlerClass = InterruptAfterBusinessRequest
    request_path = args.output / "request.json"
    request_path.write_text(json.dumps(config["request"]))
    request_path.chmod(0o600)
    state_path = args.output / "state.sqlite"
    with (
        serving(ThreadingHTTPServer(("127.0.0.1", 0), RegistryProxy)) as registry_url,
        serving(business_http) as business_url,
    ):
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "FI_API_KEY",
                "FI_SECRET_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
            }
        }
        env.update(
            {
                "CTXHUB_REGISTRY_URL": registry_url,
                "CTXHUB_CONTEXT_CREDENTIAL": config["credential"],
                "CTXHUB_BINDING_ID": config["binding_id"],
                "CTXHUB_RUNTIME_DIGEST": config["runtime_digest"],
                "CTXHUB_PROVIDER_URL": config["provider_url"],
                "CTXHUB_BUSINESS_URL": business_url,
            }
        )

        def invoke(name):
            output = args.output / f"{name}.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fi.context.adapters.simulation_entrypoint",
                    str(request_path),
                    "--state",
                    str(state_path),
                    "--output",
                    str(output),
                ],
                env=env,
                capture_output=True,
                timeout=120,
                check=False,
            )
            if not output.exists():
                raise RuntimeError(
                    f"application_cli_failed_without_receipt_{proc.returncode}"
                )
            return proc.returncode, json.loads(output.read_text())

        code, paused = invoke("paused")
        assert code == 2 and paused["status"] == "paused", (
            "outage did not pause the application"
        )
        before = business.ledger()
        assert before["request_count"] == 1 and len(before["operations"]) == 1
        assert before["operations"][0]["status"] == "applied"
        assert before["operations"][0]["amount"] == 25
        # Independently reopen persisted SQLite, not the application's returned status.
        with sqlite3.connect(f"file:{state_path}?mode=ro", uri=True) as db:
            saved = json.loads(
                db.execute(
                    "SELECT body FROM threads WHERE id=?",
                    (config["request"]["thread"],),
                ).fetchone()[0]
            )
            receipts = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM receipts WHERE thread=?",
                    (config["request"]["thread"],),
                )
            ]
        assert (
            saved["checkpoint"] == 5 and len(receipts) == 3
        )  # three model responses + two prior tools
        assert saved["phase"] == "tools"
        assert not any(
            row.get("tool_call_id") == "refund-operation" for row in saved["messages"]
        )
        assert any(
            receipt.get("operation_id") == before["operations"][0]["operation_id"]
            for receipt in receipts
        )
        available.set()
        code, resumed = invoke("resumed")
        assert code == 0 and resumed["status"] == "completed"
        assert (
            resumed["checkpoint"] == 7
        )  # persisted refund receipt + final model response
        for key in ("session_id", "version_id", "context_digest", "lock_digest"):
            assert resumed[key] == paused[key], f"resume changed {key}"
        assert resumed["version_id"] == config["version_id"]
        after = business.ledger()
        assert after == before, "resume repeated or changed the business effect"
        code, replayed = invoke("completed-reopen")
        assert code == 0 and replayed["checkpoint"] == 7
        assert business.ledger() == before
        review_path = args.output / "reconstruction-review.json"
        reconstructed_case = {
            **config["request"]["case"],
            "request": "Explain the next verification step without issuing any business action.",
        }
        review_path.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "case": reconstructed_case,
                    "reconstruction_reviewed": True,
                }
            )
        )
        review_path.chmod(0o600)
        clean_path = args.output / "clean-request.json"
        reconstruction_started = time.perf_counter_ns()
        prepared = subprocess.run(
            [
                sys.executable,
                "-m",
                "ctxhub_support.recovery",
                str(review_path),
                "--output",
                str(clean_path),
            ],
            env=env,
            capture_output=True,
            timeout=30,
            check=False,
        )
        reconstruction_elapsed_ns = time.perf_counter_ns() - reconstruction_started
        assert prepared.returncode == 0
        clean_request = json.loads(clean_path.read_text())
        assert set(clean_request) == {"schema", "case", "thread"}
        assert clean_request["thread"] != config["request"]["thread"]
        assert clean_request["case"] == reconstructed_case
        assert business.ledger() == before, "preparation replayed a business action"
        request_path = clean_path
        code, clean = invoke("clean-thread")
        assert code == 0 and clean["status"] == "completed"
        assert clean["session_id"] != resumed["session_id"]
        assert clean["checkpoint"] == 1 and clean["selected"] == []
        assert business.ledger() == before
        with sqlite3.connect(f"file:{state_path}?mode=ro", uri=True) as db:
            clean_state = json.loads(
                db.execute(
                    "SELECT body FROM threads WHERE id=?", (clean_request["thread"],)
                ).fetchone()[0]
            )
            original_state = json.loads(
                db.execute(
                    "SELECT body FROM threads WHERE id=?",
                    (config["request"]["thread"],),
                ).fetchone()[0]
            )
        assert [message["role"] for message in clean_state["messages"]] == [
            "system",
            "user",
            "assistant",
        ]
        assert original_state["checkpoint"] == 7
        boundaries = [
            row for row in observations if row["path"].endswith("/access-check/")
        ]
        assert any(row["status"] == 503 for row in boundaries)
        assert not any("resolve" in row["path"] for row in observations)
        receipt = {
            "schema": 1,
            "evidence_class": "synthetic fixture",
            "native_qualified": False,
            "registry": "real_product_through_owned_outage_proxy",
            "status": "passed",
            "paused": paused,
            "resumed": resumed,
            "business_before": before,
            "business_after": after,
            "durable_checkpoint_before_resume": saved["checkpoint"],
            "durable_receipt_count": len(receipts),
            "live_authorization_latency_ns": [row["elapsed_ns"] for row in boundaries],
            "cold_thread_reconstruction": {
                "performed": True,
                "evidence_class": "synthetic fixture",
                "review_mode": "scripted_explicit_task_review_not_human_time_study",
                "reviewed_fields": sorted(reconstructed_case),
                "preparation_elapsed_ns": reconstruction_elapsed_ns,
                "human_active_time_ns": None,
                "copied_messages": 0,
                "additional_business_requests": business.ledger()["request_count"]
                - before["request_count"],
                "result": clean,
            },
            "registry_observations": observations,
        }
        (args.output / "recovery-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
    print(
        json.dumps(
            {"status": "passed", "business_effects": 1, "native_qualified": False}
        )
    )


if __name__ == "__main__":
    main()
