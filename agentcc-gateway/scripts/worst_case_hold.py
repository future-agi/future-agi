#!/usr/bin/env python3
"""Offline launch-catalogue hold audit (Python stdlib only).

Run on a build VM, from any directory:
  python3 -I /path/to/agentcc-gateway/scripts/worst_case_hold.py \
      --gateway-root /path/to/agentcc-gateway --check
For a CAT-2 checkout, overlay the same committed test-only exporter (never its
production code), then run TestWorstCaseCatalogueExport with HOLD_CAT2=1,
HOLD_EXPORT=/absolute/catalogue.json and HOLD_SCRIPT=/absolute/this/script.py.
Replay either/both exports without Go or network:
  python3 -I worst_case_hold.py --catalogue stock.json --catalogue cat2.json --check

Source of truth: catalog.Seed (bundled model AND endpoint snapshots),
ServesManaged/Servable, the shutdown calendar, eligibility.Check and credits'
primaryEstimate. The companion Go test exports actual admission inputs; Python
independently calculates micros and --check compares EVERY row with Go. No
hand-maintained allowlist or price table. Blocked/unpriced entries are excluded
(708 add.38/41), not assigned a cheap substitute price. The spendguard's unpriced
reserve is a fallback, not an additive fee, and credits refuses those calls.

Envelope: full declared input context, plus explicit model maximum output (or
the configured reserve when the model maximum is unknown), and separately the
omitted-max_tokens default. Input and output ceilings are taken independently,
a conservative envelope even where the provider shares one context budget.
Token-priced admission does NOT clamp explicit max_tokens or input to catalogue
limits. Thus this is an operator sizing proposal for catalogue-bounded requests,
NOT a finite bound on arbitrary oversized requests. Tools/response-format input
is counted by Go's EstimateInputTokens, not dropped or added twice.
The admissible parameter space is the intersection of the managed request
policy (internal/routing/reqopts/managed_allowlist.go:78-95: n=1, modalities
not allowed) and CAT-2's inclusive bound: <=64 input images, <=65536 output
tokens, fixed input ceiling and router refusal thresholds. It searches the
greatest admitted output cap with n=1 and no modalities. Inclusive two-stage
half-up charging is NOT token ceil. Each selected endpoint OBJECT is priced
as server/hook_policy.go:applyVerdict does; IDs need not identify unique prices.
TestWorstCaseAdmissionParity replays every exported row through the real gates.
No proposal enables settings or changes any gateway/org configuration.
"""

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MAX_MICROS = (2**63 - 1) // 2


def go_round(value):
    """Go math.Round for the nonnegative values used by money conversion."""
    whole = math.floor(value)
    return whole + (value - whole >= 0.5)


def estimate(row):
    """Mirror billing.EstimateWorstCaseMicros, retaining binary64 order."""
    if "unpriced_reserve_usd" in row:
        usd = row["unpriced_reserve_usd"]
        if not usd > 0 or not math.isfinite(usd):
            return 0
        m = go_round(usd * 1_000_000 * 1e6) / 1e6
        return MAX_MICROS if m >= MAX_MICROS else math.floor(m + 0.5)
    if row.get("inclusive"):
        return inclusive_estimate(row)
    p = row["price"]
    usd = (float(row["input_tokens"]) * p.get("Prompt", 0)
           + float(row["output_tokens"]) * p.get("Completion", 0)
           + p.get("Request", 0))
    if not usd > 0:
        return 0
    micros = usd * 1_000_000 * (10_000 + max(0, row["markup_bps"])) / 10_000
    if math.isinf(micros) or micros >= MAX_MICROS:
        return MAX_MICROS
    micros = go_round(micros * 1e6) / 1e6
    return MAX_MICROS if micros >= MAX_MICROS else math.ceil(micros)


def half_up(r):
    return (2 * r.numerator + r.denominator) // (2 * r.denominator)


def charge(usd):
    upstream = half_up(usd * 1_000_000)
    return half_up(Fraction(upstream * 105, 100))


def rate(card, *fields):
    return max(Fraction(card.get(k) or "0") for k in fields)


def inclusive_estimate(row):
    q = row["inclusive"]
    n, images, out = row["n"], row["images"], row["output_tokens"]
    if q.get("Generation"):
        return charge(Fraction(q["Generation"]) * n)
    input_tokens = q["ContextLimit"] * n
    if q.get("RouterUniverse"):
        cards = json.loads(q["RouterUniverse"])
        return max(charge(
            rate(p, "Prompt", "CacheRead", "CacheWrite") * input_tokens
            + rate(p, "Completion", "Reasoning") * out * n
            + rate(p, "Request") + rate(p, "Image") * images
            + rate(p, "Generation") * n
        ) for p in cards)
    return charge(rate(q, "Prompt", "CacheRead", "CacheWrite") * input_tokens
                  + rate(q, "Completion", "Reasoning") * out * n
                  + rate(q, "Image") * images)


