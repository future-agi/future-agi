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
  {{- with $a.behavior }}
  behavior:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end -}}

{{/* A container's preStop sleep, so load balancers and Services stop sending
it traffic before the process gets SIGTERM: dict "root" $ "seconds" <n>
["native" true]. Default: the image's `sleep`. native: the kubelet's sleep
action, for images with no shell (the Go components); Kubernetes 1.30 or newer,
left out on older clusters, where the process still drains on SIGTERM.
Nothing when seconds is 0. */}}
{{- define "futureagi.preStop" -}}
{{- $seconds := int (.seconds | default 0) -}}
{{- if gt $seconds 0 -}}
{{- if not .native }}
lifecycle:
  preStop:
    exec:
      command: ["sleep", "{{ $seconds }}"]
{{- else if semverCompare ">=1.30-0" .root.Capabilities.KubeVersion.Version }}
lifecycle:
  preStop:
    sleep:
      seconds: {{ $seconds }}
{{- end }}
{{- end }}
{{- end -}}

{{/* "true" when a component can run more than one replica:
dict "replicas" <n> "autoscaling" <values>. */}}
{{- define "futureagi.multiReplica" -}}
{{- if or (and .autoscaling .autoscaling.enabled) (gt (int (.replicas | default 1)) 1) -}}true{{- end -}}
{{- end -}}

{{/* topologySpread.preset for one component, as a YAML list:
dict "root" $ "component" <selector component>.
soft: prefer other zones and nodes. hard: never two replicas on one node while
another node is free (at least two nodes), zones preferred. */}}
{{- define "futureagi.topologySpreadPreset" -}}
{{- $preset := .root.Values.topologySpread.preset | default "none" -}}
{{- if ne $preset "none" -}}
{{- $selector := include "futureagi.selectorLabels" (dict "root" .root "component" .component) | fromYaml -}}
{{- $zone := dict "maxSkew" 1 "topologyKey" "topology.kubernetes.io/zone" "whenUnsatisfiable" "ScheduleAnyway" "labelSelector" (dict "matchLabels" $selector) "matchLabelKeys" (list "pod-template-hash") -}}
{{- $node := dict "maxSkew" 1 "topologyKey" "kubernetes.io/hostname" "whenUnsatisfiable" "ScheduleAnyway" "labelSelector" (dict "matchLabels" $selector) "matchLabelKeys" (list "pod-template-hash") -}}
{{- if eq $preset "hard" -}}
{{- $node = merge (dict "whenUnsatisfiable" "DoNotSchedule" "minDomains" 2) $node -}}
{{- end -}}
{{- toYaml (list $zone $node) -}}
{{- end -}}
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
{{- /* the license, SSO clients and email Secret, only once set (so the
checksum of an install without them is unchanged) */ -}}
{{- $extra := dict -}}
{{- with $v.license }}{{ if or .key .existingSecret }}{{ $_ := set $extra "license" (pick . "key" "existingSecret" "existingSecretKey") }}{{ end }}{{ end -}}
{{- range $provider, $p := $v.auth | default dict }}{{ if or $p.clientSecret $p.existingSecret }}{{ $_ := set $extra $provider (pick $p "clientId" "clientSecret" "existingSecret") }}{{ end }}{{ end -}}
{{- with dig "email" "existingSecret" "" $v.config }}{{ $_ := set $extra "email" . }}{{ end -}}
{{- if $extra }}{{ $inputs = append $inputs $extra }}{{ end -}}
{{- toJson $inputs | sha256sum -}}
{{- end -}}
