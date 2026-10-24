package openai

import (
	"encoding/json"
	"net/url"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The OpenRouter body fields the gateway writes before a dispatch.
const (
	extraProvider   = "provider"
	extraUsage      = "usage"
	extraPlugins    = "plugins"
	extraTransforms = "transforms"
	extraRoute      = "route"
)

// maxPinnedEndpoints bounds the managed endpoint set.
const maxPinnedEndpoints = 5

// usageInclude asks OpenRouter to report the cost of the call in usage.
var usageInclude = json.RawMessage(`{"include":true}`)

// openRouterHost is OpenRouter's API host.
const openRouterHost = "openrouter.ai"

// IsOpenRouter reports whether p calls OpenRouter's API, judged by its base
// URL. A provider pointed elsewhere (a mock, a proxy) is OpenRouter only if
// its configuration says so, which the caller checks.
func (p *Provider) IsOpenRouter() bool {
	u, err := url.Parse(p.baseURL)
	if err != nil {
		return false
	}
	host := strings.ToLower(u.Hostname())
	return host == openRouterHost || strings.HasSuffix(host, "."+openRouterHost)
}

// ManagedProviderObject is the provider object every managed OpenRouter
// attempt carries: order and only are the same ranked
// endpoint set, at most 5 upstream slugs; allow_fallbacks is false, so
// OpenRouter fails over only inside the set; require_parameters is true when
// the request has tools or a response_format; data_collection and zdr are
// the request's effective data policy, with data collection denied unless
// the policy allows it.
func ManagedProviderObject(set []models.EndpointRef, eff models.EffectiveDataPolicy, requireParameters bool) *tenant.ProviderPrefs {
	no := false
	zdr := eff.ZDR
	obj := &tenant.ProviderPrefs{AllowFallbacks: &no, DataCollection: "deny", ZDR: &zdr}
	if eff.DataCollection == "allow" {
		obj.DataCollection = "allow"
	}
	if requireParameters {
		yes := true
		obj.RequireParameters = &yes
	}
	seen := make(map[string]bool, len(set))
	for _, e := range set {
		slug := endpointSlug(e)
		if slug == "" || seen[strings.ToLower(slug)] {
			continue
		}
		seen[strings.ToLower(slug)] = true
		obj.Order = append(obj.Order, slug)
		if len(obj.Order) == maxPinnedEndpoints {
			break
		}
	}
	obj.Only = append([]string(nil), obj.Order...)
	return obj
}

// endpointSlug is the OpenRouter provider slug of an endpoint: the
// "<upstream_provider>[/<tag>]" segment of its id
// ("<source>:<upstream_provider>[/<tag>]:<model>") when that segment names
// its upstream, else the upstream provider alone. Never a gateway provider
// id.
func endpointSlug(e models.EndpointRef) string {
	parts := strings.SplitN(e.ID, ":", 3)
	if len(parts) == 3 {
		slug, _, _ := strings.Cut(parts[1], "/")
		if e.UpstreamProvider == "" || strings.EqualFold(slug, e.UpstreamProvider) {
			return parts[1]
		}
	}
	return e.UpstreamProvider
}

// PinManaged writes the managed provider object and usage.include=true into
// req, replacing anything there, and returns the provider object's JSON.
//
// Its error cannot occur: every field ManagedProviderObject sets is a
// string, a []string or a *bool, and the fields with custom marshalers
// (Sort, MaxPrice, the percentile bounds) stay nil. A change that makes the
// builder set a field whose marshalling can fail must keep this invariant
// or turn the error into an attempt failure.
func PinManaged(req *models.ChatCompletionRequest, set []models.EndpointRef, eff models.EffectiveDataPolicy) (json.RawMessage, error) {
	requireParameters := len(req.Tools) > 0 || req.ResponseFormat != nil
	obj, err := json.Marshal(ManagedProviderObject(set, eff, requireParameters))
	if err != nil {
		return nil, err
	}
	setExtra(req, extraProvider, obj)
	setExtra(req, extraUsage, usageInclude)
	return obj, nil
}

// EmitBYOK re-emits the caller's validated OpenRouter routing fields to a
// BYOK OpenRouter upstream, which the customer pays for: the provider
// object, plugins, transforms and route. models (the gateway's fallback
// chain) and the gateway-only fields are never forwarded. It returns the
// provider object's JSON, nil when the caller sent none.
func EmitBYOK(req *models.ChatCompletionRequest, opts *models.RoutingOptions) (json.RawMessage, error) {
	var obj json.RawMessage
	if opts.Provider != nil {
		b, err := json.Marshal(reqopts.ProviderObject(opts.Provider))
		if err != nil {
			return nil, err
		}
		obj = b
		setExtra(req, extraProvider, obj)
	}
	if opts.Plugins != nil {
		setExtra(req, extraPlugins, opts.Plugins)
	}
	if opts.Transforms != nil {
		setExtra(req, extraTransforms, opts.Transforms)
	}
	if opts.Route != "" {
		b, err := json.Marshal(opts.Route)
		if err != nil {
			return nil, err
		}
		setExtra(req, extraRoute, b)
	}
	return obj, nil
}

// ClearRouting removes from req the OpenRouter routing fields a previous
// attempt wrote (EmitBYOK's, and PinManaged's when pinned is true), before a
// dispatch to an upstream that must get none of them. A caller's own usage
// field is kept unless the gateway pinned one over it.
func ClearRouting(req *models.ChatCompletionRequest, pinned bool) {
	if len(req.Extra) == 0 {
		return
	}
	delete(req.Extra, extraProvider)
	delete(req.Extra, extraPlugins)
	delete(req.Extra, extraTransforms)
	delete(req.Extra, extraRoute)
	if pinned {
		delete(req.Extra, extraUsage)
	}
}

func setExtra(req *models.ChatCompletionRequest, key string, v json.RawMessage) {
	if req.Extra == nil {
		req.Extra = make(map[string]json.RawMessage, 2)
	}
	req.Extra[key] = v
}
