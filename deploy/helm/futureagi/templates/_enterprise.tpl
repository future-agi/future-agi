{{/* =====================================================================
Enterprise and hardened-operations helpers: license, SSO, proxy and CA
bundle, air-gap, Reloader annotations and OpenShift security contexts.
===================================================================== */}}

{{/* Where the license comes from: "existingSecret", "key", "legacy"
(secrets.eeLicenseKey) or "" (none). */}}
{{- define "futureagi.license.source" -}}
{{- $l := .Values.license | default dict -}}
{{- if $l.existingSecret -}}existingSecret
{{- else if $l.key -}}key
{{- else if .Values.secrets.eeLicenseKey -}}legacy
{{- end -}}
{{- end -}}

{{/* "true" when the license heartbeat runs: license.heartbeat when set,
else on unless global.airgap. */}}
{{- define "futureagi.license.heartbeat" -}}
{{- $hb := dig "heartbeat" nil (.Values.license | default dict) -}}
{{- if or (kindIs "invalid" $hb) (eq (toString $hb) "") -}}
{{- ternary "false" "true" (eq (toString .Values.global.airgap) "true") -}}
{{- else -}}
{{- ternary "true" "false" (has (toString $hb | lower) (list "true" "1" "yes" "on")) -}}
{{- end -}}
{{- end -}}

