{{/* HorizontalPodAutoscaler for one Deployment:
dict "root" $ "component" <selector component> "name" <resource name> "autoscaling" <values>. */}}
{{- define "futureagi.hpa" -}}
{{- $a := .autoscaling -}}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ .name }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    {{- include "futureagi.componentLabels" (dict "root" .root "component" .component) | nindent 4 }}
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ .name }}
  minReplicas: {{ $a.minReplicas }}
  maxReplicas: {{ $a.maxReplicas }}
  metrics:
    {{- if $a.targetCPUUtilizationPercentage }}
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: {{ $a.targetCPUUtilizationPercentage }}
    {{- end }}
    {{- if $a.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: {{ $a.targetMemoryUtilizationPercentage }}
    {{- end }}
{{- end -}}

{{/* PodDisruptionBudget: dict "root" $ "component" <selector component> "name" <resource name> "pdb" <values>. */}}
{{- define "futureagi.pdb" -}}
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {{ .name }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    {{- include "futureagi.componentLabels" (dict "root" .root "component" .component) | nindent 4 }}
spec:
  maxUnavailable: {{ .pdb.maxUnavailable }}
  selector:
    matchLabels:
      {{- include "futureagi.selectorLabels" (dict "root" .root "component" .component) | nindent 6 }}
{{- end -}}

{{/* Restart the application pods when a value that feeds their Secret-backed
environment changes (Kubernetes does not restart them when a Secret changes).
Bundled datastores hash only their own credentials. */}}
{{- define "futureagi.secretsChecksum" -}}
{{- $v := .Values -}}
{{- $inputs := list $v.secrets $v.postgres.password $v.postgres.existingSecret $v.clickhouse.password $v.clickhouse.existingSecret $v.redis.password $v.redis.existingSecret $v.objectStorage.accessKey $v.objectStorage.secretKey $v.objectStorage.existingSecret -}}
{{- toJson $inputs | sha256sum -}}
{{- end -}}
