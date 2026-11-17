#!/usr/bin/env bash
# Run against the disposable, ml-enabled Hugging Face fixture stack.
set -euo pipefail
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
COMPOSE=(bash bin/e2e compose -f e2e/flows/datasets/create-upload-isolation.yml -f e2e/flows/datasets/huggingface-isolation.yml)
TEST_ENV=(
  -e DJANGO_SETTINGS_MODULE=tfc.settings.test
  -e PG_HOST=postgres -e PG_PORT=5432 -e PG_DB=futureagi
  -e PG_USER=futureagi -e PG_PASSWORD=futureagi -e PG_TEST_DB=test_huggingface
  -e REDIS_URL=redis://redis:6379/0
  -e CH_HOST=clickhouse -e CH_PORT=9000 -e CH_DATABASE=test_huggingface
  -e CH25_HOST=clickhouse -e CH25_HTTP_PORT=8123 -e CH25_TCP_PORT=9000
  -e CH25_DATABASE=test_huggingface -e FI_ALLOW_NONLOCAL_CH25_TEST_MUTATIONS=true
  -e FUTURE_AGI_LICENSE_URL=http://localhost:9
  -e FUTURE_AGI_TELEMETRY_DISABLED=true -e AWS_EC2_METADATA_DISABLED=true
)
MOUNTS=(
  -v "$ROOT/api_contracts:/app/api_contracts:ro"
  -v "$ROOT/frontend/src/generated/api-contracts:/app/frontend/src/generated/api-contracts:ro"
)
# An optional baseline file is mounted only in this short-lived test container;
# the serving API and worker always run the committed image unchanged.
if [[ -n "${HF_ACTIVITY_BASELINE:-}" ]]; then
  MOUNTS+=(-v "$HF_ACTIVITY_BASELINE:/app/backend/tfc/temporal/background_tasks/activities.py:ro")
fi
if [[ "$#" -eq 0 ]]; then
  set -- \
    tfc/temporal/background_tasks/tests/test_activities.py \
    model_hub/tests/test_dataset_huggingface_api.py \
    model_hub/tests/test_dataset_runtime_contracts.py \
    model_hub/tests/test_huggingface_revision.py \
    model_hub/tests/test_dataset_creation_import_api.py::test_create_huggingface_dataset_sets_workspace_after_validation \
    model_hub/tests/test_dataset_creation_import_api.py::test_create_huggingface_dataset_duplicate_name_does_not_charge_or_preview \
    model_hub/tests/test_dataset_creation_import_api.py::test_add_huggingface_rows_rejects_other_workspace_before_preview_or_usage \
    model_hub/tests/test_dataset_creation_import_api.py::test_add_huggingface_rows_sets_columns_and_appends_after_max_order
fi
"${COMPOSE[@]}" run --rm --no-deps -T "${TEST_ENV[@]}" "${MOUNTS[@]}" \
  --entrypoint python backend -m pytest "$@" -v -p no:cacheprovider
