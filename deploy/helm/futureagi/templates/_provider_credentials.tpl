{{/* =====================================================================
Gateway-only custody of the platform's provider credentials (OpenAI,
Anthropic, Gemini, Bedrock, OpenRouter, Azure, Vertex, the speech vendors and
the other families the gateway calls): agentccGateway.providerCredentials.

existingSecret + keys: the gateway reads each name in keys from that Secret
(the key is the variable name) as a required secretKeyRef; no other workload
reads it. gatewayOnly (hosted installs set it): the Python pods get no
secrets.llm variables, and validate.yaml refuses, through
futureagi.providerCredentials.errors, every other way a provider credential
could reach a pod other than the gateway, sit inline in the values, or leave
the gateway with an empty key. Without gatewayOnly the chart keeps its
behaviour: secrets.llm feeds the gateway and the Python pods.
===================================================================== */}}

{{/* "true" with agentccGateway.providerCredentials.gatewayOnly. */}}
{{- define "futureagi.providerCredentials.gatewayOnly" -}}
{{- if dig "providerCredentials" "gatewayOnly" false .Values.agentccGateway -}}true{{- end -}}
{{- end -}}

{{/* The names the gateway reads from agentccGateway.providerCredentials.existingSecret,
as a JSON list: keys without duplicates, empty without the Secret. */}}
{{- define "futureagi.providerCredentials.keys" -}}
{{- $pc := .Values.agentccGateway.providerCredentials | default dict -}}
{{- if $pc.existingSecret -}}
{{- $pc.keys | default list | uniq | toJson -}}
{{- else -}}
[]
{{- end -}}
{{- end -}}

{{/* The provider variable names refused on every path but the gateway's
Secret with gatewayOnly, besides every name in keys: the ${VAR} names of
agentcc-gateway/config.example.yaml's providers, the names secrets.llm sets
and the speech vendors'. */}}
{{- define "futureagi.providerCredentials.fixedNames" -}}
{{- list
      "OPENAI_API_KEY" "ANTHROPIC_API_KEY" "GEMINI_API_KEY" "GOOGLE_API_KEY" "OPENROUTER_API_KEY"
      "AWS_ACCESS_KEY_ID" "AWS_SECRET_ACCESS_KEY" "AWS_SESSION_TOKEN"
      "AZURE_CLIENT_ID" "AZURE_CLIENT_SECRET" "AZURE_TENANT_ID"
      "AZURE_OPENAI_KEY" "AZURE_OPENAI_API_KEY" "AZURE_AD_TOKEN" "GOOGLE_ACCESS_TOKEN"
      "XAI_API_KEY" "COHERE_API_KEY" "MISTRAL_API_KEY" "GROQ_API_KEY" "TOGETHER_API_KEY"
      "FIREWORKS_API_KEY" "DEEPINFRA_API_KEY" "PERPLEXITY_API_KEY" "CEREBRAS_API_KEY"
      "DEEPGRAM_API_KEY" "CARTESIA_API_KEY" "ELEVEN_API_KEY" "ELEVENLABS_API_KEY" | toJson -}}
{{- end -}}

{{/* Admission normal form only: never rewrite a caller's Kubernetes name.
Reject malformed names separately, and compare normalized names even when a
legacy unquoted YAML emitter would trim padding or consume quote characters. */}}
{{- define "futureagi.providerCredentials.name" -}}
{{- . | toString | trim | trimAll "\"'" | trim -}}
{{- end -}}

{{- define "futureagi.providerCredentials.secretNameErrors" -}}
{{- $raw := .name | default "" | toString -}}
{{- $name := include "futureagi.providerCredentials.name" $raw -}}
{{- $errors := list -}}
{{- if and $raw (or (ne $raw $name) (gt (len $name) 253) (not (regexMatch "^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$" $name))) -}}
{{- $errors = append $errors (printf "%s: Secret names must be DNS subdomains without padding or quotes" .path) -}}
{{- end -}}
{{- toJson $errors -}}
{{- end -}}

