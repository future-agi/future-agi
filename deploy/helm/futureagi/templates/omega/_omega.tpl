{{/* =====================================================================
Omega: the gateway (services/omega-gateway) and the session exchange's
signing key and internal TLS listener on the API pods. Nothing here renders
unless omega.enabled.
===================================================================== */}}

{{/* "true" when omega.enabled. */}}
{{- define "futureagi.omega.enabled" -}}
{{- if dig "enabled" false (.Values.omega | default dict) -}}true{{- end -}}
{{- end -}}

{{/* "true" when the API pods get the internal TLS listener. */}}
{{- define "futureagi.omega.internalTls" -}}
{{- if and (include "futureagi.omega.enabled" .) .Values.omega.internalTls.enabled -}}true{{- end -}}
{{- end -}}

{{/* Secret with the internal TLS listener's certificate. */}}
{{- define "futureagi.omega.internalTls.secretName" -}}
{{- .Values.omega.internalTls.existingSecret | default (printf "%s-omega-tls" (include "futureagi.component" (dict "root" . "component" "backend"))) -}}
{{- end -}}

{{/* The DNS names the API Service answers to inside the cluster: the
internal TLS certificate's SANs. */}}
{{- define "futureagi.omega.internalTls.dnsNames" -}}
{{- $backend := include "futureagi.component" (dict "root" . "component" "backend") -}}
{{- $ns := .Release.Namespace -}}
{{- list $backend (printf "%s.%s" $backend $ns) (printf "%s.%s.svc" $backend $ns) (printf "%s.%s.svc.cluster.local" $backend $ns) | toJson -}}
{{- end -}}

{{/* OMEGA_SESSION_EXCHANGE_URL: omega.gateway.sessionExchangeUrl, else the
API's internal TLS listener. */}}
{{- define "futureagi.omega.exchangeUrl" -}}
{{- $o := .Values.omega -}}
{{- if $o.gateway.sessionExchangeUrl -}}
{{- $o.gateway.sessionExchangeUrl -}}
{{- else if $o.internalTls.enabled -}}
{{- printf "https://%s.%s.svc:%v/accounts/omega/session-exchange/" (include "futureagi.component" (dict "root" . "component" "backend")) .Release.Namespace $o.internalTls.port -}}
{{- end -}}
{{- end -}}

{{/* OMEGA_ALLOWED_ORIGINS: omega.gateway.allowedOrigins, else the UI's
origin. */}}
{{- define "futureagi.omega.allowedOrigins" -}}
{{- $origins := .Values.omega.gateway.allowedOrigins | default list -}}
{{- if not $origins -}}
{{- with include "futureagi.url.app" . -}}
{{- $origins = list (include "futureagi.urlOrigin" .) -}}
{{- end -}}
{{- end -}}
{{- $origins | join "," -}}
{{- end -}}

{{/* The Console origin: https://<omega.gateway.console.host>. */}}
{{- define "futureagi.omega.consoleOrigin" -}}
{{- with .Values.omega.gateway.console.host -}}{{ printf "https://%s" . }}{{- end -}}
{{- end -}}

{{/* ---------------------------------------------------------------------
The API pods: the issuer key and the internal TLS listener
--------------------------------------------------------------------- */}}

{{- define "futureagi.omega.issuer.keyDir" -}}/var/run/secrets/futureagi/omega-issuer{{- end -}}

{{/* The session exchange's settings, as a map of plain variables for
futureagi.env.python's component values. A backend extraEnv still wins over
the key id and key file; the issuer is the chart's alone (the revocation
channel names it, and futureagi.omega.errors refuses an override). */}}
{{- define "futureagi.omega.issuer.env" -}}
{{- $out := dict -}}
{{- if include "futureagi.omega.enabled" . -}}
{{- $i := .Values.omega.issuer -}}
{{- $_ := set $out "OMEGA_SQUAD_DELEGATION_ISSUER" $i.url -}}
{{- $_ := set $out "OMEGA_SQUAD_DELEGATION_KEY_ID" $i.keyId -}}
{{- $_ := set $out "OMEGA_SQUAD_DELEGATION_PRIVATE_KEY_FILE" (printf "%s/private.pem" (include "futureagi.omega.issuer.keyDir" .)) -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/* ---------------------------------------------------------------------
The revocation channel to Omega EE (accounts/host_events): the API pods send
(a drain right after each membership or user change commits, and the
trigger-admission check), and so does every Temporal worker (the 3 s drain
on queue tasks_s, which any worker Deployment may poll). The bootstrap Job
registers that drain. The chart is the only source of these settings on
those pods (futureagi.omega.errors refuses extraEnv and secrets.extra
entries that name them; futureagi.omega.envFromPins keeps
config.extraEnvFrom from supplying them).
--------------------------------------------------------------------- */}}

