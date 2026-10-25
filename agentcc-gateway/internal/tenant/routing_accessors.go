package tenant

import "encoding/json"

// The model fallback switch. The console shows "Model Fallback
// Chains" on when an org never set fallback_enabled, so an org with chains
// and no explicit switch gets them; an explicit false stays off. A plain
// bool cannot tell unset from false, so RoutingConfig keeps an explicit false
// in fallbackOff, read and written through its JSON form here: the push
// decodes the contract's *bool, and the store's own JSON (GET
// /-/orgs/{org}/config, the Redis copy) writes it back unchanged.

// FallbackOn reports whether the org's model fallback chains run: the
// fallback_enabled the org set, else whether it has any chain.
func (r *RoutingConfig) FallbackOn() bool {
	switch {
	case r == nil:
		return false
	case r.FallbackEnabled:
		return true
	case r.fallbackOff:
		return false
	default:
		return len(r.ModelFallbacks) > 0
	}
}

// FallbackSet reports whether the org set fallback_enabled explicitly, to
// true or to false.
func (r *RoutingConfig) FallbackSet() bool {
	return r != nil && (r.FallbackEnabled || r.fallbackOff)
}

// SetFallbackEnabled sets the switch explicitly, as a push carrying
// fallback_enabled does.
func (r *RoutingConfig) SetFallbackEnabled(on bool) {
	r.FallbackEnabled, r.fallbackOff = on, !on
}

// routingConfigFields is RoutingConfig without its JSON methods.
type routingConfigFields RoutingConfig

// UnmarshalJSON decodes a routing config, keeping fallback_enabled: false
// apart from an absent fallback_enabled.
func (r *RoutingConfig) UnmarshalJSON(data []byte) error {
	aux := struct {
		*routingConfigFields
		FallbackEnabled *bool `json:"fallback_enabled,omitempty"`
	}{routingConfigFields: (*routingConfigFields)(r)}
	if err := json.Unmarshal(data, &aux); err != nil {
		return err
	}
	r.FallbackEnabled = aux.FallbackEnabled != nil && *aux.FallbackEnabled
	r.fallbackOff = aux.FallbackEnabled != nil && !*aux.FallbackEnabled
	return nil
}

// MarshalJSON encodes a routing config with fallback_enabled as set:
// true, false, or absent when the org never set it.
func (r RoutingConfig) MarshalJSON() ([]byte, error) {
	aux := struct {
		routingConfigFields
		FallbackEnabled *bool `json:"fallback_enabled,omitempty"`
	}{routingConfigFields: routingConfigFields(r)}
	if r.FallbackSet() {
		on := r.FallbackEnabled
		aux.FallbackEnabled = &on
	}
	return json.Marshal(aux)
}