def summarize(doc, check=False):
    per_model = {}
    defaults = []
    for row in doc["rows"]:
        micros = estimate(row)
        if check and micros != row["go_micros"]:
            raise ValueError(f"{row['model']} {row['scenario']}: Python={micros}, Go={row['go_micros']}")
        item = {k: row[k] for k in ("model", "scenario", "input_tokens", "output_tokens", "n", "images")}
        item.update(input_images=row.get("input_images", 0),
                    modalities=row.get("modalities", []),
                    provider_only=row.get("provider_only", ""))
        item["micros"] = micros
        item["usd"] = micros / 1_000_000
        item["formula"] = "inclusive-two-stage" if row.get("inclusive") else "token-ceil"
        if row["scenario"] == "default-output":
            defaults.append(item)
        if micros > per_model.get(row["model"], {}).get("micros", -1):
            per_model[row["model"]] = item
    ranked = sorted(per_model.values(), key=lambda r: (-r["micros"], r["model"]))
    if not ranked:
        raise ValueError("no enabled models in export")
    minute = max(5, (ranked[0]["micros"] + 999_999) // 1_000_000)
    return {
        "variant": doc["variant"], "as_of": doc["as_of"], "snapshot_id": doc["snapshot_id"], "source_revision": doc["source_revision"],
        "enabled_models": len(ranked), "excluded_models": len(doc["excluded"]),
        "parity_rows": len(doc["rows"]) if check else None,
        "maximum": ranked[0], "top_10": ranked[:10], "models": ranked,
        "default_output_maximum": max(defaults, key=lambda r: r["micros"]) if defaults else None,
        "proposed_max_usd_per_minute": minute,
        "proposed_max_usd_per_hour": max(100, 10 * minute),
        "unpriced_reserve_usd": doc["unpriced_reserve_usd"],
        "unpriced_reserve_applied_to_enabled_models": False,
        "caveat": "Catalogue-bounded request envelope, not arbitrary-input bound. Per-org caps only; aggregate exposure = active orgs x caps. During an organization's first 7 days, spending with Future AGI credits is limited to $25 per day; requests that would exceed the limit are refused with an explanation. Contact support to raise it. These requests may therefore require an ops-raised daily cap and sufficient credits.",
    }


def export_catalogue(root, destination):
    env = dict(os.environ, HOLD_EXPORT=str(destination), GIT_NO_LAZY_FETCH="1")
    command = ["go", "test", "-count=1", "./internal/plugins/credits", "-run", "^TestWorstCaseCatalogueExport$"]
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--estimate-json", action="store_true", help="parity protocol: input vector array on stdin, micros array on stdout")
    parser.add_argument("--catalogue", type=Path, action="append", default=[], help="Go catalogue export; repeat for stock and CAT-2")
    parser.add_argument("--gateway-root", type=Path, help="generate the export with the real Go loader on a build VM")
    parser.add_argument("--check", action="store_true", help="require exact micros equality with every exported Go admission")
    parser.add_argument("--json", action="store_true", help="machine-readable report including every enabled model")
    args = parser.parse_args()
    if args.estimate_json:
        print(json.dumps([estimate(row) for row in json.load(sys.stdin)]))
        return
    with tempfile.TemporaryDirectory(prefix="gateway-hold-") as tmp:
        paths = list(args.catalogue)
        if args.gateway_root or not paths:
            root = (args.gateway_root or Path(__file__).resolve().parents[1]).resolve()
            path = Path(tmp) / "catalogue.json"
            export_catalogue(root, path)
            paths.append(path)
        reports = [summarize(json.loads(p.read_text()), args.check) for p in paths]
    if args.json:
        print(json.dumps(reports, indent=2, sort_keys=True))
        return
    for report in reports:
        print(f"\n{report['variant']} as-of {report['as_of']}; enabled={report['enabled_models']}, excluded={report['excluded_models']}, parity_rows={report['parity_rows']}")
        print("model | input tokens | output tokens | n | images | hold USD")
        for row in report["top_10"]:
            print(f"{row['model']} | {row['input_tokens']} | {row['output_tokens']} | {row['n']} | {row['images']} | {row['usd']:.6f}")
        print(f"PROPOSED minute={report['proposed_max_usd_per_minute']} hour={report['proposed_max_usd_per_hour']}")
        print("Unpriced reserve: fallback only, no enabled model uses it; blocked/unpriced entries excluded.")
        print(report["caveat"])


if __name__ == "__main__":
    main()
