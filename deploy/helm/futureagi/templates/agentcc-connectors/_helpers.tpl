{{/* =====================================================================
agentcc-connectors: the K2 connector runtime (CONNECTORS-PLAN 2.6).

One stateless binary that serves /<app-id>/mcp over Streamable HTTP for
every kind: rest provider of the catalog bundle embedded in it. The bearer on
each MCP request is the end user's vendor token, which the gateway sends per
call; the runtime never stores or logs it and sends it only to the vendor
host the app's spec names.

Reachable only from the gateway pods: a ClusterIP Service, no Ingress or
route, and its own NetworkPolicy, rendered whatever networkPolicy.enabled
says.

Deploy notes
- Image: the runtime ships in the agentcc-gateway image as a second binary,
  /app/agentcc-connectors (decision D7), so it runs the gateway's image, tag
  and digest, and the gateway, this runtime and the backend come from one
  release.
- Catalog: the runtime serves only the bundle embedded in its binary; the
  chart offers no catalog directory for it. The backend (Django) checks every
  push against its own embedded copy of the same bundle and follows nothing
  else, so the gateway's connectors.catalog_dir is unsupported with Django
  pushes (AM-C23 c).
- The runtime exits 1 at start when its bundle has no kind: rest provider it
  can serve (it never answers 404 for an app it claims), so it is enabled only
  from a release whose bundle has one.
- The gateway reaches it over plain HTTP at a private address, which its MCP
  egress guard refuses unless mcp.egress.private_allow lists it (decision 39):
  the chart adds the Service's in-cluster host:port there (dialAddress below);
  with agentccGateway.existingConfigMap, the operator adds it.
- Transport (open decision, IR-CN23-3): the hop from the gateway to this
  runtime is plain HTTP, so the end user's vendor bearer token crosses the pod
  network unencrypted. The NetworkPolicy limits which pods may connect; it
  does not stop someone on the node or the CNI path from reading the traffic.
  The runtime has no TLS listener (its flags are --addr, --addr-file,
  --catalog-dir, --replay, --vendor-timeout, --max-vendor-bytes and
  --page-size; it serves plain HTTP), so TLS needs a runtime change or a
  proxy in front of it. A K2 server URL the gateway dials must be
  http://<dialAddress>/<app-id>/mcp; an https URL fails the TLS handshake
  against this listener. CN1's manifest rule writes
  https://{connectorsHost}/<app-id>/mcp, and the backend fills no
  {connectorsHost} yet. Enable the runtime only from a release where that
  decision is recorded in the plan, in the commit that makes a rest provider
  addable: either a backend rule that dials the in-cluster http URL from a
  deployment setting, with the plaintext hop written down and accepted, or
  TLS in front of the runtime, which this chart then renders.
- --replay (answer a vendor origin from a loopback address) is for
  conformance runs only; the chart never sets it.
===================================================================== */}}

{{/* "true" when agentccConnectors.enabled; empty (off) when the key is unset. */}}
{{- define "futureagi.agentccConnectors.enabled" -}}
{{- if (.Values.agentccConnectors | default dict).enabled -}}true{{- end -}}
{{- end -}}

{{/* The runtime's listen port, which is also its Service port. Fixed: the
gateway's mcp.egress.private_allow entry and the K2 server URLs name it. */}}
{{- define "futureagi.agentccConnectors.port" -}}8090{{- end -}}

{{/* Seconds the runtime drains in-flight calls after SIGTERM before it exits
(cmd/agentcc-connectors shutdownGrace). */}}
{{- define "futureagi.agentccConnectors.drainSeconds" -}}15{{- end -}}

{{/* The Service as the gateway dials it, <service>.<namespace>.svc:<port>:
the mcp.egress.private_allow entry that lets the gateway reach a private
address over plain HTTP (a host-name entry matches the name as dialled, so a
K2 server URL uses exactly this host). */}}
{{- define "futureagi.agentccConnectors.dialAddress" -}}
{{- printf "%s.%s.svc:%s" (include "futureagi.component" (dict "root" . "component" "agentcc-connectors")) .Release.Namespace (include "futureagi.agentccConnectors.port" .) -}}
{{- end -}}

{{/* GOMEMLIMIT for the runtime: 90% of its memory limit, in bytes, so the Go
garbage collector works harder before the kubelet kills the pod. Empty when
there is no memory limit or it is not a quantity. */}}
{{- define "futureagi.agentccConnectors.goMemLimit" -}}
{{- with dig "limits" "memory" "" (.Values.agentccConnectors.resources | default dict) -}}
{{- $bytes := include "futureagi.quantityBytes" . -}}
{{- if regexMatch "^[0-9]+$" $bytes -}}
{{- printf "%.0f" (floor (mulf (float64 $bytes) 0.9)) -}}
{{- end -}}
{{- end -}}
{{- end -}}
