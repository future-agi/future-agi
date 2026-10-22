package models

import "encoding/json"

// RoutingOptions are the caller's routing fields, which the gateway owns
// (the gateway parses them out of the body into rc.Routing and deletes
// them from Request.Extra, so none reaches an upstream unless the gateway
// rebuilds it).
type RoutingOptions struct {
	// Provider is the OpenRouter-shaped provider object.
	Provider *ProviderPrefs
	// Gateway is Vercel's providerOptions.gateway.
	Gateway *GatewayOptions
	// Models is the body's models[] (or Messages fallbacks[]), the request's
	// own fallback chain after its model.
	Models     []string
	Route      string
	Plugins    json.RawMessage
	Transforms json.RawMessage
	Preset     string
	Caching    json.RawMessage
	// SessionID is the body's session_id, kept here when the routing parser
	// strips it. The agent-context parse step has already read it.
	SessionID string

	// Effective is the data policy the request runs under, after the org's
	// and the caller's settings are combined.
	Effective EffectiveDataPolicy
}

// EffectiveDataPolicy is the data policy a request runs under.
type EffectiveDataPolicy struct {
	ZDR            bool
	DataCollection string // "allow" or "deny"
}

// ProviderPrefs mirrors the OpenRouter provider object (tenant.ProviderPrefs,
// the org-config shape) so this package stays free of the tenant package.
// Order, Only and Ignore name upstream provider slugs, never gateway provider
// ids.
type ProviderPrefs struct {
	Order                  []string
	Only                   []string
	Ignore                 []string
	AllowFallbacks         *bool
	RequireParameters      *bool
	DataCollection         string // "allow" or "deny"
	ZDR                    *bool
	Quantizations          []string
	Sort                   *ProviderSort
	MaxPrice               *ProviderMaxPrice
	PreferredMaxLatency    *PercentileBound
	PreferredMinThroughput *PercentileBound
}

// ProviderSort is the provider object's sort: price, throughput or latency,
// optionally partitioned by model or none.
type ProviderSort struct {
	By        string
	Partition string
}

// ProviderMaxPrice caps provider prices: prompt and completion in USD per
// million tokens, request and image in USD per unit.
type ProviderMaxPrice struct {
	Prompt     *float64
	Completion *float64
	Request    *float64
	Image      *float64
}

// PercentileBound is a latency or throughput preference: one number, or
// thresholds per percentile.
type PercentileBound struct {
	Value *float64
	P50   *float64
	P75   *float64
	P90   *float64
	P99   *float64
}

// GatewayOptions is Vercel's providerOptions.gateway object.
type GatewayOptions struct {
	Order             []string
	Only              []string
	Sort              string // cost, ttft or tps (aliases of price, latency, throughput)
	Models            []string
	Tags              []string
	User              string
	SessionID         string
	ZeroDataRetention *bool
}

// RoutingEvidence is what the gateway decided for a request, for the log, the
// response evidence and the generation record.
type RoutingEvidence struct {
	PolicyVersion     string // "v<meta.version>" or "unversioned"
	PolicyVariant     string // "stable" or "candidate"
	CatalogSnapshotID string
	AutoStep          Step
	Affinity          string // hit, new, off or broken:<reason>
	Preset            string
	PresetSource      string
}

// Clone returns a deep copy of o.
func (o RoutingOptions) Clone() RoutingOptions {
	o.Provider = o.Provider.clone()
	o.Gateway = o.Gateway.clone()
	o.Models = cloneStrings(o.Models)
	o.Plugins = cloneRaw(o.Plugins)
	o.Transforms = cloneRaw(o.Transforms)
	o.Caching = cloneRaw(o.Caching)
	return o
}

func (p *ProviderPrefs) clone() *ProviderPrefs {
	if p == nil {
		return nil
	}
	c := *p
	c.Order = cloneStrings(p.Order)
	c.Only = cloneStrings(p.Only)
	c.Ignore = cloneStrings(p.Ignore)
	c.AllowFallbacks = cloneBool(p.AllowFallbacks)
	c.RequireParameters = cloneBool(p.RequireParameters)
	c.ZDR = cloneBool(p.ZDR)
	c.Quantizations = cloneStrings(p.Quantizations)
	if p.Sort != nil {
		s := *p.Sort
		c.Sort = &s
	}
	if p.MaxPrice != nil {
		c.MaxPrice = &ProviderMaxPrice{
			Prompt:     cloneFloat(p.MaxPrice.Prompt),
			Completion: cloneFloat(p.MaxPrice.Completion),
			Request:    cloneFloat(p.MaxPrice.Request),
			Image:      cloneFloat(p.MaxPrice.Image),
		}
	}
	c.PreferredMaxLatency = p.PreferredMaxLatency.clone()
	c.PreferredMinThroughput = p.PreferredMinThroughput.clone()
	return &c
}

func (b *PercentileBound) clone() *PercentileBound {
	if b == nil {
		return nil
	}
	return &PercentileBound{
		Value: cloneFloat(b.Value),
		P50:   cloneFloat(b.P50),
		P75:   cloneFloat(b.P75),
		P90:   cloneFloat(b.P90),
		P99:   cloneFloat(b.P99),
	}
}

func (g *GatewayOptions) clone() *GatewayOptions {
	if g == nil {
		return nil
	}
	c := *g
	c.Order = cloneStrings(g.Order)
	c.Only = cloneStrings(g.Only)
	c.Models = cloneStrings(g.Models)
	c.Tags = cloneStrings(g.Tags)
	c.ZeroDataRetention = cloneBool(g.ZeroDataRetention)
	return &c
}

func cloneRaw(r json.RawMessage) json.RawMessage {
	if r == nil {
		return nil
	}
	return append(json.RawMessage(nil), r...)
}
