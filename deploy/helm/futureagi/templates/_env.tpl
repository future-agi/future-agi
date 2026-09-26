{{/* =====================================================================
Environment of the Python processes (API, workers, bootstrap job).

  include "futureagi.env.python" (dict
    "root" $                  chart context
    "component" (dict ...)    chart-owned per-component variables
    "overrides" (dict ...))   the component's user extraEnv

Secret-backed variables come first (the Redis URLs reference
$(REDIS_PASSWORD)), then every plain variable, sorted. Plain values merge in
this order, later wins: the chart's shared values, config.extraEnv, the
component's own values, the component's extraEnv. A secret-backed variable
named in an extraEnv map is left out, so the user's value is the only one;
a user's REDIS_PASSWORD keeps its place at the top, before the URLs.
NO_STARTUP_DB_MUTATIONS is "true" everywhere except the bootstrap job.
===================================================================== */}}
{{- define "futureagi.env.python" -}}
{{- $root := .root -}}
{{- $v := $root.Values -}}
{{- $overrides := merge (dict) (.overrides | default dict) $v.config.extraEnv -}}
{{- $appSecret := include "futureagi.appSecretName" $root -}}
{{- $chartSecret := include "futureagi.secretName" $root -}}
{{- $backend := include "futureagi.component" (dict "root" $root "component" "backend") -}}
{{- $appUrl := include "futureagi.url.app" $root -}}
{{- $apiUrl := include "futureagi.url.api" $root -}}
{{- $chHost := include "futureagi.clickhouse.host" $root -}}
{{- $pgHost := include "futureagi.postgres.host" $root -}}
{{- $pgPort := include "futureagi.postgres.port" $root -}}
{{- $storage := include "futureagi.objectStorage.backend" $root -}}

{{- /* ---- secret-backed variables ---- */ -}}
{{- $secretEnv := list
      (dict "name" "SECRET_KEY" "secret" $appSecret "key" "SECRET_KEY")
      (dict "name" "INTEGRATION_ENCRYPTION_KEY" "secret" $appSecret "key" "INTEGRATION_ENCRYPTION_KEY")
      (dict "name" "AGENTCC_INTERNAL_API_KEY" "secret" $appSecret "key" "AGENTCC_INTERNAL_API_KEY")
      (dict "name" "AGENTCC_ADMIN_TOKEN" "secret" $appSecret "key" "AGENTCC_ADMIN_TOKEN")
      (dict "name" "PROPERTY_CATALOG_CH_PASSWORD" "secret" $appSecret "key" "PROPERTY_CATALOG_API_PASSWORD") -}}
{{- if .catalogWriter -}}
{{- $secretEnv = append $secretEnv (dict "name" "PROPERTY_CATALOG_CONSUMER_PASSWORD" "secret" $appSecret "key" "PROPERTY_CATALOG_CONSUMER_PASSWORD") -}}
{{- end -}}
{{- if $v.secrets.agentccWebhookSecret -}}
{{- $secretEnv = append $secretEnv (dict "name" "AGENTCC_WEBHOOK_SECRET" "secret" $chartSecret "key" "AGENTCC_WEBHOOK_SECRET") -}}
{{- end -}}
{{- if $v.secrets.eeLicenseKey -}}
{{- $secretEnv = append $secretEnv (dict "name" "EE_LICENSE_KEY" "secret" $chartSecret "key" "EE_LICENSE_KEY") -}}
{{- end -}}
{{- if $v.secrets.mailgunApiKey -}}
{{- $secretEnv = append $secretEnv (dict "name" "MAILGUN_API_KEY" "secret" $chartSecret "key" "MAILGUN_API_KEY") -}}
{{- end -}}
{{- range $name := keys $v.secrets.extra | sortAlpha -}}
{{- $secretEnv = append $secretEnv (dict "name" $name "secret" $chartSecret "key" $name) -}}
{{- end -}}
{{- range $secretEnv }}
{{- if not (hasKey $overrides .name) }}
{{ include "futureagi.secretEnv" . }}
{{- end }}
{{- end }}
{{- if not (hasKey $overrides "PG_PASSWORD") }}
{{ include "futureagi.postgres.passwordEnv" (dict "root" $root "name" "PG_PASSWORD") }}
{{- end }}
{{- if not (hasKey $overrides "CH_PASSWORD") }}
{{ include "futureagi.clickhouse.passwordEnv" (dict "root" $root "name" "CH_PASSWORD") }}
{{- end }}
{{- if hasKey $overrides "REDIS_PASSWORD" }}
- name: REDIS_PASSWORD
  value: {{ get $overrides "REDIS_PASSWORD" | toString | quote }}
{{- else if eq (include "futureagi.redis.auth" $root) "true" }}
{{ include "futureagi.redis.passwordEnv" (dict "root" $root "name" "REDIS_PASSWORD") }}
{{- end }}
{{- $keyNames := ternary (list "GCS_HMAC_ACCESS_KEY" "GCS_HMAC_SECRET_KEY") (list "S3_ACCESS_KEY" "S3_SECRET_KEY") (eq $storage "gcs") -}}
{{- if not (hasKey $overrides (index $keyNames 0)) }}
{{ include "futureagi.objectStorage.keyEnv" (dict "root" $root "name" (index $keyNames 0) "kind" "access") }}
{{- end }}
{{- if not (hasKey $overrides (index $keyNames 1)) }}
{{ include "futureagi.objectStorage.keyEnv" (dict "root" $root "name" (index $keyNames 1) "kind" "secret") }}
{{- end }}
{{- include "futureagi.env.llm" (dict "root" $root "overrides" $overrides) }}

