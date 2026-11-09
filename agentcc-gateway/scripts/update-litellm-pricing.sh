#!/usr/bin/env bash
# scripts/update-litellm-pricing.sh
# Refreshes the bundled model pricing from litellm, for the gateway and the
# collector alike. Run from agentcc-gateway/.
#
# Upstream drops entries over time, and a model a customer still pins would
# then be priced at nothing. So every entry upstream no longer has is kept as
# it was; every other entry takes upstream's value.
set -euo pipefail

URL="https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
DEST="internal/modeldb/litellm.json"
COLLECTOR="../fi-collector/pkg/pricing/model_prices.json"

upstream=$(mktemp)
trap 'rm -f "$upstream"' EXIT

echo "Downloading litellm pricing data..."
curl -sSfL "$URL" -o "$upstream"

python3 - "$upstream" "$DEST" <<'PY'
import json
import sys

upstream_path, dest = sys.argv[1], sys.argv[2]
with open(upstream_path) as f:
    merged = json.load(f)
with open(dest) as f:
    current = json.load(f)

kept = [model for model in current if model not in merged]
for model in kept:
    merged[model] = current[model]

# Upstream also drops capability flags from entries it keeps. The gateway
# refuses tools, vision or json_schema for a model whose flag says no, so a
# flag the current file states as true is carried forward.
carried = 0
for model, entry in merged.items():
    old = current.get(model)
    if not isinstance(old, dict) or not isinstance(entry, dict):
        continue
    for flag, value in old.items():
        if flag.startswith("supports_") and value is True and flag not in entry:
            entry[flag] = True
            carried += 1

with open(dest, "w") as f:
    json.dump(merged, f, indent=4, ensure_ascii=False)
    f.write("\n")
print(f"{len(merged)} entries: {len(merged) - len(kept)} from upstream, {len(kept)} kept that upstream dropped, {carried} capability flags carried forward")
PY

cp "$DEST" "$COLLECTOR"
echo "Saved to $DEST and $COLLECTOR"