{{/* Every chart-selected non-provider Secret reader. Generic extraVolumes
and envFrom sources are checked below. Optional readers follow their producer
conditions, including effective helper fallbacks, not the ESO writer census. */}}
{{- define "futureagi.providerCredentials.secretReaders" -}}
{{- $v := .Values -}}
{{- $readers := dict -}}
{{- range $group, $target := include "futureagi.externalSecrets.targets" . | fromJson -}}
{{- if not (has $group (list "llm" "providerCredentials")) -}}
{{- $_ := set $readers $target.path $target.name -}}
{{- end -}}
{{- end -}}
{{- $_ := set $readers "the chart's own Secret" (include "futureagi.secretName" .) -}}
{{- $_ := set $readers "global.caBundle.secret" (dig "caBundle" "secret" "" ($v.global | default dict)) -}}
{{- if include "futureagi.omega.enabled" . -}}
{{- $o := $v.omega -}}
{{- $_ := set $readers "omega.hostEvents.existingSecret" $o.hostEvents.existingSecret -}}
{{- $_ := set $readers "omega.issuer.privateKey.existingSecret" $o.issuer.privateKey.existingSecret -}}
{{- $ca := include "futureagi.omega.hostEvents.ca" . | fromJson -}}
{{- if eq (get $ca "kind") "secret" -}}
{{- $path := ternary "omega.hostEvents.ca.existingSecret" "omega.gateway.upstreamCa.existingSecret" (ne (include "futureagi.providerCredentials.name" ($o.hostEvents.ca.existingSecret | default "")) "") -}}
{{- $_ := set $readers $path $ca.name -}}
{{- end -}}
{{- if include "futureagi.omega.internalTls" . -}}
{{- $_ := set $readers "omega.internalTls.existingSecret" (include "futureagi.omega.internalTls.secretName" .) -}}
{{- end -}}
{{- $_ := set $readers "omega.gateway.jwks.existingSecret" $o.gateway.jwks.existingSecret -}}
{{- $_ := set $readers "omega.gateway.upstreamCa.existingSecret" $o.gateway.upstreamCa.existingSecret -}}
{{- $_ := set $readers "omega.gateway.rateLimitRedis.existingSecret" $o.gateway.rateLimitRedis.existingSecret -}}
{{- if $o.gateway.otlp.tracesUrl -}}
{{- $_ := set $readers "omega.gateway.otlp.credentials.existingSecret" $o.gateway.otlp.credentials.existingSecret -}}
{{- end -}}
{{- end -}}
{{- if include "futureagi.hub.enabled" . -}}
{{- $_ := set $readers "hub.checker.tokenSecret" (include "futureagi.hub.tokenSecretName" .) -}}
{{- $_ := set $readers "hub.checker.runner.tlsSecret" $v.hub.checker.runner.tlsSecret -}}
{{- end -}}
{{- toJson $readers -}}
{{- end -}}

{{/* Hosted reserved names: a single policy for plain and Secret-backed
sources. Include the release and shared Redis pins as well as test endpoints. */}}
{{- define "futureagi.providerCredentials.hostedReserved" -}}
{{- $name := include "futureagi.providerCredentials.name" . | upper -}}
{{- if or (has $name (list
  "AGENTCC_DEPLOYMENT_PROFILE" "AGENTCC_HOSTED_ONEKEY_ACTIVATION" "AGENTCC_HOSTED_RELEASE_REVISION"
  "AGENTCC_ALLOW_INSECURE_ADMIN_TOKEN" "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS" "AGENTCC_AUTH_ENABLED"
  "AGENTCC_SYNC_ON_STARTUP" "AGENTCC_SYNC_INTERVAL" "AGENTCC_CONTROL_PLANE_URL" "AGENTCC_CONTROL_PLANE_TOKEN"
  "AGENTCC_MCP_EGRESS_PRIVATE_ALLOW" "AGENTCC_INTERNAL_API_KEY" "AGENTCC_ADMIN_TOKEN" "AGENTCC_WEBHOOK_SECRET"
  "INTEGRATION_ENCRYPTION_KEY" "NO_STARTUP_DB_MUTATIONS" "STARTUP_DB_MUTATION_MODE" "REGISTER_TEMPORAL_SCHEDULES"
  "DJANGO_CACHE_BACKEND" "AGENTCC_REDIS_ADDRESS" "AGENTCC_REDIS_DB" "AGENTCC_REDIS_PASSWORD"))
  (hasPrefix "AGENTCC_TEST_" $name) (hasPrefix "INTEGRATIONS_TEST_" $name) -}}true{{- end -}}
{{- end -}}