{{/* Secret-backed env entries (secretEnv dicts, as JSON) for the license, the
SSO clients and email: dict "root" $. */}}
{{- define "futureagi.env.enterpriseSecrets" -}}
{{- $root := .root -}}
{{- $v := $root.Values -}}
{{- $chartSecret := include "futureagi.secretName" $root -}}
{{- $out := list -}}
{{- $license := include "futureagi.license.source" $root -}}
{{- if eq $license "existingSecret" -}}
{{- $out = append $out (dict "name" "EE_LICENSE_KEY" "secret" $v.license.existingSecret "key" ($v.license.existingSecretKey | default "EE_LICENSE_KEY")) -}}
{{- else if $license -}}
{{- $out = append $out (dict "name" "EE_LICENSE_KEY" "secret" $chartSecret "key" "EE_LICENSE_KEY") -}}
{{- end -}}
{{- $email := $v.config.email | default dict -}}
{{- if $email.existingSecret -}}
{{- $out = append $out (dict "name" "MAILGUN_API_KEY" "secret" $email.existingSecret "key" ($email.existingSecretKey | default "MAILGUN_API_KEY")) -}}
{{- else if $v.secrets.mailgunApiKey -}}
{{- $out = append $out (dict "name" "MAILGUN_API_KEY" "secret" $chartSecret "key" "MAILGUN_API_KEY") -}}
{{- end -}}
{{- range $provider, $names := include "futureagi.sso.envNames" $root | fromYaml -}}
{{- $p := dig $provider dict ($v.auth | default dict) -}}
{{- if $p.existingSecret -}}
{{- $out = append $out (dict "name" $names.id "secret" $p.existingSecret "key" ($p.clientIdKey | default $names.id)) -}}
{{- $out = append $out (dict "name" $names.secret "secret" $p.existingSecret "key" ($p.clientSecretKey | default $names.secret)) -}}
{{- else if $p.clientSecret -}}
{{- $out = append $out (dict "name" $names.secret "secret" $chartSecret "key" $names.secret) -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* The environment variables each OAuth provider's client ID and secret go
to (Google's are named AUTH0_* in the app). */}}
{{- define "futureagi.sso.envNames" -}}
google: {id: AUTH0_CLIENT_ID, secret: AUTH0_CLIENT_SECRET, callback: /saml2_auth/auth/callback/, label: Google}
github: {id: GITHUB_CLIENT_ID, secret: GITHUB_CLIENT_SECRET, callback: /saml2_auth/github/callback/, label: GitHub}
microsoft: {id: MICROSOFT_CLIENT_ID, secret: MICROSOFT_CLIENT_SECRET, callback: /saml2_auth/microsoft/callback/, label: Microsoft}
{{- end -}}

{{/* The Secret each externalSecrets.secrets group creates, as JSON
{group: {path, name}}: path is the existingSecret value that names it, name
that value ("" when unset). external-secrets.yaml writes that Secret and
validate.yaml requires the name. */}}
{{- define "futureagi.externalSecrets.targets" -}}
{{- $out := dict -}}
{{- range $group, $path := dict
      "app" "secrets.existingSecret"
      "llm" "secrets.llm.existingSecret"
      "license" "license.existingSecret"
      "email" "config.email.existingSecret"
      "google" "auth.google.existingSecret"
      "github" "auth.github.existingSecret"
      "microsoft" "auth.microsoft.existingSecret"
      "admin" "bootstrap.admin.existingSecret"
      "postgres" "postgres.existingSecret"
      "clickhouse" "clickhouse.existingSecret"
      "redis" "redis.existingSecret"
      "objectStorage" "objectStorage.existingSecret" -}}
{{- $name := $.Values -}}
{{- range splitList "." $path }}{{ $name = get ($name | default dict) . }}{{ end -}}
{{- $_ := set $out $group (dict "path" $path "name" ($name | default "" | toString)) -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* Providers with a client configured, as a list of names. */}}
{{- define "futureagi.sso.enabled" -}}
{{- $out := list -}}
{{- range $provider, $names := include "futureagi.sso.envNames" . | fromYaml -}}
{{- $p := dig $provider dict ($.Values.auth | default dict) -}}
{{- if or $p.existingSecret $p.clientId $p.clientSecret }}{{ $out = append $out $provider }}{{ end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* Plain license and SSO variables of the Python processes, as YAML. */}}
{{- define "futureagi.env.enterprisePlain" -}}
{{- $v := .Values -}}
{{- $out := dict -}}
{{- $l := $v.license | default dict -}}
{{- if include "futureagi.license.source" . -}}
{{- $_ := set $out "EE_LICENSE_CLOCK_SKEW_SECONDS" (toString ($l.clockSkewSeconds | default 300)) -}}
{{- end -}}
{{- with $l.url }}{{ $_ := set $out "FUTURE_AGI_LICENSE_URL" . }}{{ end -}}
{{- with $l.publicKey }}{{ $_ := set $out "EE_LICENSE_PUBLIC_KEY" . }}{{ end -}}
{{- if ne (include "futureagi.license.heartbeat" .) "true" -}}
{{- $_ := set $out "FUTURE_AGI_ENTERPRISE_HEARTBEAT_DISABLED" "true" -}}
{{- end -}}
{{- range $provider, $names := include "futureagi.sso.envNames" . | fromYaml -}}
{{- $p := dig $provider dict ($v.auth | default dict) -}}
{{- if and $p.clientId (not $p.existingSecret) }}{{ $_ := set $out $names.id $p.clientId }}{{ end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/* ---------------------------------------------------------------------
Proxy, CA bundle and air-gap
--------------------------------------------------------------------- */}}

{{/* "true" when global.caBundle names a ConfigMap or Secret. */}}
{{- define "futureagi.caBundle.enabled" -}}
{{- $ca := .Values.global.caBundle | default dict -}}
{{- if or $ca.configMap $ca.secret -}}true{{- end -}}
{{- end -}}

{{- define "futureagi.caBundle.path" -}}/etc/futureagi/ca/ca.crt{{- end -}}

{{- define "futureagi.caBundle.volume" -}}
{{- if include "futureagi.caBundle.enabled" . -}}
{{- $ca := .Values.global.caBundle -}}
- name: ca-bundle
  {{- if $ca.configMap }}
  configMap:
    name: {{ $ca.configMap }}
  {{- else }}
  secret:
    secretName: {{ $ca.secret }}
  {{- end }}
    items:
      - key: {{ $ca.key | default "ca.crt" }}
        path: ca.crt
{{- end -}}
{{- end -}}

{{- define "futureagi.caBundle.volumeMount" -}}
{{- if include "futureagi.caBundle.enabled" . -}}
- name: ca-bundle
  mountPath: /etc/futureagi/ca
  readOnly: true
{{- end -}}
{{- end -}}

{{/* NO_PROXY: localhost, the cluster's service suffixes, every Service of the
release, the PostgreSQL, ClickHouse, Redis and Temporal hosts, then
global.proxy.noProxy. An external object-storage endpoint is left out: a
public S3 endpoint may only be reachable through the proxy. */}}
{{- define "futureagi.noProxy" -}}
{{- $root := . -}}
{{- $v := .Values -}}
{{- $ns := .Release.Namespace -}}
{{- $hosts := list "localhost" "127.0.0.1" "::1" (printf ".%s" $ns) (printf ".%s.svc" $ns) ".svc" ".cluster.local" -}}
{{- range $c := list "backend" "frontend" "fi-collector" "agentcc-gateway" "serving" "code-executor" "postgres" "clickhouse" "redis" "temporal" "minio" -}}
{{- $hosts = append $hosts (include "futureagi.component" (dict "root" $root "component" $c)) -}}
{{- end -}}
{{- $hosts = append $hosts (include "futureagi.postgres.host" $root) -}}
{{- $hosts = append $hosts (include "futureagi.clickhouse.host" $root) -}}
{{- $hosts = append $hosts (include "futureagi.redis.host" $root) -}}
{{- $hosts = append $hosts (include "futureagi.temporal.address" $root | splitList ":" | first) -}}
{{- with dig "pooler" "host" "" (.Values.postgres | default dict) }}{{ $hosts = append $hosts . }}{{ end -}}
{{- with dig "readReplica" "host" "" (.Values.postgres | default dict) }}{{ $hosts = append $hosts . }}{{ end -}}
{{- range splitList "," (dig "proxy" "noProxy" "" $v.global | toString) }}{{ with trim . }}{{ $hosts = append $hosts . }}{{ end }}{{ end -}}
{{- $hosts | compact | uniq | join "," -}}
{{- end -}}

{{/* Chart-level environment shared by the Future AGI pods: the proxy, the CA
bundle and the air-gap switches, as YAML. dict "root" $ "kind" <kind>, where
kind is python (backend, workers, bootstrap), serving, collector, gateway or
frontend. A component's extraEnv wins over all of it. */}}
{{- define "futureagi.env.platform" -}}
{{- $root := .root -}}
{{- $v := $root.Values -}}
{{- $kind := .kind -}}
{{- $out := dict -}}
{{- $proxy := $v.global.proxy | default dict -}}
{{- if or $proxy.httpProxy $proxy.httpsProxy -}}
{{- $noProxy := include "futureagi.noProxy" $root -}}
{{- with $proxy.httpProxy }}{{ $_ := set $out "HTTP_PROXY" . }}{{ $_ := set $out "http_proxy" . }}{{ end -}}
{{- with $proxy.httpsProxy }}{{ $_ := set $out "HTTPS_PROXY" . }}{{ $_ := set $out "https_proxy" . }}{{ end -}}
{{- $_ := set $out "NO_PROXY" $noProxy -}}
{{- $_ := set $out "no_proxy" $noProxy -}}
{{- end -}}
{{- if and (include "futureagi.caBundle.enabled" $root) (ne $kind "frontend") -}}
{{- $path := include "futureagi.caBundle.path" $root -}}
{{- $_ := set $out "SSL_CERT_FILE" $path -}}
{{- if has $kind (list "python" "serving") -}}
{{- $_ := set $out "REQUESTS_CA_BUNDLE" $path -}}
{{- $_ := set $out "CURL_CA_BUNDLE" $path -}}
{{- $_ := set $out "NODE_EXTRA_CA_CERTS" $path -}}
{{- end -}}
{{- if and (has $kind (list "python" "collector")) (eq $v.postgres.mode "external") (has $v.postgres.external.sslMode (list "verify-ca" "verify-full")) -}}
{{- $_ := set $out "PGSSLROOTCERT" $path -}}
{{- end -}}
{{- end -}}
{{- if eq (toString $v.global.airgap) "true" -}}
{{- if has $kind (list "python" "serving") -}}
{{- $_ := set $out "LITELLM_LOCAL_MODEL_COST_MAP" "True" -}}
{{- end -}}
{{- if eq $kind "serving" -}}
{{- $_ := set $out "HF_HUB_OFFLINE" "1" -}}
{{- $_ := set $out "TRANSFORMERS_OFFLINE" "1" -}}
{{- end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/* ---------------------------------------------------------------------
Workload metadata and security contexts
--------------------------------------------------------------------- */}}

{{/* Deployment metadata annotations: reloader.annotations when
reloader.enabled. */}}
{{- define "futureagi.workloadAnnotations" -}}
{{- if dig "enabled" false (.Values.reloader | default dict) -}}
{{- with .Values.reloader.annotations }}
annotations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
{{- end -}}

{{/* "true" when security contexts leave the IDs and seccomp profile to the
platform: global.compatibility.openshift.adaptSecurityContext force, or auto
on a cluster that serves security.openshift.io/v1. */}}
{{- define "futureagi.openshift.adapt" -}}
{{- $mode := dig "compatibility" "openshift" "adaptSecurityContext" "auto" .Values.global | toString -}}
{{- if or (eq $mode "force") (and (eq $mode "auto") (.Capabilities.APIVersions.Has "security.openshift.io/v1")) -}}true{{- end -}}
{{- end -}}
