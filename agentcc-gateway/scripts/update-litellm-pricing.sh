#!/usr/bin/env bash
# scripts/update-litellm-pricing.sh
# Refreshes the bundled model pricing from litellm, for the gateway, the
# collector and the backend alike. Run from agentcc-gateway/.
#
# Upstream drops entries over time, and a model a customer still pins would
# then be priced at nothing. So every entry upstream no longer has is kept as
# it was; every other entry takes upstream's value.
#
# models.dev fills the models litellm does not list. Those entries carry
# "source": "models.dev", are derived again on each run, and lose to litellm.
# The merge lives in scripts/merge_pricing.py.
set -euo pipefail

URL="https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
MODELS_DEV_URL="https://models.dev/api.json"
DEST="internal/modeldb/litellm.json"
COLLECTOR="../fi-collector/pkg/pricing/model_prices.json"
PYTHON_COPY="../futureagi/agentic_eval/core_evals/run_prompt/gateway_prices.json"

upstream=$(mktemp)
models_dev=$(mktemp)
trap 'rm -f "$upstream" "$models_dev"' EXIT

echo "Downloading litellm pricing data..."
curl -sSfL "$URL" -o "$upstream"
echo "Downloading models.dev pricing data..."
curl -sSfL "$MODELS_DEV_URL" -o "$models_dev"

python3 scripts/merge_pricing.py "$upstream" "$models_dev" "$DEST"

cp "$DEST" "$COLLECTOR"
cp "$DEST" "$PYTHON_COPY"
echo "Saved to $DEST, $COLLECTOR and $PYTHON_COPY"
