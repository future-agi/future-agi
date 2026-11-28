"""Run the packaged support application once, as its own CLI, with one context-only credential."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import measure


def run_cell(*, registry_url, credential, binding_id, runtime_digest, order_id, case_id, request_text,
             workdir: Path, model: str, business_url="http://127.0.0.1:8099") -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    stem = hashlib.sha256(f"{order_id}/{uuid4()}".encode()).hexdigest()[:16]
    request_path, output = workdir / f"{stem}.request.json", workdir / f"{stem}.result.json"
    request_path.write_text(json.dumps({"schema": 1, "thread": str(uuid4()),
        "case": {"case_id": case_id, "order_id": order_id, "request": request_text}}))
    env = {
        "PATH": os.environ["PATH"], "HOME": str(workdir), "FI_SKILLS_TRACING": "off", "PYTHONPATH": str(measure.SOURCE / "src"),
        "CTXHUB_REGISTRY_URL": registry_url, "CTXHUB_CONTEXT_CREDENTIAL": credential,
        "CTXHUB_BINDING_ID": binding_id, "CTXHUB_RUNTIME_DIGEST": runtime_digest,
        "CTXHUB_PROVIDER_URL": measure.PROVIDER_URL, "CTXHUB_PROVIDER_KEY": os.environ.get("CTXHUB_PROVIDER_KEY", ""),
        "CTXHUB_BUSINESS_URL": business_url, "CTXHUB_MODEL": model,
    }
    proc = subprocess.run([sys.executable, "-m", "ctxhub_support.entrypoint", str(request_path),
        "--state", str(workdir / f"{stem}.sqlite"), "--output", str(output)],
        env=env, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode not in {0, 2} or not output.exists():
        raise RuntimeError(f"application_crashed rc={proc.returncode} {proc.stderr[-300:]}")
    return json.loads(output.read_text())
