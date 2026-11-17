#!/usr/bin/env bash
# Ordinary cloud job, never an egress-kit walk. All providers are synthetic.
set -uo pipefail
ROOT=$(pwd)
OUT=${OUT:?provide an evidence directory}
mkdir -p "$OUT"
export OLLAMA_HOST=127.0.0.1:9 OLLAMA_URL=http://127.0.0.1:9 OLLAMA_BASE_URL=http://127.0.0.1:9
export FUTURE_AGI_LICENSE_URL=http://127.0.0.1:9 LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONDONTWRITEBYTECODE=1 CI=1
export GOMAXPROCS=32 TEST_REDIS_ADDR=127.0.0.1:16380 TEST_REDIS_DB=11
bash "${RIG_EXEC_ROOT:-$HOME/rig-exec}/vm/exec/exec-preflight.sh" quick >"$OUT/network-guard.log" 2>&1 || exit 70
status=0
step(){ local name=$1; shift; "$@" >"$OUT/$name.log" 2>&1; local rc=$?; echo "$name $rc" | tee -a "$OUT/codes.txt"; if test "$rc" != 0; then status=1; tail -n 12 "$OUT/$name.log"; fi; }
step head bash -c 'git rev-parse HEAD; test "$(git rev-parse HEAD)" = "$OMEGA_EXEC_SOURCE_HEAD" && cd agentcc-gateway && go version'
PATTERN=${FOCUSED_PATTERN:-'TestSol|TestMixed|TestR602|TestOwned|TestInclusive|TestR601|TestRouter|TestManagedDimensional|TestShippedConfigsOfferNoFutureAGIModels|TestNewOrgDefaultNeedsAManagedProvider'}
focused(){ (cd "$ROOT/agentcc-gateway" && go test "$@" -p 32 -timeout 20m -count=1 -json -run "$PATTERN" ./internal/billing/... ./internal/redisstate ./internal/server ./internal/plugins/cost ./internal/plugins/credits ./internal/routerbilling ./internal/catalog ./internal/providers/openai ./cmd/agentcc); }
step focused focused
step focused-race focused -race
if test "${RED_ONLY:-0}" != 1; then
  step all bash -c 'cd agentcc-gateway && go test -p 32 -timeout 20m -count=1 -json ./...'
  step race bash -c 'cd agentcc-gateway && go test -race -p 32 -timeout 20m -count=1 -json ./internal/server/... ./internal/plugins/... ./internal/redisstate/... ./internal/billing/...'
fi
if test "${RED_ONLY:-0}" != 1; then
  export ONEKEY_OUTBOX_FIXTURES="$OUT/onekey_outbox.json"
  step outbox bash -c 'cd agentcc-gateway && go test -count=1 -run "^TestMixedDjangoOutbox$" ./internal/redisstate'
  export ONEKEY_UNDISPATCHED_OUTBOX="$OUT/onekey_undispatched_outbox.json"
  step undispatched-outbox bash -c 'cd agentcc-gateway && go test -count=1 -run "^TestOwnedR4UndispatchedInclusiveFallbackReleasesImmediately$" ./internal/server'
  step django bash agentcc-gateway/scripts/onekey-cat/django.sh "$OUT"
  step contracts python3 scripts/generate-agentcc-gateway-contracts.py --check
  step env-reference futureagi/.venv/bin/python scripts/env_reference.py --check
  step python-identity git diff --exit-code 973e3a1fe10b11c44160e85df24894a381cd678a HEAD -- futureagi ':!futureagi/agentcc/tests/test_onekey_go_outbox.py'
  step whitespace git diff --check 973e3a1fe10b11c44160e85df24894a381cd678a HEAD
  step markers bash -c '! git grep -n -E "^(<<<<<<< |=======$|>>>>>>> )" HEAD -- agentcc-gateway futureagi/agentcc'
fi
python3 -I - "$OUT" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]);summary={}
for name in ('focused','focused-race','all','race'):
 if not (p/(name+'.log')).exists(): continue
 rows=[]
 for line in (p/(name+'.log')).read_text().splitlines():
  try: rows.append(json.loads(line))
  except ValueError: pass
 failures=[r for r in rows if r.get('Action')=='fail']; failed={(r.get('Package'),r.get('Test')) for r in failures}
 summary[name]={a:sum(r.get('Action')==a and bool(r.get('Test')) for r in rows) for a in ('pass','fail','skip')}
 summary[name]['failures']=failures
 summary[name]['skipped_tests']=[r.get('Test') for r in rows if r.get('Action')=='skip' and r.get('Test')]
 summary[name]['top_level_passes']=[r['Test'] for r in rows if r.get('Action')=='pass' and r.get('Test') and '/' not in r['Test']]
 summary[name]['passing_subtests']={root:sum(r.get('Action')=='pass' and r.get('Test','').startswith(root+'/') for r in rows) for root in summary[name]['top_level_passes']}
 summary[name]['failure_output']=[r.get('Output') for r in rows if r.get('Action')=='output' and (r.get('Package'),r.get('Test')) in failed][-120:]
(p/'summary.json').write_text(json.dumps(summary,indent=2))
PY
exit "$status"
