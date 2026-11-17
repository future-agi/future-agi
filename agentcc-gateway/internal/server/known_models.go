package server

import "github.com/futureagi/agentcc-gateway/internal/providers"

// The gateway's own registry, for the requests not limited to their org's
// providers (internal keys and licence-authorized requests): on a gateway
// with keys they are served only catalogue or configured models.

// resolveRegistryModel resolves model in the gateway's registry. With keys,
// a "<provider>/<model>" match or the only configured provider serves only a
// model that provider lists or the catalogue has for it
// (Registry.ResolveKnownWithRouting); router targets and model_map are
// unchanged. Without keys it is Registry.ResolveWithRouting.
func (h *Handlers) resolveRegistryModel(model string) (*providers.ResolveResult, error) {
	if h.keyStore == nil {
		return h.registry.ResolveWithRouting(model)
	}
	return h.registry.ResolveKnownWithRouting(model, h.catalogueKnows)
}

// resolveRegistryProvider is resolveRegistryModel's provider.
func (h *Handlers) resolveRegistryProvider(model string) (providers.Provider, error) {
	result, err := h.resolveRegistryModel(model)
	if err != nil {
		return nil, err
	}
	return result.Provider, nil
}

// catalogueKnows reports whether the gateway's catalogue has model for
// providerID (catalogHas: listed and priced); false with no catalogue.
func (h *Handlers) catalogueKnows(providerID, model string) bool {
	return h.catalog != nil && h.catalogHas(providerID, model)
}

// resolve is the realtime handler's resolveRegistryProvider.
func (rh *RealtimeHandler) resolve(model string) (providers.Provider, error) {
	var (
		result *providers.ResolveResult
		err    error
	)
	if rh.keyStore == nil {
		result, err = rh.registry.ResolveWithRouting(model)
	} else {
		result, err = rh.registry.ResolveKnownWithRouting(model, rh.known)
	}
	if err != nil {
		return nil, err
	}
	return result.Provider, nil
}
