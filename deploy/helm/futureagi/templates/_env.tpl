{{/* =====================================================================
Environment of the Python processes (API, workers, bootstrap job).

  include "futureagi.env.python" (dict
    "root" $                  chart context
    "component" (dict ...)    chart-owned per-component variables
    "overrides" (dict ...))   the component's user extraEnv

POD_IP comes first (ALLOWED_HOSTS references $(POD_IP)), then the
secret-backed variables (the Redis URLs reference $(REDIS_PASSWORD)), then
every plain variable, sorted. Plain values merge in
this order, later wins: the chart's shared values, config.extraEnv, the
component's own values, the component's extraEnv. A secret-backed variable
named in an extraEnv map is left out, so the user's value is the only one;
a user's REDIS_PASSWORD keeps its place at the top, before the URLs.
NO_STARTUP_DB_MUTATIONS is "true" everywhere except the bootstrap job.
"direct" true (the bootstrap job): Django connects to PostgreSQL directly,
never through postgres.pooler or a read replica.
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
{{- $secretEnv = append $secretEnv (dict "name" "AGENTCC_WEBHOOK_SECRET" "secret" (include "futureagi.webhookSecretName" $root) "key" "AGENTCC_WEBHOOK_SECRET") -}}
{{- /* license, email and SSO clients: _enterprise.tpl */ -}}
{{- $secretEnv = concat $secretEnv (include "futureagi.env.enterpriseSecrets" (dict "root" $root) | fromJsonArray) -}}
{{- range $name := keys $v.secrets.extra | sortAlpha -}}
{{- $secretEnv = append $secretEnv (dict "name" $name "secret" $chartSecret "key" $name) -}}
{{- end -}}
{{- /* The pod's IP, before ALLOWED_HOSTS: Kubernetes expands $(POD_IP) only
from a variable defined earlier. A user's POD_IP keeps this place. */ -}}
{{- if hasKey $overrides "POD_IP" }}
- name: POD_IP
  value: {{ get $overrides "POD_IP" | toString | quote }}
{{- else }}
- name: POD_IP
  valueFrom:
    fieldRef:
      fieldPath: status.podIP
{{- end }}
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
{{- /* ALLOWED_HOSTS: an explicit list (plus the names the probes and the
in-cluster callers use, and the pod's IP, the Host of load balancers that
health-check pods directly: an AWS ALB with target-type ip, GKE), an explicit
"*", or, when empty, the API host once the API has a public URL ("*" while it
is only port-forwarded). */ -}}
{{- $allowedHosts := $v.config.allowedHosts | toString -}}
{{- $apiPublic := and (ne $apiUrl "") (eq (include "futureagi.isLocalUrl" $apiUrl) "") -}}
{{- if not $allowedHosts }}{{ $allowedHosts = ternary "" "*" $apiPublic }}{{ end -}}
{{- if ne $allowedHosts "*" -}}
{{- /* [$(POD_IP)]: on an IPv6 pod Django matches the bracketed address. */ -}}
{{- $extraHosts := list "localhost" "127.0.0.1" "$(POD_IP)" "[$(POD_IP)]" $backend (printf "%s.%s" $backend $root.Release.Namespace) (printf "%s.%s.svc" $backend $root.Release.Namespace) (printf "%s.%s.svc.cluster.local" $backend $root.Release.Namespace) -}}
{{- if $apiUrl }}{{ $extraHosts = append $extraHosts (include "futureagi.urlHost" $apiUrl | splitList ":" | first) }}{{ end -}}
{{- $allowedHosts = concat (compact (splitList "," $allowedHosts)) $extraHosts | uniq | join "," -}}
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
      "APP_VERSION" ($v.backend.image.tag | default $v.image.tag | default $root.Chart.AppVersion)
      "FUTURE_AGI_TELEMETRY_DISABLED" (ternary "false" "true" (and $v.config.telemetry (ne (toString $v.global.airgap) "true")))
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
      "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS" (toString $v.agentccGateway.allowPrivateProviderURLs)
      "USAGE_EVENTS_ENABLED" (toString $v.config.usageEvents)
      "MODEL_SERVING_URL" (ternary (printf "http://%s:8080" (include "futureagi.component" (dict "root" $root "component" "serving"))) "" $v.serving.enabled)
      "FI_COLLECTOR_HOST" (include "futureagi.component" (dict "root" $root "component" "fi-collector"))
      "FI_COLLECTOR_OTLP_PORT" (toString $v.fiCollector.service.grpcPort)
      "FI_COLLECTOR_PUBLIC_URL" (include "futureagi.url.collectorPublic" $root)
      "SIM_COLLECTOR_OTLP_ENDPOINT" (printf "%s:%v" (include "futureagi.component" (dict "root" $root "component" "fi-collector")) $v.fiCollector.service.grpcPort)
      "ALK_RUNNER_API_URL" (printf "http://%s:%v" $backend $v.backend.service.port)
      "AWS_REGION" $v.secrets.llm.awsRegion