{{- /* ---- plain variables ---- */ -}}
{{- $allowedHosts := $v.config.allowedHosts | toString -}}
{{- if ne $allowedHosts "*" -}}
{{- $extraHosts := list "localhost" "127.0.0.1" $backend (printf "%s.%s" $backend $root.Release.Namespace) (printf "%s.%s.svc" $backend $root.Release.Namespace) (printf "%s.%s.svc.cluster.local" $backend $root.Release.Namespace) -}}
{{- if $apiUrl }}{{ $extraHosts = append $extraHosts (include "futureagi.urlHost" $apiUrl | splitList ":" | first) }}{{ end -}}
{{- $allowedHosts = concat (splitList "," $allowedHosts) $extraHosts | uniq | join "," -}}
{{- end -}}
{{- $csrf := list -}}
{{- if $appUrl }}{{ $csrf = append $csrf $appUrl }}{{ end -}}
{{- if $v.config.extraCsrfOrigins }}{{ $csrf = append $csrf $v.config.extraCsrfOrigins }}{{ end -}}
{{- $redisHost := include "futureagi.redis.host" $root -}}
{{- $objectsEndpoint := include "futureagi.objectStorage.endpoint" $root -}}
{{- $plain := dict
      "ENV_TYPE" $v.config.envType
      "DEBUG" "false"
      "DJANGO_SETTINGS_MODULE" "tfc.settings.settings"
      "LOG_LEVEL" $v.config.logLevel
      "NO_STARTUP_DB_MUTATIONS" "true"
      "FI_SKIP_CH25_MIGRATION" "1"
      "FI_CDC_MODE" $v.config.cdcMode
      "FUTURE_AGI_VERSION" ($v.backend.image.tag | default $v.image.tag | default $root.Chart.AppVersion)
      "FUTURE_AGI_TELEMETRY_DISABLED" (ternary "false" "true" $v.config.telemetry)
      "OTEL_ENABLED" (toString $v.config.otel)
      "RECAPTCHA_ENABLED" (toString $v.config.recaptcha)
      "ALLOWED_HOSTS" $allowedHosts
      "BASE_URL" ($apiUrl | default "http://localhost:8000")
      "FRONTEND_URL" ($appUrl | default "http://localhost:3000")
      "APP_URL" ($appUrl | default "http://localhost:3000")
      "FI_HELM_NAMESPACE" $root.Release.Namespace
      "FI_HELM_FULLNAME" (include "futureagi.fullname" $root)
      "PG_HOST" $pgHost
      "PG_PORT" $pgPort
      "PG_USER" $v.postgres.user
      "PG_DB" $v.postgres.database
      "PGBOUNCER_HOST" $pgHost
      "PGBOUNCER_PORT" $pgPort
      "PGSSLMODE" (ternary "disable" $v.postgres.external.sslMode (eq $v.postgres.mode "bundled"))
      "CH_HOST" $chHost
      "CH_PORT" (include "futureagi.clickhouse.nativePort" $root)
      "CH_HTTP_PORT" (include "futureagi.clickhouse.httpPort" $root)
      "CH_USER" $v.clickhouse.user
      "CH_USERNAME" $v.clickhouse.user
      "CH_ENABLED" "true"
      "CH_DATABASE" $v.clickhouse.database
      "CH25_DATABASE" $v.clickhouse.database
      "CH_USE_REPLICATED_ENGINES" "false"
      "CH25_DROP_LEGACY_CDC_CHAIN" "true"
      "CH25_EVAL_LOGGER_TABLE" "tracer_eval_logger"
      "CH25_QUERY_TYPES_V2_ONLY" "span_list,trace_list,session_list,voice_call_list,dashboard,monitor_metrics,eval_metrics,filter_builder,trace_detail,annotation_labels"
      "PROPERTY_CATALOG_DATABASE" $v.clickhouse.propertyCatalogDatabase
      "PROPERTY_CATALOG_CH_HOST" $chHost
      "PROPERTY_CATALOG_CH_PORT" (include "futureagi.clickhouse.nativePort" $root)
      "PROPERTY_CATALOG_CH_USER" "observed_catalog_reader"
      "REDIS_HOST" $redisHost
      "REDIS_PORT" (include "futureagi.redis.port" $root)
      "REDIS_URL" (include "futureagi.redis.url" (dict "root" $root "db" 0))
      "REDIS_CACHE_URL" (include "futureagi.redis.url" (dict "root" $root "db" 1))
      "REDIS_LOCK_URL" (include "futureagi.redis.url" (dict "root" $root "db" 2))
      "REDIS_STATE_URL" (include "futureagi.redis.url" (dict "root" $root "db" 2))
      "CHANNEL_LAYER_BACKEND" "redis"
      "CHANNEL_REDIS_URL" (include "futureagi.redis.url" (dict "root" $root "db" 3))
      "WEBSOCKET_ENDPOINT" (printf "http://%s:%v/call-websocket/" $backend $v.backend.service.port)
      "STORAGE_BACKEND" $storage
      "MINIO_URL" (include "futureagi.url.objects" $root)
      "UPLOAD_BUCKET_NAME" $v.objectStorage.bucket
      "S3_REGION" $v.objectStorage.region
      "AWS_DEFAULT_REGION" $v.objectStorage.region
      "TEMPORAL_HOST" (include "futureagi.temporal.address" $root)
      "TEMPORAL_NAMESPACE" $v.temporal.namespace
      "EXACT_AGGREGATION_TASK_QUEUE" (ternary "exact_aggregation" "tasks_xl" $v.worker.exactAggregation.enabled)
      "AGENTCC_INTERNAL_URL" (printf "http://%s:%v" (include "futureagi.component" (dict "root" $root "component" "agentcc-gateway")) $v.agentccGateway.service.port)
      "AGENTCC_GATEWAY_INTERNAL_URL" (printf "http://%s:%v" (include "futureagi.component" (dict "root" $root "component" "agentcc-gateway")) $v.agentccGateway.service.port)
      "CODE_EXECUTOR_URL" (ternary (printf "http://%s:8060" (include "futureagi.component" (dict "root" $root "component" "code-executor"))) "" $v.codeExecutor.enabled)
      "CODE_EXECUTOR_LOCAL_FALLBACK" (toString $v.codeExecutor.localFallback)
      "MODEL_SERVING_URL" (ternary (printf "http://%s:8080" (include "futureagi.component" (dict "root" $root "component" "serving"))) "" $v.serving.enabled)
      "FI_COLLECTOR_HOST" (include "futureagi.component" (dict "root" $root "component" "fi-collector"))
      "FI_COLLECTOR_OTLP_PORT" (toString $v.fiCollector.service.grpcPort)
      "FI_COLLECTOR_PUBLIC_URL" (include "futureagi.url.collectorPublic" $root)
      "ALK_RUNNER_API_URL" (printf "http://%s:%v" $backend $v.backend.service.port)
      "AWS_REGION" $v.secrets.llm.awsRegion
-}}
{{- if $v.config.corsAllowedOrigins }}{{ $_ := set $plain "CORS_ALLOWED_ORIGINS" $v.config.corsAllowedOrigins }}{{ end -}}
{{- if $csrf }}{{ $_ := set $plain "EXTRA_CSRF_ORIGINS" (join "," $csrf) }}{{ end -}}
{{- if $objectsEndpoint }}{{ $_ := set $plain "S3_ENDPOINT_URL" $objectsEndpoint }}{{ end -}}
{{- with $v.config.email.mailgunSenderDomain }}{{ $_ := set $plain "MAILGUN_SENDER_DOMAIN" . }}{{ end -}}
{{- with $v.config.email.fromEmail }}{{ $_ := set $plain "DEFAULT_FROM_EMAIL" . }}{{ end -}}
{{- with $v.config.email.replyTo }}{{ $_ := set $plain "DEFAULT_REPLY_TO_EMAIL" . }}{{ end -}}
{{- with $v.config.email.serverEmail }}{{ $_ := set $plain "SERVER_EMAIL" . }}{{ end -}}
{{- $plain = mergeOverwrite $plain $v.config.extraEnv (.component | default dict) (.overrides | default dict) -}}
{{- $_ := unset $plain "REDIS_PASSWORD" -}}
{{- range $name := keys $plain | sortAlpha }}
- name: {{ $name }}
  value: {{ get $plain $name | toString | quote }}
{{- end }}
{{- end -}}

