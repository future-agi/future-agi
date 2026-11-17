# Source before running the Hub's Django commands by hand (dev-up.sh and hub-manage.sh do it themselves):
#   . scripts/omega-hub/lane-env.sh
# The dev stack runs the integrated app (omega_hub is in INSTALLED_APPS and tfc/urls.py) on the
# platform's own settings, `tfc.settings.settings` with ENV_TYPE=local (DEBUG on, console email),
# against the local test stack only: Postgres on 15499 in a database of its own, Redis on 16379,
# no ClickHouse, and the EE licence check pointed at loopback so nothing reaches a cloud endpoint.
# Every value is a default; the environment still wins. pytest keeps pytest.ini's tfc.settings.test.
export OMEGA_HUB_SETTINGS="${OMEGA_HUB_SETTINGS:-tfc.settings.settings}"
export ENV_TYPE="${ENV_TYPE:-local}"
export PG_PORT="${PG_PORT:-15499}"
export PG_TEST_DB="${PG_TEST_DB:-test_omega_hub_lane}"
# tfc.settings.settings reaches Postgres through PGBOUNCER_HOST/PORT; locally that is the test stack.
export PGBOUNCER_HOST="${PGBOUNCER_HOST:-localhost}" PGBOUNCER_PORT="${PGBOUNCER_PORT:-$PG_PORT}"
export PG_USER="${PG_USER:-test_user}" PG_PASSWORD="${PG_PASSWORD:-test_password}"
export CH_ENABLED="${CH_ENABLED:-false}"
export FI_SKIP_CH25_MIGRATION="${FI_SKIP_CH25_MIGRATION:-1}"
export FI_SKIP_CH25_SCHEMA_APPLY="${FI_SKIP_CH25_SCHEMA_APPLY:-1}"
export CH25_HTTP_PORT="${CH25_HTTP_PORT:-9}" CH25_TCP_PORT="${CH25_TCP_PORT:-9}" CH_PORT="${CH_PORT:-9}"
export NO_STARTUP_DB_MUTATIONS="${NO_STARTUP_DB_MUTATIONS:-false}"
export FUTURE_AGI_LICENSE_URL="${FUTURE_AGI_LICENSE_URL:-http://127.0.0.1:9/license}"
export FUTURE_AGI_TELEMETRY_DISABLED="${FUTURE_AGI_TELEMETRY_DISABLED:-true}"
export FUTURE_AGI_TELEMETRY_URL="${FUTURE_AGI_TELEMETRY_URL:-http://127.0.0.1:9/telemetry}"
export REDIS_URL="${REDIS_URL:-redis://localhost:16379/0}"
export REDIS_LOCK_URL="${REDIS_LOCK_URL:-redis://localhost:16379/2}"
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-colima}"
