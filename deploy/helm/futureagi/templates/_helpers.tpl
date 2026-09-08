{{- define "futureagi.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "futureagi.fullname" -}}
{{- if .Values.fullnameOverride }}{{ .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}{{- else }}{{ printf "%s-%s" .Release.Name (include "futureagi.name" .) | trunc 63 | trimSuffix "-" }}{{- end }}
{{- end }}

{{- define "futureagi.chart" -}}
{{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "futureagi.labels" -}}
helm.sh/chart: {{ include "futureagi.chart" . }}
app.kubernetes.io/name: {{ include "futureagi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "futureagi.selectorLabels" -}}
app.kubernetes.io/name: {{ include "futureagi.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "futureagi.componentLabels" -}}
{{ include "futureagi.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "futureagi.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}{{ default (include "futureagi.fullname" .) .Values.serviceAccount.name }}{{- else }}{{ default "default" .Values.serviceAccount.name }}{{- end }}
{{- end }}

{{- define "futureagi.secretName" -}}
{{- default (printf "%s-secrets" (include "futureagi.fullname" .)) .Values.secrets.existingSecret }}
{{- end }}

{{- define "futureagi.image" -}}
{{- $registry := .root.Values.global.imageRegistry -}}
{{- if $registry }}{{ printf "%s/%s:%s" ($registry | trimSuffix "/") .image.repository .image.tag }}{{- else }}{{ printf "%s:%s" .image.repository .image.tag }}{{- end }}
{{- end }}

{{- define "futureagi.postgresqlHost" -}}{{- if .Values.postgresql.enabled }}{{ printf "%s-postgresql" (include "futureagi.fullname" .) }}{{ else }}{{ required "postgresql.external.host is required when postgresql.enabled=false" .Values.postgresql.external.host }}{{ end }}{{- end }}
{{- define "futureagi.clickhouseHost" -}}{{- if .Values.clickhouse.enabled }}{{ printf "%s-clickhouse" (include "futureagi.fullname" .) }}{{ else }}{{ required "clickhouse.external.host is required when clickhouse.enabled=false" .Values.clickhouse.external.host }}{{ end }}{{- end }}
{{- define "futureagi.redisHost" -}}{{- if .Values.redis.enabled }}{{ printf "%s-redis" (include "futureagi.fullname" .) }}{{ else }}{{ required "redis.external.host is required when redis.enabled=false" .Values.redis.external.host }}{{ end }}{{- end }}
{{- define "futureagi.rabbitmqHost" -}}{{- if .Values.rabbitmq.enabled }}{{ printf "%s-rabbitmq" (include "futureagi.fullname" .) }}{{ else }}{{ required "rabbitmq.external.host is required when rabbitmq.enabled=false" .Values.rabbitmq.external.host }}{{ end }}{{- end }}
{{- define "futureagi.minioHost" -}}{{ printf "%s-minio" (include "futureagi.fullname" .) }}{{- end }}
{{- define "futureagi.objectStorageEndpoint" -}}
{{- if .Values.minio.enabled -}}
{{ printf "http://%s-minio:9000" (include "futureagi.fullname" .) }}
{{- else if .Values.config.s3Endpoint -}}
{{ .Values.config.s3Endpoint }}
{{- else if eq .Values.config.storageBackend "gcs" -}}
https://storage.googleapis.com
{{- else -}}
https://s3.amazonaws.com
{{- end -}}
{{- end }}
{{- define "futureagi.temporalHost" -}}{{- if .Values.temporal.enabled }}{{ printf "%s-temporal" (include "futureagi.fullname" .) }}{{ else }}{{ required "temporal.external.host is required when temporal.enabled=false" .Values.temporal.external.host }}{{ end }}{{- end }}

{{- define "futureagi.podPlacement" -}}
{{- with .Values.global.nodeSelector }}
nodeSelector: {{- toYaml . | nindent 2 }}
{{- end }}

{{- with .Values.global.affinity }}
affinity: {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.global.tolerations }}
tolerations: {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "futureagi.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
