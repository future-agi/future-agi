{{/* Credential-bearing leaves of agentcc-gateway/internal/config/config.go.
Patterns are compared SEGMENT BY SEGMENT, not against a joined user path:
a dotted map key must not impersonate a credential field. '*' is one map
entry or list index. The guardrail Config map is read by internal/guardrails.
Public verification keys are not secrets. credentials_file is a mount path,
and cluster.redis_url may instead be a credential-free URL. All header
values are treated as credentials: a custom vendor header cannot bypass us.
*/}}
{{- define "futureagi.providerConfig.rules" -}}
{{- dict
  "provider" (list "providers.*.api_key" "providers.*.aws_access_key_id" "providers.*.aws_secret_access_key" "providers.*.aws_session_token" "providers.*.service_account_json")
  "credential" (list
    "providers.*.headers.*"
    "secrets.vault.token" "secrets.vault.app_role_id" "secrets.vault.app_role_secret"
    "secrets.aws.access_key_id" "secrets.aws.secret_access_key"
    "secrets.azure.tenant_id" "secrets.azure.client_id" "secrets.azure.client_secret"
    "cache.s3.access_key_id" "cache.s3.secret_access_key"
    "cache.azure_blob.connection_string" "cache.azure_blob.sas_token"
    "cache.redis.password" "redis.password"
    "cache.semantic.qdrant.api_key" "cache.semantic.weaviate.api_key" "cache.semantic.pinecone.api_key"
    "auth.keys.*.key" "admin.token" "control_plane.admin_token" "control_plane.webhook_secret"
    "mcp.servers.*.auth.token" "mcp.servers.*.auth.key"
    "a2a.agents.*.auth.token" "a2a.agents.*.auth.key"
    "otel.headers.*" "audit.sinks.*.headers.*" "alerting.channels.*.headers.*"
    "guardrails.rules.*.config.api_key" "guardrails.rules.*.config.secret_key"
    "guardrails.rules.*.config.access_key" "guardrails.rules.*.config.token"
    "guardrails.rules.*.config.client_id" "guardrails.rules.*.config.client_secret"
    "guardrails.rules.*.config.headers.*")
  "file" (list "providers.*.credentials_file" "cache.gcs.credentials_file")
  "url" (list "cluster.redis_url") | toJson -}}
{{- end -}}

{{- define "futureagi.providerConfig.matches" -}}
{{- $pattern := splitList "." .pattern -}}
{{- $matches := eq (len $pattern) (len .parts) -}}
{{- if $matches -}}
{{- range $i, $part := .parts -}}
{{- $expected := index $pattern $i -}}
{{- if and (ne $expected "*") (ne $expected (toString $part)) -}}{{- $matches = false -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- if $matches -}}true{{- end -}}
{{- end -}}