{{- define "futureagi.omega.hostEvents.dir" -}}/var/run/secrets/futureagi/omega-host-events{{- end -}}
{{- define "futureagi.omega.hostEvents.caDir" -}}/etc/futureagi/omega-host-events-ca{{- end -}}

{{/* The CA the sender checks Omega EE's certificate with, as JSON
{"kind": "secret"|"configMap", "name", "key"}: omega.hostEvents.ca, else
omega.gateway.upstreamCa (the CA the gateway trusts EE with). {} for the
pods' default trust (global.caBundle's REQUESTS_CA_BUNDLE when set, else
public roots), the gateway's own fallback. Passed to the sender alone
(OMEGA_HOST_EVENTS_CA_FILE), never as the pods' trust store. */}}
{{- define "futureagi.omega.hostEvents.ca" -}}
{{- $out := dict -}}
{{- if include "futureagi.omega.enabled" . -}}
{{- $o := .Values.omega -}}
{{- range $source := list (dig "hostEvents" "ca" dict $o) (dig "gateway" "upstreamCa" dict $o) -}}
{{- if not $out -}}
{{- $key := $source.key | default "ca.crt" | toString | trim -}}
{{- $secret := $source.existingSecret | default "" | toString | trim -}}
{{- $configMap := $source.existingConfigMap | default "" | toString | trim -}}
{{- if $secret -}}
{{- $out = dict "kind" "secret" "name" $secret "key" $key -}}
{{- else if $configMap -}}
{{- $out = dict "kind" "configMap" "name" $configMap "key" $key -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* The sender's settings (accounts/host_events/config.py), as a map of
plain variables for futureagi.env.python's component values. Events name
the delegation issuer, so the sender needs it too. */}}
{{- define "futureagi.omega.hostEvents.env" -}}
{{- $out := dict -}}
{{- if include "futureagi.omega.enabled" . -}}
{{- $h := .Values.omega.hostEvents -}}
{{- $_ := set $out "OMEGA_SQUAD_DELEGATION_ISSUER" .Values.omega.issuer.url -}}
{{- $_ := set $out "OMEGA_HOST_EVENTS_URL" ($h.url | default "" | toString | trim) -}}
{{- $_ := set $out "OMEGA_HOST_EVENTS_KEY_ID" ($h.keyId | default "" | toString | trim) -}}
{{- $_ := set $out "OMEGA_HOST_EVENTS_SECRET_FILE" (printf "%s/secret" (include "futureagi.omega.hostEvents.dir" .)) -}}
{{- if $h.allowHttp -}}
{{- $_ := set $out "OMEGA_HOST_EVENTS_ALLOW_HTTP" "1" -}}
{{- end -}}
{{- if include "futureagi.omega.hostEvents.ca" . | fromJson -}}
{{- $_ := set $out "OMEGA_HOST_EVENTS_CA_FILE" (printf "%s/ca.crt" (include "futureagi.omega.hostEvents.caDir" .)) -}}
{{- end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/* The bootstrap Job registers the drain schedule
(register_temporal_schedules), every 3 s where it sees the delegation
issuer and hourly where it does not (tfc/temporal/schedules/omega_host_events.py).
It sends nothing, so it gets the issuer and no secret. */}}
{{- define "futureagi.omega.scheduleEnv" -}}
{{- $out := dict -}}
{{- if include "futureagi.omega.enabled" . -}}
{{- $_ := set $out "OMEGA_SQUAD_DELEGATION_ISSUER" .Values.omega.issuer.url -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/* The variables only the chart sets on the API, worker and bootstrap pods,
as JSON {name: the value it comes from}. futureagi.omega.errors refuses them
in extraEnv and secrets.extra; futureagi.omega.envFromPins keeps
config.extraEnvFrom from supplying them. */}}
{{- define "futureagi.omega.reservedEnv" -}}
{{- toJson (dict
      "OMEGA_SQUAD_DELEGATION_ISSUER" "omega.issuer.url"
      "OMEGA_HOST_EVENTS_URL" "omega.hostEvents.url"
      "OMEGA_HOST_EVENTS_KEY_ID" "omega.hostEvents.keyId"
      "OMEGA_HOST_EVENTS_SECRET_FILE" "omega.hostEvents.existingSecret"
      "OMEGA_HOST_EVENTS_ALLOW_HTTP" "omega.hostEvents.allowHttp"
      "OMEGA_HOST_EVENTS_CA_FILE" "omega.hostEvents.ca") -}}
{{- end -}}

