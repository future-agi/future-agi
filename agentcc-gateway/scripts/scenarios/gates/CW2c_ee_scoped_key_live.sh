#!/usr/bin/env bash
# EE scoped-key gate check, converted to assert the OFF state. While
# the EE relay known issue holds, Omega EE's provisioning API is OFF, so this gate
# ASSERTS the OFF behaviour of the scoped-key path, through Django's real stack served by its dev server (the
# mounted routes, the auth classes, the middleware) as the gateway's control plane, the real gateway binary
# syncing keys and org configs from that Django every 2 s, and agentcc-mcpmock as the member server:
#
#   0. Django serves the gateway its key list.
#   1. An Owner asks for EE's credential, then a rotation: each answer is exactly 409 feature_unavailable_733
#      in Django's error envelope, no credential exists afterwards, and no provisioning state changed.
#   2. With a credential an earlier release issued (seeded in the database, as an upgraded deployment may
#      still hold one), EE's mint for workspace 1, its retry with the same Idempotency-Key, the mint
#      for workspace 2 and the ensure each get exactly the same 409; EE's find by Idempotency-Key lists no key;
#      no agent key, mint record, virtual server, gateway key, org config or EE audit row is made.
#   3. After two of the gateway's syncs, Django's key list (what the gateway loads) and the gateway's own
#      keys are unchanged: no scoped agent key reaches the gateway.
#
# The ON scenario (scoped agent keys synced with scopes and budget, scope and model refusals, the budget's
# 402, the cap, rotation and expiry, the audit trail, the idempotent mint) is NOT run, and its absence is never
# counted as a pass. The switch has no test configuration (broker_enabled() in
# futureagi/agentcc/services/mcp_service_provisioning.py answers False: OFF at launch, not an operator
# opt-in), and the agent keys the provisioning API mints have no person, which this gateway refuses. The ON scenario is OWED
# to the post-launch grant design; it is this path at bbaf95c973 (blob a90ea08bf4f7ddb3b3da63e727c865c2088bd691).
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<n> GATE_DJANGO_MANAGE=<manage.py wrapper> \
#     bash gates/CW2c_ee_scoped_key_live.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
export SCN_LANE=${SCN_LANE:-cn22c-ee-scoped-key}
# shellcheck source=/dev/null
source "$GATES_DIR/lib/django_live.sh"
# shellcheck source=/dev/null
source "$GATES_DIR/../lib_mcp.sh"
w3_need_django

export AGENTCC_GATEWAY_PUBLIC_URL=https://gateway.cw2c-live.example.com
export AGENTCC_GATEWAY_MCP_ENDPOINT=/mcp

# The OFF answer, exactly: build_error_envelope (tfc/utils/api_errors.py) of the provisioning OFF refusal
# (broker_off_error), a 409 with a string message and no field.
OFF_MESSAGE='Unavailable in this release: EE provisioning is disabled (KI-733-EE-RELAY).'
OFF_BODY=$(jq -cnS --arg m "$OFF_MESSAGE" \
  '{status: false, type: "conflict", code: "feature_unavailable_733", detail: $m, message: $m, error: $m, result: $m}')

# Spec section 3.7: every mint carries an Idempotency-Key, a UUIDv7 EE stores before the call.
UUID7_PY="
import os as _os, time as _time, uuid as _uuid
def uuid7():
    raw = bytearray(int(_time.time() * 1000).to_bytes(6, 'big') + _os.urandom(10))
    raw[6] = (raw[6] & 0x0F) | 0x70
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(_uuid.UUID(bytes=bytes(raw)))
"
# Full persisted rows, including mcp, all mutable columns and last_used_at;
# only field digests enter the evidence or a failing assertion's diagnostics.
OFF_PY=$(cat "$GATES_DIR/lib/ee_off_state.py") || scn_fail setup "cannot load OFF state snapshot"

scn_mcp_up a --path / --fixtures "$GATES_DIR/../fixtures/M9/a"
DJ_PORT=$(_scn_free_port)
DJ_URL=http://127.0.0.1:$DJ_PORT
mkdir -p "$SCN_BIN"
TEMPLATE=$SCN_BIN/cn22c-live.yaml
sed -e "s/@@CN22C_A_PORT@@/$SCN_MCP_A_PORT/" -e "s#@@DJANGO_URL@@#$DJ_URL#" \
  "$GATES_DIR/fixtures/CW2c/ee-scoped-key-live.yaml" >"$TEMPLATE"
grep -q '@@CN22C_\|@@DJANGO_' "$TEMPLATE" && scn_fail setup "unrendered placeholders in $TEMPLATE"
scn_mcp_gateway_template "$TEMPLATE"
SCN_CATALOG_WAIT_SKIP=all scn_up
bs_django_up

