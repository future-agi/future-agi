"""Container runtime for hosted Skills-native verification (synthetic fixture class).

It receives the Hub launch, proves its measured identity/isolation through the
authenticated attempt channel, then executes the packaged
fi.context.adapters.simulation_entrypoint. Each cell runs the packaged
`ctxhub_support.entrypoint` CLI with only the arm's context-only credential.
Guests return observations; the Hub grades. This module holds no expectation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

# This process only accepts internal verification work. The separately invoked
# production_request.py is an application entrypoint, not a child of this server.
os.environ["FI_SKILLS_TRACING"] = "off"

import httpx
from app_cell import run_cell
import measure  # puts the read-only application source on sys.path
from ctxhub_support.business import Business
from ctxhub_support.business import server as business_server

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "simulation_entrypoint",
    measure.SOURCE / "src/fi/context/adapters/simulation_entrypoint.py",
)
_adapter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_adapter)
execute = _adapter.execute

TOKEN = os.environ["CTXHUB_RUNTIME_TOKEN"]
BUSINESS_DB = Path(os.environ.get("CTXHUB_BUSINESS_DB", "/fi_business/business.sqlite"))
BUSINESS_PORT = 8099
PROVIDER_KEY = os.environ.get("CTXHUB_PROVIDER_KEY", "")
ACTIVE = threading.Lock()
MAX_BODY = 20 * 1024 * 1024


def log(event: str, **fields) -> None:
    print(json.dumps({"event": event, **fields}), flush=True)


class Job:
    def __init__(self, launch: dict):
        self.launch = launch
        self.payload = launch["payload"]
        self.config = self.payload["configuration"]
        prefix = f"{launch['callback_url']}/model-hub/skills/native/attempts/{launch['attempt_id']}"
        self.prefix = prefix
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {launch['token']}",
                "X-Harness-Fence": launch["fence"],
            },
            timeout=60,
            trust_env=False,
            follow_redirects=False,
        )
        self.business = Business(BUSINESS_DB)
        self.last_order: str | None = None
        self.state_key = self.config["state_schema"].split("/", 1)[0]

    def post(self, name: str, body: dict) -> dict:
        response = self.client.post(f"{self.prefix}/{name}/", json=body)
        if response.status_code != 200:
            raise RuntimeError(f"{name}_rejected_{response.status_code}")
        return response.json()

    def order(self, task: dict, cell: dict) -> tuple[str, dict]:
        for fixture in task["fixtures"]:
            try:
                value = json.loads(fixture["text"])
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get(self.state_key), dict):
                state = value[self.state_key]
                break
        else:
            raise RuntimeError("business_state_fixture_missing")
        key = f"{self.launch['attempt_id']}/{cell['case_id']}/{cell['variant']}/{cell['repeat_index']}"
        return "synthetic-" + hashlib.sha256(key.encode()).hexdigest()[:20], state

    def launch_cell(self, *, task, cell, snapshot, configuration):
        arm = self.launch["arms"][cell["variant"]]
        order_id, state = self.order(task, cell)
        self.business.seed(
            order_id,
            age=int(state.get("age_days", 30)),
            amount=int(state.get("amount", 25)),
            disputed=bool(state.get("disputed", False)),
        )
        self.last_order = order_id
        try:
            return run_cell(
                registry_url=self.launch["registry_url"], credential=arm["credential"], binding_id=arm["binding_id"],
                runtime_digest=self.payload["configuration_digest"], order_id=order_id, case_id=task["id"],
                request_text=task["prompt"], workdir=measure.STATE / self.launch["attempt_id"],
                model=configuration["model"]["model_id"],
            )
        except RuntimeError as exc:
            log("cell_crashed", detail=str(exc)[:300])
            raise RuntimeError("application_crashed") from exc

    def business_view(self) -> dict:
        ledger = self.business.ledger()
        operations = [row for row in ledger["operations"] if row["order_id"] == self.last_order]
        return {"operations": operations, "request_count": len(operations)}

    def ingest(self, observation: dict) -> None:
        self.post("observations", {**observation, "business": self.business_view()})

    def run(self) -> None:
        try:
            self.post("health", measure.health(self.payload))
            execute(self.payload, launch_cell=self.launch_cell, ingest=self.ingest)
        except Exception as exc:  # noqa: BLE001 - reported with a bounded typed code
            code = str(exc) if str(exc).replace("_", "").isalnum() else "guest_failed"
            log("job_failed", attempt=self.launch["attempt_id"], error=type(exc).__name__, detail=str(exc)[:200])
            try:
                self.client.post(f"{self.prefix}/failure/", json={"reason": code[:64].lower()})
            except httpx.HTTPError:
                pass
        finally:
            self.client.close()
            ACTIVE.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        return hmac.compare_digest(supplied, TOKEN)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ready": True, "protocol": measure.PROTOCOL})
        if self.path.startswith("/ci/"):
            name = self.path.removeprefix("/ci/")
            report = measure.OUTPUT / "ci" / f"{name}.json"
            if not name.replace("-", "").isalnum() or not report.is_file():
                return self.reply(404, {})
            return self.reply(200, json.loads(report.read_text()))
        if self.path == "/config":
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            return self.reply(200, measure.configuration())
        self.reply(404, {})

    def do_POST(self):
        if self.path != "/launch":
            return self.reply(404, {})
        if not self.authorized():
            return self.reply(401, {"error": "unauthorized"})
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_BODY:
            return self.reply(413, {"error": "payload_size"})
        try:
            launch = json.loads(self.rfile.read(size))
            if launch.get("environment", {"FI_SKILLS_TRACING": "off"}) != {"FI_SKILLS_TRACING": "off"}:
                raise ValueError("native_test_tracing_must_be_off")
            assert launch["protocol"] == measure.PROTOCOL and launch["schema"] == 1
            for key in ("attempt_id", "token", "fence", "callback_url", "registry_url", "arms", "payload"):
                assert key in launch
        except (ValueError, KeyError, AssertionError):
            return self.reply(400, {"error": "invalid_launch"})
        if not ACTIVE.acquire(blocking=False):
            return self.reply(429, {"error": "runtime_busy"})
        try:
            job = Job(launch)
        except Exception:  # noqa: BLE001
            ACTIVE.release()
            return self.reply(400, {"error": "invalid_launch"})
        threading.Thread(target=job.run, daemon=True).start()
        self.reply(202, {"accepted": True})


def main() -> None:
    BUSINESS_DB.parent.mkdir(parents=True, exist_ok=True)
    threading.Thread(
        target=business_server(Business(BUSINESS_DB), BUSINESS_PORT).serve_forever, daemon=True
    ).start()
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()  # noqa: S104 - internal network only


if __name__ == "__main__":
    main()
