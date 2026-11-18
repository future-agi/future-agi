{{/* Use the existing listener override so --reuse-values keeps working. Do
not rewrite its host: an address reachable only inside the pod cannot be the
target of Kubernetes HTTP readiness or the Service. Reject it before rollout. */}}
{{- define "futureagi.collector.adminAddr" -}}
{{- $addr := dig "FI_ADMIN_ADDR" ":9464" (.Values.fiCollector.extraEnv | default dict) | toString -}}
{{- if not (regexMatch `^(:|0\.0\.0\.0:|\[::\]:)[1-9][0-9]{0,4}$` $addr) -}}
{{- fail "fiCollector.extraEnv.FI_ADMIN_ADDR must be a pod-reachable wildcard address such as :9500; remove the override to use :9464. A loopback bind cannot serve Kubernetes readiness or Service traffic." -}}
{{- end -}}
{{- $port := regexFind `[0-9]+$` $addr | atoi -}}
{{- if or (lt $port 1024) (gt $port 65535) (eq $port 4317) (eq $port 4318) -}}
{{- fail "fiCollector.extraEnv.FI_ADMIN_ADDR must use an unprivileged port (1024-65535) other than the OTLP ports 4317 and 4318; remove the override to use :9464." -}}
{{- end -}}
{{- $addr -}}
{{- end -}}

{{- define "futureagi.collector.adminPort" -}}
{{- include "futureagi.collector.adminAddr" . | regexFind "[0-9]+$" | atoi -}}
{{- end -}}