{{/* config.extraEnvFrom feeds the API, worker and bootstrap containers from
Secrets or ConfigMaps the chart cannot read. Where it is set, every reserved
variable (futureagi.omega.reservedEnv) is pinned to "", as a map for the
component's merge, after the chart's own settings (which win): Kubernetes
prefers a container's env to its envFrom, so no source can turn delegation or
the revocation channel on, or redirect it. accounts/host_events/config.py
reads "" as unset (DISABLED without the issuer and the url) and the session
exchange (accounts/omega_session.py) refuses a blank issuer, so with
omega.enabled false nothing signs and nothing sends. Without
config.extraEnvFrom, nothing (the render is unchanged). */}}
{{- define "futureagi.omega.envFromPins" -}}
{{- $out := dict -}}
{{- if .Values.config.extraEnvFrom -}}
{{- range $name := include "futureagi.omega.reservedEnv" . | fromJson | keys -}}
{{- $_ := set $out $name "" -}}
{{- end -}}
{{- end -}}
{{- toYaml $out -}}
{{- end -}}

{{/* Omega EE's NetworkPolicy (omega-os values-futureagi.yaml) admits Future
AGI's host-event senders by this pod label, with the release's
app.kubernetes.io/instance. The API and worker templates set it on their pod
template exactly when futureagi.omega.hostEvents.env is not empty (the same
map that gives them the channel), never on another pod and never in a
selector (immutable on upgrade); futureagi.omega.errors refuses it anywhere
in the values. */}}
{{- define "futureagi.omega.hostEvents.senderLabelKey" -}}omega.futureagi.ai/host-events-sender{{- end -}}

{{- define "futureagi.omega.hostEvents.senderLabel" -}}
{{ include "futureagi.omega.hostEvents.senderLabelKey" . }}: "true"
{{- end -}}

{{/* The shared secret (0440: root-owned, readable through the fsGroup, the
issuer key's rule, which accounts/host_events/config.py applies too), and
EE's CA when there is one (not a secret: a Secret's 0440 or a ConfigMap's
0444, as the gateway mounts it). Names are quoted: a Secret named 123 or a
key named true stays a string. */}}
{{- define "futureagi.omega.hostEvents.volumes" -}}
{{- if include "futureagi.omega.enabled" . -}}
{{- $h := .Values.omega.hostEvents -}}
{{- $ca := include "futureagi.omega.hostEvents.ca" . | fromJson -}}
- name: omega-host-events
  secret:
    secretName: {{ $h.existingSecret | default "" | toString | trim | quote }}
    defaultMode: 0440
    items:
      - key: {{ $h.secretKey | default "" | toString | trim | quote }}
        path: secret
{{- if $ca }}
- name: omega-host-events-ca
  {{- if eq $ca.kind "secret" }}
  secret:
    secretName: {{ $ca.name | quote }}
    defaultMode: 0440
  {{- else }}
  configMap:
    name: {{ $ca.name | quote }}
    defaultMode: 0444
  {{- end }}
    items:
      - key: {{ $ca.key | quote }}
        path: ca.crt
{{- end }}
{{- end -}}
{{- end -}}

{{- define "futureagi.omega.hostEvents.volumeMounts" -}}
{{- if include "futureagi.omega.enabled" . -}}
- name: omega-host-events
  mountPath: {{ include "futureagi.omega.hostEvents.dir" . }}
  readOnly: true
{{- if include "futureagi.omega.hostEvents.ca" . | fromJson }}
- name: omega-host-events-ca
  mountPath: {{ include "futureagi.omega.hostEvents.caDir" . }}
  readOnly: true
{{- end }}
{{- end -}}
{{- end -}}

