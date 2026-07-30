#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_PATH="${ROOT_DIR}/api_contracts/openapi/swagger.json"
DJANGO_SETTINGS="${DJANGO_SETTINGS_MODULE:-tfc.settings.test}"
API_URL="${API_CONTRACT_BASE_URL:-http://localhost:8000}"

mkdir -p "$(dirname "${OUTPUT_PATH}")"

cd "${ROOT_DIR}/futureagi"
uv run python manage.py generate_swagger "${OUTPUT_PATH}" \
  --format json \
  --overwrite \
  --mock-request \
  --url "${API_URL}" \
  --settings "${DJANGO_SETTINGS}" \
  --verbosity 0
