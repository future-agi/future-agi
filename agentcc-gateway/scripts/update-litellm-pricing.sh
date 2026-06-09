#!/usr/bin/env bash
# scripts/update-litellm-pricing.sh
# Downloads the latest litellm model pricing data.
set -euo pipefail

URL="https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
DEST="internal/modeldb/litellm.json"

echo "Downloading litellm pricing data..."
curl -sSfL "$URL" -o "$DEST"
echo "Saved to $DEST ($(wc -l < "$DEST") lines)"