-}}
{{- /* CORS_ALLOWED_ORIGINS: explicit origins; "*" leaves it unset (every
origin); empty allows the UI's origin (and extraCsrfOrigins) once the UI has
a public URL, and every origin while it is only port-forwarded. */ -}}
{{- $cors := $v.config.corsAllowedOrigins | toString | trim -}}
{{- if and (not $cors) $appUrl (eq (include "futureagi.isLocalUrl" $appUrl) "") -}}
{{- $origins := list (include "futureagi.urlOrigin" $appUrl) -}}
{{- range splitList "," ($v.config.extraCsrfOrigins | toString) }}{{ with trim . }}{{ $origins = append $origins (include "futureagi.urlOrigin" .) }}{{ end }}{{ end -}}
{{- $cors = $origins | uniq | join "," -}}
{{- end -}}
{{- if and $cors (ne $cors "*") }}{{ $_ := set $plain "CORS_ALLOWED_ORIGINS" $cors }}{{ end -}}
{{- if $csrf }}{{ $_ := set $plain "EXTRA_CSRF_ORIGINS" (join "," $csrf) }}{{ end -}}
{{- /* Django's pooled connection (PGBOUNCER_*) and read replica. PG_HOST stays
the direct server: the outbox CDC's advisory lock needs a session. */ -}}
{{- $pooler := $v.postgres.pooler | default dict -}}
{{- $replica := $v.postgres.readReplica | default dict -}}
{{- if and $pooler.enabled .direct -}}
{{- $_ := set $plain "PG_DIRECT_HOST" $pgHost -}}
{{- $_ := set $plain "PG_DIRECT_PORT" $pgPort -}}
{{- else if $pooler.enabled -}}
{{- $_ := set $plain "PGBOUNCER_HOST" $pooler.host -}}
{{- $_ := set $plain "PGBOUNCER_PORT" (toString $pooler.port) -}}
{{- end -}}
{{- if and $replica.enabled (not .direct) -}}
{{- $_ := set $plain "PGBOUNCER_READ_HOST" $replica.host -}}
{{- $_ := set $plain "PGBOUNCER_READ_PORT" (toString $replica.port) -}}
{{- $_ := set $plain "PG_READ_DB" ($replica.database | default $v.postgres.database) -}}
{{- with $replica.optIn }}{{ $_ := set $plain "READ_REPLICA_OPT_IN" (join "," .) }}{{ end -}}
{{- end -}}
{{- if $objectsEndpoint }}{{ $_ := set $plain "S3_ENDPOINT_URL" $objectsEndpoint }}{{ end -}}
{{- with $v.config.email.mailgunSenderDomain }}{{ $_ := set $plain "MAILGUN_SENDER_DOMAIN" . }}{{ end -}}
{{- with $v.config.email.fromEmail }}{{ $_ := set $plain "DEFAULT_FROM_EMAIL" . }}{{ end -}}
{{- with $v.config.email.replyTo }}{{ $_ := set $plain "DEFAULT_REPLY_TO_EMAIL" . }}{{ end -}}
{{- with $v.config.email.serverEmail }}{{ $_ := set $plain "SERVER_EMAIL" . }}{{ end -}}
{{- /* license and SSO settings, then the proxy, CA bundle and air-gap
variables (_enterprise.tpl); every extraEnv wins over them. */ -}}
{{- $plain = mergeOverwrite $plain (include "futureagi.env.enterprisePlain" $root | fromYaml) (include "futureagi.env.platform" (dict "root" $root "kind" "python") | fromYaml) -}}
{{- $plain = mergeOverwrite $plain $v.config.extraEnv (.component | default dict) (.overrides | default dict) -}}
{{- $_ := unset $plain "REDIS_PASSWORD" -}}
{{- $_ := unset $plain "POD_IP" -}}
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

