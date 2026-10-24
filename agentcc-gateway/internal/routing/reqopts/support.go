// Package reqopts owns the caller's routing fields: it
// hoists a literal extra_body, parses the OpenRouter and Vercel routing
// options out of a chat request into models.RoutingOptions, validates them,
// refuses the ones the gateway does not support, and holds the managed
// parameter allowlist. The server's routing-options hooks call it; nothing
// here touches the network.
package reqopts

import "sync"

// Routing fields the gateway interprets, as the support registry and the
// 400 routing_field_not_yet_supported name them. The package that
// implements a field registers it with MarkSupported from init(), using
// these constants.
const (
	FieldProviderOrder                  = "provider.order"
	FieldProviderOnly                   = "provider.only"
	FieldProviderIgnore                 = "provider.ignore"
	FieldProviderAllowFallbacks         = "provider.allow_fallbacks"
	FieldProviderRequireParameters      = "provider.require_parameters"
	FieldProviderDataCollection         = "provider.data_collection"
	FieldProviderZDR                    = "provider.zdr"
	FieldProviderQuantizations          = "provider.quantizations"
	FieldProviderSort                   = "provider.sort"
	FieldProviderMaxPrice               = "provider.max_price"
	FieldProviderPreferredMaxLatency    = "provider.preferred_max_latency"
	FieldProviderPreferredMinThroughput = "provider.preferred_min_throughput"

	FieldGatewayOrder             = "providerOptions.gateway.order"
	FieldGatewayOnly              = "providerOptions.gateway.only"
	FieldGatewaySort              = "providerOptions.gateway.sort"
	FieldGatewayModels            = "providerOptions.gateway.models"
	FieldGatewayTags              = "providerOptions.gateway.tags"
	FieldGatewayUser              = "providerOptions.gateway.user"
	FieldGatewaySessionID         = "providerOptions.gateway.sessionId"
	FieldGatewayZeroDataRetention = "providerOptions.gateway.zeroDataRetention"

	FieldModels    = "models"
	FieldFallbacks = "fallbacks"
	FieldPreset    = "preset"
	FieldCaching   = "caching"
)

// InterpretedFields lists every field the support gate covers. plugins,
// route and transforms are absent on purpose: the gateway does not
// interpret them (they pass through on BYOK OpenRouter and are refused on
// managed traffic), and session_id is handled outside this gate.
var InterpretedFields = []string{
	FieldProviderOrder, FieldProviderOnly, FieldProviderIgnore,
	FieldProviderAllowFallbacks, FieldProviderRequireParameters,
	FieldProviderDataCollection, FieldProviderZDR, FieldProviderQuantizations,
	FieldProviderSort, FieldProviderMaxPrice,
	FieldProviderPreferredMaxLatency, FieldProviderPreferredMinThroughput,
	FieldGatewayOrder, FieldGatewayOnly, FieldGatewaySort, FieldGatewayModels,
	FieldGatewayTags, FieldGatewayUser, FieldGatewaySessionID,
	FieldGatewayZeroDataRetention,
	FieldModels, FieldFallbacks, FieldPreset, FieldCaching,
}

var support struct {
	mu     sync.RWMutex
	fields map[string]bool
}

// MarkSupported records that the gateway implements field, so a request
// that sends it is accepted. Call it from init() with a Field constant.
func MarkSupported(field string) {
	support.mu.Lock()
	defer support.mu.Unlock()
	if support.fields == nil {
		support.fields = make(map[string]bool)
	}
	support.fields[field] = true
}

// Supported reports whether field is registered.
func Supported(field string) bool {
	support.mu.RLock()
	defer support.mu.RUnlock()
	return support.fields[field]
}
