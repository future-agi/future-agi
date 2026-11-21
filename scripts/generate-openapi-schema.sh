#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_PATH="${ROOT_DIR}/api_contracts/openapi/swagger.json"
DJANGO_SETTINGS="${DJANGO_SETTINGS_MODULE:-tfc.settings.openapi}"
API_URL="${API_CONTRACT_BASE_URL:-http://localhost:8000}"
ALLOW_SURFACE_SHRINK="${OPENAPI_ALLOW_SURFACE_SHRINK:-false}"

mkdir -p "$(dirname "${OUTPUT_PATH}")"

TEMP_OUTPUT_PATH="$(mktemp "${TMPDIR:-/tmp}/futureagi-openapi.XXXXXX.json")"
cleanup() {
  rm -f "${TEMP_OUTPUT_PATH}"
}
trap cleanup EXIT

cd "${ROOT_DIR}/futureagi"
uv run python manage.py generate_swagger "${TEMP_OUTPUT_PATH}" \
  --format json \
  --overwrite \
  --mock-request \
  --url "${API_URL}" \
  --settings "${DJANGO_SETTINGS}" \
  --verbosity 0

if [[ -f "${OUTPUT_PATH}" && "${ALLOW_SURFACE_SHRINK}" != "true" ]]; then
  node - "${OUTPUT_PATH}" "${TEMP_OUTPUT_PATH}" <<'NODE'
const fs = require("fs");

const [previousPath, generatedPath] = process.argv.slice(2);
const previous = JSON.parse(fs.readFileSync(previousPath, "utf8"));
const generated = JSON.parse(fs.readFileSync(generatedPath, "utf8"));
const operationMethods = new Set([
  "get",
  "post",
  "put",
  "patch",
  "delete",
  "head",
  "options",
]);
const operationSet = (schema) => {
  const operations = new Set();
  for (const [route, pathItem] of Object.entries(schema.paths || {})) {
    for (const method of Object.keys(pathItem || {})) {
      if (operationMethods.has(method)) operations.add(`${method} ${route}`);
    }
  }
  return operations;
};
// Reviewed unreleased F01 cutover: replace only this route, keeping the
// generated GET operation ID (drf-yasg omits HEAD). This is not a blanket surface-shrink exemption; definitions
// and every other path/operation remain subject to the original guard.
const legacyMedia = "/tracer/v1/attachment-media/{occurrence_id}/{grant_id}";
const boundMedia = "/tracer/v1/attachment-media/{media_session_id}/{occurrence_id}/{grant_id}";
const mediaReplacement = previous.paths?.[legacyMedia] &&
  !generated.paths?.[legacyMedia] &&
  ["get"].every((method) =>
    previous.paths[legacyMedia][method]?.operationId &&
    previous.paths[legacyMedia][method].operationId === generated.paths?.[boundMedia]?.[method]?.operationId
  ) &&
  Object.keys(previous.paths[legacyMedia]).filter((method) => operationMethods.has(method)).length === 1;
const reviewedReplacement = (entry) => mediaReplacement &&
  [legacyMedia, `get ${legacyMedia}`].includes(entry);
const missingFrom = (before, after) =>
  [...before].filter((entry) => !after.has(entry) && !reviewedReplacement(entry));
const missingPaths = missingFrom(
  new Set(Object.keys(previous.paths || {})),
  new Set(Object.keys(generated.paths || {})),
);
const missingDefinitions = missingFrom(
  new Set(Object.keys(previous.definitions || {})),
  new Set(Object.keys(generated.definitions || {})),
);
const missingOperations = missingFrom(
  operationSet(previous),
  operationSet(generated),
);

if (
  missingPaths.length > 0 ||
  missingDefinitions.length > 0 ||
  missingOperations.length > 0
) {
  const sample = (values) => values.slice(0, 20).join(", ");
  console.error(
    [
      "Refusing to replace the checked-in OpenAPI contract with a smaller surface.",
      `Missing paths (${missingPaths.length}): ${sample(missingPaths)}`,
      `Missing operations (${missingOperations.length}): ${sample(missingOperations)}`,
      `Missing definitions (${missingDefinitions.length}): ${sample(missingDefinitions)}`,
      "Set OPENAPI_ALLOW_SURFACE_SHRINK=true only for an explicitly reviewed API removal.",
    ].join("\n"),
  );
  process.exit(1);
}
NODE
fi

mv "${TEMP_OUTPUT_PATH}" "${OUTPUT_PATH}"