{{/* LLM provider keys: dict "root" $ "overrides" <map> ["gateway" true].
The gateway reads Gemini's key as GEMINI_API_KEY. */}}
{{- define "futureagi.env.llm" -}}
{{- $v := .root.Values.secrets.llm -}}
{{- $chartSecret := include "futureagi.secretName" .root -}}
{{- $overrides := .overrides | default dict -}}
{{- $gateway := .gateway | default false -}}
{{- $keys := list
      (list "OPENAI_API_KEY" $v.openaiApiKey)
      (list "ANTHROPIC_API_KEY" $v.anthropicApiKey)
      (list "GOOGLE_API_KEY" $v.googleApiKey)
      (list "AWS_ACCESS_KEY_ID" $v.awsAccessKeyId)
      (list "AWS_SECRET_ACCESS_KEY" $v.awsSecretAccessKey) -}}
{{- range $keys }}
{{- $key := index . 0 -}}
{{- $name := ternary "GEMINI_API_KEY" $key (and $gateway (eq $key "GOOGLE_API_KEY")) -}}
{{- if not (hasKey $overrides $name) }}
{{- if $v.existingSecret }}
{{ include "futureagi.secretEnv" (dict "name" $name "secret" $v.existingSecret "key" $key "optional" true) }}
{{- else if index . 1 }}
{{ include "futureagi.secretEnv" (dict "name" $name "secret" $chartSecret "key" $key) }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Writable paths of the Python pods (the image's root filesystem is
read-only and owned by root): Django's STATIC_ROOT and log directory, the
paths the SAML and dataset-compare code write to, $HOME and /tmp. */}}
{{- define "futureagi.python.writablePaths" -}}
tmp: /tmp
home: /home/appuser
static: /app/backend/static
media: /app/backend/media
logs: /app/backend/tfc/logs
metadata: /app/backend/tfc/metadata
saml-logs: /app/backend/tfc/saml_logs
compare: /app/backend/tfc/compare
{{- end -}}

