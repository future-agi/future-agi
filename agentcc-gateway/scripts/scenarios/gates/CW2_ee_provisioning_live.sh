#!/usr/bin/env bash
# EE provisioning gate check, converted to assert the OFF state. While
# the EE relay known issue holds, Omega EE's provisioning API is OFF, so
# this gate ASSERTS the OFF behaviour, through Django's real stack (the mounted routes, the auth classes, the
# views) and the real gateway binary, with agentcc-mcpmock as the org's member server, as the ON run had them:
#
#   1. An Owner asks for EE's service credential (POST /agentcc/mcp/service/credential/, as a person), then
#      for a rotation: each answer is exactly 409 feature_unavailable_733 in Django's error envelope, no
#      credential exists afterwards, and no provisioning state changed.
#   2. A credential an earlier release issued (seeded in the database, as an upgraded deployment may still
#      hold one) authenticates, but EE's mint for each workspace and its ensure each get exactly the same
#      409: no agent key, mint record, virtual server, gateway key, org config or EE audit row is made.
#   3. The gateway's key list is unchanged and it holds no virtual server for the org.
#
# The ON scenario (EE mints agent keys and ensures a virtual server in agent mode, the agent key lists and
# calls the mock's tools, other keys get the virtual server's 404, detach, revoke) is NOT run, and its
# absence is never counted as a pass. The switch has no test configuration (broker_enabled() in
# futureagi/agentcc/services/mcp_service_provisioning.py answers False: OFF at launch, not an operator
# opt-in), and the agent keys the provisioning API mints have no person, which this gateway refuses. The ON scenario is OWED
# to the post-launch grant design; it is this path at bbaf95c973 (blob adb037eac44ce0b8019ea10ab18587598c698fa7).
#
#   SCN_REDIS=<host:port> SCN_REDIS_DB=<n> GATE_DJANGO_MANAGE=<manage.py wrapper> \
#     bash gates/CW2_ee_provisioning_live.sh
set -u
GATES_DIR=$(cd "$(dirname "$0")" && pwd)
export SCN_LANE=${SCN_LANE:-x6-cw2-ee-provisioning}
# shellcheck source=/dev/null
source "$GATES_DIR/lib/django_live.sh"
# shellcheck source=/dev/null
source "$GATES_DIR/../lib_mcp.sh"
w3_need_django

# The url an ensure would answer is built on this origin (rule 8: public HTTPS). A fake public origin,
# never contacted.
export AGENTCC_GATEWAY_PUBLIC_URL=https://gateway.cw2-live.example.com
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
gateway_key_ids() { # the gateway's key ids, sorted, from the last GET /-/keys
  jq -cS '[.data[] | .id] | sort' <"$SCN_DIR/last.body"
}

scn_mcp_up a --path / --fixtures "$GATES_DIR/../fixtures/M9/a"
mkdir -p "$SCN_BIN"
TEMPLATE=$SCN_BIN/cn22-live.yaml
sed -e "s/@@CN22_A_PORT@@/$SCN_MCP_A_PORT/" "$GATES_DIR/fixtures/CW2/ee-provisioning-live.yaml" >"$TEMPLATE"
grep -q '@@CN22_' "$TEMPLATE" && scn_fail setup "unrendered placeholders in $TEMPLATE"
scn_mcp_gateway_template "$TEMPLATE"
SCN_CATALOG_WAIT_SKIP=all scn_up
bs_django_up

bs_org "CW2 CN22 OFF live $(date -u +%Y-%m-%dT%H:%M:%SZ)"
ORG=$BS_ORG
scn_assert_eq 0.gateway_key_list 200 "$(scn_admin GET /-/keys)"
GW_KEYS_BEFORE=$(gateway_key_ids)

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
owner = User.objects.create_user(email=f'cn22-off-{secrets.token_hex(4)}@example.com',
    password=secrets.token_urlsafe(16), name='CN22 OFF Owner', organization=org,
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
write_config_version(org, {'mcp': {'enabled': True, 'servers': {'github': {'url': BS_ARGS[1],
    'transport': 'http'}}}}, description='CW2 CN22 OFF live run: the member server')
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

scn_check "2. a credential from an earlier release: EE's mints and ensure are exactly the same 409, nothing made"
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
mints = [answer(ee.post('/agentcc/mcp/service/agent-keys/', {'workspace': ws}, format='json',
    HTTP_IDEMPOTENCY_KEY=uuid7())) for ws in BS_ARGS[1:3]]
ensure = answer(ee.post('/agentcc/mcp/service/virtual-servers/ensure/', {'workspace': BS_ARGS[1],
    'connection_id': 'cw2-live-1', 'servers': ['github'], 'mode': 'agent',
    'agent_key_id': 'key_' + secrets.token_hex(12)}, format='json'))
bs_out({'mints': mints, 'ensure': ensure, 'before': before, 'after': ee_state(org.pk)})" \
  "$ORG" "$(jq -r '.workspaces[0]' "$P")" "$(jq -r '.workspaces[1]' "$P")"
E=$SCN_DIR/bs-ee.json
scn_assert_eq 2.mint_workspace_1_status 409 "$(jq -r '.mints[0].status' "$E")"
scn_assert_eq 2.mint_workspace_1_body "$OFF_BODY" "$(jq -cS '.mints[0].body' "$E")"
scn_assert_eq 2.mint_workspace_2_status 409 "$(jq -r '.mints[1].status' "$E")"
scn_assert_eq 2.mint_workspace_2_body "$OFF_BODY" "$(jq -cS '.mints[1].body' "$E")"
scn_assert_eq 2.ensure_status 409 "$(jq -r .ensure.status "$E")"
scn_assert_eq 2.ensure_body "$OFF_BODY" "$(jq -cS .ensure.body "$E")"
scn_assert_eq 2.no_agent_key_minted 0 "$(jq '.after.agent_keys | length' "$E")"
scn_assert_eq 2.no_virtual_server 0 "$(jq '.after.virtual_servers | length' "$E")"
scn_assert_eq 2.no_state_change "$(jq -cS .before "$E")" "$(jq -cS .after "$E")"

scn_check "3. the gateway's keys are unchanged and it holds no virtual server for the org"
scn_assert_eq 3.gateway_key_list 200 "$(scn_admin GET /-/keys)"
scn_assert_eq 3.gateway_keys_unchanged "$GW_KEYS_BEFORE" "$(gateway_key_ids)"
scn_assert_eq 3.org_config 200 "$(scn_admin GET "/-/orgs/$ORG/config")"
scn_assert_jq 3.gateway_holds_no_virtual_server '(.mcp.virtual_servers // {}) | length' 0
scn_pass
