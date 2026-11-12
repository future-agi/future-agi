"""Checks that tie the omega-ee-attach/v1 fixtures to their sources.

- code_checks: the gateway and Django code the fixtures copy (constants, JSON field names, error shapes)
  still says what the fixtures say. Paths are relative to the repository root.
- typed_results: the names the attach contract lists, extracted by the rule in typed-results.json.
- doc_table_names: the first column of the spec's marked typed-results table.
- sums: SHA256SUMS against the files, and the set digest the spec states.
Each check returns a list of (name, True | message).
"""

import hashlib
import json
import os
import re

GW = "agentcc-gateway/internal/"
FIXTURES = "agentcc-gateway/scripts/scenarios/fixtures/omega-ee-attach"
DOC_DIGEST = re.compile(r"Set digest: `sha256:([0-9a-f]{64})`")
TABLE = re.compile(r"<!-- typed-results:begin -->(.*?)<!-- typed-results:end -->", re.S)
UNKNOWN_KEY_MESSAGE = (
    "This gateway API key is not recognized: it may be mistyped, revoked or deleted. "
    "Check it on the Gateway API Keys page, or create a new one there."
)

# (name, file, regex that must match). Each line of the spec that says "the gateway does X" for a built
# part rests on one of these.
CODE_PATTERNS = [
    (
        "meta keys are io.futureagi/error and io.futureagi/tool_call",
        GW + "mcp/denial.go",
        r'MetaKeyError\s*=\s*"io\.futureagi/error"\s*\n\s*MetaKeyToolCall\s*=\s*"io\.futureagi/tool_call"',
    ),
    (
        "a denial's text is '<message> [<code>]'",
        GW + "mcp/denial.go",
        r'return d\.Message \+ " \[" \+ d\.Code \+ "\]".*Content:\s*\[\]ContentPart\{\{Type: "text", Text: d\.Error\(\)\}\}',
    ),
    (
        "upstream io.futureagi/* _meta is stripped before EE sees a result",
        GW + "mcp/respond.go",
        r"return protocol\.StripGatewayMeta\(raw\), nil",
    ),
    (
        "exposed names join with __ and are capped at 64",
        GW + "mcp/vserver/names.go",
        r'const Separator = "__".*MaxNameLen\s*=\s*64',
    ),
    (
        "exposed names keep only [A-Za-z0-9_.-]",
        GW + "mcp/vserver/names.go",
        r"case c >= 'a' && c <= 'z', c >= 'A' && c <= 'Z', c >= '0' && c <= '9', c == '_', c == '\.', c == '-':",
    ),
    (
        "prefixed names are <member key>__<tool>",
        GW + "mcp/vserver/compose.go",
        r"return EncodePrefixed\(t\.Member\.Key\(\), t\.Name\)",
    ),
    (
        "the virtual-server route is {endpoint}/v/{vs_id}",
        GW + "server/routes_mcp_vs.go",
        r'path := mcpEndpoint\(s\.cfg\) \+ "/v/\{vs_id\}"',
    ),
    (
        "an unknown or foreign virtual server is 404 -32600 'virtual server not found'",
        GW + "mcp/hook_vserver.go",
        r'Code: "virtual_server_not_found", Message: "virtual server not found",\s*RPCCode: ErrCodeInvalidRequest, HTTPStatus: http\.StatusNotFound',
    ),
    (
        "JSON-RPC invalid request is -32600",
        GW + "mcp/types.go",
        r"ErrCodeInvalidRequest\s*=\s*-32600",
    ),
    (
        "an MCP 401 is -32001",
        GW + "mcp/principal.go",
        r"const ErrCodeUnauthorized = -32001",
    ),
    (
        "a 401 carries WWW-Authenticate and data.code",
        GW + "mcp/principal.go",
        r'h\.Set\("WWW-Authenticate", bearerChallenge\(s\.resourceMetadataURL\(r\)\)\)\s*\n\s*data, _ := json\.Marshal\(map\[string\]string\{"code": code\}\)',
    ),
    (
        'the 401 challenge is Bearer realm="agentcc-gateway"',
        GW + "models/errors.go",
        r'const BearerChallenge = `Bearer realm="agentcc-gateway"`',
    ),
    (
        "with request auth on, the key gate covers the MCP endpoint",
        GW + "server/server.go",
        r"keyGatedRoots = append\(keyGatedRoots, mcpEndpoint\(cfg\)\).*"
        r"handler = middleware\.KeyAuthScoped\(authKeyStore, authEnabled, keyGateIPACL\(cfg, tenantStore\), s\.keyScopes\.gate\(\), keyGatedRoots\.\.\.\)\(handler\)",
    ),
    (
        "the key gate hands missing/unknown keys to MCP sign-in or returns 401, after pending-key 503",
        GW + "middleware/auth.go",
        r'apiKey := auth\.DelegatedPrincipal\(r\.Context\(\)\)\s*'
        r'if apiKey == nil && \(IsLicenseAuthorized\(r\.Context\(\)\) \|\| !authEnabled \|\| keyStore == nil\) \{\s*'
        r'next\.ServeHTTP\(w, r\)\s*return\s*\}\s*rawKey := ""\s*if apiKey == nil \{\s*'
        r'rawKey = gatedAPIKey\(r\)\s*if rawKey != "" \{\s*(?://[^\n]*\n\s*)*'
        r'if keyStore\.ConfigKeyPending\(rawKey\) \{\s*'
        r'writeGateError\(w, r, &models\.APIError\{Status: http\.StatusServiceUnavailable,\s*'
        r'Type: models\.ErrTypeServer, Code: "key_list_not_loaded", Message: "[^"\n]*"\}\)\s*'
        r'return\s*\}\s*apiKey = keyStore\.Authenticate\(rawKey\)\s*\}\s*\}\s*'
        r'if apiKey == nil \{\s*(?://[^\n]*\n\s*)*if scopes\.signsInOwnCallers\(r\.URL\.Path\) \{\s*'
        r'check := func\(orgID string\) \*models\.APIError \{\s*'
        r'return ipACL\.CheckClient\(clientIP, orgID, nil, requestID\)\s*\}\s*'
        r'next\.ServeHTTP\(w, r\.WithContext\(context\.WithValue\(r\.Context\(\), orgAddressCheckKey\{\}, check\)\)\)\s*'
        r'return\s*\}\s*writeGateError\(w, r, ErrUnauthorizedKeyFor\(r\.URL\.Path, rawKey\)\)\s*return\s*\}',
    ),
    (
        "MCP sign-in handoff requires its flag and the canonical MCP subtree",
        GW + "middleware/auth.go",
        r'func \(s KeyScopes\) signsInOwnCallers\(p string\) bool \{\s*'
        r'if !s\.MCPSignIn \|\| s\.MCPRoot == "" \{\s*return false\s*\}\s*'
        r'root, c := httppath\.Canonical\(s\.MCPRoot\), httppath\.Canonical\(p\)\s*'
        r'return c == root \|\| strings\.HasPrefix\(c, root\+"/"\)\s*\}',
    ),
    (
        "MCP sign-in handoff is wired only with MCP and both client OAuth flags enabled",
        GW + "server/key_scope.go",
        r'if cfg\.MCP\.Enabled \{\s*st\.mcpRoot = mcpEndpoint\(cfg\)\s*'
        r'st\.mcpSignIn = cfg\.MCP\.ClientOAuthEnabled && cfg\.MCP\.OAuth\.Enabled\s*\}\s*return st\s*\}'
        r'.*?func \(st \*keyScopeState\) gate\(\) middleware\.KeyScopes \{\s*'
        r'return middleware\.KeyScopes\{MCPRoot: st\.mcpRoot, Refusals: st\.reporter, MCPSignIn: st\.mcpSignIn\}\s*\}',
    ),
    (
        "a non-Messages path, including MCP, keeps the model-path error writer",
        GW + "middleware/auth_errors.go",
        r"func writeGateError\(w http\.ResponseWriter, r \*http\.Request, apiErr \*models\.APIError\) \{\s*"
        r"if !isMessagesPath\(r\.URL\.Path\) \{\s*models\.WriteError\(w, apiErr\)\s*return\s*\}",
    ),
    (
        "only the canonical Messages subtree uses the alternate error shape",
        GW + "middleware/auth_errors.go",
        r'func isMessagesPath\(p string\) bool \{\s*c := httppath\.Canonical\(p\)\s*'
        r'return c == "/v1/messages" \|\| strings\.HasPrefix\(c, "/v1/messages/"\)\s*\}',
    ),
    (
        "unknown gateway keys retain the unknown reason and exact message",
        GW + "middleware/auth_errors.go",
        r'ReasonUnknownAPIKey\s*=\s*"unknown_api_key".*'
        r'msgUnknownAPIKey = "This gateway API key is not recognized: it may be mistyped, revoked or deleted\. " \+\s*'
        r'"Check it on the Gateway API Keys page, or create a new one there\."',
    ),
    (
        "the auth helper classifies keys and preserves the unknown gateway-key case",
        GW + "middleware/auth_errors.go",
        r'func ErrUnauthorizedKey\(rawKey string\) \*models\.APIError \{\s*'
        r'reason, msg := ReasonUnknownAPIKey, msgUnknownAPIKey\s*'
        r'switch key := strings\.TrimSpace\(rawKey\); \{\s*'
        r'case key == "":\s*reason, msg = ReasonMissingAPIKey, msgMissingAPIKey\s*'
        r'case strings\.HasPrefix\(key, gatewayKeyPrefix\):\s*'
        r'// A gateway key the gateway does not accept: unknown or revoked alike\.\s*'
        r'case looksLikeFutureAGIKey\(key\):\s*reason, msg = ReasonFutureAGIAPIKey, msgFutureAGIAPIKey\s*'
        r'default:\s*reason, msg = ReasonNotAGatewayKey, msgNotAGatewayKey\s*\}\s*'
        r'apiErr := models\.ErrUnauthorized\(msg\)\s*'
        r'apiErr\.Metadata = map\[string\]string\{"reason": reason\}\s*return apiErr\s*\}',
    ),
    (
        "the path-aware auth helper changes only the missing GenAI-key message",
        GW + "middleware/auth_errors.go",
        r'func ErrUnauthorizedKeyFor\(p, rawKey string\) \*models\.APIError \{\s*'
        r'apiErr := ErrUnauthorizedKey\(rawKey\)\s*'
        r'if strings\.TrimSpace\(rawKey\) == "" && isGenAIPath\(p\) \{\s*'
        r'apiErr\.Message = msgMissingGenAIKey\s*\}\s*return apiErr\s*\}',
    ),
    (
        "the model-path writer preserves message, type, code, param and metadata",
        GW + "models/errors.go",
        r'func WriteError\(w http\.ResponseWriter, err \*APIError\) \{\s*'
        r'resp := ErrorResponse\{\s*Error: ErrorDetail\{\s*'
        r'Message:\s*RedactCredentials\(err\.Message\),\s*Type:\s*err\.Type,\s*'
        r'Code:\s*err\.Code,\s*Param:\s*err\.Param,\s*Metadata:\s*err\.Metadata,\s*\},\s*\}',
    ),
    (
        "the model-path writer sends JSON with the error's HTTP status",
        GW + "models/errors.go",
        r'w\.Header\(\)\.Set\("Content-Type", "application/json"\)\s*'
        r'if err\.Status == http\.StatusUnauthorized && w\.Header\(\)\.Get\("WWW-Authenticate"\) == "" \{\s*'
        r'w\.Header\(\)\.Set\("WWW-Authenticate", BearerChallenge\)\s*\}\s*'
        r'w\.WriteHeader\(err\.Status\)\s*json\.NewEncoder\(w\)\.Encode\(resp\)',
    ),
    (
        "ErrUnauthorized is 401 authentication_error invalid_api_key",
        GW + "models/errors.go",
        r'return newAPIError\(http\.StatusUnauthorized, ErrTypeAuthentication, "invalid_api_key", message\)',
    ),
    (
        "newAPIError keeps the status, type and code it is given (the release line redacts the message)",
        GW + "models/errors.go",
        r"func newAPIError\(status int, errType, code, message string\) \*APIError \{\s*\n\s*"
        r"return &APIError\{Status: status, Type: errType, Code: code, Message: RedactCredentials\(message\)\}",
    ),
    (
        "the model path's 401 carries the Bearer challenge",
        GW + "models/errors.go",
        r'if err\.Status == http\.StatusUnauthorized && w\.Header\(\)\.Get\("WWW-Authenticate"\) == "" \{\s*\n\s*w\.Header\(\)\.Set\("WWW-Authenticate", BearerChallenge\)',
    ),
    (
        "a refused caller gets id null",
        GW + "mcp/http_auth.go",
        r'writeRPCDenial\(w, json\.RawMessage\("null"\), s\.unauthorized\(r, fail\.code, fail\.message\)\)',
    ),
    (
        "the caller key is the Authorization bearer, else x-api-key",
        GW + "mcp/http_auth.go",
        r'strings\.HasPrefix\(auth, "Bearer "\).*return r\.Header\.Get\("x-api-key"\)',
    ),
    (
        "tool_denied names rule and virtual_server",
        GW + "mcp/hook_acl.go",
        r'meta := map\[string\]any\{"rule": rule\}\s*\n\s*if cc\.VirtualServer != "" \{\s*\n\s*meta\["virtual_server"\] = cc\.VirtualServer',
    ),
    (
        "tool_call_id is tc_<ULID>",
        GW + "mcp/hook_meter.go",
        r'cc\.ToolCallID = "tc_" \+ ulid\.Make\(\)\.String\(\)',
    ),
    (
        "gateway key ids are key_ + 24 hex",
        GW + "auth/keystore.go",
        r'func newKeyID\(\) string \{\s*\n\s*b := make\(\[\]byte, 12\).*?return "key_" \+ hex\.EncodeToString\(b\)',
    ),
    (
        "a key principal copies the key store's trusted person identity",
        GW + "mcp/principal.go",
        r'func \(ia \*identityAuth\) keyPrincipal\(key \*auth\.APIKey\) Principal \{\s*'
        r'id := ia\.keys\.Identity\(key\)\s*'
        r'return Principal\{Kind: PrincipalKey, KeyID: id\.KeyID, OrgID: id\.OrgID, UserID: id\.UserID\}\s*\}',
    ),
    (
        "only sync or delegated keys with canonical user_id supply a person identity",
        GW + "auth/keystore.go",
        r'func \(ks \*KeyStore\) Identity\(key \*APIKey\) Identity \{\s*'
        r'ks\.mu\.RLock\(\)\s*defer ks\.mu\.RUnlock\(\)\s*'
        r'id := Identity\{KeyID: key\.ID, OrgID: key\.Metadata\["org_id"\]\}\s*(?://[^\n]*\n\s*)*'
        r'if key\.Source == "sync" \|\| key\.Source == "delegated" \{\s*'
        r'if user := key\.Metadata\["user_id"\]; uuidRE\.MatchString\(user\) \{\s*'
        r'id\.UserID = user\s*\}\s*\}\s*return id\s*\}',
    ),
    (
        "trusted key person IDs use the canonical lowercase UUID syntax",
        GW + "auth/envelope.go",
        r'var uuidRE = regexp\.MustCompile\(`\^\[0-9a-f\]\{8\}-\[0-9a-f\]\{4\}-'
        r'\[0-9a-f\]\{4\}-\[0-9a-f\]\{4\}-\[0-9a-f\]\{12\}\$`\)',
    ),
    (
        "server credential modes include none, shared and per_user",
        GW + "tenant/config_mcp.go",
        r"Mode\s+string `json:\"mode,omitempty\"` // none \(default\), shared, per_user",
    ),
    (
        "the gateway's default MCP protocol versions include 2025-03-26 and 2025-06-18",
        GW + "config/config.go",
        r'return \[\]string\{"2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"\}',
    ),
    (
        "a request without MCP-Protocol-Version is served as 2025-03-26",
        GW + "mcp/protocol/era.go",
        r"const DefaultLegacyVersion = Version20250326",
    ),
    (
        "a session the gateway does not hold for the caller is a bare 404 (validateSession)",
        GW + "mcp/method_initialize.go",
        r"func \(s \*Server\) validateSession\(.*?if session == nil \{\s*\n\s*w\.WriteHeader\(http\.StatusNotFound\)\s*\n\s*return nil",
    ),
    (
        "tools/call and tools/list check the session before the chain, so its 404 comes first",
        GW + "mcp/chain.go",
        r"func \(s \*Server\) runToolsCall\(w http\.ResponseWriter, r \*http\.Request, msg \*Message\) \{\s*\n\s*session := s\.validateSession\(w, r, msg\).*"
        r"func \(s \*Server\) runListOrRead\(.*?\{\s*\n\s*session := s\.validateSession\(w, r, msg\)",
    ),
    (
        "a session is bound to the principal (kind, key, user, client, org) and the virtual server",
        GW + "mcp/session.go",
        r"type sessionBinding struct \{\s*Kind\s+PrincipalKind\s+KeyID\s+string\s+UserID\s+string\s+ClientID\s+string\s+"
        r"OrgID\s+string\s+KeyHash\s+string\s+VirtualServer\s+string\s*\}.*"
        r"if json\.Unmarshal\(data, &rec\) != nil \|\| rec\.binding\(\) != b \{\s*\n\s*return nil, nil",
    ),
    (
        "a session id lasts at most 24 hours however active",
        GW + "mcp/session.go",
        r"const sessionTokenLifetime = 24 \* time\.Hour",
    ),
    (
        "an idle session ends after mcp.session_ttl, 30 minutes by default",
        GW + "config/config.go",
        r"SessionTTL:\s+30 \* time\.Minute,",
    ),
    (
        "the key gate applies the IP rules before and after the key through writeGateError",
        GW + "middleware/auth.go",
        r'if err := ipACL\.CheckClient\(clientIP, "", nil, requestID\); err != nil \{\s*\n\s*writeGateError\(w, r, err\)\s*\n\s*return.*'
        r"if err := ipACL\.CheckClient\(clientIP, identity\.OrgID, apiKey\.AllowedIPs, requestID\); err != nil \{\s*\n\s*writeGateError\(w, r, err\)\s*\n\s*return",
    ),
    (
        "with the gateway's ip_acl off, only the key's allowed_ips apply",
        GW + "plugins/ipacl/ipacl.go",
        r"func \(p \*Plugin\) CheckClient\(.*?\{\s*\n\s*if p != nil \{.*?return CheckKeyIP\(ip, keyAllowedIPs\)",
    ),
    (
        "ErrForbidden is 403 permission_error permission_denied",
        GW + "models/errors.go",
        r'return newAPIError\(http\.StatusForbidden, ErrTypePermission, "permission_denied", message\)',
    ),
    (
        "allowed_ips reach a key only from static config; Django sets none",
        GW + "middleware/clientip.go",
        r"Django sets no\s*\n//\s*allowed_ips\.",
    ),
    (
        "the key gate applies an org's ip_acl while the gateway's is on, or with it off where org configs arrive and client IPs resolve",
        GW + "server/keygate_ipacl.go",
        r"case cfg\.IPACL\.Enabled:\s*\n\s*return ipacl\.New\(cfg\.IPACL, tenantStore\)\s*\n\s*"
        r"case tenantStore != nil && middleware\.OrgIPACLWithoutNodeSwitch\(cfg\):\s*\n\s*return ipacl\.New\(config\.IPACLConfig\{\}, tenantStore\)",
    ),
    (
        "a static config key gets a legacy id key_<n>",
        GW + "auth/keystore.go",
        r'id := fmt\.Sprintf\("key_%d", ks\.counter\)',
    ),
    (
        "gateways before random key ids reissued ids after a restart",
        "futureagi/agentcc/services/auth_bridge.py",
        r"Gateways before random key IDs reissued IDs after a restart",
    ),
    (
        "access lists match keys, agents and users, with no workspace dimension",
        GW + "mcp/vserver/access.go",
        r"type Principal struct \{\s*KeyID\s+string\s+AgentID\s+string\s+UserID\s+string\s*\}.*"
        r"return listed\(a\.Keys, p\.KeyID\) \|\| listed\(a\.Agents, p\.AgentID\) \|\| listed\(a\.Users, p\.UserID\)",
    ),
    (
        "access rules are judged on the addressed virtual server only",
        GW + "mcp/hook_acl.go",
        r"vserver\.DeniedByRules\(v\.vs\.Access\.Rules, principalOf\(cc\)",
    ),
    (
        "Django members create and remove their own keys; update, partial_update and sync stay admin-only",
        "futureagi/agentcc/views/api_key.py",
        r'class AgentccAPIKeyViewSet\(\s*GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet\s*\):\s*'
        r'permission_classes = \[IsAuthenticated, PersonalGatewayKeyAccess\]\s*(?:#[^\n]*\n\s*)*'
        r'workspace_write_exempt = True\s*(?:#[^\n]*\n\s*)*'
        r'gateway_admin_actions = frozenset\(\{"update", "partial_update", "sync"\}\)'
        r'.*?qs = AgentccAPIKey\.no_workspace_objects\.filter\(deleted=False\)\s*'
        r'qs = scope_to_request_organization\(qs, self\.request\)\s*'
        r'if not can_manage_gateway_keys\(self\.request\):\s*qs = qs\.filter\(user=self\.request\.user\)\s*'
        r'return qs\.select_related\("project", "user"\)'
        r'.*?def create\(self, request, \*args, \*\*kwargs\):\s*'
        r'from accounts\.api_key_policy import require_personal_key_session\s*require_personal_key_session\(request\)'
        r'(?:(?!\n    def ).)*?user=request\.user,'
        r'(?:(?!\n    def ).)*?result\["key"\] = raw_key  # Only returned on creation\.'
        r'.*?def destroy\(self, request, \*args, \*\*kwargs\):\s*"""[^\"]*"""\s*try:\s*'
        r'api_key = self\.get_object\(\)\s*auth_bridge\.delete_key\(api_key\)'
        r'.*?def revoke\(self, request, pk=None\):\s*try:\s*'
        r'api_key = self\.get_object\(\)\s*api_key, gateway_failed = auth_bridge\.revoke_key\(api_key\)',
    ),
    (
        "personal key self-service requires live membership and limits revoke/destroy to own keys or admins",
        "futureagi/agentcc/key_permissions.py",
        r'class PersonalGatewayKeyAccess\(BasePermission\):.*?def has_permission\(self, request, view\):\s*'
        r'if not explicit_scope_matches\(request\) or key_membership\(request\) is None:\s*return False\s*'
        r'if getattr\(view, "action", None\) == "create" and not has_personal_key_session\(\s*request\s*\):\s*'
        r'self\.message = "[^"\n]*"\s*return False\s*(?:#[^\n]*\n\s*)*'
        r'personal_write = \(getattr\(view, "action", None\), request\.method\) in \{\s*'
        r'\("create", "POST"\),\s*\("revoke", "POST"\),\s*\("destroy", "DELETE"\),\s*'
        r'\} and has_personal_key_session\(request\)\s*if \(\s*'
        r'request\.method in \{"POST", "PUT", "PATCH", "DELETE"\}\s*'
        r'and not personal_write\s*and not has_workspace_write_access\(request\)\s*\):\s*'
        r'self\.message = "[^"\n]*"\s*return False\s*return True\s*'
        r'def has_object_permission\(self, request, view, obj\):\s*(?:#[^\n]*\n\s*)*'
        r'return obj\.user_id == request\.user\.pk or can_manage_gateway_keys\(request\)',
    ),
    (
        "gateway key administrators have live membership at Admin level or above",
        "futureagi/agentcc/key_permissions.py",
        r'return \(\s*live_gateway_memberships\(\)\s*'
        r'\.filter\(user_id=user\.pk, organization_id=org\.pk\)\s*\.first\(\)\s*\)\s*'
        r'def can_manage_gateway_keys\(request\):\s*membership = key_membership\(request\)\s*'
        r'return membership is not None and membership\.level_or_legacy >= Level\.ADMIN',
    ),
    (
        "named gateway admin actions add the organization-admin permission",
        "futureagi/agentcc/permissions.py",
        r'class GatewayAdminActionsMixin:.*?def get_permissions\(self\):\s*'
        r'permissions = super\(\)\.get_permissions\(\)\s*(?:#[^\n]*\n\s*)*'
        r'if getattr\(self, "action", None\) in self\.gateway_admin_actions:\s*'
        r'permissions\.append\(IsOrgGatewayAdmin\(\)\)\s*return permissions',
    ),
    (
        "the organization-admin permission checks the request organization's membership level",
        "futureagi/agentcc/permissions.py",
        r'def is_gateway_admin\(request\) -> bool:\s*"""[^\"]*"""\s*'
        r'user = getattr\(request, "user", None\)\s*organization = getattr\(request, "organization", None\)\s*'
        r'if user is None or not user\.is_authenticated or organization is None:\s*return False\s*'
        r'level = user\.get_membership_level\(organization\)\s*return level is not None and level >= Level\.ADMIN'
        r'.*?class IsOrgGatewayAdmin\(BasePermission\):\s*"""[^\"]*"""\s*message = GATEWAY_ADMIN_MESSAGE\s*'
        r'def has_permission\(self, request, view\):\s*return is_gateway_admin\(request\)',
    ),
    (
        "a key's answer carries gateway_key_id",
        "futureagi/agentcc/serializers/api_key.py",
        r'class AgentccAPIKeySerializer\(serializers\.ModelSerializer\):.*?fields = \[[^\]]*"gateway_key_id",',
    ),
    (
        "a failed config push keeps the saved version and reports gateway_sync_failed",
        "futureagi/agentcc/services/org_config_writer.py",
        r'GATEWAY_SYNC_FAILED = "gateway_sync_failed".*# A failed push keeps the saved version \(fail open\)',
    ),
    (
        "gateway replicas re-read keys and org settings every 60 s by default (sync_interval)",
        "deploy/env-reference.toml",
        r"default = \"`60s` \(Helm: `60s`, from `agentccGateway\.config\.control_plane\.sync_interval`\)\".*?"
        r"re-reads keys and org settings from the app this often, so .*?several gateway replicas catch up",
    ),
    (
        "Helm sets the gateway's sync_interval to 60s",
        "deploy/helm/futureagi/values.yaml",
        r"\n\s+sync_interval: 60s\n",
    ),
    (
        "Django creates a gateway key through one base URL (one replica)",
        "futureagi/agentcc/services/gateway_client.py",
        r"def get_gateway_client\(\):.*?base_url=AGENTCC_GATEWAY_INTERNAL_URL,",
    ),
    (
        "POST /-/keys creates the key in this replica's store",
        GW + "server/admin_keys.go",
        r"key, rawKey := h\.keyStore\.Create\(req\.Name, owner, req\.Models, req\.Providers, req\.Metadata\)",
    ),
    (
        "a replica authenticates a key from its own store only",
        GW + "auth/keystore.go",
        r"func \(ks \*KeyStore\) Authenticate\(rawKey string\) \*APIKey \{\s*\n\s*hash := HashKey\(rawKey\).*?key, ok := ks\.byHash\[hash\]",
    ),
    (
        "other replicas load a key on their next key sync, from /agentcc/api-keys/bulk/",
        GW + "auth/sync.go",
        r'endpoint := baseURL \+ "/agentcc/api-keys/bulk/"',
    ),
    (
        "the key sync runs every control_plane.sync_interval, and never when it is 0",
        GW + "tenant/spend_sync.go",
        r'if interval <= 0 \|\| baseURL == "" \{\s*\n\s*return\s*\n\s*\}.*?'
        r"auth\.SyncKeysFromControlPlane\(syncCtx, baseURL, adminToken, keyStore\)",
    ),
    (
        "the gateway passes control_plane.sync_interval to the sync",
        "agentcc-gateway/cmd/agentcc/main.go",
        r"go tenant\.RunControlPlaneSync\(syncCtxBg, cfg\.ControlPlane\.SyncOnStartup, cfg\.ControlPlane\.SyncInterval,",
    ),
    (
        "a revocation is broadcast to every replica over Redis",
        GW + "server/admin_keys.go",
        r"revoked := h\.keyStore\.Revoke\(keyID\).*?h\.revocationPublisher\.Publish\(r\.Context\(\), keyID\)",
    ),
    (
        "every replica revokes a key another replica broadcast",
        "agentcc-gateway/cmd/agentcc/main.go",
        r"go broadcaster\.Subscribe\(syncCtxBg, func\(keyID string\) \{\s*\n\s*keyStore\.Revoke\(keyID\)",
    ),
    (
        "with MCP off, no MCP route is registered",
        GW + "server/routes_mcp.go",
        r"func \(s \*Server\) registerMCPRoutes\(router \*Router\) \{\s*\n\s*cfg := s\.cfg\s*\n\s*if !cfg\.MCP\.Enabled \{\s*\n\s*return",
    ),
    (
        "with MCP off, the key gate does not cover the MCP endpoint",
        GW + "server/server.go",
        r"if cfg\.MCP\.Enabled \{\s*\n\s*keyGatedRoots = append\(keyGatedRoots, mcpEndpoint\(cfg\)\)",
    ),
    (
        "a path the gateway does not serve gets the router's not_found",
        GW + "server/router.go",
        r'models\.WriteError\(w, models\.ErrNotFound\("not_found", rt\.unknownEndpointMessage\(method, path\)\)\)',
    ),
    (
        "the router's unknown-endpoint helper starts with the exact path prefix",
        GW + "server/router_hints.go",
        r'func \(rt \*Router\) unknownEndpointMessage\(method, path string\) string \{\s*'
        r'msg := "Unknown endpoint: " \+ path',
    ),
    (
        "the router's no-hint fallback ends the unknown-endpoint message with a period",
        GW + "server/router_hints.go",
        r'\n\treturn msg \+ "\."\n\}',
    ),
    (
        "ErrNotFound is 404 with type not_found",
        GW + "models/errors.go",
        r'ErrTypeNotFound\s*=\s*"not_found".*?func ErrNotFound\(code, message string\) \*APIError \{\s*\n\s*'
        r"return newAPIError\(http\.StatusNotFound, ErrTypeNotFound, code, message\)",
    ),
    (
        "the virtual server is resolved in the chain that tools/list and tools/call run",
        GW + "mcp/chain.go",
        r"\{name: hookResolveVirtualServer, run: resolveVirtualServer, onRetry: true\}.*?"
        r"\{name: hookResolveVirtualServer, run: resolveVirtualServer\}.*?s\.runChain\(cc, toolsCallChain\)",
    ),
    (
        "build_error_envelope sets details whenever it sets attr",
        "futureagi/tfc/utils/api_errors.py",
        r'attr = first_error_attr\(resolved_details\)\s*\n\s*if attr:\s*\n\s*body\["attr"\] = attr\s*\n\s*if resolved_details:\s*\n\s*body\["details"\] = resolved_details',
    ),
    (
        "a 503 is service_unavailable in Django's envelope",
        "futureagi/tfc/utils/api_errors.py",
        r"status\.HTTP_503_SERVICE_UNAVAILABLE: ApiErrorType\.SERVICE_UNAVAILABLE,",
    ),
    (
        "Django's error envelope",
        "futureagi/tfc/utils/api_errors.py",
        r'"status": False,\s*\n\s*"type": resolved_type,\s*\n\s*"code": str\(resolved_code or "error"\),\s*\n\s*"detail": message,\s*\n\s*"message": message,\s*\n\s*"error": message,',
    ),
    (
        "header key authentication uses the shared personal-key admission boundary",
        "futureagi/accounts/authentication.py",
        r'org_api_key = authenticate_api_key\(api_key, secret_key\)',
    ),
    (
        "only enabled person-owned keys are admitted and verified historical keys return retirement guidance",
        "futureagi/accounts/api_key_policy.py",
        r'row\.type != "user".*?not row\.user_id.*?not row\.enabled.*?is_retired_key\(row\)'
        r'.*?raise AuthenticationFailed.*?row is not None and is_retired_key\(row\)'
        r'.*?verify_secret\(row\.secret_key_hash, "", presented_secret\)'
        r'.*?row\.secret_key_hash and verdict\.ok:.*?raise APIKeyRetired\(\)',
    ),
    (
        "a Django key is live only while active, not deleted and not expired",
        "futureagi/agentcc/services/auth_bridge.py",
        r"status=AgentccAPIKey\.ACTIVE,\s*\n\s*deleted=False,\s*\n\s*user__isnull=False,\s*\n\s*"
        r"user__is_active=True,\s*\n\s*\)\s*\n\s*\.filter\(\s*\n\s*"
        r"Q\(expires_at__isnull=True\) \| Q\(expires_at__gt=timezone\.now\(\)\),\s*\n\s*Exists\(membership\),\s*\n\s*\)",
    ),
    (
        "Django's key service creates a key on the gateway before it stores it",
        "futureagi/agentcc/services/auth_bridge.py",
        r"def provision_key\(.*?result = client\.create_key\(.*?"
        r"AgentccAPIKey\.no_workspace_objects\.update_or_create\(",
    ),
    (
        "revoking marks a key revoked even when the gateway cannot be reached",
        "futureagi/agentcc/services/auth_bridge.py",
        r'def revoke_key\(api_key\):(?:(?!\ndef ).)*?api_key\.status = AgentccAPIKey\.REVOKED\s*\n\s*'
        r'api_key\.save\(update_fields=\["status", "updated_at"\]\)\s*\n\s*gateway_failed = False\s*\n\s*'
        r'try:\s*\n\s*client = get_gateway_client\(\)\s*\n\s*client\.revoke_key\(api_key\.gateway_key_id\)\s*\n\s*'
        r'except GatewayClientError:(?:(?!\ndef ).)*?gateway_failed = True\s*\n\s*return api_key, gateway_failed',
    ),
    (
        "the key list the gateway pulls holds live keys only",
        "futureagi/agentcc/views/api_key_bulk.py",
        r"loadable = gateway_loadable_keys\(\)\s*\n\s*result = gateway_key_payloads\(loadable\)",
    ),
    # The EE agent key is scoped (spec sections 3.3, 3.5, 3.7 and 5.3).
    (
        "a scoped key outside its scope is the key gate's 403 key_scope_denied",
        GW + "keyscope/errors.go",
        r'CodeScopeDenied = "key_scope_denied".*?func ErrScopeDenied\(.*?Status: http\.StatusForbidden,\s*\n\s*'
        r"Type:\s+models\.ErrTypePermission,\s*\n\s*Code:\s+CodeScopeDenied,",
    ),
    (
        "the mcp scope is the MCP endpoint's subtree; models is POST chat/completions, embeddings, responses",
        GW + "keyscope/scope.go",
        r'"/v1/chat/completions": "POST",\s*\n\s*"/v1/embeddings":\s+"POST",\s*\n\s*"/v1/responses":\s+"POST",.*?'
        r'c == root \|\| strings\.HasPrefix\(c, root\+"/"\)',
    ),
    (
        "a spent agent key budget is 402 agent_key_budget_exhausted, limit_source agentcc_key_limit, scope key:<id>",
        GW + "keyscope/errors.go",
        r'CodeBudgetExhausted = "agent_key_budget_exhausted".*?LimitSource:\s+models\.LimitSourceKeyLimit,.*?'
        r"Scope:\s+models\.ScopeKey\(keyID\),",
    ),
    (
        "Django answers an agent key on any of its routes 403 key_scope_denied",
        "futureagi/agentcc/middleware_ee_agent_key.py",
        r"build_error_envelope\(\s*\n\s*SCOPE_DENIED, status_code=403, code=policy\.CODE_SCOPE_DENIED\s*\n\s*\)",
    ),
    (
        "the mint refuses past the org's cap on live agent keys with 409 agent_key_cap_reached",
        "futureagi/agentcc/services/mcp_service_provisioning.py",
        r"409, ee_agent_key_policy\.CODE_CAP_REACHED, AGENT_KEY_CAP_REACHED",
    ),
    (
        "an agent key expires 90 days after it is minted, and carries scopes mcp,models",
        "futureagi/agentcc/services/ee_agent_key_policy.py",
        r'SCOPES = "mcp,models".*?LIFETIME = timedelta\(days=90\)',
    ),
    # The idempotent mint (spec sections 3.5 and 3.7).
    (
        "the mint's Idempotency-Key header, its 24-hour window and the three codes",
        "futureagi/agentcc/services/ee_agent_key_mint.py",
        r'HEADER = "Idempotency-Key"\s*\n.*?WINDOW = timedelta\(hours=24\)\s*\n\s*\n'
        r'CODE_KEY_INVALID = "idempotency_key_invalid"\s*\nCODE_REPLAYED = "agent_key_mint_replayed"\s*\n'
        r'CODE_REUSED = "idempotency_key_reused"',
    ),
    (
        "the Idempotency-Key is a UUIDv7: version 7 and the RFC variant",
        "futureagi/agentcc/services/ee_agent_key_mint.py",
        r'UUID7 = re\.compile\(\s*\n\s*r"\^\[0-9a-f\]\{8\}-\[0-9a-f\]\{4\}-7\[0-9a-f\]\{3\}-\[89ab\]\[0-9a-f\]\{3\}-\[0-9a-f\]\{12\}\$"',
    ),
    (
        "a mint without a UUIDv7 Idempotency-Key is 400 idempotency_key_invalid naming the header",
        "futureagi/agentcc/views/mcp_service_provisioning.py",
        r"except mint_idempotency\.InvalidKey as exc:\s*\n\s*return _error\(\s*\n\s*provisioning\.ProvisioningError\(\s*\n"
        r"\s*400,\s*\n\s*mint_idempotency\.CODE_KEY_INVALID,\s*\n\s*str\(exc\),\s*\n\s*mint_idempotency\.HEADER,",
    ),
    (
        "under the org lock the mint checks its Idempotency-Key's record before the cap and before the gateway",
        "futureagi/agentcc/services/mcp_service_provisioning.py",
        r"lock_org\(org\.pk\).*?prior = mint_idempotency\.claim\(.*?if prior is None:\s*\n\s*api_key, raw_key = _provision_locked\("
        r".*?mint_idempotency\.remember\(",
    ),
    (
        "a replay is 409 agent_key_mint_replayed with the key's fields; another request is 422 idempotency_key_reused",
        "futureagi/agentcc/services/mcp_service_provisioning.py",
        r"422, mint_idempotency\.CODE_REUSED, mint_idempotency\.REUSED.*?"
        r"409, mint_idempotency\.CODE_REPLAYED, mint_idempotency\.REPLAYED, extra=fields",
    ),
    (
        "a replay names agent_key_id, created_at, expires_at and revoked, never the key",
        "futureagi/agentcc/services/ee_agent_key_mint.py",
        r'return \{\s*\n\s*"agent_key_id": key\.gateway_key_id,\s*\n\s*"created_at": key\.created_at,\s*\n'
        r'\s*"expires_at": key\.expires_at,\s*\n\s*"revoked": ',
    ),
    (
        "one record per (org, EE service credential, Idempotency-Key hash)",
        "futureagi/agentcc/models/ee_agent_key_mint.py",
        r'fields=\["organization", "service_credential", "key_sha256"\],\s*\n\s*name="agentcc_ee_one_mint_per_idempotency_key"',
    ),
    (
        "EE's service credential may read the workspace's agent keys (the find), as an Owner or Admin may",
        "futureagi/agentcc/views/ee_agent_key_policy.py",
        r"authentication_classes = \[\s*\n\s*EEServiceCredentialIfPresent,.*?"
        r"permission_classes = \[IsEEServiceCredential \| \(IsAuthenticated & IsOrgGatewayAdmin\)\]",
    ),
]


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def go_body(src, start):
    """The text of the Go declaration that begins with start, up to its closing brace at column 0."""
    i = src.find(start)
    if i < 0:
        return None
    j = src.find("\n}\n", i)
    return src[i : j if j >= 0 else len(src)]


