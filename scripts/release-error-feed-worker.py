"""Dispatch the private worker release and wait for its verified image receipt."""

import json
import os
import re
import subprocess
import time
from urllib.parse import quote

REPO = "future-agi/omega-error-feed-worker"
IMAGE = "futureagi/omega-error-feed-worker"


def api(path: str, payload: dict | None = None, *, missing_ok: bool = False):
    args = ["gh", "api", f"repos/{REPO}/{path}"]
    if payload is not None:
        args += ["--method", "POST", "--input", "-"]
    result = subprocess.run(args, input=json.dumps(payload) if payload else None,
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        if missing_ok and "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(f"Worker GitHub API request failed: {path}")
    return json.loads(result.stdout) if result.stdout.strip() else None


def validate_receipt(release: dict, version: str, source_sha: str | None = None) -> dict:
    receipt = json.loads(release["body"])
    if (release.get("draft") or release.get("prerelease")
            or release.get("tag_name") != f"platform/{version}"
            or receipt.get("schema_version") != 1
            or receipt.get("version") != version
            or receipt.get("image") != f"{IMAGE}:{version}"
            or not re.fullmatch(r"[a-f0-9]{40}", receipt.get("source_sha", ""))
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", receipt.get("digest", ""))
            or (source_sha and receipt["source_sha"] != source_sha)):
        raise ValueError("Invalid or mismatched worker image receipt")
    return receipt


def release_worker(version: str, *, retry_only: bool, source_ref: str,
                   request_id: str, timeout_seconds: int = 2400) -> dict:
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a stable platform version")
    path = f"releases/tags/{quote('platform/' + version, safe='')}"
    existing = api(path, missing_ok=True)
    if existing:
        return validate_receipt(existing, version)
    if retry_only:
        raise RuntimeError("No verified worker image exists; complete its release before retrying the deployment bump")
    if not source_ref or not re.fullmatch(r"[0-9]+-[0-9]+", request_id):
        raise ValueError("Set OMEGA_WORKER_RELEASE_REF and provide a valid run-attempt request ID")
    source_sha = api(f"commits/{quote(source_ref, safe='')}")["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("Invalid resolved worker commit")
    api("dispatches", {"event_type": "platform-release", "client_payload": {
        "version": version, "source_sha": source_sha, "request_id": request_id}})
    title = f"worker-release {version} {request_id}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        runs = api("actions/workflows/release-image.yml/runs?event=repository_dispatch&per_page=100")
        matching = [run for run in runs["workflow_runs"] if run["display_title"] == title]
        if matching:
            run = max(matching, key=lambda item: item["id"])
            if run["status"] == "completed":
                if run["conclusion"] != "success":
                    raise RuntimeError(f"Worker build {run['conclusion']}: {run['html_url']}")
                return validate_receipt(api(path), version, source_sha)
        print(f"Waiting for worker release {version} ({source_sha})", flush=True)
        time.sleep(15)
    raise TimeoutError("Worker release timed out; no deployment bump allowed. Inspect the worker run before retrying.")


if __name__ == "__main__":
    result = release_worker(os.environ["VERSION"],
                            retry_only=os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch",
                            source_ref=os.environ.get("WORKER_RELEASE_REF", ""),
                            request_id=os.environ["RELEASE_REQUEST_ID"])
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"digest={result['digest']}\nsource_sha={result['source_sha']}\n")
    print(f"Verified {result['image']}@{result['digest']} from {result['source_sha']}")
