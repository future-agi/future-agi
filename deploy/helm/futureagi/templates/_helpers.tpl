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
Future AGI images fall back to image.tag, then the chart's appVersion.
===================================================================== */}}

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

{{- define "futureagi.url.app" -}}
{{- if .Values.urls.app -}}
{{- trimSuffix "/" .Values.urls.app -}}
{{- else if and .Values.ingress.enabled .Values.ingress.app.host -}}
{{- include "futureagi.hostUrl" (dict "root" . "host" .Values.ingress.app.host) -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.url.api" -}}
{{- if .Values.urls.api -}}
{{- trimSuffix "/" .Values.urls.api -}}
{{- else if and .Values.ingress.enabled .Values.ingress.api.host -}}
{{- include "futureagi.hostUrl" (dict "root" . "host" .Values.ingress.api.host) -}}
{{- end -}}
{{- end -}}

{{- define "futureagi.url.otlp" -}}
{{- if .Values.urls.otlp -}}
{{- trimSuffix "/" .Values.urls.otlp -}}
{{- else if and .Values.ingress.enabled .Values.ingress.otlp.enabled -}}
{{- $host := .Values.ingress.otlp.host | default .Values.ingress.api.host -}}
{{- if $host -}}{{- include "futureagi.hostUrl" (dict "root" . "host" $host) -}}{{- end -}}
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
http://localhost:9005
{{- end -}}
{{- end -}}

{{/* Host part of a URL (APP_URL is a bare host). */}}
{{- define "futureagi.urlHost" -}}
{{- $u := urlParse . -}}
{{- $u.host -}}
{{- end -}}

{{/* =====================================================================
Pod settings
===================================================================== */}}

{{/* Security contexts: dict "defaults" <map> "overrides" <map>. */}}
{{- define "futureagi.mergeYaml" -}}
{{- toYaml (mergeOverwrite (deepCopy .defaults) (.overrides | default dict)) -}}
{{- end -}}

{{- define "futureagi.podSecurityContext" -}}
{{- $defaults := dict "runAsNonRoot" true "runAsUser" (int .uid) "runAsGroup" (int .uid) "fsGroup" (int .uid) "seccompProfile" (dict "type" "RuntimeDefault") -}}
{{- include "futureagi.mergeYaml" (dict "defaults" $defaults "overrides" .overrides) -}}
{{- end -}}

{{- define "futureagi.containerSecurityContext" -}}
{{- $defaults := dict "allowPrivilegeEscalation" false "readOnlyRootFilesystem" (ne (toString .readOnly) "false") "capabilities" (dict "drop" (list "ALL")) -}}
{{- include "futureagi.mergeYaml" (dict "defaults" $defaults "overrides" .overrides) -}}
{{- end -}}

{{/* nodeSelector, tolerations, affinity, spread and priority for one pod:
dict "root" $ "values" <component values> ["spread" true]. */}}
{{- define "futureagi.scheduling" -}}
{{- $root := .root -}}
{{- $c := .values -}}
{{- with ($c.nodeSelector | default $root.Values.nodeSelector) }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with ($c.tolerations | default $root.Values.tolerations) }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with ($c.affinity | default $root.Values.affinity) }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- if .spread }}
{{- with ($c.topologySpreadConstraints | default $root.Values.topologySpreadConstraints) }}
topologySpreadConstraints:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
{{- with $root.Values.priorityClassName }}
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
