package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// The model an org's own provider is sent.
//
// A request whose model is "<provider>/<host id>" matches the org's provider
// <provider> when the org registered <host id> for it (orgModelMatches). The
// host takes <host id>: an OpenAI-format host refuses "openai/gpt-4o-mini".
// So the registered id is what reaches the host, on every route and wire
// format, while rc.Model keeps the model the caller asked for. A registered id
// with a slash of its own (together's "meta-llama/...", a Bedrock ARN) keeps
// it: only the matched prefix goes.

// metaOrgUpstreamModel is the request metadata key, and so the request log's,
// that holds the id the org registered for its provider and its host was
// sent, when it differs from the model the request was routed with.
const metaOrgUpstreamModel = "org_upstream_model"

// orgModelFor is the model to hand p, the org's own provider, for a request
// routed with requested that matched the id host it registered, so that p's
// host receives host. An adapter that sends its model as it is (the OpenAI
// adapter) gets host. An adapter that drops a first "/" segment itself
// (providers.UpstreamModelNamer) gets host when that leaves host alone; when
// host has a slash of its own it gets requested, "<provider>/<host>", which
// it cuts to host once. Either way host is cut exactly
// once.
func orgModelFor(p providers.Provider, requested, host string) string {
	if host == "" || host == requested {
		return requested
	}
	n, ok := p.(providers.UpstreamModelNamer)
	if !ok || n.UpstreamModelName(host) == host || n.UpstreamModelName(requested) != host {
		return host
	}
	return requested
}

// noteOrgUpstreamModel records host, the id the org registered, on rc when it
// is not requested, the model the request was routed with.
func noteOrgUpstreamModel(rc *models.RequestContext, requested, host string) {
	if host != "" && host != requested {
		rc.Metadata[metaOrgUpstreamModel] = host
	}
}

// passThroughModel is the model a native pass-through body names (the
// Anthropic Messages API, the GenAI API), for a request routed to routed.
// Such a body reaches the host as written, with no adapter to cut a prefix,
// so a request the org's own provider took under its registered id names
// that id.
func passThroughModel(rc *models.RequestContext, routed string) string {
	if host := rc.Metadata[metaOrgUpstreamModel]; host != "" {
		return host
	}
	return routed
}

// fallbackChainModel is the model a chat request's model fallback chain is
// looked up under, given routed, the model its first attempt was sent. An org
// provider match sends the org's registered id rather than the model the
// request was routed with (resolveProviderOrgFirst), and the chain is keyed
// by the latter.
func fallbackChainModel(rc *models.RequestContext, routed string) string {
	if m := rc.Metadata["org_provider_model_match"]; m != "" {
		return m
	}
	return routed
}