{{/* The refusals, as a JSON list of fixed messages that name key paths and
variable names only: a value never reaches an error. validate.yaml reports
them with its own. */}}
{{- define "futureagi.providerCredentials.errors" -}}
{{- $v := .Values -}}
{{- $g := $v.agentccGateway -}}
{{- $pc := $g.providerCredentials | default dict -}}
{{- $gatewayOnly := eq (include "futureagi.providerCredentials.gatewayOnly" .) "true" -}}
{{- $secret := include "futureagi.providerCredentials.name" ($pc.existingSecret | default "") -}}
{{- $keys := $pc.keys | default list -}}
{{- /* Keep original names too: config expansion is case-sensitive, so a
listed lowercase custom variable must remain protected outside credentials. */ -}}
{{- $names := $keys -}}
{{- range concat (include "futureagi.providerCredentials.fixedNames" . | fromJsonArray) $keys -}}
{{- $names = append $names (include "futureagi.providerCredentials.name" . | upper) -}}
{{- end -}}
{{- $names = uniq $names -}}
{{- $errors := list -}}
{{- if and $keys (not $secret) -}}
{{- $errors = append $errors "agentccGateway.providerCredentials.keys needs agentccGateway.providerCredentials.existingSecret" -}}
{{- end -}}
{{- if and $secret (not $keys) -}}
{{- $errors = append $errors "agentccGateway.providerCredentials.existingSecret needs agentccGateway.providerCredentials.keys" -}}
{{- end -}}
{{- if $gatewayOnly -}}
{{- if not $secret -}}
{{- $errors = append $errors "agentccGateway.providerCredentials.existingSecret is required with agentccGateway.providerCredentials.gatewayOnly: the Secret holding the gateway's provider credentials" -}}
{{- end -}}
{{- if not $keys -}}
{{- $errors = append $errors "agentccGateway.providerCredentials.keys is required with agentccGateway.providerCredentials.gatewayOnly: the variable names the gateway reads from that Secret" -}}
{{- end -}}
{{- /* secrets.llm feeds the Python pods, and its references are optional (a
missing key starts the gateway with an empty one): neither fits a credential
only the gateway may read. */ -}}
{{- range $field := list "openaiApiKey" "anthropicApiKey" "googleApiKey" "awsAccessKeyId" "awsSecretAccessKey" -}}
{{- if get $v.secrets.llm $field -}}
{{- $errors = append $errors (printf "secrets.llm.%s: inline provider keys are refused with agentccGateway.providerCredentials.gatewayOnly; put them in the Secret agentccGateway.providerCredentials.existingSecret names" $field) -}}
{{- end -}}
{{- end -}}
{{- if $v.secrets.llm.existingSecret -}}
{{- $errors = append $errors "secrets.llm.existingSecret: refused with agentccGateway.providerCredentials.gatewayOnly (it reaches the Python pods and its references are optional); use agentccGateway.providerCredentials.existingSecret" -}}
{{- end -}}
{{- /* a provider credential as a plain value in the gateway's pod spec */ -}}
{{- range $raw := keys ($g.extraEnv | default dict) | sortAlpha -}}
{{- $name := include "futureagi.providerCredentials.name" $raw | upper -}}
{{- if not (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $raw) -}}
{{- $errors = append $errors "agentccGateway.extraEnv: environment names must be plain variable names" -}}
{{- end -}}
{{- if and (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $name) (has $name $names) -}}
{{- $errors = append $errors (printf "agentccGateway.extraEnv.%s: a provider credential may not be a plain pod-spec value; list it in agentccGateway.providerCredentials.keys" $name) -}}
{{- end -}}
{{- end -}}
{{- /* a provider credential on any path that reaches a pod other than the gateway */ -}}
{{- $paths := list
      (list "config.extraEnv" ($v.config.extraEnv | default dict))
      (list "backend.extraEnv" ($v.backend.extraEnv | default dict))
      (list "worker.extraEnv" ($v.worker.extraEnv | default dict))
      (list "frontend.extraEnv" ($v.frontend.extraEnv | default dict))
      (list "serving.extraEnv" ($v.serving.extraEnv | default dict))
      (list "codeExecutor.extraEnv" ($v.codeExecutor.extraEnv | default dict))
      (list "fiCollector.extraEnv" ($v.fiCollector.extraEnv | default dict))
      (list "secrets.extra" ($v.secrets.extra | default dict))
      (list "hub.checker.extraEnv" ($v.hub.checker.extraEnv | default dict))
      (list "omega.gateway.extraEnv" ($v.omega.gateway.extraEnv | default dict)) -}}
{{- range $i, $queue := $v.worker.queues | default list -}}
{{- $paths = append $paths (list (printf "worker.queues[%d].extraEnv" $i) (dig "extraEnv" dict $queue)) -}}
{{- end -}}
{{- range $entry := $paths -}}
{{- $where := index $entry 0 -}}
{{- range $raw := keys (index $entry 1) | sortAlpha -}}
{{- $name := include "futureagi.providerCredentials.name" $raw | upper -}}
{{- if not (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $raw) -}}
{{- $errors = append $errors (printf "%s: environment names must be plain variable names" $where) -}}
{{- end -}}
{{- if and (regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $name) (has $name $names) -}}
{{- $errors = append $errors (printf "%s.%s: provider credentials reach only the gateway with agentccGateway.providerCredentials.gatewayOnly" $where $name) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* Opaque sources cannot be checked for provider names. There is no implicit
app-Secret exception: use the chart's explicit app secretKeyRefs instead. */ -}}
{{- if $v.config.extraEnvFrom -}}
{{- $errors = append $errors "config.extraEnvFrom: opaque sources are refused with agentccGateway.providerCredentials.gatewayOnly; use explicit app Secret references or secrets.extra" -}}
{{- end -}}
{{- if $g.extraEnvFrom -}}
{{- $errors = append $errors "agentccGateway.extraEnvFrom: refused with agentccGateway.providerCredentials.gatewayOnly; use agentccGateway.providerCredentials" -}}
{{- end -}}
{{- if $g.existingConfigMap -}}
{{- $errors = append $errors "agentccGateway.existingConfigMap: refused with agentccGateway.providerCredentials.gatewayOnly; use checked inline config with Secret-backed references" -}}
{{- end -}}
{{- /* Both gateway credential Secrets: no other pod reads them as environment
or volume, and no shared chart Secret has either name. They may share each
other's name because both uses are exclusive to the gateway. */ -}}
{{- $readers := include "futureagi.providerCredentials.secretReaders" . | fromJson -}}
{{- range $path, $name := $readers -}}
{{- $errors = concat $errors (include "futureagi.providerCredentials.secretNameErrors" (dict "path" $path "name" $name) | fromJsonArray) -}}
{{- end -}}
{{- range $secretPath, $rawSecret := dict "agentccGateway.providerCredentials.existingSecret" ($pc.existingSecret | default "") "agentccGateway.gcpCredentials.existingSecret" ($g.gcpCredentials.existingSecret | default "") -}}
{{- $errors = concat $errors (include "futureagi.providerCredentials.secretNameErrors" (dict "path" $secretPath "name" $rawSecret) | fromJsonArray) -}}
{{- $secret := include "futureagi.providerCredentials.name" $rawSecret -}}
{{- if $secret -}}
{{- range $source := $v.config.extraEnvFrom | default list -}}
{{- if eq (include "futureagi.providerCredentials.name" (dig "secretRef" "name" "" $source)) $secret -}}
{{- $errors = append $errors (printf "config.extraEnvFrom: it reads %s, which only the gateway may read" $secretPath) -}}
{{- end -}}
{{- end -}}
{{- range $component := list "backend" "worker" "fiCollector" "bootstrap" -}}
{{- $mounted := false -}}
{{- range (index $v $component).extraVolumes | default list -}}
{{- $raw := dig "secret" "secretName" "" . -}}
{{- $errors = concat $errors (include "futureagi.providerCredentials.secretNameErrors" (dict "path" (printf "%s.extraVolumes" $component) "name" $raw) | fromJsonArray) -}}
{{- if eq (include "futureagi.providerCredentials.name" $raw) $secret -}}{{- $mounted = true -}}{{- end -}}
{{- range dig "projected" "sources" list . -}}
{{- $raw := dig "secret" "name" "" . -}}
{{- $errors = concat $errors (include "futureagi.providerCredentials.secretNameErrors" (dict "path" (printf "%s.extraVolumes" $component) "name" $raw) | fromJsonArray) -}}
{{- if eq (include "futureagi.providerCredentials.name" $raw) $secret -}}{{- $mounted = true -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- if $mounted -}}
{{- $errors = append $errors (printf "%s.extraVolumes: it mounts %s, which only the gateway may read" $component $secretPath) -}}
{{- end -}}
{{- end -}}
{{- range $path, $name := $readers -}}
{{- if eq $secret (include "futureagi.providerCredentials.name" ($name | default "")) -}}
{{- $errors = append $errors (printf "%s must name a Secret no other workload reads (it equals %s)" $secretPath $path) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* The inline gateway configuration: a credential field is a ${VAR}
reference to a name the gateway reads from the Secret, or (api_key only, the
one field the gateway resolves them in) a secret URI; never a literal, and
never a variable the gateway has no Secret key for (an unset ${VAR} expands
to an empty key and the provider starts without credentials). An
existingConfigMap is not visible here. */ -}}
{{- if not $g.existingConfigMap -}}
{{- $config := $g.config | default dict -}}
{{- $walk := dict "errors" list "secretURI" false -}}
{{- include "futureagi.providerConfig.walk" (dict "root" . "state" $walk "parts" list "node" $config "keys" $keys "names" $names) -}}
{{- $errors = concat $errors $walk.errors -}}
{{- if and $walk.secretURI (not (or (dig "serviceAccount" "create" false $g) (dig "serviceAccount" "name" "" $g))) -}}
{{- $errors = append $errors "agentccGateway.serviceAccount: a dedicated account is required for secret URIs with agentccGateway.providerCredentials.gatewayOnly" -}}
{{- end -}}
{{- range $id, $provider := $config.providers | default dict -}}
{{- if kindIs "map" $provider -}}
{{- range $field := list "api_key" "aws_access_key_id" "aws_secret_access_key" "aws_session_token" "service_account_json" -}}
{{- $text := get $provider $field | default "" | toString -}}
{{- $var := "" -}}
{{- if regexMatch "^\\$\\{[A-Za-z_][A-Za-z0-9_]*\\}$" $text -}}
{{- $var = trimSuffix "}" (trimPrefix "${" $text) -}}
{{- else if regexMatch "^\\$[A-Za-z_][A-Za-z0-9_]*$" $text -}}
{{- $var = trimPrefix "$" $text -}}
{{- end -}}
{{- if and $text (not $var) -}}
{{- if ne $field "api_key" -}}
{{- $errors = append $errors (printf "agentccGateway.config.providers.%s.%s must be a ${VAR} reference, never a literal (the gateway resolves secret URIs in api_key only)" $id $field) -}}
{{- else if not (regexMatch "^(vault|aws-sm|gcp-sm|azure-kv)://" $text) -}}
{{- $errors = append $errors (printf "agentccGateway.config.providers.%s.api_key must be a ${VAR} reference or a secret URI (vault://, aws-sm://, gcp-sm://, azure-kv://), never a literal" $id) -}}
{{- end -}}
{{- else if and $var (not (has $var $keys)) -}}
{{- $errors = append $errors (printf "agentccGateway.config.providers.%s.%s references a variable that is not in agentccGateway.providerCredentials.keys" $id $field) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* A stdio MCP server's child process inherits the gateway's whole
environment (agentcc-gateway/internal/mcp/transport.go). */ -}}
{{- $mcp := $config.mcp | default dict -}}
{{- range $id, $server := $mcp.servers | default dict -}}
{{- if and (kindIs "map" $server) (or (eq (get $server "transport" | toString) "stdio") (get $server "command")) -}}
{{- $errors = append $errors (printf "agentccGateway.config.mcp.servers.%s: a stdio MCP server inherits the gateway's environment, provider credentials included; refused with agentccGateway.providerCredentials.gatewayOnly" $id) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- toJson $errors -}}
{{- end -}}
