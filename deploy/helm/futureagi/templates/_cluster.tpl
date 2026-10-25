{{/* Keep the chart's scheduling overrides, but never let replicas of one shard
share a node. Keeper voters use the same helper without a shard selector. */}}
{{- define "futureagi.clickhouse.clusterScheduling" -}}
{{- $scheduling := include "futureagi.scheduling" (dict "root" .root "values" .values) | fromYaml | default dict -}}
{{- $affinity := get $scheduling "affinity" | default dict -}}
{{- $anti := get $affinity "podAntiAffinity" | default dict -}}
{{- $rules := get $anti "requiredDuringSchedulingIgnoredDuringExecution" | default list -}}
{{- $rule := dict "labelSelector" (dict "matchLabels" .selector) "topologyKey" "kubernetes.io/hostname" -}}
{{- $_ := set $anti "requiredDuringSchedulingIgnoredDuringExecution" (append $rules $rule) -}}
{{- $_ := set $affinity "podAntiAffinity" $anti -}}
{{- $_ := set $scheduling "affinity" $affinity -}}
{{- toYaml $scheduling -}}
{{- end -}}