{{/* The writable paths, plus global.caBundle when set. */}}
{{- define "futureagi.python.volumes" -}}
{{- range $name, $path := include "futureagi.python.writablePaths" . | fromYaml }}
- name: {{ $name }}
  emptyDir:
    sizeLimit: {{ ternary "1Gi" "256Mi" (eq $name "tmp") }}
{{- end }}
{{- with include "futureagi.caBundle.volume" . }}
{{ . }}
{{- end }}
{{- end -}}

{{- define "futureagi.python.volumeMounts" -}}
{{- range $name, $path := include "futureagi.python.writablePaths" . | fromYaml }}
- name: {{ $name }}
  mountPath: {{ $path }}
{{- end }}
{{- with include "futureagi.caBundle.volumeMount" . }}
{{ . }}
{{- end }}
{{- end -}}

{{/* =====================================================================
fi-collector: OTLP in, ClickHouse out, API keys checked against Postgres.
Observed attributes (property suggestions) go straight into the index the
bootstrap job creates, as its writer: FI_OBSERVED_CATALOG_MODE=direct. With
FI_OBSERVED_CATALOG_KAFKA_BROKERS in fiCollector.extraEnv the collector
publishes them to Kafka instead, for your own fi-property-catalog-consumer.
===================================================================== */}}
{{- define "futureagi.env.collector" -}}
{{- $root := .root -}}
{{- $v := $root.Values -}}
{{- $overrides := $v.fiCollector.extraEnv | default dict -}}
{{- $pgHost := include "futureagi.postgres.host" $root -}}
{{- $pgPort := include "futureagi.postgres.port" $root -}}
{{- $redisTls := and (eq $v.redis.mode "external") $v.redis.external.tls -}}
{{- $observedMode := get $overrides "FI_OBSERVED_CATALOG_MODE" | default (ternary "kafka" "direct" (hasKey $overrides "FI_OBSERVED_CATALOG_KAFKA_BROKERS")) | toString -}}
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
{{- if and (eq $observedMode "direct") (not (hasKey $overrides "FI_OBSERVED_CATALOG_CH_PASSWORD")) }}
{{ include "futureagi.secretEnv" (dict "name" "FI_OBSERVED_CATALOG_CH_PASSWORD" "secret" (include "futureagi.appSecretName" $root) "key" "PROPERTY_CATALOG_CONSUMER_PASSWORD") }}
{{- end }}
{{- /* The observed-attribute spool gets half its default cap: it shares the
pod's 1Gi emptyDir with the dead-letter file. */ -}}
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
      "USAGE_EVENTS_ENABLED" (toString $v.config.usageEvents)
      "FI_OBSERVED_CATALOG_MODE" $observedMode
      "FI_OBSERVED_CATALOG_SPOOL_DIR" "/var/lib/fi-collector/observed-catalog"
      "FI_OBSERVED_CATALOG_MAX_SPOOL_BYTES" "268435456"
      "GOMEMLIMIT" $v.fiCollector.goMemLimit
