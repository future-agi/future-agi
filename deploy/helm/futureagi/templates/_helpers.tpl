{{/* =====================================================================
Names and labels
===================================================================== */}}

{{- define "futureagi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Resource name prefix: the release name, plus "-futureagi" unless it
already contains the chart name. Components append "-<component>", so the
prefix is capped to leave room for the longest suffix. */}}
{{- define "futureagi.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 40 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 40 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 40 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Name of one component's resources: <fullname>-<component>. */}}
{{- define "futureagi.component" -}}
{{- printf "%s-%s" (include "futureagi.fullname" .root) .component | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "futureagi.labels" -}}
helm.sh/chart: {{ include "futureagi.chart" . }}
app.kubernetes.io/name: {{ include "futureagi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: futureagi
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* Labels of one component: dict "root" $ "component" "backend". */}}
{{- define "futureagi.componentLabels" -}}
{{ include "futureagi.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "futureagi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "futureagi.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Pod labels of a bundled datastore: no chart or app version, so a chart
upgrade does not restart it. dict "root" $ "component" "postgres". */}}
{{- define "futureagi.datastorePodLabels" -}}
{{ include "futureagi.selectorLabels" . }}
app.kubernetes.io/part-of: futureagi
{{- with .root.Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* =====================================================================
Images: dict "root" $ "image" <image values> ["fallback" <image values>]
["digestKey" <key of image.digests>]
Future AGI images fall back to image.tag, then the chart's appVersion.

Digest precedence: the component's own image.digest; else the digest stamped
into image.digests.<digestKey> by the release packaging, but only when
image.pinDigests is on, the resolved tag is the chart's appVersion and the
repository is the published one. So `--set image.tag=...` or another
repository path never pairs another image with the release's digest. The
registry is not compared: a mirror (image.registry, global.imageRegistry)
keeps the digest, as `crane copy` and `oras copy -r` do; a different build
pushed under the same repository path needs image.pinDigests=false or its own
image.digest. An image without a digestKey (a bundled datastore) takes the
digest in futureagi.datastorePins for its exact repository:tag, under the
same switch.
===================================================================== */}}

{{/* Published repository of each image.digests key. */}}
{{- define "futureagi.publishedRepositories" -}}
{{- toJson (dict
      "backend" "futureagi/future-agi"
      "frontend" "futureagi/frontend"
      "fiCollector" "futureagi/fi-collector"
      "agentccGateway" "futureagi/agentcc-gateway"
      "serving" "futureagi/serving"
      "codeExecutor" "futureagi/code-executor") -}}
{{- end -}}

{{/* Digests of the bundled datastore images the chart was tested with, by
repository:tag. A bundled datastore image without its own image.digest runs
pinned only on exactly this repository and tag (and while image.pinDigests is
on): another tag or repository runs by tag. Keep the tags in step with
values.yaml; hack/rendered_checks.py fails when the default render is not
pinned. */}}
{{- define "futureagi.datastorePins" -}}
{{- toJson (dict
      "library/postgres:16.15-trixie" "sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54"
      "library/redis:7.4.11-alpine" "sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499"
      "coollabsio/minio:RELEASE.2025-10-15T17-29-55Z" "sha256:69b55a1c1c5dc285ce04db96689f5b2102317fc77a50680a1874ca6efd1c87f9") -}}
{{- end -}}

{{- define "futureagi.image" -}}
{{- $root := .root -}}
{{- $img := .image -}}
{{- $fallback := .fallback | default dict -}}
{{- $registry := $img.registry | default $fallback.registry | default $root.Values.image.registry -}}
{{- if $root.Values.global.imageRegistry -}}
{{- $registry = $root.Values.global.imageRegistry -}}
{{- end -}}
{{- $repository := $img.repository | default $fallback.repository -}}
{{- $tag := $img.tag | default $fallback.tag | default $root.Values.image.tag | default $root.Chart.AppVersion | toString -}}
{{- $digest := $img.digest | default $fallback.digest -}}
{{- if and (not $digest) .digestKey $root.Values.image.pinDigests -}}
{{- $stamped := get ($root.Values.image.digests | default dict) .digestKey | default "" -}}
{{- $published := get (include "futureagi.publishedRepositories" $root | fromJson) .digestKey | default "" -}}
{{- if and $stamped (eq $tag (toString $root.Chart.AppVersion)) (eq $repository $published) -}}
{{- $digest = $stamped -}}
{{- end -}}
{{- end -}}
{{- if and (not $digest) (not .digestKey) $root.Values.image.pinDigests -}}
{{- $digest = get (include "futureagi.datastorePins" $root | fromJson) (printf "%s:%s" $repository $tag) | default "" -}}
{{- end -}}
{{- $ref := printf "%s:%s" $repository $tag -}}
{{- if $registry -}}
{{- $ref = printf "%s/%s" (trimSuffix "/" $registry) $ref -}}
{{- end -}}
{{- if $digest -}}
{{- $ref = printf "%s@%s" $ref $digest -}}
{{- end -}}
{{- $ref -}}
{{- end -}}

{{- define "futureagi.imagePullPolicy" -}}
{{- $fallback := .fallback | default dict -}}
{{- .image.pullPolicy | default $fallback.pullPolicy | default .root.Values.image.pullPolicy -}}
{{- end -}}

{{- define "futureagi.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* =====================================================================
Service accounts and secrets
===================================================================== */}}

{{- define "futureagi.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- .Values.serviceAccount.name | default (include "futureagi.fullname" .) -}}
{{- else -}}
{{- .Values.serviceAccount.name | default "default" -}}
{{- end -}}
{{- end -}}

{{/* automountServiceAccountToken of the pods that run as the chart's
ServiceAccount (serviceAccount.automountServiceAccountToken). The code sandbox
never mounts it: it runs untrusted code. */}}
{{- define "futureagi.automountServiceAccountToken" -}}
{{- if .Values.serviceAccount.automountServiceAccountToken }}true{{ else }}false{{ end -}}
{{- end -}}

{{- define "futureagi.bootstrapServiceAccountName" -}}
{{- if .Values.bootstrap.serviceAccount.create -}}
{{- .Values.bootstrap.serviceAccount.name | default (include "futureagi.component" (dict "root" . "component" "bootstrap")) -}}
{{- else -}}
{{- .Values.bootstrap.serviceAccount.name | default "default" -}}
{{- end -}}
{{- end -}}

{{/* The chart's own Secret: generated keys, bundled datastore passwords and
secrets given inline in the values. */}}
{{- define "futureagi.secretName" -}}
{{- printf "%s-secrets" (include "futureagi.fullname" .) -}}
{{- end -}}

{{/* Where the application keys live. */}}
{{- define "futureagi.appSecretName" -}}
{{- .Values.secrets.existingSecret | default (include "futureagi.secretName" .) -}}
{{- end -}}

{{/* Where AGENTCC_WEBHOOK_SECRET lives: the chart's Secret when it is given
inline, else with the application keys. */}}
{{- define "futureagi.webhookSecretName" -}}
{{- if .Values.secrets.agentccWebhookSecret -}}
{{- include "futureagi.secretName" . -}}
{{- else -}}
{{- include "futureagi.appSecretName" . -}}
{{- end -}}
{{- end -}}

{{/* env entry reading a Secret key: dict "name" "secret" "key" ["optional"]. */}}
{{- define "futureagi.secretEnv" -}}
- name: {{ .name }}
  valueFrom:
    secretKeyRef:
      name: {{ .secret }}
      key: {{ .key }}
      {{- if .optional }}
      optional: true
      {{- end }}
{{- end -}}

{{/* =====================================================================
Datastore endpoints
===================================================================== */}}

{{- define "futureagi.postgres.host" -}}
{{- if eq .Values.postgres.mode "bundled" -}}
{{- include "futureagi.component" (dict "root" . "component" "postgres") -}}
{{- else -}}
{{- .Values.postgres.external.host -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.postgres.port" -}}
{{- if eq .Values.postgres.mode "bundled" -}}5432{{- else -}}{{ .Values.postgres.external.port }}{{- end -}}
{{- end -}}

{{/* dict "root" $ "name" <env name> */}}
{{- define "futureagi.postgres.passwordEnv" -}}
{{- $v := .root.Values.postgres -}}
{{- if $v.existingSecret -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" $v.existingSecret "key" $v.existingSecretPasswordKey) }}
{{- else -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" (include "futureagi.secretName" .root) "key" "PG_PASSWORD") }}
{{- end -}}
{{- end -}}

{{- define "futureagi.clickhouse.host" -}}
{{- if eq .Values.clickhouse.mode "bundled" -}}
{{- include "futureagi.component" (dict "root" . "component" "clickhouse") -}}
{{- else -}}
{{- .Values.clickhouse.external.host -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.clickhouse.httpPort" -}}
{{- if eq .Values.clickhouse.mode "bundled" -}}8123{{- else -}}{{ .Values.clickhouse.external.httpPort }}{{- end -}}
{{- end -}}

{{- define "futureagi.clickhouse.nativePort" -}}
{{- if eq .Values.clickhouse.mode "bundled" -}}9000{{- else -}}{{ .Values.clickhouse.external.nativePort }}{{- end -}}
{{- end -}}

{{/* dict "root" $ "name" <env name> */}}
{{- define "futureagi.clickhouse.passwordEnv" -}}
{{- $v := .root.Values.clickhouse -}}
{{- if $v.existingSecret -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" $v.existingSecret "key" $v.existingSecretPasswordKey) }}
{{- else -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" (include "futureagi.secretName" .root) "key" "CH_PASSWORD") }}
{{- end -}}
{{- end -}}

{{- define "futureagi.redis.host" -}}
{{- if eq .Values.redis.mode "bundled" -}}
{{- include "futureagi.component" (dict "root" . "component" "redis") -}}
{{- else -}}
{{- .Values.redis.external.host -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.redis.port" -}}
{{- if eq .Values.redis.mode "bundled" -}}6379{{- else -}}{{ .Values.redis.external.port }}{{- end -}}
{{- end -}}

{{/* "true" when Redis requires a password. */}}
{{- define "futureagi.redis.auth" -}}
{{- if or (eq .Values.redis.mode "bundled") .Values.redis.password .Values.redis.existingSecret -}}true{{- end -}}
{{- end -}}

{{/* dict "root" $ "name" <env name> */}}
{{- define "futureagi.redis.passwordEnv" -}}
{{- $v := .root.Values.redis -}}
{{- if $v.existingSecret -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" $v.existingSecret "key" $v.existingSecretPasswordKey) }}
{{- else -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" (include "futureagi.secretName" .root) "key" "REDIS_PASSWORD") }}
{{- end -}}
{{- end -}}

{{/* Redis URL for one database number; the password comes from the
REDIS_PASSWORD variable defined earlier in the same env list. */}}
{{- define "futureagi.redis.url" -}}
{{- $root := .root -}}
{{- $scheme := ternary "rediss" "redis" (and (eq $root.Values.redis.mode "external") $root.Values.redis.external.tls) -}}
{{- $auth := ternary ":$(REDIS_PASSWORD)@" "" (eq (include "futureagi.redis.auth" $root) "true") -}}
{{- printf "%s://%s%s:%s/%d" $scheme $auth (include "futureagi.redis.host" $root) (include "futureagi.redis.port" $root) (int .db) -}}
{{- end -}}

{{- define "futureagi.temporal.address" -}}
{{- if eq .Values.temporal.mode "bundled" -}}
{{- printf "%s:7233" (include "futureagi.component" (dict "root" . "component" "temporal")) -}}
{{- else -}}
{{- .Values.temporal.external.address -}}
{{- end -}}
{{- end -}}

{{/* STORAGE_BACKEND actually used. */}}
{{- define "futureagi.objectStorage.backend" -}}
{{- if eq .Values.objectStorage.mode "bundled" -}}minio{{- else -}}{{ .Values.objectStorage.backend }}{{- end -}}
{{- end -}}

{{- define "futureagi.objectStorage.endpoint" -}}
{{- if eq .Values.objectStorage.mode "bundled" -}}
{{- printf "http://%s:9000" (include "futureagi.component" (dict "root" . "component" "minio")) -}}
{{- else -}}
{{- .Values.objectStorage.external.endpoint -}}
{{- end -}}
{{- end -}}

{{/* env entries for one object-storage credential: dict "root" "name" "kind" (access|secret). */}}
{{- define "futureagi.objectStorage.keyEnv" -}}
{{- $v := .root.Values.objectStorage -}}
{{- if $v.existingSecret -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" $v.existingSecret "key" (ternary $v.existingSecretAccessKeyKey $v.existingSecretSecretKeyKey (eq .kind "access"))) }}
{{- else -}}
{{ include "futureagi.secretEnv" (dict "name" .name "secret" (include "futureagi.secretName" .root) "key" (ternary "S3_ACCESS_KEY" "S3_SECRET_KEY" (eq .kind "access"))) }}
{{- end -}}
{{- end -}}

{{/* Any datastore bundled? */}}
{{- define "futureagi.anyBundled" -}}
{{- if or (eq .Values.postgres.mode "bundled") (eq .Values.clickhouse.mode "bundled") (eq .Values.redis.mode "bundled") (eq .Values.temporal.mode "bundled") (eq .Values.objectStorage.mode "bundled") -}}true{{- end -}}
{{- end -}}

{{/* When the bootstrap job runs on install: bootstrap.installHook, with
`auto` resolved (post-install when a bundled datastore must exist first). */}}
{{- define "futureagi.bootstrap.installHook" -}}
{{- if eq .Values.bootstrap.installHook "auto" -}}
{{- ternary "post-install" "pre-install" (eq (include "futureagi.anyBundled" .) "true") -}}
{{- else -}}
{{- .Values.bootstrap.installHook -}}
{{- end -}}
{{- end -}}

{{/* The Argo CD sync wave of the bundled datastores: before the bootstrap
job's (-1, bootstrap/job.yaml), which waits for them, and the rest of the
release's (0). Helm and Flux ignore it. */}}
{{- define "futureagi.datastoreSyncWave" -}}
argocd.argoproj.io/sync-wave: "-2"
{{- end -}}

{{/* =====================================================================
Public URLs
===================================================================== */}}

{{/* https when the ingress terminates TLS for this host. */}}
{{- define "futureagi.hostUrl" -}}
{{- $scheme := "http" -}}
{{- range .root.Values.ingress.tls -}}
{{- if has $.host (.hosts | default list) -}}{{- $scheme = "https" -}}{{- end -}}
{{- end -}}
{{- printf "%s://%s" $scheme .host -}}
{{- end -}}

{{/* Hostname of one Gateway API route: dict "root" $ "route" app|api|otlp.
app and api fall back to the ingress hosts, otlp to the API host. */}}
{{- define "futureagi.gatewayApi.host" -}}
{{- $v := .root.Values -}}
{{- $g := $v.gatewayApi -}}
{{- $app := $g.app.host | default $v.ingress.app.host -}}
{{- $api := $g.api.host | default $v.ingress.api.host -}}
{{- if eq .route "app" -}}{{ $app }}
{{- else if eq .route "api" -}}{{ $api }}
{{- else -}}{{ $g.otlp.host | default $api }}
{{- end -}}
{{- end -}}

{{/* URL of a Gateway API host: https when gatewayApi.tls. */}}
{{- define "futureagi.gatewayApi.url" -}}
{{- $host := include "futureagi.gatewayApi.host" . -}}
{{- if $host -}}{{ printf "%s://%s" (ternary "https" "http" (ne (toString .root.Values.gatewayApi.tls) "false")) $host }}{{- end -}}
{{- end -}}

{{- define "futureagi.url.app" -}}
{{- if .Values.urls.app -}}
{{- trimSuffix "/" .Values.urls.app -}}
{{- else if and .Values.ingress.enabled .Values.ingress.app.host -}}
{{- include "futureagi.hostUrl" (dict "root" . "host" .Values.ingress.app.host) -}}
{{- else if .Values.gatewayApi.enabled -}}
{{- include "futureagi.gatewayApi.url" (dict "root" . "route" "app") -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.url.api" -}}
{{- if .Values.urls.api -}}
{{- trimSuffix "/" .Values.urls.api -}}
{{- else if and .Values.ingress.enabled .Values.ingress.api.host -}}
{{- include "futureagi.hostUrl" (dict "root" . "host" .Values.ingress.api.host) -}}
{{- else if .Values.gatewayApi.enabled -}}
{{- include "futureagi.gatewayApi.url" (dict "root" . "route" "api") -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.url.otlp" -}}
{{- if .Values.urls.otlp -}}
{{- trimSuffix "/" .Values.urls.otlp -}}
{{- else if and .Values.ingress.enabled .Values.ingress.otlp.enabled -}}
{{- $host := .Values.ingress.otlp.host | default .Values.ingress.api.host -}}
{{- if $host -}}{{- include "futureagi.hostUrl" (dict "root" . "host" $host) -}}{{- end -}}
{{- else if and .Values.gatewayApi.enabled .Values.gatewayApi.otlp.enabled -}}
{{- include "futureagi.gatewayApi.url" (dict "root" . "route" "otlp") -}}
{{- end -}}
{{- end -}}

{{/* FI_COLLECTOR_PUBLIC_URL: the OTLP/HTTP base URL SDKs get as FI_BASE_URL.
Without a public one, the port-forward to localhost:4318 the notes print. */}}
{{- define "futureagi.url.collectorPublic" -}}
{{- include "futureagi.url.otlp" . | default "http://localhost:4318" -}}
{{- end -}}

{{/* MINIO_URL: the object URLs handed to browsers. */}}
{{- define "futureagi.url.objects" -}}
{{- if .Values.urls.objects -}}
{{- trimSuffix "/" .Values.urls.objects -}}
{{- else if and .Values.ingress.enabled .Values.ingress.objects.host -}}
{{- include "futureagi.hostUrl" (dict "root" . "host" .Values.ingress.objects.host) -}}
{{- else if and (eq .Values.objectStorage.mode "external") .Values.objectStorage.external.endpoint -}}
{{- trimSuffix "/" .Values.objectStorage.external.endpoint -}}
{{- else -}}
{{- include "futureagi.url.objectsLocal" . -}}
{{- end -}}
{{- end -}}

{{/* The bundled object storage on this machine: its LoadBalancer or NodePort
download port, or the local end of the port-forward the install notes print. */}}
{{- define "futureagi.url.objectsLocal" -}}
{{- printf "http://localhost:%v" (dig "bundled" "service" "downloadPort" 9005 .Values.objectStorage) -}}
{{- end -}}

{{/* "true" when a URL points at this machine (a port-forward): localhost,
*.localhost or a loopback address. */}}
{{- define "futureagi.isLocalUrl" -}}
{{- $host := (urlParse .).hostname | default "" | lower -}}
{{- if or (has $host (list "localhost" "127.0.0.1" "::1" "0.0.0.0")) (hasSuffix ".localhost" $host) -}}true{{- end -}}
{{- end -}}

{{/* scheme://host[:port] of a URL: a CORS origin. */}}
{{- define "futureagi.urlOrigin" -}}
{{- $u := urlParse . -}}
{{- printf "%s://%s" $u.scheme $u.host -}}
{{- end -}}

{{/* Host part of a URL (APP_URL is a bare host). */}}
{{- define "futureagi.urlHost" -}}
{{- /* host[:port] of a URL, login dropped. No urlParse: it fails on a login
with an unescaped '#' or '%', which the schema accepts. The login ends at the
last '@' before the path. */ -}}
{{- $rest := regexReplaceAll "^[A-Za-z][A-Za-z0-9+.-]*://" (toString .) "" -}}
{{- $rest = regexReplaceAll "^[^/]*@" $rest "" -}}
{{- regexFind "^[^/?#]*" $rest -}}
{{- end -}}

{{/* =====================================================================
Pod settings
===================================================================== */}}

{{/* dict "root" $ "uid" <n> ["overrides" <map>]. On OpenShift (see
futureagi.openshift.adapt) the IDs and the seccomp profile are left out, so
the restricted-v2 SCC assigns them. */}}
{{- define "futureagi.podSecurityContext" -}}
{{- $defaults := dict "runAsNonRoot" true "runAsUser" (int .uid) "runAsGroup" (int .uid) "fsGroup" (int .uid) "seccompProfile" (dict "type" "RuntimeDefault") -}}
{{- $ctx := mergeOverwrite (deepCopy $defaults) (.overrides | default dict) -}}
{{- if and .root (include "futureagi.openshift.adapt" .root) -}}
{{- $ctx = omit $ctx "runAsUser" "runAsGroup" "fsGroup" "seccompProfile" -}}
{{- end -}}
{{- toYaml $ctx -}}
{{- end -}}

{{/* dict "root" $ ["overrides" <map>] ["readOnly" false]. */}}
{{- define "futureagi.containerSecurityContext" -}}
{{- $defaults := dict "allowPrivilegeEscalation" false "readOnlyRootFilesystem" (ne (toString .readOnly) "false") "capabilities" (dict "drop" (list "ALL")) -}}
{{- $ctx := mergeOverwrite (deepCopy $defaults) (.overrides | default dict) -}}
{{- if and .root (include "futureagi.openshift.adapt" .root) -}}
{{- $ctx = omit $ctx "runAsUser" "runAsGroup" "seccompProfile" -}}
{{- end -}}
{{- toYaml $ctx -}}
{{- end -}}

{{/* nodeSelector, tolerations, affinity, spread and priority for one pod:
dict "root" $ "values" <component values> ["fallback" <values>] ["spread" true]
["component" <selector component>] ["multi" true].
Each setting: the component's, else the fallback's (worker.* for a queue),
else the top-level one. Spread: an explicit list, whose entries get the
component's labelSelector when they have none, else topologySpread.preset
when the component can run more than one replica ("multi"). */}}
{{- define "futureagi.scheduling" -}}
{{- $root := .root -}}
{{- $c := .values -}}
{{- $f := .fallback | default dict -}}
{{- with ($c.nodeSelector | default $f.nodeSelector | default $root.Values.nodeSelector) }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with ($c.tolerations | default $f.tolerations | default $root.Values.tolerations) }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with ($c.affinity | default $f.affinity | default $root.Values.affinity) }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- if .spread }}
{{- $spread := $c.topologySpreadConstraints | default $f.topologySpreadConstraints | default $root.Values.topologySpreadConstraints | default list -}}
{{- if and $spread .component -}}
{{- $selector := include "futureagi.selectorLabels" (dict "root" $root "component" .component) | fromYaml -}}
{{- $filled := list -}}
{{- range $spread }}
{{- $filled = append $filled (ternary . (merge (dict "labelSelector" (dict "matchLabels" $selector)) .) (hasKey . "labelSelector")) -}}
{{- end }}
{{- $spread = $filled -}}
{{- else if and .component .multi -}}
{{- $spread = include "futureagi.topologySpreadPreset" (dict "root" $root "component" .component) | fromYamlArray -}}
{{- end }}
{{- with $spread }}
topologySpreadConstraints:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
{{- with ($c.priorityClassName | default $f.priorityClassName | default $root.Values.priorityClassName) }}
priorityClassName: {{ . }}
{{- end }}
{{- end -}}

{{/* StorageClass line for a PVC: dict "root" $ "storageClass" <value>. */}}
{{- define "futureagi.storageClass" -}}
{{- $class := .storageClass | default .root.Values.global.storageClass -}}
{{- if $class }}
storageClassName: {{ if eq $class "-" }}""{{ else }}{{ $class | quote }}{{ end }}
{{- end }}
{{- end -}}

{{/* env entries from a NAME: value map. */}}
{{- define "futureagi.envMap" -}}
{{- range $name, $value := . }}
- name: {{ $name }}
  value: {{ $value | toString | quote }}
{{- end }}
{{- end -}}

{{/* Queue name as a DNS label: tasks_s -> tasks-s. */}}
{{- define "futureagi.queueSlug" -}}
{{- . | replace "_" "-" | lower -}}
{{- end -}}

{{/* An optional block: nothing when empty, else indented on a new line.
dict "text" <rendered text> "indent" <n>. */}}
{{- define "futureagi.block" -}}
{{- $text := .text | trim -}}
{{- if $text -}}
{{- $text | nindent (int .indent) -}}
{{- end -}}
{{- end -}}

{{/*
A Kubernetes quantity (20Gi, 1024Mi, 0.5Gi, 50G, 1500000000) as a byte count,
so sizes compare the way the API server stores them (it canonicalizes 1024Mi
to 1Gi). Anything it cannot parse comes back unchanged.
*/}}
{{- define "futureagi.quantityBytes" -}}
{{- $q := toString . | trim -}}
{{- $units := dict "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 "Pi" 1125899906842624 "k" 1000 "K" 1000 "M" 1000000 "G" 1000000000 "T" 1000000000000 "P" 1000000000000000 -}}
{{- $num := regexFind "^[0-9]+([.][0-9]+)?" $q -}}
{{- $suffix := trimPrefix $num $q -}}
{{- if and $num (or (eq $suffix "") (hasKey $units $suffix)) -}}
{{- $mult := 1 -}}
{{- if $suffix }}{{ $mult = index $units $suffix }}{{ end -}}
{{- printf "%.0f" (mulf (float64 $num) (float64 $mult)) -}}
{{- else -}}
{{- $q -}}
{{- end -}}
{{- end }}