def ready_waits_for_key_list(root):
    """True while a starting replica whose control plane syncs keys reports not ready until a key list has loaded.

    The release line's readiness rule: Server sets waitForKeyList from cfg.ControlPlane.SyncsKeys(), and readyHandler answers
    503 while it is set and the key store has neither loaded a key list nor heard that the app has no key table.
    main.go still starts the startup sync in a goroutine before srv.Start(), so the 401 window of section 3.1
    remains for a deployment that does not route by /readyz.
    """
    server = read(root, GW + "server/server.go")
    ready = go_body(server, "func (s *Server) readyHandler(")
    main = read(root, "agentcc-gateway/cmd/agentcc/main.go")
    sync = re.search(
        r"\n\s*go tenant\.RunControlPlaneSync\(syncCtxBg, cfg\.ControlPlane\.SyncOnStartup, "
        r"cfg\.ControlPlane\.SyncInterval,\s*cfg\.ControlPlane\.URL, cfg\.ControlPlane\.AdminToken, "
        r"tenantStore, keyStore\)\n",
        main,
    )
    serve = main.find("if err := srv.Start(); err != nil {")
    return (
        re.search(r"s\.waitForKeyList = cfg\.ControlPlane\.SyncsKeys\(\)", server)
        is not None
        and ready is not None
        and re.search(
            r"case s\.waitForKeyList && !s\.keyStore\.KeyListLoaded\(\) && !s\.keyStore\.AppHasNoKeyTable\(\):\s*\n"
            r"\s*w\.WriteHeader\(http\.StatusServiceUnavailable\)",
            ready,
        )
        is not None
        and sync is not None
        and 0 <= sync.start() < serve
    )


