#!/usr/bin/env bash
# Focused real-authentication proof on the pinned disposable cloud snapshot.
set -euo pipefail
repo=$(realpath "$1")
expected=$2
head=$(git -C "$repo" rev-parse HEAD)
test "$(uname -s)" = Linux
test "${OMEGA_EXEC_SOURCE_HEAD:-}" = "$head"
run="$repo/.key-role-checks/round3-$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$run/raw" "$run/results"
printf 'head=git:%s\n' "$head" > "$run/results/revisions.txt"
compose=(docker compose -p key-round3 -f "$repo/futureagi/docker-compose.test.yml")
"${compose[@]}" up -d --wait
trap '"${compose[@]}" down' EXIT
(cd "$repo/futureagi" && uv sync --locked)
code=0
env -i HOME="$HOME" PATH="$PATH" \
  DJANGO_SETTINGS_MODULE=tfc.settings.test ENV_TYPE=local TESTING=true \
  PG_HOST=127.0.0.1 PG_PORT=15432 PG_DB=test_tfc PG_TEST_DB=test_key_round3 \
  PG_USER=test_user PG_PASSWORD=test_password PGBOUNCER_HOST=127.0.0.1 PGBOUNCER_PORT=15432 \
  REDIS_URL=redis://127.0.0.1:16379/0 REDIS_LOCK_URL=redis://127.0.0.1:16379/2 \
  REDIS_CACHE_URL=redis://127.0.0.1:16379/3 \
  CH_HOST=127.0.0.1 CH_PORT=19000 CH_DATABASE=test_tfc CH_USERNAME=default CH_PASSWORD='' \
  CH25_HOST=127.0.0.1 CH25_HTTP_PORT=18123 CH25_TCP_PORT=19000 CH25_DATABASE=test_tfc \
  FI_CH_TEST_SIDECAR_NATIVE_PORT=19000 FI_CH_TEST_SIDECAR_HTTP_PORT=18123 \
  MINIO_ENDPOINT=127.0.0.1:19005 MINIO_URL=127.0.0.1:19005 \
  MINIO_ACCESS_KEY=minioadmin MINIO_SECRET_KEY=minioadmin \
  FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 FUTURE_AGI_TELEMETRY_URL=http://127.0.0.1:9 \
  FUTURE_AGI_TELEMETRY_DISABLED=true MODEL_SERVING_URL=http://127.0.0.1:9 AWS_EC2_METADATA_DISABLED=true \
  KEY_CHECK_SOCKET_LOG="$run/results/socket-guard.txt" \
  "$repo/futureagi/.venv/bin/python" "$repo/scripts/key-role-checks/run_pytest.py" "$repo/futureagi" \
  -q --tb=short -rA --create-db -p no:cacheprovider --junitxml="$run/raw/focused.xml" \
  accounts/tests/test_membership_role_history.py accounts/tests/test_system_key_principal.py \
  accounts/tests/test_key_management_scope.py > "$run/raw/focused.log" 2>&1 || code=$?
python3 -I - "$run" "$code" "$expected" <<'PY'
import json, pathlib, re, sys, xml.etree.ElementTree as ET
run = pathlib.Path(sys.argv[1])
code, expected = map(int, sys.argv[2:])
root = ET.parse(run / 'raw/focused.xml').getroot()
rows = []
for c in root.iter('testcase'):
    f = c.find('failure')
    rows.append({'test': c.get('classname') + '::' + c.get('name'),
                 'outcome': 'error' if c.find('error') is not None else 'failed' if f is not None else 'skipped' if c.find('skipped') is not None else 'passed',
                 'assertion': f is not None and (f.get('message', '').startswith('assert ') or f.get('message', '').startswith('AssertionError:'))})
summary = {'exit': code, 'expected_exit': expected, 'cases': rows}
(run / 'results/summary.json').write_text(json.dumps(summary, indent=2) + '\n')
for p in (run / 'raw').iterdir():
    (run / 'results' / p.name).write_text(re.sub(r'\b[0-9a-fA-F]{32,64}\b', '[fixture-value]', p.read_text()))
print(json.dumps(summary, indent=2))
assert code == expected
assert not any(r['outcome'] in ('error', 'skipped') for r in rows)
if expected == 1:
    for family in ('test_removed_owner_has_no_role_or_private_workspace', 'test_resolved_no_organization_does_not_fall_back_to_home_org', 'test_system_key_selects_owner_membership_not_first_home_user', 'test_system_key_changes_require_current_org_admin'):
        assert any(family in r['test'] and r['assertion'] for r in rows), family
else:
    assert all(r['outcome'] == 'passed' for r in rows)
PY
