package routing

// breakerKeySep separates the provider from the model or endpoint in a
// breaker key. Provider ids are config names and never contain it.
const breakerKeySep = "|"

// BreakerKey names the circuit breaker a call to model at endpointID ("" when
// the provider has no endpoint ids) through providerID records into: one
// circuit per model, "<provider>|<model>", or per endpoint when the attempt
// pins an aggregator or managed endpoint, "<provider>|<endpoint id>". One
// failing model (or endpoint) therefore opens only its own circuit, never
// the provider's others; the provider-level view aggregates them
// (CircuitBreakerRegistry). With no model either, the key is the provider.
func BreakerKey(providerID, model, endpointID string) string {
	switch {
	case endpointID != "":
		return providerID + breakerKeySep + endpointID
	case model != "":
		return providerID + breakerKeySep + model
	default:
		return providerID
	}
}

// ModelFor is the model a call to t serves when requested was asked for: the
// target's model override, else requested.
func (t RoutingTarget) ModelFor(requested string) string {
	if t.ModelOverride != "" {
		return t.ModelOverride
	}
	return requested
}