def go_tags(src, struct):
    m = re.search(rf"type {struct} struct \{{(.*?)\n\}}", src, re.S)
    return re.findall(r'json:"([a-z_]+)[,"]', m.group(1)) if m else []


def protocol_fixture_checks(root):
    """Pin complete model-path envelopes, not just the helpers' names or schema."""
    out = []
    cases = [
        ("401-invalid-api-key.json", 401, "authentication_error", "invalid_api_key",
         UNKNOWN_KEY_MESSAGE, {"reason": "unknown_api_key"}),
        ("403-ip-not-allowed.json", 403, "permission_error", "permission_denied",
         "Access denied: IP address is not allowed", None),
        ("404-mcp-not-served.json", 404, "not_found", "not_found",
         "Unknown endpoint: /mcp/v/vs_01K7CN5EEAGENTM0DE00000001.", None),
    ]
    for name, status, kind, code, message, metadata in cases:
        headers = {"content-type": "application/json"}
        if status == 401:
            headers["www-authenticate"] = 'Bearer realm="agentcc-gateway"'
        error = {"message": message, "type": kind, "param": None, "code": code}
        if metadata is not None:
            error["metadata"] = metadata
        expected = {"http": {"status": status, "headers": headers}, "body": {"error": error}}
        actual = load_json(os.path.join(root, FIXTURES, "protocol", name))
        out.append((
            f"protocol/{name} is the composed product's exact envelope",
            actual == expected or "status, headers or error fields differ from the composed source pin",
        ))
    return out