# Django's dev server: the gateway's control plane for this run (keys, org configs, refusals).
DJ_PID=""
dj_down() { [ -n "$DJ_PID" ] && kill "$DJ_PID" 2>/dev/null; DJ_PID=""; }
trap 'dj_down; _scn_mcp_down; scn_down' EXIT
env AGENTCC_GATEWAY_INTERNAL_URL="$SCN_GW" AGENTCC_ADMIN_TOKEN="$SCN_ADMIN_TOKEN" NO_STARTUP_DB_MUTATIONS=false \
  bash "$GATE_DJANGO_MANAGE" runserver "127.0.0.1:$DJ_PORT" --noreload >"$SCN_DIR/django-server.log" 2>&1 &
DJ_PID=$!
deadline=$(($(date +%s) + 120))
until curl -sS -o /dev/null "$DJ_URL/agentcc/api-keys/bulk/" 2>/dev/null; do
  kill -0 "$DJ_PID" 2>/dev/null || scn_fail setup "Django's dev server exited: $(tail -20 "$SCN_DIR/django-server.log")"
  [ "$(date +%s)" -ge "$deadline" ] && scn_fail setup "Django's dev server did not answer in 120 s"
  sleep 1
done
bulk() { # Django's key list as the gateway loads it, into $SCN_DIR/bulk.json; prints the HTTP status
  curl -sS -o "$SCN_DIR/bulk.json" -w '%{http_code}' -H "Authorization: Bearer $SCN_ADMIN_TOKEN" "$DJ_URL/agentcc/api-keys/bulk/"
}
bulk_state() { # the listed key ids and how many keys Django has ever stored
  jq -cS '{ids: ([.result[] | .id] | sort), issued_ever: .snapshot.issued_ever}' <"$SCN_DIR/bulk.json"
}
gateway_key_ids() { # the gateway's key ids, sorted, from the last GET /-/keys
  jq -cS '[.data[] | .id] | sort' <"$SCN_DIR/last.body"
}
scn_assert_eq 0.django_serves_the_key_list_to_the_gateway 200 "$(bulk)"

bs_org "CW2c CN22c OFF live $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG=$BS_ORG

# sync_wait: two of the gateway's 2 s key and config syncs.
sync_wait() { sleep 5; }

scn_check "1. an Owner asks for EE's credential, then a rotation: exactly 409 feature_unavailable_733, nothing made"
bs_django_py owner "$OFF_PY
import secrets
from rest_framework.test import APIClient
from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.services.org_config_writer import write_config_version
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
org = Organization.objects.get(pk=BS_ARGS[0])
owner = User.objects.create_user(email=f'cn22c-off-{secrets.token_hex(4)}@example.com',
    password=secrets.token_urlsafe(16), name='CN22c OFF Owner', organization=org,
    organization_role=OrganizationRoles.OWNER)
membership = OrganizationMembership.no_workspace_objects.create(user=owner, organization=org,
    role=OrganizationRoles.OWNER, level=Level.OWNER, is_active=True)
spaces = []
for name, default in (('Engineering', True), ('Support', False)):
    ws = Workspace.no_workspace_objects.create(name=name, organization=org, is_default=default,
        is_active=True, created_by=owner)
    WorkspaceMembership.no_workspace_objects.create(user=owner, workspace=ws, role='Workspace Owner',
        level=Level.OWNER, organization_membership=membership, is_active=True)
    spaces.append(ws)
from agentcc.models.provider_credential import AgentccProviderCredential
from integrations.services.credentials import CredentialManager
# The org's own key on the operator provider (BYOK), as the console stores it; the push assembles providers.
AgentccProviderCredential.no_workspace_objects.create(organization=org, provider_name='operator-openai',
    encrypted_credentials=CredentialManager.encrypt({'api_key': 'scn-test-org-byok-key'}),
    api_format='openai', models_list=['gpt-4o'])
write_config_version(org, {
    'mcp': {'enabled': True, 'servers': {'github': {'url': BS_ARGS[1], 'transport': 'http'}}},
}, description='CW2c CN22 OFF live run: the member server')
def bearer(user):
    from django.utils import timezone
    from accounts.authentication import generate_encrypted_message
    from accounts.models.auth_token import AuthToken, AuthTokenType
    access = AuthToken.objects.create(user=user, auth_type=AuthTokenType.ACCESS.value, is_active=True,
        last_used_at=timezone.now())
    token = generate_encrypted_message({'user_id': str(user.id), 'id': str(access.id)})
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
    return c
person = bearer(owner)
before = ee_state(org.pk)
issued = answer(person.post('/agentcc/mcp/service/credential/', {}, format='json'))
rotated = answer(person.post('/agentcc/mcp/service/credential/rotate/', {}, format='json'))
bs_out({'owner': str(owner.id), 'workspaces': [str(w.id) for w in spaces], 'issued': issued,
    'rotated': rotated, 'before': before, 'after': ee_state(org.pk)})" "$ORG" "$SCN_MCP_A_URL/mcp"
