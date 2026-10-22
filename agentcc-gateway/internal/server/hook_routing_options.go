package server

import (
	"log/slog"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

// Request-log metadata keys this file writes.
const (
	metaRoutingOptions         = "routing_options"          // the caller's routing fields, compact JSON, at most 1 KB
	metaUpstreamProviderObject = "upstream_provider_object" // the provider object last sent to OpenRouter
)

// openRouterType is the config.yaml provider type of OpenRouter.
const openRouterType = "openrouter"

// applyRoutingOptions is call-order step 6: it hoists a literal extra_body,
// parses the caller's routing fields into rc.Routing and deletes them from
// the body, keeping body session_id in rc.Routing.SessionID (the parse step
// has read it already), and refuses a routing field the gateway does not
// support (reqopts.CheckSupported). Keep observeHook as its first call.
func (h *Handlers) applyRoutingOptions(rc *models.RequestContext, req *models.ChatCompletionRequest) *models.APIError {
	h.observeHook(hookApplyRoutingOptions, rc)
	if !reqopts.HasRoutingFields(req.Extra) {
		return nil
	}
	res, apiErr := reqopts.Parse(req)
	if apiErr != nil {
		return apiErr
	}
	res.Options.Effective = rc.Routing.Effective
	rc.Routing = res.Options
	if apiErr := reqopts.CheckSupported(res.Interpreted); apiErr != nil {
		return apiErr
	}
	rc.SetMetadata(metaRoutingOptions, reqopts.Summary(res))
	return nil
}

// checkRoutedRequest refuses what a managed request may not send, once
// resolveRoute has decided the request is managed: plugins, route or
// transforms (400 unsupported_routing_field), and any parameter outside the
// managed allowlist (400 unsupported_field_for_managed). It is not a
// call-order hook: ChatCompletion calls it right after resolveRoute, before
// the pre-plugins take a credit hold.
func (h *Handlers) checkRoutedRequest(rc *models.RequestContext) *models.APIError {
	if credentialType(rc) != models.CredentialManaged {
		return nil
	}
	return reqopts.CheckManaged(rc.Request, &rc.Routing)
}

// rewriteUpstreamRouting is call-order step 15, run immediately before each
// dispatch to provider: it writes the routing the gateway decided into the
// outbound request. A managed attempt sends the hashed org id as `user`
// and nothing outside the managed allowlist; to OpenRouter it also sends
// the gateway-built provider object and usage.include=true. A BYOK attempt
// to OpenRouter re-emits the caller's validated provider object, plugins,
// transforms and route; any other upstream gets none of them. Keep
// observeHook as its first call.
func (h *Handlers) rewriteUpstreamRouting(rc *models.RequestContext, provider providers.Provider, req *models.ChatCompletionRequest) {
	h.observeHook(hookRewriteUpstream, rc)
	if credentialType(rc) == models.CredentialManaged {
		h.rewriteManaged(rc, provider, req)
		return
	}
	if !hasPassThrough(&rc.Routing) && rc.Billing.UpstreamUser == "" {
		return // nothing the gateway owns is in, or must leave, this request
	}
	h.rewriteBYOK(rc, provider, req)
}

func (h *Handlers) rewriteManaged(rc *models.RequestContext, provider providers.Provider, req *models.ChatCompletionRequest) {
	// Take out what an earlier attempt wrote first, so only caller fields
	// count below. checkRoutedRequest refused those for the routed provider;
	// an attempt it never saw (a fallback hop that became managed) is cut
	// down to the allowlist here, so none of it reaches FutureAGI's account.
	openai.ClearRouting(req, rc.Billing.UpstreamUser != "")
	if removed := reqopts.StripForManaged(req); len(removed) > 0 {
		slog.Warn("managed attempt: removed fields outside the managed allowlist",
			"request_id", rc.RequestID, "provider", rc.Provider, "fields", removed)
	}
	rc.Billing.UpstreamUser = reqopts.UpstreamUser(rc.Metadata["org_id"])
	req.User = rc.Billing.UpstreamUser
	if !h.openRouterTarget(rc, provider) {
		return
	}
	obj, err := openai.PinManaged(req, rc.Billing.EndpointSet, rc.Routing.Effective)
	if err != nil {
		slog.Error("managed attempt: building the OpenRouter provider object", "request_id", rc.RequestID, "error", err)
		return
	}
	rc.SetMetadata(metaUpstreamProviderObject, string(obj))
}

func (h *Handlers) rewriteBYOK(rc *models.RequestContext, provider providers.Provider, req *models.ChatCompletionRequest) {
	// After a managed attempt, never hand its hashed user or its pinned
	// fields to a BYOK upstream. The caller's own user is not restorable
	// here (rc.UserID may hold the key owner), so it is dropped.
	pinned := rc.Billing.UpstreamUser != ""
	if pinned && req.User == rc.Billing.UpstreamUser {
		req.User = ""
	}
	openai.ClearRouting(req, pinned)
	if !hasPassThrough(&rc.Routing) || !h.openRouterTarget(rc, provider) {
		return
	}
	obj, err := openai.EmitBYOK(req, &rc.Routing)
	if err != nil {
		slog.Error("byok attempt: re-emitting the OpenRouter routing fields", "request_id", rc.RequestID, "error", err)
		return
	}
	if obj != nil {
		rc.SetMetadata(metaUpstreamProviderObject, string(obj))
	}
}

// hasPassThrough reports whether o carries a field a BYOK OpenRouter
// upstream receives.
func hasPassThrough(o *models.RoutingOptions) bool {
	return o.Provider != nil || o.Plugins != nil || o.Transforms != nil || o.Route != ""
}

// openRouterTarget reports whether a dispatch to p goes to OpenRouter: p's
// base URL is OpenRouter's, or the gateway provider it serves (rc.Provider,
// or p itself) is configured with type openrouter.
func (h *Handlers) openRouterTarget(rc *models.RequestContext, p providers.Provider) bool {
	if o, ok := p.(interface{ IsOpenRouter() bool }); ok && o.IsOpenRouter() {
		return true
	}
	for _, mp := range h.registry.ManagedProviders() {
		if mp.Type == openRouterType && (mp.ID == rc.Provider || mp.ID == p.ID()) {
			return true
		}
	}
	return false
}