{{/* Volumes of the API pods: the signing key (0440: root-owned, readable
through the fsGroup, which accounts/omega_session.py accepts), the
listener's certificate and the revocation channel's secret. */}}
{{- define "futureagi.omega.backend.volumes" -}}
{{- if include "futureagi.omega.enabled" . -}}
- name: omega-issuer
  secret:
    secretName: {{ .Values.omega.issuer.privateKey.existingSecret }}
    defaultMode: 0440
    items:
      - key: {{ .Values.omega.issuer.privateKey.key }}
        path: private.pem
{{- if include "futureagi.omega.internalTls" . }}
- name: omega-tls
  secret:
    secretName: {{ include "futureagi.omega.internalTls.secretName" . }}
    defaultMode: 0440
    items:
      - key: tls.crt
        path: tls.crt
      - key: tls.key
        path: tls.key
{{- end }}
{{ include "futureagi.omega.hostEvents.volumes" . }}
{{- end -}}
{{- end -}}

{{- define "futureagi.omega.backend.volumeMounts" -}}
{{- if include "futureagi.omega.enabled" . -}}
- name: omega-issuer
  mountPath: {{ include "futureagi.omega.issuer.keyDir" . }}
  readOnly: true
{{ include "futureagi.omega.hostEvents.volumeMounts" . }}
{{- end -}}
{{- end -}}

{{/* The internal TLS listener: ghostunnel terminates TLS on
omega.internalTls.port and forwards each connection, byte for byte, to the
API on the pod's loopback. No client certificate is asked for (mutual TLS is
lane NEW:ENCRYPTION); NetworkPolicies decide who may connect. */}}
{{- define "futureagi.omega.backend.tlsSidecar" -}}
{{- if include "futureagi.omega.internalTls" . -}}
{{- $t := .Values.omega.internalTls -}}
- name: omega-tls
  image: {{ include "futureagi.image" (dict "root" . "image" $t.image) }}
  imagePullPolicy: {{ include "futureagi.imagePullPolicy" (dict "root" . "image" $t.image) }}
  args:
    - server
    - --listen=0.0.0.0:{{ $t.port }}
    - --target=127.0.0.1:8000
    - --cert=/etc/omega-tls/tls.crt
    - --key=/etc/omega-tls/tls.key
    - --disable-authentication
    - --timed-reload={{ $t.reloadInterval }}
  ports:
    - name: https-internal
      containerPort: {{ $t.port }}
      protocol: TCP
  {{- include "futureagi.block" (dict "text" (include "futureagi.preStop" (dict "root" . "seconds" .Values.backend.preStopSleepSeconds "native" true)) "indent" 2) }}
  # Through the listener to the API: proves the certificate loads and the
  # API answers behind it. The kubelet does not verify the certificate.
  readinessProbe:
    httpGet:
      path: /health/
      port: https-internal
      scheme: HTTPS
      httpHeaders:
        - name: Host
          value: localhost
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 3
  resources:
    {{- toYaml $t.resources | nindent 4 }}
  securityContext:
    {{- include "futureagi.containerSecurityContext" (dict "root" . "overrides" (dict)) | nindent 4 }}
  volumeMounts:
    - name: omega-tls
      mountPath: /etc/omega-tls
      readOnly: true
{{- end -}}
{{- end -}}

{{/* ---------------------------------------------------------------------
The gateway
--------------------------------------------------------------------- */}}

{{- define "futureagi.omega.gateway.jwksPath" -}}/etc/omega-gateway/jwks/jwks.json{{- end -}}
{{- define "futureagi.omega.gateway.caPath" -}}/etc/omega-gateway/ca/ca.crt{{- end -}}
{{- define "futureagi.omega.gateway.otlpPath" -}}/var/run/secrets/futureagi/omega-otlp/credentials.json{{- end -}}

{{/* The gateway's ServiceAccount (omega.gateway.serviceAccount): its own unless
`create` is off and no name is set, then the API's. */}}
{{- define "futureagi.omega.gateway.serviceAccountName" -}}
{{- $sa := .Values.omega.gateway.serviceAccount | default dict -}}
{{- if $sa.create -}}
{{- $sa.name | default (include "futureagi.component" (dict "root" . "component" "omega-gateway")) -}}
{{- else -}}
{{- $sa.name | default (include "futureagi.serviceAccountName" .) -}}
{{- end -}}
{{- end -}}