def code_checks(root, schema):
    out = protocol_fixture_checks(root)
    for name, rel, pattern in CODE_PATTERNS:
        try:
            found = re.search(pattern, read(root, rel), re.S) is not None
        except OSError as err:
            out.append((name, f"cannot read {rel}: {err.strerror}"))
            continue
        out.append((name, found or f"{rel} no longer matches {pattern}"))
    defs = schema["$defs"]
    tags = go_tags(read(root, GW + "mcp/denial.go"), "ErrorObject")
    errs = [d for d in defs if d.startswith("err_")]
    good = tags == ["type", "code", "message", "param", "metadata"] and all(
        defs[d]["required"] == tags for d in errs
    )
    out.append(
        (
            "error object fields equal denial.go ErrorObject",
            good or f"ErrorObject tags {tags}",
        )
    )
    tags = go_tags(read(root, GW + "mcp/hook_evidence.go"), "toolCallMeta")
    good = tags == defs["tool_call_meta"]["required"] and sorted(tags) == sorted(
        defs["tool_call_meta"]["properties"]
    )
    out.append(
        (
            "tool_call meta fields equal hook_evidence.go toolCallMeta",
            good or f"toolCallMeta tags {tags}",
        )
    )
    consts = set(
        re.findall(
            r'ErrType\w+\s*=\s*"([a-z_]+)"',
            read(root, GW + "models/errors.go")
            + read(root, GW + "models/errors_billing.go"),
        )
    )
    extra = set(defs["error_type"]["enum"]) - consts
    out.append(
        (
            "error types exist in the gateway's models",
            not extra or f"not in the code: {sorted(extra)}",
        )
    )
    codes = set(
        re.findall(r'Auth\w+\s*=\s*"([a-z_]+)"', read(root, GW + "mcp/principal.go"))
    )
    data = set(
        defs["mcp_protocol_error"]["properties"]["body"]["properties"]["error"][
            "properties"
        ]["data"]["properties"]["code"]["enum"]
    )
    out.append(
        (
            "401 data codes cover principal.go's",
            codes <= data or f"missing: {sorted(codes - data)}",
        )
    )
    init = go_body(
        read(root, GW + "mcp/method_initialize.go"),
        "func (s *Server) handleInitialize(",
    )
    good = (
        init is not None
        and "s.sessions.create(" in init
        and "validateSession" not in init
    )
    out.append(
        (
            "initialize always opens a new session and never checks a session id it is sent",
            good
            or "handleInitialize no longer creates a session without validating one",
        )
    )
    good = init is not None and "runChain" not in init and "VirtualServer" not in init
    out.append(
        (
            "initialize never resolves the virtual server, so its 404 comes on tools/list or tools/call",
            good
            or "handleInitialize now resolves the virtual server: update spec sections 3.1 and 5.3",
        )
    )
    keys = read(root, GW + "server/admin_keys.go")
    create = go_body(keys, "func (h *KeyHandlers) CreateKey(")
    good = (
        create is not None
        and "h.keyStore.Create(" in create
        and "Publish" not in create
        and "revocationPublisher" not in create
    )
    out.append(
        (
            "creating a key broadcasts nothing to the other replicas (revoking does)",
            good
            or "CreateKey now broadcasts: update spec section 3.1 (the key window) and integrator note R4",
        )
    )
    out.append(
        (
            "a starting replica whose control plane syncs keys is not ready before a key list has loaded",
            ready_waits_for_key_list(root)
            or "readiness no longer waits for the key sync: restate spec section 3.1's bullet on a replica "
            "that starts (section 4.2's 401 window stays), and change this check",
        )
    )
    synced = go_body(read(root, GW + "auth/keystore.go"), "type SyncedKey struct {")
    out.append(
        (
            "a synced (Django) key carries no allowed_ips at the gateway",
            (synced is not None and "AllowedIPs" not in synced)
            or "SyncedKey now has AllowedIPs: update spec section 3.3 rule 3 and section 5.3",
        )
    )
    written = set(
        re.findall(
            r'models\.ErrForbidden\("([^"]+)"\)',
            read(root, GW + "plugins/ipacl/ipacl.go"),
        )
    )
    shown = load_json(os.path.join(root, FIXTURES, "protocol/403-ip-not-allowed.json"))[
        "body"
    ]["error"]["message"]
    out.append(
        (
            "the 403 fixture's message is one ipacl writes (EE keys on code, not message)",
            shown in written or f"{shown!r} is not among {sorted(written)}",
        )
    )
    good = (
        defs["exposed_tool_name"]["maxLength"] == 64
        and "__" in defs["exposed_tool_name"]["pattern"]
    )
    out.append(
        (
            "schema name rule is vserver's",
            good or "exposed_tool_name must be maxLength 64 with __",
        )
    )
    return out


