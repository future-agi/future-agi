{{/* =====================================================================
Omega Hub (values `hub`, README "Omega Hub"): the registry runs in the
backend (futureagi/omega_hub); the checker Deployment (hub/checker.yaml)
claims each upload and runs its keyless test and demo on the runner host, a
dedicated machine whose Docker runs containers on gVisor (deploy/hub-runner).
With `hub.enabled` off nothing here renders and no other object changes
(hack/rendered_hub_checks.py).
===================================================================== */}}

{{- define "futureagi.hub.enabled" -}}
{{- if .Values.hub.enabled }}true{{ end -}}
{{- end -}}

{{/* Where OMEGA_HUB_CHECKER_TOKEN lives: hub.checker.tokenSecret, else the
chart's Secret (generated there, hub.secretData). */}}
{{- define "futureagi.hub.tokenSecretName" -}}
{{- .Values.hub.checker.tokenSecret | default (include "futureagi.secretName" .) -}}
{{- end -}}

{{/* The generated token, kept on upgrade: dict "root" $ "old" <stored data>
"data" <the Secret's data, filled in place>. */}}
{{- define "futureagi.hub.secretData" -}}
{{- if and (include "futureagi.hub.enabled" .root) (not .root.Values.hub.checker.tokenSecret) -}}
{{- $_ := set .data "OMEGA_HUB_CHECKER_TOKEN" (get .old "OMEGA_HUB_CHECKER_TOKEN" | default (randAlphaNum 64 | b64enc)) -}}
{{- end -}}
{{- end -}}

{{/* The token for the Python processes: a JSON list of secretEnv entries. */}}
{{- define "futureagi.hub.secretEnv" -}}
{{- if include "futureagi.hub.enabled" .root -}}
{{- toJson (list (dict "name" "OMEGA_HUB_CHECKER_TOKEN" "secret" (include "futureagi.hub.tokenSecretName" .root) "key" "OMEGA_HUB_CHECKER_TOKEN")) -}}
{{- else -}}
[]
{{- end -}}
{{- end -}}

{{/* The Hub's plain settings for the Python processes (YAML map; empty when
off). config.extraEnv and a component's extraEnv still win. */}}
{{- define "futureagi.hub.env" -}}
{{- if include "futureagi.hub.enabled" . -}}
{{- $h := .Values.hub -}}
{{- $app := include "futureagi.url.app" . | default "http://localhost:3000" -}}
{{- $api := include "futureagi.url.api" . | default "http://localhost:8000" -}}
{{- $env := dict
      "OMEGA_HUB_STORAGE" "object"
      "OMEGA_HUB_BUCKET" $h.bucket
      "OMEGA_HUB_APP_URL" ($h.appUrl | default (printf "%s/dashboard/hub" (trimSuffix "/" $app)))
      "OMEGA_HUB_PUBLIC_API_URL" ($h.publicApiUrl | default (printf "%s/omega-hub/v1" (trimSuffix "/" $api))) -}}
{{- with $h.maintainerOrgId }}{{ $_ := set $env "OMEGA_HUB_MAINTAINER_ORG_ID" . }}{{ end -}}
{{- if ne (toString $h.trustedProxyDepth) "" }}{{ $_ := set $env "OMEGA_HUB_TRUSTED_PROXY_DEPTH" (toString $h.trustedProxyDepth) }}{{ end -}}
{{- with $h.clientIpHeader }}{{ $_ := set $env "OMEGA_HUB_CLIENT_IP_HEADER" . }}{{ end -}}
{{- toYaml $env -}}
{{- end -}}
{{- end -}}

{{/* The UI's runtime config: the Omega products (the Hub among them) show
to the organisations their rollout admits (manage.py omega_rollout). */}}
{{- define "futureagi.hub.frontendEnv" -}}
{{- if include "futureagi.hub.enabled" . -}}
VITE_OMEGA_CONSOLE: "1"
{{- end -}}
{{- end -}}

{{/* The runner host's port, from hub.checker.runner.dockerHost. */}}
{{- define "futureagi.hub.runnerPort" -}}
{{- $hostPort := .Values.hub.checker.runner.dockerHost | trimPrefix "tcp://" | trimSuffix "/" -}}
{{- $parts := splitList ":" $hostPort -}}
{{- if gt (len $parts) 1 }}{{ last $parts }}{{ else }}2376{{ end -}}
{{- end -}}

{{/* Values the chart refuses with `hub.enabled`: a JSON list of messages,
added to validate.yaml's. */}}
{{- define "futureagi.hub.errors" -}}
{{- $errors := list -}}
{{- if include "futureagi.hub.enabled" . -}}
{{- $c := .Values.hub.checker -}}
{{- if not (or $c.image.tag $c.image.digest) -}}
{{- $errors = append $errors "hub.checker.image.tag (or .digest) is required: the checker image is not published with the chart's release; build services/omega-hub-checker, push it and name it here" -}}
{{- end -}}
{{- if not $c.runner.image -}}
{{- $errors = append $errors "hub.checker.runner.image is required: the image every upload runs in on the runner host, pinned by digest (the release manifest's runtimes.hubRunner)" -}}
{{- end -}}
{{- if not (regexMatch "^tcp://[^/:]+(:[0-9]+)?/?$" ($c.runner.dockerHost | default "")) -}}
{{- $errors = append $errors "hub.checker.runner.dockerHost is required, as tcp://<runner host>:2376: the dedicated runner host's Docker (deploy/hub-runner), never a node's own" -}}
{{- end -}}
{{- if not $c.runner.tlsSecret -}}
{{- $errors = append $errors "hub.checker.runner.tlsSecret is required: a Secret with the runner host's client certificate (keys ca.pem, cert.pem, key.pem); the runner host accepts no client without it" -}}
{{- end -}}
{{- end -}}
{{- toJson $errors -}}
{{- end -}}
