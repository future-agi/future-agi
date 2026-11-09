package reqopts

import (
	"encoding/json"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// MaxSummaryBytes bounds the routing_options log metadata.
const MaxSummaryBytes = 1024

// summary is the logged form of a request's routing options. The caller's
// end-user and session ids in providerOptions.gateway are logged only as
// present, never by value.
type summary struct {
	Fields     []string              `json:"fields,omitempty"`
	Provider   *tenant.ProviderPrefs `json:"provider,omitempty"`
	Models     []string              `json:"models,omitempty"`
	Gateway    *gatewaySummary       `json:"gateway,omitempty"`
	Route      string                `json:"route,omitempty"`
	Plugins    json.RawMessage       `json:"plugins,omitempty"`
	Transforms json.RawMessage       `json:"transforms,omitempty"`
	Preset     string                `json:"preset,omitempty"`
	Caching    json.RawMessage       `json:"caching,omitempty"`
	SessionID  bool                  `json:"session_id,omitempty"`
	Truncated  bool                  `json:"truncated,omitempty"`
}

type gatewaySummary struct {
	Order             []string `json:"order,omitempty"`
	Only              []string `json:"only,omitempty"`
	Sort              string   `json:"sort,omitempty"`
	Models            []string `json:"models,omitempty"`
	Tags              []string `json:"tags,omitempty"`
	User              bool     `json:"user,omitempty"`
	SessionID         bool     `json:"session_id,omitempty"`
	ZeroDataRetention *bool    `json:"zero_data_retention,omitempty"`
}

// Summary is res as compact JSON for the request log's routing_options
// metadata, at most MaxSummaryBytes: when the full form is longer, only the
// field names are kept, marked truncated.
func Summary(res Result) string {
	o := res.Options
	s := summary{
		Fields:     fieldNames(res),
		Provider:   ProviderObject(o.Provider),
		Models:     o.Models,
		Route:      o.Route,
		Plugins:    o.Plugins,
		Transforms: o.Transforms,
		Preset:     o.Preset,
		Caching:    o.Caching,
		SessionID:  o.SessionID != "",
	}
	if g := o.Gateway; g != nil {
		s.Gateway = &gatewaySummary{Order: g.Order, Only: g.Only, Sort: g.Sort, Models: g.Models,
			Tags: g.Tags, User: g.User != "", SessionID: g.SessionID != "", ZeroDataRetention: g.ZeroDataRetention}
	}
	if b, err := json.Marshal(s); err == nil && len(b) <= MaxSummaryBytes {
		return string(b)
	}
	b, _ := json.Marshal(summary{Fields: s.Fields, Truncated: true})
	if len(b) > MaxSummaryBytes {
		return `{"truncated":true}`
	}
	return string(b)
}

// fieldNames is every routing field res carries: the interpreted ones, then
// the pass-through ones.
func fieldNames(res Result) []string {
	names := append([]string(nil), res.Interpreted...)
	o := res.Options
	if o.Route != "" {
		names = append(names, keyRoute)
	}
	if o.Plugins != nil {
		names = append(names, keyPlugins)
	}
	if o.Transforms != nil {
		names = append(names, keyTransforms)
	}
	if o.SessionID != "" {
		names = append(names, keySessionID)
	}
	return names
}