{{/* "true" when the gateway trusts an extra CA for its HTTPS hops. */}}
{{- define "futureagi.omega.gateway.ca" -}}
{{- $ca := .Values.omega.gateway.upstreamCa -}}
{{- if or $ca.existingSecret $ca.existingConfigMap -}}true{{- end -}}
{{- end -}}

{{/* The gateway's environment, in cloud mode (src/config.mjs): HTTPS,
non-loopback upstreams, signatures checked, limits in Redis. */}}
{{- define "futureagi.omega.gateway.env" -}}
{{- $root := . -}}
{{- $o := .Values.omega -}}
{{- $g := $o.gateway -}}
{{- $overrides := $g.extraEnv | default dict -}}
{{- if not (hasKey $overrides "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL") }}
{{ include "futureagi.secretEnv" (dict "name" "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL" "secret" $g.rateLimitRedis.existingSecret "key" $g.rateLimitRedis.key) }}
{{- end }}
{{- $plain := dict
      "OMEGA_GATEWAY_MODE" "cloud"
      "HOST" "0.0.0.0"
      "PORT" "8080"
      "OMEGA_GATEWAY_BASE_PATH" "/omega/api"
      "OMEGA_REMOTE_BASE_URL" $g.ee.url
      "OMEGA_GATEWAY_EE_READY_PATH" $g.ee.readyPath
      "OMEGA_SESSION_EXCHANGE_URL" (include "futureagi.omega.exchangeUrl" $root)
      "OMEGA_ASSERTION_ISSUER" $o.issuer.url
      "OMEGA_ASSERTION_JWKS_FILE" (include "futureagi.omega.gateway.jwksPath" $root)
      "OMEGA_ALLOWED_ORIGINS" (include "futureagi.omega.allowedOrigins" $root)
      "OMEGA_GATEWAY_TRUSTED_PROXY_HOPS" (toString $g.trustedProxyHops)
      "OMEGA_MAX_BODY_BYTES" (toString (int64 $g.maxBodyBytes))
      "OMEGA_MAX_BUNDLE_BYTES" (toString (int64 $g.maxBundleBytes))
      "OMEGA_GATEWAY_DRAIN_MS" (toString (int64 $g.drainMs))
      "NODE_ENV" "production"
-}}
{{- if include "futureagi.omega.gateway.ca" $root -}}
{{- $_ := set $plain "NODE_EXTRA_CA_CERTS" (include "futureagi.omega.gateway.caPath" $root) -}}
{{- else if include "futureagi.caBundle.enabled" $root -}}
{{- $_ := set $plain "NODE_EXTRA_CA_CERTS" (include "futureagi.caBundle.path" $root) -}}
{{- end -}}
{{- if $g.console.enabled -}}
{{- $_ := set $plain "OMEGA_CONSOLE_ORIGIN" (include "futureagi.omega.consoleOrigin" $root) -}}
{{- $_ := set $plain "OMEGA_CONSOLE_PORT" "8081" -}}
{{- $_ := set $plain "OMEGA_CONSOLE_TRUSTED_TYPES" $g.console.trustedTypes -}}
{{- with $g.console.staticDir }}{{ $_ := set $plain "OMEGA_CONSOLE_STATIC_DIR" . }}{{ end -}}
{{- end -}}
{{- with $g.otlp.tracesUrl -}}
{{- $_ := set $plain "OMEGA_GATEWAY_OTLP_TRACES_URL" . -}}
{{- $_ := set $plain "OMEGA_GATEWAY_OTLP_PROJECT" $g.otlp.project -}}
{{- if $g.otlp.credentials.existingSecret -}}
{{- $_ := set $plain "OMEGA_GATEWAY_OTLP_CREDENTIALS_FILE" (include "futureagi.omega.gateway.otlpPath" $root) -}}
{{- end -}}
{{- end -}}
{{- $plain = mergeOverwrite $plain (include "futureagi.env.platform" (dict "root" $root "kind" "omega-gateway") | fromYaml) (omit $overrides "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL") -}}
{{- if hasKey $overrides "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL" -}}
{{- $_ := set $plain "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL" (get $overrides "OMEGA_GATEWAY_RATE_LIMIT_REDIS_URL") -}}
{{- end -}}
{{- range $name := keys $plain | sortAlpha }}
- name: {{ $name | quote }}
  value: {{ get $plain $name | toString | quote }}
{{- end }}
{{- end -}}
