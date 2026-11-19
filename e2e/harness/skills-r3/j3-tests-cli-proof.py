"""Live proof that whole-context Tests are usable from the public CLI, not only the UI.

Prerequisite provisioning and evidence only, never a measured complete job. It drives
`python -m fi.context.testing preview|create|get|results` with a user-bound API key,
exactly as a customer shell would, against the owned full stack. The reviewer inputs
(flagged-file classifications, consent) are explicit fields of the JSON the operator
supplies; nothing is refreshed or acknowledged implicitly. Private credentials stay
beside the input and are never written to the evidence file.

Usage (cloud VM): J3_PYTHON=... python3 j3-tests-cli-proof.py INPUT.json OUTPUT.json
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

PYTHON = os.environ["J3_PYTHON"]
FAKE = "http://localhost:" + os.environ.get("E2E_SKILLS_FIXTURE_PORT", "28084")


def http(method, url, headers, body=None):
    request = urllib.request.Request(url, method=method, headers={**headers, "Content-Type": "application/json"},
                                     data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        # Synthetic fixture: the error body names the rejected control, never a credential.
        raise SystemExit(f"{method} {url.split('/', 3)[-1]} -> {error.code} {error.read()[:200].decode(errors='replace')}")


def cli(env, *args):
    result = subprocess.run([PYTHON, "-m", "fi.context.testing", *args], env=env, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise SystemExit(f"CLI {args[0]} exited {result.returncode}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def main():
    config = json.loads(Path(sys.argv[1]).read_text())
    author, owner = config["author"], config["owner"]
    api = config["apiUrl"]
    auth = lambda actor: {"Authorization": f"Bearer {actor['access']}", "X-Organization-Id": actor["organizationId"],
                          "X-Workspace-Id": actor["workspaceId"]}
    # A user-bound key: consumption credentials cannot author private paid jobs.
    issued = http("POST", f"{api}/accounts/key/generate_secret_key/", auth(author), {"key_name": f"j3-cli-{uuid4().hex[:8]}"})["result"]
    env = {**os.environ, "FI_API_KEY": issued["api_key"], "FI_SECRET_KEY": issued["secret_key"],
           "FI_BASE_URL": api, "FI_ORGANIZATION_ID": author["organizationId"], "FI_WORKSPACE_ID": author["workspaceId"]}
    namespace = f"e2e-skills-{uuid4()}"
    provider = http("POST", f"{api}/model-hub/api-keys/", auth(owner), {"provider": "openai", "key": namespace})
    provider_id = (provider.get("result") or provider)["id"]
    registered = False
    parent = config["parents"][2]  # The pinned parent: an exact published release, no draft needed.
    prompt = f"cli-proof-{uuid4().hex[:8]}: explain a purchasing request"
    try:
        http("POST", f"{FAKE}/scenarios", {}, {"namespace": namespace, "rules": [
            {"match": {"request": prompt, "turn": 0}, "response": {"message": {"role": "assistant", "content": "approved-response"}, "finish_reason": "stop"}}], "github": []})
        registered = True
        options = http("GET", f"{api}/model-hub/skills/testing-options/", auth(author))["result"]
        version = http("GET", f"{api}/model-hub/skills/contexts/{parent['id']}/versions/1/inspection/", auth(author))["result"]["manifest"]
        configuration = {
            "harness": "contexts-text-v1.2", "candidate": {"kind": "version", "version": 1, "digest": version["digest"]},
            "cases": {"kind": "ad_hoc", "cases": [{"id": str(uuid4()), "title": "CLI context regression", "source_group_id": "manual-context-test",
                "prompt": prompt, "fixtures": [], "expected_trigger": None,
                "checks": [{"id": str(uuid4()), "kind": "contains", "critical": True, "config": {"text": "approved-response", "case_sensitive": False}}],
                "reviewed": True}]},
            "baselines": [{"kind": "without_context"}], "competitors": [],
            "model": {"model_id": "gpt-4o-mini", "temperature": 0, "max_output_tokens": 1024}, "judge": None,
            "profile": "quick", "repetitions": 1,
            "limits": {"max_total_tokens": options["limits"]["profiles"]["quick"]["max_total_tokens"], "max_estimated_cost_usd": "2.00"},
            "purpose": "regression", "parent_run_id": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            plan = Path(directory, "configuration.json")
            plan.write_text(json.dumps(configuration))
            preview = cli(env, "preview", parent["id"], "--input", str(plan))
            # The operator reviews each flagged file; the classification is explicit input.
            classifications = [{"skill_id": bundle["skill_id"], "path": item["path"], "sha256": item["sha256"],
                                "classification": "reference_material",
                                "reason": "Synthetic purchase-policy reference; no expected-answer text is bundled."}
                               for bundle in preview["inventory"] for item in bundle["files"] if "evaluation_artifact" in item["flags"]]
            review = {"configuration": configuration, "expected_fingerprint": preview["fingerprint"],
                      "inventory_review": {"fingerprint": preview["fingerprint"], "reviewed_all_files": True,
                                           "no_known_answer_keys": True, "classifications": classifications,
                                           "false_positive_secret_findings": []}}
            reviewed = Path(directory, "reviewed-request.json")
            reviewed.write_text(json.dumps(review))
            key = str(uuid4())
            created = cli(env, "create", parent["id"], "--input", str(reviewed), "--idempotency-key", key, "--acknowledge-provider-processing")
            replay = cli(env, "create", parent["id"], "--input", str(reviewed), "--idempotency-key", key, "--acknowledge-provider-processing")
            deadline = time.time() + 240
            while True:
                run = cli(env, "get", parent["id"], created["id"])
                if run["status"] in {"completed", "failed", "cancelled"} or time.time() > deadline:
                    break
                time.sleep(3)
            results = cli(env, "results", parent["id"], created["id"])
            listing = cli(env, "list", parent["id"])
        record = {"evidence_class": "synthetic fixture", "interface": "python -m fi.context.testing", "context": parent["id"],
                  "preview_fingerprint": preview["fingerprint"], "flagged_files_classified": len(classifications),
                  "run_id": created["id"], "replay_same_run": replay["id"] == created["id"], "status": run["status"],
                  "candidate_digest": run["candidate"]["digest"], "release_digest": version["digest"],
                  "results": results, "listed_run_ids": [row["id"] for row in listing.get("results", [])]}
        Path(sys.argv[2]).write_text(json.dumps(record, indent=2))
        assert run["status"] == "completed" and record["replay_same_run"] and record["candidate_digest"] == version["digest"], record["status"]
        print(json.dumps({key: record[key] for key in ("status", "replay_same_run", "flagged_files_classified", "run_id")}))
    finally:
        http("DELETE", f"{api}/model-hub/api-keys/{provider_id}/", auth(owner))
        if registered:
            http("DELETE", f"{FAKE}/scenarios/{namespace}", {})


if __name__ == "__main__":
    if os.name != "posix" or not Path("/proc").exists():
        raise SystemExit("Run only on the assigned Linux cloud VM")
    main()