P=$SCN_DIR/bs-owner.json
scn_assert_eq 1.credential_issue_status 409 "$(jq -r .issued.status "$P")"
scn_assert_eq 1.credential_issue_body "$OFF_BODY" "$(jq -cS .issued.body "$P")"
scn_assert_eq 1.no_credential_issued 0 "$(jq '.after.credentials | length' "$P")"
scn_assert_eq 1.rotate_status 409 "$(jq -r .rotated.status "$P")"
scn_assert_eq 1.rotate_body "$OFF_BODY" "$(jq -cS .rotated.body "$P")"
scn_assert_eq 1.no_state_change "$(jq -cS .before "$P")" "$(jq -cS .after "$P")"

# The keys Django lists and the gateway holds once the org and its config are in place (two syncs).
sync_wait
scn_assert_eq 1.django_key_list 200 "$(bulk)"
BULK_BEFORE=$(bulk_state)
scn_assert_eq 1.gateway_key_list 200 "$(scn_admin GET /-/keys)"
GW_KEYS_BEFORE=$(gateway_key_ids)

scn_check "2. a credential from an earlier release: EE's mints, its replay and its ensure are exactly the same 409"
bs_django_py ee "$UUID7_PY$OFF_PY
import hashlib, secrets
from rest_framework.test import APIClient
from accounts.models.organization import Organization
from agentcc.models.mcp_service_provisioning import AgentccEEServiceCredential
org = Organization.objects.get(pk=BS_ARGS[0])
from django.utils import timezone
secret = secrets.token_urlsafe(32)
# Fresh auth bookkeeping stays within LAST_USED_RESOLUTION during these OFF calls,
# so even last_used_at can be compared exactly rather than omitted.
seeded = AgentccEEServiceCredential.objects.create(organization=org, api_key='ees_' + secrets.token_hex(16),
    secret_hash=hashlib.sha256(secret.encode()).hexdigest(), last_used_at=timezone.now())
before = ee_state(org.pk)
ee = APIClient()
ee.credentials(HTTP_X_API_KEY=seeded.api_key, HTTP_X_SECRET_KEY=secret)
url = '/agentcc/mcp/service/agent-keys/'
key = uuid7()
first = answer(ee.post(url, {'workspace': BS_ARGS[1]}, format='json', HTTP_IDEMPOTENCY_KEY=key))
replay = answer(ee.post(url, {'workspace': BS_ARGS[1]}, format='json', HTTP_IDEMPOTENCY_KEY=key))
other = answer(ee.post(url, {'workspace': BS_ARGS[2]}, format='json', HTTP_IDEMPOTENCY_KEY=uuid7()))
ensure = answer(ee.post('/agentcc/mcp/service/virtual-servers/ensure/', {'workspace': BS_ARGS[1],
    'connection_id': 'cw2c-live-1', 'servers': ['github'], 'mode': 'agent',
    'agent_key_id': 'key_' + secrets.token_hex(12)}, format='json'))
found = answer(ee.get(f'/agentcc/mcp/service/workspaces/{BS_ARGS[1]}/agent-keys/', {'mint_idempotency_key': key}))
bs_out({'first': first, 'replay': replay, 'other': other, 'ensure': ensure, 'found': found,
    'before': before, 'after': ee_state(org.pk)})" \
  "$ORG" "$(jq -r '.workspaces[0]' "$P")" "$(jq -r '.workspaces[1]' "$P")"
E=$SCN_DIR/bs-ee.json
for call in first replay other ensure; do
  scn_assert_eq "2.${call}_status" 409 "$(jq -r ".$call.status" "$E")"
  scn_assert_eq "2.${call}_body" "$OFF_BODY" "$(jq -cS ".$call.body" "$E")"
done
scn_assert_eq 2.find_by_idempotency_key 200 "$(jq -r .found.status "$E")"
scn_assert_eq 2.find_lists_no_key 0 "$(jq '.found.body.result.keys | length' "$E")"
scn_assert_eq 2.no_agent_key_minted 0 "$(jq '.after.agent_keys | length' "$E")"
scn_assert_eq 2.no_mint_record 0 "$(jq '.after.mint_records | length' "$E")"
scn_assert_eq 2.no_state_change "$(jq -cS .before "$E")" "$(jq -cS .after "$E")"

scn_check "3. after two syncs no scoped key reaches the gateway: Django's list and the gateway's keys are unchanged"
sync_wait
scn_assert_eq 3.django_key_list 200 "$(bulk)"
scn_assert_eq 3.django_key_list_unchanged "$BULK_BEFORE" "$(bulk_state)"
scn_assert_eq 3.gateway_key_list 200 "$(scn_admin GET /-/keys)"
scn_assert_eq 3.gateway_keys_unchanged "$GW_KEYS_BEFORE" "$(gateway_key_ids)"
scn_pass