{{/* Accumulates fixed messages in state.errors; never emits node values or
user-controlled map keys. Walk maps (including their keys) and lists because
os.ExpandEnv runs over the entire config, not just the fields Go unmarshals.
*/}}
{{- define "futureagi.providerConfig.walk" -}}
{{- $ctx := . -}}
{{- $kind := "" -}}
{{- if not .mapKey -}}
{{- range $candidate, $patterns := include "futureagi.providerConfig.rules" .root | fromJson -}}
{{- range $pattern := $patterns -}}
{{- if include "futureagi.providerConfig.matches" (dict "parts" $ctx.parts "pattern" $pattern) -}}
{{- $kind = $candidate -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $errors := .state.errors -}}
{{- $text := "" -}}
{{- if kindIs "string" .node -}}{{- $text = .node -}}{{- end -}}
{{- $var := "" -}}
{{- if regexMatch "^\\$\\{[A-Za-z_][A-Za-z0-9_]*\\}$" $text -}}
{{- $var = trimSuffix "}" (trimPrefix "${" $text) -}}
{{- else if regexMatch "^\\$[A-Za-z_][A-Za-z0-9_]*$" $text -}}
{{- $var = trimPrefix "$" $text -}}
{{- end -}}
{{- $uri := and (eq $kind "provider") (eq (last .parts) "api_key") (regexMatch "^(vault|aws-sm|gcp-sm|azure-kv)://" $text) -}}
{{- if $uri -}}{{- $_ := set .state "secretURI" true -}}{{- end -}}
{{- if and .node (has $kind (list "credential" "provider")) -}}
{{- /* Existing provider-field checks supply their more specific messages. */ -}}
{{- if and (not $var) (not $uri) (or (eq $kind "credential") (not (kindIs "string" .node))) -}}
{{- $errors = append $errors "agentccGateway.config: credential fields must be Secret-backed variable references, never literals" -}}
{{- else if and $var (eq $kind "credential") -}}
{{- /* The app/Redis references already emitted by env.gateway are allowed
only at their corresponding config fields, not as provider credentials. */ -}}
{{- $owned := false -}}
{{- range $pattern, $names := dict
      "auth.keys.*.key" (list "AGENTCC_INTERNAL_API_KEY")
      "admin.token" (list "AGENTCC_ADMIN_TOKEN")
      "control_plane.admin_token" (list "AGENTCC_ADMIN_TOKEN" "AGENTCC_CONTROL_PLANE_TOKEN")
      "control_plane.webhook_secret" (list "AGENTCC_WEBHOOK_SECRET")
      "redis.password" (list "AGENTCC_REDIS_PASSWORD")
      "cache.redis.password" (list "AGENTCC_REDIS_PASSWORD") -}}
{{- if and (has $var $names) (include "futureagi.providerConfig.matches" (dict "parts" $ctx.parts "pattern" $pattern)) -}}
{{- $owned = true -}}
{{- end -}}
{{- end -}}
{{- $g := .root.Values.agentccGateway -}}
{{- if hasKey ($g.extraEnv | default dict) $var -}}{{- $owned = false -}}{{- end -}}
{{- if and (eq $var "AGENTCC_CONTROL_PLANE_TOKEN") (not $g.controlPlaneSync) -}}{{- $owned = false -}}{{- end -}}
{{- if and (eq $var "AGENTCC_REDIS_PASSWORD") (not (and (include "futureagi.gateway.redis" .root) (eq (include "futureagi.redis.auth" .root) "true"))) -}}{{- $owned = false -}}{{- end -}}
{{- if not (or $owned (has $var .keys)) -}}
{{- $errors = append $errors "agentccGateway.config: credential reference is not in agentccGateway.providerCredentials.keys or its chart-owned Secret path" -}}
{{- end -}}
{{- end -}}
{{- else if and .node (eq $kind "file") -}}
{{- $g := .root.Values.agentccGateway -}}
{{- $mount := eq $text "/var/run/secrets/futureagi/gcp/credentials.json" -}}
{{- if and (eq $var "GOOGLE_APPLICATION_CREDENTIALS") (not (hasKey ($g.extraEnv | default dict) $var)) (not (has $var .keys)) -}}{{- $mount = true -}}{{- end -}}
{{- if not (and $g.gcpCredentials.existingSecret $mount) -}}
{{- $errors = append $errors "agentccGateway.config: credentials_file must use the gateway-only gcpCredentials mount" -}}
{{- end -}}
{{- else if and $var (eq $kind "url") -}}
{{- if not (has $var .keys) -}}
{{- $errors = append $errors "agentccGateway.config: credential reference is not in agentccGateway.providerCredentials.keys or its chart-owned Secret path" -}}
{{- end -}}
{{- end -}}
{{- if regexMatch "^[A-Za-z][A-Za-z0-9+.-]*://[^/]*@" $text -}}
{{- $errors = append $errors "agentccGateway.config: inline URL credentials are refused with agentccGateway.providerCredentials.gatewayOnly" -}}
{{- end -}}
{{- /* No references to protected names in URLs, metadata, arguments, map
keys, unknown fields, or fragments of a URI. Only whole credential refs are
allowed. The URI scheme-prefix check is an accident guard, not inspection. */ -}}
{{- $allowedReference := and $var (has $kind (list "credential" "provider" "url")) -}}
{{- range $reference := regexFindAll "\\$\\{[^}]*\\}|\\$[A-Za-z_][A-Za-z0-9_]*" $text -1 -}}
{{- $name := trimPrefix "$" $reference | trimPrefix "{" | trimSuffix "}" -}}
{{- if and (has $name $ctx.names) (not $allowedReference) -}}
{{- $errors = append $errors "agentccGateway.config: provider credential references are refused outside credential fields" -}}
{{- end -}}
{{- end -}}
{{- $_ := set .state "errors" ($errors | uniq) -}}
{{- if kindIs "map" .node -}}
{{- range $key, $value := .node -}}
{{- $parts := append $ctx.parts $key -}}
{{- include "futureagi.providerConfig.walk" (dict "root" $ctx.root "state" $ctx.state "parts" $parts "node" $key "keys" $ctx.keys "names" $ctx.names "mapKey" true) -}}
{{- include "futureagi.providerConfig.walk" (dict "root" $ctx.root "state" $ctx.state "parts" $parts "node" $value "keys" $ctx.keys "names" $ctx.names) -}}
{{- end -}}
{{- else if kindIs "slice" .node -}}
{{- range $i, $value := .node -}}
{{- include "futureagi.providerConfig.walk" (dict "root" $ctx.root "state" $ctx.state "parts" (append $ctx.parts (toString $i)) "node" $value "keys" $ctx.keys "names" $ctx.names) -}}
{{- end -}}
{{- end -}}
{{- end -}}