def plan_lines(path):
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    return lines[1178], lines[1179], lines


def typed_results(l1179, l1180):
    names = {
        t
        for t in re.findall(r"`([^`]+)`", l1179)
        if re.fullmatch(r"[a-z][a-z0-9_]*\*?", t)
    }
    names |= set(re.findall(r"io\.futureagi/[a-z_]+", l1179 + "\n" + l1180))
    m = re.search(r'io\.futureagi/tool_call"\]\s*\{([^}]*)\}', l1180)
    fields = (
        [re.sub(r"\s*\(.*?\)", "", f).strip() for f in m.group(1).split(",")]
        if m
        else []
    )
    enums = (
        [e.split(" | ") for e in re.findall(r"\(([a-z]+(?: \| [a-z]+)+)\)", m.group(1))]
        if m
        else []
    )
    return names | set(fields), fields, enums


def where_moved(lines):
    marks = {
        "Typed results": r"^6\. \*\*Typed results\.\*\*",
        "Read-back": r"^7\. \*\*Read-back\.\*\*",
    }
    return ", ".join(
        "{} at line {}".format(
            k, next((i + 1 for i, line in enumerate(lines) if re.match(p, line)), "?")
        )
        for k, p in marks.items()
    )


def doc_table_names(spec):
    m = TABLE.search(spec)
    if not m:
        return None
    rows = [r for r in m.group(1).splitlines() if r.startswith("|")][2:]
    return [(re.findall(r"`([^`]+)`", r.split("|")[1]) or [None])[0] for r in rows]