-}}
{{- if eq $observedMode "direct" -}}
{{- $_ := set $plain "FI_OBSERVED_CATALOG_CH_URL" (get $plain "FI_CH_URL") -}}
{{- $_ := set $plain "FI_OBSERVED_CATALOG_CH_DATABASE" $v.clickhouse.propertyCatalogDatabase -}}
{{- $_ := set $plain "FI_OBSERVED_CATALOG_CH_USERNAME" "observed_catalog_writer" -}}
{{- end -}}
{{- if not $redisTls }}{{ $_ := set $plain "FI_AUTH_REDIS_ADDR" (printf "%s:%s" (include "futureagi.redis.host" $root) (include "futureagi.redis.port" $root)) }}{{ end -}}
{{- $plain = mergeOverwrite $plain (include "futureagi.env.platform" (dict "root" $root "kind" "collector") | fromYaml) $overrides -}}
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
{{/* "true" when the gateway keeps rate limits, budgets and other shared
state in Redis: agentccGateway.redis.enabled true, or auto with more than one
replica. The gateway has no Redis TLS, so auto stays off with
redis.external.tls. */}}
{{- define "futureagi.gateway.redis" -}}
{{- $g := .Values.agentccGateway -}}
{{- $mode := dig "redis" "enabled" "auto" $g | toString -}}
{{- $tls := and (eq .Values.redis.mode "external") .Values.redis.external.tls -}}
{{- if eq $mode "true" -}}true
{{- else if and (eq $mode "auto") (not $tls) (eq (include "futureagi.multiReplica" $g) "true") -}}true
{{- end -}}
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
{{- $secretEnv = append $secretEnv (dict "name" "AGENTCC_WEBHOOK_SECRET" "secret" (include "futureagi.webhookSecretName" $root) "key" "AGENTCC_WEBHOOK_SECRET") -}}
{{- range $secretEnv }}
{{- if not (hasKey $overrides .name) }}
{{ include "futureagi.secretEnv" . }}
{{- end }}
{{- end }}
{{- include "futureagi.env.llm" (dict "root" $root "overrides" $overrides "gateway" true) }}
{{- $redis := eq (include "futureagi.gateway.redis" $root) "true" -}}
{{- if and $redis (eq (include "futureagi.redis.auth" $root) "true") (not (hasKey $overrides "AGENTCC_REDIS_PASSWORD")) }}
{{ include "futureagi.redis.passwordEnv" (dict "root" $root "name" "AGENTCC_REDIS_PASSWORD") }}
{{- end }}
{{- $plain := dict
      "AGENTCC_PORT" (include "futureagi.gateway.port" $root)
      "AGENTCC_ALLOW_PRIVATE_PROVIDER_URLS" (toString $g.allowPrivateProviderURLs)
      "AWS_REGION" $v.secrets.llm.awsRegion
      "FI_BASE_URL" (printf "http://%s:%v" $backend $v.backend.service.port)
      "GOMEMLIMIT" $g.goMemLimit
-}}
{{- if $g.controlPlaneSync -}}
{{- $_ := set $plain "AGENTCC_CONTROL_PLANE_URL" (printf "http://%s:%v" $backend $v.backend.service.port) -}}
{{- $_ := set $plain "AGENTCC_SYNC_ON_STARTUP" "true" -}}
{{- end -}}
{{- if $redis -}}
{{- $_ := set $plain "AGENTCC_REDIS_ADDRESS" (printf "%s:%s" (include "futureagi.redis.host" $root) (include "futureagi.redis.port" $root)) -}}
{{- $_ := set $plain "AGENTCC_REDIS_DB" (toString (dig "redis" "db" 4 $g)) -}}
{{- end -}}
{{- if $g.gcpCredentials.existingSecret -}}
{{- $_ := set $plain "GOOGLE_APPLICATION_CREDENTIALS" "/var/run/secrets/futureagi/gcp/credentials.json" -}}
{{- end -}}
{{- $plain = mergeOverwrite $plain (include "futureagi.env.platform" (dict "root" $root "kind" "gateway") | fromYaml) $overrides -}}
{{- range $name := keys $plain | sortAlpha }}
- name: {{ $name }}
  value: {{ get $plain $name | toString | quote }}
{{- end }}
{{- end -}}
