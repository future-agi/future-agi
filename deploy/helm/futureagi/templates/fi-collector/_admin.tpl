{{/* Use the existing listener override so --reuse-values keeps working. Do
not rewrite its host: an address reachable only inside the pod cannot be the
target of Kubernetes HTTP readiness or the Service. Reject it before rollout. */}}
{{- define "futureagi.collector.adminAddr" -}}
{{- $env := .Values.fiCollector.extraEnv | default dict -}}
{{- $addr := dig "FI_ADMIN_ADDR" ":9464" $env | toString -}}
{{- if not (regexMatch `^(:|0\.0\.0\.0:|\[::\]:)[1-9][0-9]{0,4}$` $addr) -}}
{{- fail "fiCollector.extraEnv.FI_ADMIN_ADDR must be a pod-reachable wildcard address such as :9500; remove the override to use :9464. A loopback bind cannot serve Kubernetes readiness or Service traffic." -}}
{{- end -}}
{{- $port := regexFind `[0-9]+$` $addr | atoi -}}
{{- if or (lt $port 1024) (gt $port 65535) (eq $port 4317) (eq $port 4318) -}}
{{- fail "fiCollector.extraEnv.FI_ADMIN_ADDR must use an unprivileged port (1024-65535) other than the OTLP ports 4317 and 4318; remove the override to use :9464." -}}
{{- end -}}
{{- /* Default OTLP ports are reserved above. Check saved receiver overrides too:
admin is a wildcard, so a loopback or host-specific receiver can also collide.
Empty overrides leave the binary defaults in place. Only HTTP supports off/disable. */ -}}
{{- range $key := list "FI_HTTP_ADDR" "FI_GRPC_ADDR" -}}
  {{- $receiver := get $env $key | toString -}}
  {{- if and $receiver (not (and (eq $key "FI_HTTP_ADDR") (has $receiver (list "off" "disable")))) -}}
    {{- if not (regexMatch `:\+?[0-9]+$` $receiver) -}}
      {{- fail (printf "fiCollector.extraEnv.%s must specify a numeric port so Helm can check for overlap with FI_ADMIN_ADDR; replace a service name or variable with its numeric port before upgrading." $key) -}}
    {{- end -}}
    {{- $receiverPort := regexFind `[0-9]+$` $receiver -}}
    {{- if eq (regexReplaceAll `^0+` $receiverPort "") (toString $port) -}}
      {{- fail (printf "fiCollector.extraEnv.FI_ADMIN_ADDR conflicts with %s on port %d; set FI_ADMIN_ADDR to a different Pod port before upgrading. The admin Service still accepts 9464." $key $port) -}}
    {{- end -}}
  {{- end -}}
{{- end -}}
{{- $addr -}}
{{- end -}}

{{- define "futureagi.collector.adminPort" -}}
{{- include "futureagi.collector.adminAddr" . | regexFind "[0-9]+$" | atoi -}}
{{- end -}}
