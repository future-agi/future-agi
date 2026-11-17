{{/* Collector billing follows the backend's EE/cloud deployment intent, not
USAGE_EVENTS_ENABLED. Pass only the boolean: no license/cloud secret is copied
into the collector. Opaque envFrom sources require an explicit mode. */}}
{{- define "futureagi.collector.billingEnforcement" -}}
{{- $v := .Values -}}
{{- $mode := dig "enforcement" "auto" ($v.billing | default dict) | toString -}}
{{- if not (has $mode (list "auto" "true" "false")) -}}
{{- fail "billing.enforcement must be auto, true or false" -}}
{{- end -}}
{{- if ne $mode "auto" -}}
{{- $mode -}}
{{- else -}}
{{- /* Match env.python: backend.extraEnv wins over config.extraEnv; either
explicit env entry replaces the corresponding secret-backed variable. */ -}}
{{- $env := mergeOverwrite (dict) $v.config.extraEnv ($v.backend.extraEnv | default dict) -}}
{{- $cloud := get $v.secrets.extra "CLOUD_DEPLOYMENT" -}}
{{- if hasKey $env "CLOUD_DEPLOYMENT" -}}
{{- $cloud = get $env "CLOUD_DEPLOYMENT" -}}
{{- end -}}
{{- $cloud = $cloud | toString | trim | upper -}}
{{- $licensed := ne (include "futureagi.license.source" .) "" -}}
{{- if hasKey $v.secrets.extra "EE_LICENSE_KEY" -}}
{{- $licensed = ne (get $v.secrets.extra "EE_LICENSE_KEY" | toString) "" -}}
{{- end -}}
{{- if hasKey $env "EE_LICENSE_KEY" -}}
{{- $licensed = ne (get $env "EE_LICENSE_KEY" | toString) "" -}}
{{- end -}}
{{- ternary "true" "false" (or $licensed (has $cloud (list "US" "EU" "DEV"))) -}}
{{- end -}}
{{- end -}}
