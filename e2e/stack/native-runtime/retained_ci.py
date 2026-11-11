"""The customer's incumbent regression pipeline (synthetic fixture).

An existing CI job owned by the application team: it holds its own dataset and decides pass/fail
from the business ledger, independently of Hub grading. It is pointed at one exact context version
by the binding and context-only credential it is handed.
  retained_ci.py --binding-id ID --context-id ID --label RUN
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import measure
from app_cell import run_cell
from ctxhub_support.business import Business

DATASET = json.loads(Path(__file__).with_name("ci_dataset.json").read_text())
OUTPUT = measure.OUTPUT / "ci"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--context-id", required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    config = measure.configuration()
    business = Business(Path(os.environ.get("CTXHUB_BUSINESS_DB", "/fi_business/business.sqlite")))
    rows = []
    for case in DATASET["cases"]:
        order = "synthetic-ci-" + hashlib.sha256(f"{args.label}/{case['name']}".encode()).hexdigest()[:16]
        business.seed(order, age=case["state"]["age_days"], amount=case["state"]["amount"], disputed=case["state"]["disputed"])
        result = run_cell(registry_url=os.environ["CTXHUB_REGISTRY_URL"], credential=os.environ["CTXHUB_CONTEXT_CREDENTIAL"],
            binding_id=args.binding_id, runtime_digest="sha256:" + hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
            order_id=order, case_id=case["name"], request_text=case["request"], workdir=measure.STATE / "ci" / args.label,
            model=config["model"]["model_id"])
        operations = [r for r in business.ledger()["operations"] if r["order_id"] == order]
        applied = [r for r in operations if r["status"] == "applied"]
        rows.append({"case": case["name"], "version_id": result.get("version_id"), "context_digest": result.get("context_digest"),
            "status": result["status"], "actions": [r["action"] for r in applied], "declined": len(operations) - len(applied),
            "passed": result["status"] == "completed" and [r["action"] for r in applied] == [case["expected_action"]]})
    report = {"schema": 1, "evidence_class": "synthetic fixture", "pipeline": DATASET["pipeline"], "label": args.label,
        "context_id": args.context_id, "binding_id": args.binding_id,
        "version_ids": sorted({r["version_id"] for r in rows if r["version_id"]}), "cases": rows,
        "passed": all(r["passed"] for r in rows), "configuration_identity": DATASET["pipeline"] + "@" + hashlib.sha256(json.dumps(DATASET, sort_keys=True).encode()).hexdigest()[:12]}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / f"{args.label}.json").write_text(json.dumps(report))
    # The pipeline prints its own result, as CI does: the run reference, the configuration identity, the decision and
    # each case, so the person reading its output has everything to record without opening the report file.
    print(json.dumps({"label": args.label, "passed": report["passed"], "decision": "passed" if report["passed"] else "failed",
        "configuration_identity": report["configuration_identity"], "version_ids": report["version_ids"],
        "cases": [{"case": r["case"], "status": r["status"], "passed": r["passed"]} for r in rows],
        "url": f"https://ci.native-runtime.internal/runs/{args.label}"}))  # a recorded reference of a synthetic CI run, never fetched
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
