"""Bounded synthetic-fixture SDK actions. No private service or DB mutation."""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fi.platform import Client, ContextAuth, RequestOptions


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["source-aba", "provider", "heartbeat"])
    parser.add_argument("input", type=Path)
    parser.add_argument("value")
    args = parser.parse_args()
    config = json.loads(args.input.read_text())
    actor = config["sdkActor"]
    with Client(api_key=actor["apiKey"], secret_key=actor["secretKey"],
                base_url=config["apiUrl"], workspace_id=actor["workspaceId"],
                default_headers={"X-Organization-Id": actor["organizationId"]}, max_retries=0) as api:
        root = "/model-hub/skills/"
        if args.operation == "provider":
            result = api.request("POST", "/model-hub/api-keys/", body={"provider": "openai", "key": args.value})
            print(json.dumps({"id": result["id"]}))
        elif args.operation == "source-aba":
            path = root + config["leaves"][0]["id"] + "/"
            results = []
            for version in [7, 8]:
                current = api.request("GET", path)
                pointer = next(row["version"] for row in current["labels"] if row["name"] == "production")
                moved = api.request("PUT", path + "labels/production/", body={"version": version,
                    "expected_version": pointer, "expected_release_revision": current["release_revision"],
                    "note": "Synthetic source ABA fixture"})
                results.append(moved)
            print(json.dumps(results))
        else:
            index = int(args.value)
            state = json.loads((args.input.parent / f"runtime-{index}.json").read_text())
            binding, session = state["binding"], state["initial_session"]
            # A receipt names the session's own incarnation and a fresh sequence.
            state["sequence"] = state.get("sequence", 0) + 1
            credential = api.request("POST", root + "contexts/credentials/", body={"purpose": "report", "grants": [],
                "binding_ids": [binding["id"]], "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
                options=RequestOptions(idempotency_key=str(uuid4())))
            try:
                with Client(auth=ContextAuth(credential["secret"]), base_url=config["apiUrl"], max_retries=0) as reporter:
                    receipt = reporter.request("POST", root + f"contexts/bindings/{binding['id']}/receipts/", body={
                        "event_id": str(uuid4()), "session_id": session["session_id"], "instance_id": state["initial_request"]["incarnation_id"], "sequence": state["sequence"],
                        "stage": "resolved", "version_id": session["version_id"], "digest": session["digest"],
                        "materialization_digest": None, "observed_at": datetime.now(timezone.utc).isoformat(),
                        "details": {"path": None, "sha256": None, "ranges": [], "runtime_version": "deepagents-0.4.11",
                            "adapter_version": "j3-synthetic-1", "diagnostic_codes": [], "trace_ref": None}})
                other = config["parents"][(index + 1) % 3]
                unrelated = api.request("POST", root + f"contexts/{other['id']}/bindings/", body={
                    "name": f"unrelated-{uuid4()}", "kind": "runtime", "selector": {"label": "staging"}, "update_mode": "new_thread",
                    "profile": "synthetic", "profile_version": "1", "repository": None, "expected_report_interval_seconds": 3600},
                    options=RequestOptions(idempotency_key=str(uuid4())))
                state.setdefault("unrelated_bindings", []).append({"id": unrelated["id"], "revision": unrelated["revision"]})
                path = args.input.parent / f"runtime-{index}.json"
                path.write_text(json.dumps(state)); path.chmod(0o600)
                print(json.dumps({"receipt": receipt, "unrelated_binding": unrelated["id"]}))
            finally:
                api.request("DELETE", root + f"contexts/credentials/{credential['id']}/")


if __name__ == "__main__":
    main()