def sums_files(manifest):
    return sorted(
        {"schema.json", "fixtures.json", "typed-results.json"}
        | {f["file"] for f in manifest["fixtures"]}
    )


def sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def write_sums(fixdir, manifest):
    body = "".join(
        f"{sha256_file(os.path.join(fixdir, f))}  {f}\n" for f in sums_files(manifest)
    )
    with open(os.path.join(fixdir, "SHA256SUMS"), "w", encoding="utf-8") as fh:
        fh.write(body)
    return hashlib.sha256(body.encode()).hexdigest()


def sums(fixdir, manifest, spec):
    out = []
    path = os.path.join(fixdir, "SHA256SUMS")
    with open(path, "rb") as fh:
        raw = fh.read()
    listed = [line.split("  ", 1) for line in raw.decode().splitlines()]
    files = [f for _, f in listed]
    out.append(
        (
            "SHA256SUMS lists exactly the contract files",
            files == sums_files(manifest)
            or f"listed {files}, want {sums_files(manifest)}",
        )
    )
    bad = [
        f
        for h, f in listed
        if not os.path.isfile(os.path.join(fixdir, f))
        or sha256_file(os.path.join(fixdir, f)) != h
    ]
    out.append(
        (
            "every file matches SHA256SUMS",
            not bad or f"changed without re-pinning: {bad}",
        )
    )
    on_disk = sorted(
        os.path.relpath(os.path.join(d, n), fixdir)
        for d, _, ns in os.walk(fixdir)
        for n in ns
        if n.endswith(".json")
    )
    stray = sorted(set(on_disk) - set(sums_files(manifest)))
    out.append(("no JSON file outside the manifest", not stray or f"unlisted: {stray}"))
    digest = hashlib.sha256(raw).hexdigest()
    stated = DOC_DIGEST.findall(spec)
    out.append(
        (
            f"the spec states the set digest sha256:{digest}",
            stated == [digest] or f"the spec states {stated or 'none'}",
        )
    )
    return out


def load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