{{- define "futureagi.python.volumes" -}}
{{- range $name, $path := include "futureagi.python.writablePaths" . | fromYaml }}
- name: {{ $name }}
  emptyDir:
    sizeLimit: {{ ternary "1Gi" "256Mi" (eq $name "tmp") }}
{{- end }}
{{- end -}}

{{- define "futureagi.python.volumeMounts" -}}
{{- range $name, $path := include "futureagi.python.writablePaths" . | fromYaml }}
- name: {{ $name }}
  mountPath: {{ $path }}
{{- end }}
{{- end -}}

{{/* =====================================================================
fi-collector: OTLP in, ClickHouse out, API keys checked against Postgres.
===================================================================== */}}
{{- define "futureagi.env.collector" -}}
{{- $root := .root -}}
{{- $v := $root.Values -}}
{{- $overrides := $v.fiCollector.extraEnv | default dict -}}
{{- $pgHost := include "futureagi.postgres.host" $root -}}
{{- $pgPort := include "futureagi.postgres.port" $root -}}
{{- $redisTls := and (eq $v.redis.mode "external") $v.redis.external.tls -}}
{{- if not (hasKey $overrides "FI_CH_PASSWORD") }}
{{ include "futureagi.clickhouse.passwordEnv" (dict "root" $root "name" "FI_CH_PASSWORD") }}
{{- end }}
{{- if not (hasKey $overrides "FI_PG_WRITE_PASSWORD") }}
{{ include "futureagi.postgres.passwordEnv" (dict "root" $root "name" "FI_PG_WRITE_PASSWORD") }}
{{- end }}
{{- if not (hasKey $overrides "FI_PG_READ_PASSWORD") }}
{{ include "futureagi.postgres.passwordEnv" (dict "root" $root "name" "FI_PG_READ_PASSWORD") }}
{{- end }}
{{- if and (not $redisTls) (eq (include "futureagi.redis.auth" $root) "true") (not (hasKey $overrides "FI_AUTH_REDIS_PASSWORD")) }}
{{ include "futureagi.redis.passwordEnv" (dict "root" $root "name" "FI_AUTH_REDIS_PASSWORD") }}
{{- end }}
{{- $plain := dict
      "FI_CH_URL" (printf "http://%s:%s" (include "futureagi.clickhouse.host" $root) (include "futureagi.clickhouse.httpPort" $root))
      "FI_CH_DATABASE" $v.clickhouse.database
      "FI_CH_USERNAME" $v.clickhouse.user
      "FI_PG_WRITE_HOST" $pgHost
      "FI_PG_WRITE_PORT" $pgPort
      "FI_PG_WRITE_DATABASE" $v.postgres.database
      "FI_PG_WRITE_USER" $v.postgres.user
      "FI_PG_READ_HOST" $pgHost
      "FI_PG_READ_PORT" $pgPort
      "FI_PG_READ_DATABASE" $v.postgres.database
      "FI_PG_READ_USER" $v.postgres.user
      "PGSSLMODE" (ternary "disable" $v.postgres.external.sslMode (eq $v.postgres.mode "bundled"))
      "FI_GRPC_ADDR" ":4317"
      "FI_HTTP_ADDR" ":4318"
      "FI_ADMIN_ADDR" ":9464"
      "FI_DEAD_LETTER_FILE" "/var/lib/fi-collector/dead_letter.jsonl"
      "FI_OBSERVED_CATALOG_MODE" "disabled"
      "GOMEMLIMIT" $v.fiCollector.goMemLimit
-}}
{{- if not $redisTls }}{{ $_ := set $plain "FI_AUTH_REDIS_ADDR" (printf "%s:%s" (include "futureagi.redis.host" $root) (include "futureagi.redis.port" $root)) }}{{ end -}}
{{- $plain = mergeOverwrite $plain $overrides -}}
{{- range $name := keys $plain | sortAlpha }}
- name: {{ $name }}
  value: {{ get $plain $name | toString | quote }}
{{- end }}
{{- end -}}

