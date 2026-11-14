"""Install a path-aware SPA router on the exact dev frontend distribution.

Uses the existing dev deployment workflow identity. Never changes IAM, Access,
origins, cache policies, error responses, or another viewer-request function.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

HOST = "dev.futureagi.com"
COMMENT = "FutureAGI dev-only SPA routes; managed by deploy-dev-spa-router.py"


class CloudFrontError(RuntimeError):
    def __init__(self, operation: str, code: str):
        self.code = code
        super().__init__(f"CloudFront {operation} failed: {code}")


def aws(operation: str, *arguments: str) -> dict:
    result = subprocess.run(
        ["aws", "cloudfront", operation, *arguments, "--output", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        match = re.search(r"An error occurred \(([^)]+)\)", result.stderr)
        raise CloudFrontError(operation, match[1] if match else "command_failed")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def validate_target(config: dict) -> None:
    aliases = config.get("Aliases", {})
    if aliases.get("Quantity") != 1 or aliases.get("Items") != [HOST]:
        raise ValueError("Refusing a distribution not dedicated to dev.futureagi.com")
    if not config.get("Enabled"):
        raise ValueError("Refusing a disabled distribution")


def attach_router(config: dict, arn: str) -> dict:
    validate_target(config)
    updated = copy.deepcopy(config)
    behavior = updated["DefaultCacheBehavior"]
    if any(
        entry.get("EventType") == "viewer-request"
        for entry in behavior.get("LambdaFunctionAssociations", {}).get("Items", [])
    ):
        raise ValueError("An existing viewer-request Lambda must not be overwritten")
    associations = behavior.get("FunctionAssociations", {}).get("Items", [])
    for entry in associations:
        if entry.get("EventType") == "viewer-request" and entry.get("FunctionARN") != arn:
            raise ValueError("An existing viewer-request function must not be overwritten")
    items = [entry for entry in associations if entry.get("EventType") != "viewer-request"]
    items.append({"EventType": "viewer-request", "FunctionARN": arn})
    behavior["FunctionAssociations"] = {"Quantity": len(items), "Items": items}
    return updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution-id", required=True)
    parser.add_argument("--code", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Z0-9]{8,32}", args.distribution_id):
        raise ValueError("Invalid distribution ID")
    code = args.code.resolve()
    if not code.is_file() or code.stat().st_size > 10_000:
        raise ValueError("Invalid CloudFront function source")
    original = aws("get-distribution-config", "--id", args.distribution_id)
    validate_target(original["DistributionConfig"])
    name = "fagi-dev-spa-" + args.distribution_id.lower()
    behavior = original["DistributionConfig"]["DefaultCacheBehavior"]
    if any(entry.get("EventType") == "viewer-request" for entry in
           behavior.get("LambdaFunctionAssociations", {}).get("Items", [])):
        raise ValueError("An existing viewer-request Lambda must not be overwritten")
    for entry in behavior.get("FunctionAssociations", {}).get("Items", []):
        if entry.get("EventType") == "viewer-request" and not entry.get("FunctionARN", "").endswith("/" + name):
            raise ValueError("An existing viewer-request function must not be overwritten")
    desired = code.read_bytes()
    with tempfile.TemporaryDirectory(prefix="dev-spa-") as directory:
        root = Path(directory)
        try:
            current = aws("describe-function", "--name", name, "--stage", "DEVELOPMENT")
        except CloudFrontError as error:
            if error.code not in {"NoSuchFunctionExists", "NoSuchFunction"}:
                raise
            current = None
        if current:
            function_config = current["FunctionSummary"]["FunctionConfig"]
            if function_config.get("Comment") != COMMENT:
                raise ValueError("Existing function has a different owner")
            existing_code = root / "existing.js"
            aws("get-function", "--name", name, "--stage", "DEVELOPMENT", str(existing_code))
            if existing_code.read_bytes() != desired:
                raise ValueError("Existing function source differs; refusing an unreviewed overwrite")
        else:
            current = aws(
                "create-function",
                "--name", name,
                "--function-config", json.dumps({"Comment": COMMENT, "Runtime": "cloudfront-js-2.0"}),
                "--function-code", "fileb://" + str(code),
            )
        arn = current["FunctionSummary"]["FunctionMetadata"]["FunctionARN"]
        updated = attach_router(original["DistributionConfig"], arn)
        aws("publish-function", "--name", name, "--if-match", current["ETag"])
        if updated != original["DistributionConfig"]:
            # The ETag prevents discarding a concurrent operator's change.
            config_path = root / "distribution.json"
            config_path.write_text(json.dumps(updated))
            config_path.chmod(0o600)
            aws("update-distribution", "--id", args.distribution_id,
                "--if-match", original["ETag"],
                "--distribution-config", "file://" + str(config_path))
            aws("wait", "distribution-deployed", "--id", args.distribution_id)
        print(json.dumps({"host": HOST, "router": name, "status": "deployed",
                          "changed_distribution": updated != original["DistributionConfig"]}))


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except (CloudFrontError, ValueError) as error:
        raise SystemExit(str(error)) from None
