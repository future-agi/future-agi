#!/usr/bin/env bash
# scripts/release/blast-radius-drill.sh
# The blast-radius drill (docs/gateway/security/blast-radius.md), against the
# local stack: two gateway replicas on one Redis, the mock upstream, the real
# binaries. It proves the revocation paths that can run here and prints the
# rest of the checklist as manual items, never as passed.
#
#   SCN_REDIS=127.0.0.1:16379 SCN_REDIS_DB=<your db> \
#     bash agentcc-gateway/scripts/release/blast-radius-drill.sh
#
# Automated:
#   1. a virtual key the control plane pushed (POST /-/keys/sync, as Django
#      does) works on both replicas
#   2. a virtual key cannot call an admin route; a wrong admin token gets 401
#   3. revoked on replica A (DELETE /-/keys/{id}, as Django does), the key gets
#      401 on A at once and on replica B within DRILL_MAX_MS (default 2000),
#      through Redis pub/sub; the measured time is printed
# Built on the scenario library (scripts/scenarios/lib.sh); SCN_* variables
# apply. Exit 0 when every automated item passes.
set -u
# shellcheck source-path=SCRIPTDIR source=../scenarios/lib.sh
source "$(dirname "$0")/../scenarios/lib.sh"
[ -n "${SCN_REDIS:-}" ] || scn_fail drill "SCN_REDIS is required: revocation reaches other replicas through Redis"
DRILL_MAX_MS=${DRILL_MAX_MS:-2000}
scn_up

# --- replica B: the same config on another port ---------------------------
GW_B="http://127.0.0.1:$(_scn_free_port)"
unset_env=()
for var in $(env | sed -n 's/^\(AGENTCC_[A-Za-z0-9_]*\)=.*/\1/p'); do unset_env+=(-u "$var"); done
env ${unset_env[@]+"${unset_env[@]}"} AGENTCC_PORT="${GW_B##*:}" \
  "$SCN_BIN/agentcc" --config "$SCN_DIR/config.yaml" >"$SCN_DIR/gateway-b.log" 2>&1 &
PID_B=$!
trap 'kill "$PID_B" 2>/dev/null; scn_down' EXIT
_scn_wait_http gateway-b "$GW_B/healthz" 30 "$PID_B"
echo "drill: replica A $SCN_GW, replica B $GW_B, Redis $SCN_REDIS db ${SCN_REDIS_DB:-0}"

now_ms() { python3 -I -c 'import time; print(int(time.time() * 1000))'; }
# status <gateway> <bearer> <method> <path> [json]
status() {
  local args=(-sS -o /dev/null -w '%{http_code}' -X "$3" "$1$4" -H "Authorization: Bearer $2")
  [ $# -ge 5 ] && args+=(-H 'Content-Type: application/json' --data-binary "$5")
  curl "${args[@]}"
}

# A key only this run knows: a unique id, since revocations reach every
# gateway on the Redis server (the channel is global).
RAW="drill-$(python3 -I -c 'import secrets; print(secrets.token_hex(16))')"
KEY_ID="drill-key-${RAW#drill-}"
HASH=$(printf '%s' "$RAW" | python3 -I -c 'import hashlib, sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')
SYNC=$(printf '{"keys":[{"id":"%s","name":"blast-radius-drill","key_hash":"%s","metadata":{"org_id":"%s"}}]}' \
  "$KEY_ID" "$HASH" "$SCN_ORG")

scn_check "1. a control-plane key works on both replicas"
for gw in "$SCN_GW" "$GW_B"; do
  scn_assert_eq "1.sync ${gw##*:}" 200 "$(status "$gw" "$SCN_ADMIN_TOKEN" POST /-/keys/sync "$SYNC")"
done
scn_assert_eq 1.key_on_A 200 "$(status "$SCN_GW" "$RAW" GET /v1/models)"
scn_assert_eq 1.key_on_B 200 "$(status "$GW_B" "$RAW" GET /v1/models)"

scn_check "2. a virtual key is not an admin credential"
scn_assert_eq 2.virtual_key_on_admin_route 401 "$(status "$SCN_GW" "$RAW" GET /-/keys)"
scn_assert_eq 2.wrong_admin_token 401 "$(status "$GW_B" "drill-wrong-admin-token" GET /-/keys)"

scn_check "3. revoked on replica A, the key stops on A at once and on B within ${DRILL_MAX_MS} ms"
start=$(now_ms)
scn_assert_eq 3.revoke_on_A 200 "$(status "$SCN_GW" "$SCN_ADMIN_TOKEN" DELETE "/-/keys/$KEY_ID")"
scn_assert_eq 3.key_on_A_after_revoke 401 "$(status "$SCN_GW" "$RAW" GET /v1/models)"
code_b=200
while :; do
  code_b=$(status "$GW_B" "$RAW" GET /v1/models)
  elapsed=$(($(now_ms) - start))
  if [ "$code_b" = 401 ] || [ "$elapsed" -gt "$DRILL_MAX_MS" ]; then break; fi
  sleep 0.02
done
scn_assert_eq 3.key_on_B_after_revoke 401 "$code_b"
echo "    revocation reached replica B in ${elapsed} ms (limit ${DRILL_MAX_MS} ms)"
scn_assert_eq 3.config_key_still_works_on_B 200 "$(status "$GW_B" "$SCN_KEY" GET /v1/models)"

cat <<EOF

=== manual items (docs/gateway/security/blast-radius.md; not run by this script)
- Redis unavailable at revoke time: confirm the revoked key is refused on every replica.
- Admin token rotation: restart every replica.
- Django: rotate AGENTCC_ADMIN_TOKEN, the Fernet key and provider credentials; see the Django section.
- Console session: log out or reset the password (both delete the cached session).
- Runtime token and delegated grant revocation: follow docs/gateway/security/blast-radius.md.
EOF
scn_pass