{{/* =====================================================================
agentcc-gateway: provider keys, the backend's shared key and admin token,
and the control-plane sync that gives every replica the same virtual keys.
===================================================================== */}}

{{/* The gateway's listen port: config.server.port, pinned with AGENTCC_PORT
(which wins over the config file, an existingConfigMap's too) so the probes
and the Service always find it. */}}
{{- define "futureagi.gateway.port" -}}
{{- dig "server" "port" 8080 .Values.agentccGateway.config | int -}}
{{- end -}}
{{- define "futureagi.env.gateway" -}}
{{- $root := .root -}}
{{- $v := $root.Values -}}
{{- $g := $v.agentccGateway -}}
{{- $overrides := $g.extraEnv | default dict -}}
{{- $appSecret := include "futureagi.appSecretName" $root -}}
{{- $backend := include "futureagi.component" (dict "root" $root "component" "backend") -}}
{{- $secretEnv := list
      (dict "name" "AGENTCC_INTERNAL_API_KEY" "secret" $appSecret "key" "AGENTCC_INTERNAL_API_KEY")
      (dict "name" "AGENTCC_ADMIN_TOKEN" "secret" $appSecret "key" "AGENTCC_ADMIN_TOKEN") -}}
{{- if $g.controlPlaneSync -}}
{{- $secretEnv = append $secretEnv (dict "name" "AGENTCC_CONTROL_PLANE_TOKEN" "secret" $appSecret "key" "AGENTCC_ADMIN_TOKEN") -}}
{{- end -}}
{{- if $v.secrets.agentccWebhookSecret -}}
{{- $secretEnv = append $secretEnv (dict "name" "AGENTCC_WEBHOOK_SECRET" "secret" (include "futureagi.secretName" $root) "key" "AGENTCC_WEBHOOK_SECRET") -}}
{{- end -}}
{{- range $secretEnv }}
{{- if not (hasKey $overrides .name) }}
{{ include "futureagi.secretEnv" . }}
{{- end }}
{{- end }}
{{- include "futureagi.env.llm" (dict "root" $root "overrides" $overrides "gateway" true) }}
{{- $plain := dict
      "AGENTCC_PORT" (include "futureagi.gateway.port" $root)
      "AWS_REGION" $v.secrets.llm.awsRegion
      "FI_BASE_URL" (printf "http://%s:%v" $backend $v.backend.service.port)
      "GOMEMLIMIT" $g.goMemLimit
-}}
{{- if $g.controlPlaneSync -}}
{{- $_ := set $plain "AGENTCC_CONTROL_PLANE_URL" (printf "http://%s:%v" $backend $v.backend.service.port) -}}
{{- $_ := set $plain "AGENTCC_SYNC_ON_STARTUP" "true" -}}
{{- end -}}
{{- if $g.gcpCredentials.existingSecret -}}
{{- $_ := set $plain "GOOGLE_APPLICATION_CREDENTIALS" "/var/run/secrets/futureagi/gcp/credentials.json" -}}
{{- end -}}
{{- $plain = mergeOverwrite $plain $overrides -}}
{{- range $name := keys $plain | sortAlpha }}
- name: {{ $name }}
  value: {{ get $plain $name | toString | quote }}
{{- end }}
{{- end -}}
