#!/usr/bin/env python3
"""Generate the opt-in dimensional settlement contract; run on staging only.

No v1 producer/consumer or model offer changes. The new seam remains unconnected
until the drain supports this version and provider unit contracts are qualified.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "api_contracts/gateway/examples/managed-settlement-v2.schema.json"


def rendered():
    schema = json.loads((DEST.parent / "managed-settlement-v1.schema.json").read_text())
    schema["$id"] = schema["$id"].replace("v1", "v2")
    schema["title"] = "managed-settlement-v2"
    schema["description"] = (
        "Opt-in dimensional outbox contract. All v1 fields retain their meaning. "
        "dimensions is compact JSON; missing quantity means unknown, never zero. "
        "estimated_bound is a conservative charge, not measured provider cost. "
        "Do not enable a producer until the consumer supports v2."
    )
    schema["properties"]["v"] = {"const": "2"}
    schema["required"].append("dimensions")
    schema["properties"]["dimensions"] = {
        "type": "string", "contentMediaType": "application/json",
        "contentSchema": {"$ref": "#/$defs/DimensionalReceipt"},
    }
    decimal = {"type": "string", "maxLength": 64, "pattern": r"^(0|[1-9][0-9]*)(\.[0-9]+)?$"}
    schema["$defs"]["DimensionalReceipt"] = {
        "type": "object", "additionalProperties": False,
        "required": ["contract", "model", "endpoint", "snapshot", "state", "lines"],
        "properties": {
            **{k: {"type": "string", "minLength": 1} for k in ["contract", "model", "endpoint", "snapshot"]},
            "state": {"enum": ["measured", "estimated_bound", "provider_total", "estimated_pending_reconciliation"]},
            "inclusive_attempts": {"type": "array", "minItems": 1, "maxItems": 16, "items": {"$ref": "#/$defs/InclusiveAttempt"}},
            "provider_total_usd": decimal,
            "lines": {"type": "array", "minItems": 0, "maxItems": 11, "items": {"$ref": "#/$defs/DimensionalLine"}},
        },
    }
    schema["$defs"]["DimensionalLine"] = {
        "type": "object", "additionalProperties": False,
        "required": ["unit", "rate_usd", "cap", "estimated", "covered_by_total"],
        "properties": {
            "unit": {"enum": ["uncached_input_token", "cache_read_token", "cache_write_token", "text_output_token", "reasoning_token", "input_image", "output_image", "song", "clip", "search_query", "request"]},
            "rate_usd": decimal,
            "cap": {"type": "integer", "minimum": 0, "maximum": 1000000000},
            "quantity": {"type": "integer", "minimum": 0, "maximum": 1000000000},
            "estimated": {"type": "boolean"},
            "covered_by_total": {"type": "boolean"},
        },
    }
    schema["$defs"]["InclusiveAttempt"] = {
        "type": "object", "additionalProperties": False,
        "required": ["attempt_id", "model", "served_model", "generation_id", "hold_micros", "upstream_micros", "billed_micros", "estimated"],
        "properties": {
            **{k: {"type": "string"} for k in ["attempt_id", "model", "served_model", "generation_id", "anomaly", "provider_total_usd"]},
            **{k: {"type": "integer", "minimum": 0} for k in ["hold_micros", "upstream_micros", "billed_micros"]},
            "estimated": {"type": "boolean"},
        },
    }
    return json.dumps(schema, indent=2) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = rendered()
    if args.check:
        # The repository's mandatory pre-commit hook formats JSON with
        # Prettier. Compare the contract, not whitespace introduced by it.
        if not DEST.exists() or json.loads(DEST.read_text()) != json.loads(content):
            raise SystemExit("dimensional contract is stale; regenerate on staging")
        print("dimensional contract current")
    else:
        DEST.write_text(content)
        print("generated dimensional contract")


if __name__ == "__main__":
    main()
