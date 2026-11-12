{{/* The omega values validate.yaml refuses, as a JSON list of messages (empty
unless omega.enabled). They mirror what the gateway (src/config.mjs, cloud
mode) and the session exchange (accounts/omega_session.py) refuse at start, so
a mistake fails the install instead of a pod. */}}
{{- define "futureagi.omega.errors" -}}
{{- $errors := list -}}
{{- if include "futureagi.omega.enabled" . -}}
{{- $o := .Values.omega -}}
{{- $g := $o.gateway -}}
{{- $https := "^https://[^/?#@\\s]+(/[^?#@\\s]*)?$" -}}
{{- $local := "^https://(localhost|127\\.0\\.0\\.1|0\\.0\\.0\\.0|\\[::1\\]|[^/:]+\\.local|[^/:]+\\.localhost)([:/]|$)" -}}
{{- if not (regexMatch $https ($o.issuer.url | toString)) -}}
{{- $errors = append $errors "omega.issuer.url is required with omega.enabled: the https issuer URL the session exchange signs as (Omega EE's hostAssertions.issuer)" -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$" ($o.issuer.keyId | toString)) -}}
{{- $errors = append $errors "omega.issuer.keyId is required with omega.enabled: the kid of the signing key (letters, digits, . _ -; at most 128)" -}}
{{- end -}}
{{- if not $o.issuer.privateKey.existingSecret -}}
{{- $errors = append $errors "omega.issuer.privateKey.existingSecret is required with omega.enabled: the Secret holding the session exchange's RSA private key" -}}
{{- end -}}
{{- /* The revocation channel (accounts/host_events/config.py refuses the same,
and then sends nothing: a removed member's Console streams live on). */ -}}
{{- $h := $o.hostEvents | default dict -}}
{{- $heUrl := $h.url | default "" | toString | trim -}}
{{- $heKeyId := $h.keyId | default "" | toString | trim -}}
{{- /* Stricter than the sender's urlsplit (config.py _check_url), never looser:
a DNS name or IPv4 address, an optional all-digit port 1-65535 split at the
first colon, no user, query or fragment. A bracketed IPv6 address is refused
(the sender's own check of it is not one a pattern can repeat). */ -}}
{{- /* The path must end in EE's receiver, /host/events (EE HOST_EVENTS_PATH), optionally
under a prefix; a bare EE base URL such as omega.gateway.ee.url would deliver nothing. */ -}}
{{- $heHostPath := "[A-Za-z0-9][A-Za-z0-9_.-]*(:[0-9]{1,5})?(/[^?#@\\s]*)?/host/events$" -}}
{{- if not $heUrl -}}
{{- $errors = append $errors "omega.hostEvents.url is required with omega.enabled: Omega EE's revocation receiver, https://<ee>/host/events (e.g. https://omega-os.omega.svc:8443/host/events); without it a member removed or downgraded here keeps their open Console streams for up to 15 minutes" -}}
{{- else if not (or (regexMatch (printf "^https://%s" $heHostPath) $heUrl) (and $h.allowHttp (regexMatch (printf "^http://%s" $heHostPath) $heUrl))) -}}
{{- $errors = append $errors "omega.hostEvents.url must be https://<DNS name or IPv4 address>[:<port>][/<prefix>]/host/events (Omega EE's revocation receiver, not the EE base URL), with no user, query or fragment (plain http only with omega.hostEvents.allowHttp, on a private network)" -}}
{{- else -}}
{{- $hePort := regexReplaceAll "^[a-z]+://[^/:]*:?([0-9]*).*$" $heUrl "${1}" -}}
{{- if and $hePort (or (lt (atoi $hePort) 1) (gt (atoi $hePort) 65535)) -}}
{{- $errors = append $errors "omega.hostEvents.url must be on a port from 1 to 65535: the sender refuses a port above 65535 or not all digits, and port 0 reaches nothing" -}}
{{- end -}}
{{- end -}}
{{- if not $heKeyId -}}
{{- $errors = append $errors "omega.hostEvents.keyId is required with omega.enabled: the keyId Omega EE lists the shared secret under (its OMEGA_HOST_EVENTS_SECRETS_FILE)" -}}
{{- else if not (regexMatch "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$" $heKeyId) -}}
{{- $errors = append $errors "omega.hostEvents.keyId must be letters, digits, . _ or -, starting with a letter or digit, at most 64 (accounts/host_events/signing.py KEY_ID)" -}}
{{- end -}}
{{- if not ($h.existingSecret | default "" | toString | trim) -}}
{{- $errors = append $errors "omega.hostEvents.existingSecret is required with omega.enabled: the Secret with the raw shared secret (32 bytes or more) Omega EE checks host events with, e.g. omega-host-events" -}}
{{- end -}}
{{- if not ($h.secretKey | default "" | toString | trim) -}}
{{- $errors = append $errors "omega.hostEvents.secretKey is required with omega.enabled: the key of the shared secret in omega.hostEvents.existingSecret (default secret)" -}}
{{- end -}}
{{- $heCa := $h.ca | default dict -}}
{{- if and ($heCa.existingSecret | default "" | toString | trim) ($heCa.existingConfigMap | default "" | toString | trim) -}}
{{- $errors = append $errors "omega.hostEvents.ca: set existingSecret or existingConfigMap, not both" -}}
{{- end -}}
{{- if not (or $g.image.tag $g.image.digest) -}}
{{- $errors = append $errors "omega.gateway.image.tag (or digest) is required with omega.enabled: no gateway image is published under the chart's version; build services/omega-gateway and push it" -}}
{{- end -}}
{{- if not (regexMatch $https ($g.ee.url | toString)) -}}
{{- $errors = append $errors "omega.gateway.ee.url is required with omega.enabled: Omega EE's https URL inside the cluster, e.g. https://omega-os.omega.svc:8443" -}}
{{- else if regexMatch $local ($g.ee.url | toString) -}}
{{- $errors = append $errors "omega.gateway.ee.url must name a cluster address, not a loopback or .local host" -}}
{{- end -}}
{{- if eq (empty $g.jwks.existingConfigMap) (empty $g.jwks.existingSecret) -}}
{{- $errors = append $errors "omega.gateway.jwks needs existingConfigMap or existingSecret (one of them): the issuer's public JWKS the gateway checks signatures with" -}}
{{- end -}}
{{- if not $g.rateLimitRedis.existingSecret -}}
{{- $errors = append $errors "omega.gateway.rateLimitRedis.existingSecret is required with omega.enabled: the Secret with the rate-limit Redis URL, so limits hold across replicas" -}}
{{- end -}}
{{- if and $g.upstreamCa.existingSecret $g.upstreamCa.existingConfigMap -}}
{{- $errors = append $errors "omega.gateway.upstreamCa: set existingSecret or existingConfigMap, not both" -}}
{{- end -}}
{{- $exchange := include "futureagi.omega.exchangeUrl" . -}}
{{- if not $exchange -}}
{{- $errors = append $errors "omega.gateway.sessionExchangeUrl is required when omega.internalTls.enabled is false: the gateway calls the session exchange over https" -}}
{{- else if not (regexMatch $https $exchange) -}}
{{- $errors = append $errors "omega.gateway.sessionExchangeUrl must be an https URL" -}}
{{- end -}}
{{- if and $o.internalTls.enabled (not $o.internalTls.existingSecret) (not (dig "name" "" ($o.internalTls.certManager.issuerRef | default dict))) -}}
{{- $errors = append $errors "omega.internalTls needs existingSecret or certManager.issuerRef.name: the listener's certificate" -}}
{{- end -}}
{{- if and $o.internalTls.enabled (eq (int $o.internalTls.port) 8000) -}}
{{- $errors = append $errors "omega.internalTls.port must differ from the API's own port 8000" -}}
{{- end -}}
{{- $drainSeconds := div (add (int64 $g.drainMs) 999) 1000 -}}
{{- $needed := add (int64 $g.preStopSleepSeconds) $drainSeconds 5 -}}
{{- if lt (int64 $g.terminationGracePeriodSeconds) $needed -}}
{{- $errors = append $errors (printf "omega.gateway.terminationGracePeriodSeconds must be at least %d: the preStop sleep (%d s) + drainMs (%d s) + 5 s for the last span flush" $needed (int64 $g.preStopSleepSeconds) $drainSeconds) -}}
{{- end -}}
{{- range $origin := splitList "," (include "futureagi.omega.allowedOrigins" .) | compact -}}
{{- if or (not (regexMatch "^https://[^/?#@\\s]+$" $origin)) (regexMatch $local $origin) -}}
{{- $errors = append $errors (printf "omega: the gateway's browser origin %s must be https and not local (set urls.app, the ingress or Gateway API host, or omega.gateway.allowedOrigins)" $origin) -}}
{{- end -}}
{{- end -}}
{{- if and (not .Values.ingress.enabled) (not .Values.gatewayApi.enabled) -}}
{{- $errors = append $errors "omega.enabled needs ingress.enabled or gatewayApi.enabled: browsers reach the gateway at /omega/api on the UI host" -}}
{{- end -}}
{{- if $g.console.enabled -}}
{{- $console := $g.console.host | toString -}}
{{- $app := include "futureagi.url.app" . | include "futureagi.urlHost" -}}
{{- $api := include "futureagi.url.api" . | include "futureagi.urlHost" -}}
{{- if not $console -}}
{{- $errors = append $errors "omega.gateway.console.host is required with omega.gateway.console.enabled: the Console's own origin" -}}
{{- else if or (eq $console $app) (eq $console $api) -}}
{{- $errors = append $errors "omega.gateway.console.host must differ from the UI and API hosts: the Console's own origin is what isolates it" -}}
{{- end -}}
{{- if not (include "futureagi.omega.allowedOrigins" .) -}}
{{- $errors = append $errors "omega.gateway.console.enabled needs the UI origin (urls.app, ingress.app.host, gatewayApi.app.host or omega.gateway.allowedOrigins): the Console may be framed only by it" -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* The chart is the only source of the delegation issuer and the revocation
channel on the API, worker and bootstrap pods, with omega.enabled or not. An
extraEnv or secrets.extra entry naming one could blank or redirect it on some
pods (a worker left MISCONFIGURED keeps bulk removals from reaching EE), or
turn delegation on with no channel at all. Names are matched as Kubernetes
gets them (quoted, as written; validate.yaml refuses whitespace around one):
in any case, any OMEGA_HOST_EVENTS_* name, and a name that is not a plain
variable name ([A-Za-z_][A-Za-z0-9_]*) but carries a reserved one (quotes or
a comment in it). config.extraEnvFrom, which cannot be read here, is pinned
instead (futureagi.omega.envFromPins). Messages name the setting, never its
value (secrets.extra holds secrets). */ -}}
{{- $reserved := include "futureagi.omega.reservedEnv" . | fromJson -}}
{{- $omegaOn := include "futureagi.omega.enabled" . -}}
{{- range $scope := include "futureagi.env.userMaps" . | fromJsonArray -}}
{{- $where := index $scope 0 -}}
{{- $env := index $scope 1 -}}
{{- range $key := keys $env | sortAlpha -}}
{{- $name := $key | trim | upper -}}
{{- $plainName := regexMatch "^[A-Za-z_][A-Za-z0-9_]*$" $key -}}
{{- $source := "" -}}
{{- if hasKey $reserved $name -}}
{{- $source = get $reserved $name -}}
{{- else if hasPrefix "OMEGA_HOST_EVENTS_" $name -}}
{{- $source = "omega.hostEvents" -}}
{{- else if and (not $plainName) (contains "OMEGA_SQUAD_DELEGATION_ISSUER" $name) -}}
{{- $source = "omega.issuer.url" -}}
{{- else if and (not $plainName) (contains "OMEGA_HOST_EVENTS_" $name) -}}
{{- $source = "omega.hostEvents" -}}
{{- end -}}
{{- if $source -}}
{{- $at := ternary (printf "%s.%s" $where $key) (printf "%s[%q]" $where $key) $plainName -}}
{{- if $omegaOn -}}
{{- $errors = append $errors (printf "omega: %s: the chart sets it from %s on the API and worker pods; remove it (a blank or other value there leaves that pod's revocation channel to Omega EE misconfigured or misdirected)" $at $source) -}}
{{- else if get $env $key | default "" | toString | trim -}}
{{- $errors = append $errors (printf "omega: %s configures Omega delegation without omega.enabled, so no pod gets the revocation channel to Omega EE or its secret: set omega.enabled with omega.issuer and omega.hostEvents instead" $at) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- /* Omega EE's NetworkPolicy admits every pod with the sender label to its
revocation receiver, so the chart is its only writer: on the API and worker
pods that get the channel (futureagi.omega.hostEvents.env), from the same
condition. The key anywhere in the values (commonLabels, any podLabels or
podAnnotations, a sender's own) is refused, naming where. */ -}}
{{- $label := include "futureagi.omega.hostEvents.senderLabelKey" . -}}
{{- range $at := include "futureagi.omega.senderLabelPaths" (dict "node" .Values "path" "" "key" $label) | fromJsonArray -}}
{{- $errors = append $errors (printf "omega: %s holds %s: Omega EE's NetworkPolicy admits every pod with that label to its revocation receiver, so only the chart sets it, on the API and worker pods that send host events with omega.enabled; remove it" $at $label) -}}
{{- end -}}
{{- toJson $errors -}}
{{- end -}}

{{/* Where the values hold a key (case and surrounding whitespace aside), as a
JSON list of the paths of the maps holding it: dict "node" <values> "path"
<the node's path, "" at the top> "key" <lower-case key>. */}}
{{- define "futureagi.omega.senderLabelPaths" -}}
{{- $found := list -}}
{{- $path := .path -}}
{{- $key := .key -}}
{{- if kindIs "map" .node -}}
{{- range $k, $v := .node -}}
{{- $at := ternary $k (printf "%s.%s" $path $k) (eq $path "") -}}
{{- if eq (toString $k | trim | lower) $key -}}
{{- $found = append $found (ternary "the top level of the values" $path (eq $path "")) -}}
{{- end -}}
{{- if or (kindIs "map" $v) (kindIs "slice" $v) -}}
{{- $found = concat $found (include "futureagi.omega.senderLabelPaths" (dict "node" $v "path" $at "key" $key) | fromJsonArray) -}}
{{- end -}}
{{- end -}}
{{- else if kindIs "slice" .node -}}
{{- range $i, $v := .node -}}
{{- if or (kindIs "map" $v) (kindIs "slice" $v) -}}
{{- $found = concat $found (include "futureagi.omega.senderLabelPaths" (dict "node" $v "path" (printf "%s[%d]" $path $i) "key" $key) | fromJsonArray) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- toJson $found -}}
{{- end -}}
