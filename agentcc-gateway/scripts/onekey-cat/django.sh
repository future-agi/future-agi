#!/usr/bin/env bash
set -euo pipefail
OUT=$1
cd futureagi
export DJANGO_SETTINGS_MODULE=tfc.settings.test
export PG_HOST=localhost PG_PORT=15499 PG_DB=gw_cat3 PG_TEST_DB=gw_cat3_test PG_USER=test_user PG_PASSWORD=test_password
export REDIS_URL=redis://localhost:16380/25 REDIS_LOCK_URL=redis://localhost:16380/25 REDIS_CACHE_URL=redis://localhost:16380/25
export TEST_CLICKHOUSE_HOST=localhost TEST_CLICKHOUSE_PORT=38123
export CH_HOST=localhost CH_PORT=39000 CH_NATIVE_PORT=39000 CH_DATABASE=gw_cat3
export CH25_HOST=localhost CH25_HTTP_PORT=38123 CH25_NATIVE_PORT=39000 CH25_TCP_PORT=39000 CH25_DATABASE=gw_cat3
export MINIO_ENDPOINT=localhost:39005 MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin
export DEBUG=1 TEST=1 PYTHON_DOTENV_DISABLED=1 PYTHONDONTWRITEBYTECODE=1 FI_SKIP_CH25_MIGRATION=1
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 LITELLM_LOCAL_MODEL_COST_MAP=True
export OLLAMA_HOST=127.0.0.1:9 OLLAMA_URL=http://127.0.0.1:9 OLLAMA_BASE_URL=http://127.0.0.1:9 OLLAMA_API_BASE=http://127.0.0.1:9
# Keep module-patching test files on independent workers: distributing individual
# tests can retain view classes from a temporarily restored sys.modules graph.
status=0
.venv/bin/python -m pytest -n 32 --dist=loadfile -p no:cacheprovider -rfEsxX --junitxml="$OUT/django.xml" \
 agentcc/tests/test_onekey_go_outbox.py \
 agentcc/tests/test_managed_credits_api.py agentcc/tests/test_managed_credits_command.py \
 agentcc/tests/test_managed_credits_schema_names.py agentcc/tests/test_managed_estimate_floor.py \
 agentcc/tests/test_topup_single_path.py \
 ee/usage/tests/test_managed_settlement.py ee/usage/tests/test_ledger_interface_contract.py \
 ee/usage/tests/test_ledger_wallet.py ee/usage/tests/test_ledger_refund_attribution.py || status=1
.venv/bin/python -m pytest -p no:cacheprovider -rfEsxX --junitxml="$OUT/django-managed.xml" ee/usage/tests/test_managed_credits.py || status=1
exit "$status"
