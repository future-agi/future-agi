package capability

import (
	"fmt"
	"sort"
	"sync"
)

// Spec is how a capability is called: its fallback class and the endpoint
// type its requests are logged under.
type Spec struct {
	Class        Class
	EndpointType string
}

// Registry holds the capabilities this gateway serves and each one's vendor
// adapters. Every vendor adapter is registered here; nothing else calls an
// adapter.
type Registry struct {
	mu   sync.RWMutex
	caps map[string]*registered
}

type registered struct {
	spec     Spec
	adapters map[string]Adapter
}

// NewRegistry returns an empty registry.
func NewRegistry() *Registry {
	return &Registry{caps: make(map[string]*registered)}
}

// RegisterCapability adds a capability. Registering it twice is an error.
func (r *Registry) RegisterCapability(capability string, spec Spec) error {
	if capability == "" || !spec.Class.valid() || spec.EndpointType == "" {
		return fmt.Errorf("capability %q: a name, a fallback class and an endpoint type are required", capability)
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, dup := r.caps[capability]; dup {
		return fmt.Errorf("capability %q is already registered", capability)
	}
	r.caps[capability] = &registered{spec: spec, adapters: make(map[string]Adapter)}
	return nil
}

// Register adds vendor's adapter for a registered capability.
func (r *Registry) Register(capability, vendor string, a Adapter) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	c := r.caps[capability]
	if c == nil {
		return fmt.Errorf("capability %q is not registered", capability)
	}
	if vendor == "" || a == nil {
		return fmt.Errorf("capability %q: an adapter needs a vendor", capability)
	}
	if _, dup := c.adapters[vendor]; dup {
		return fmt.Errorf("capability %q already has an adapter for %q", capability, vendor)
	}
	c.adapters[vendor] = a
	return nil
}

// Spec returns capability's spec.
func (r *Registry) Spec(capability string) (Spec, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	c := r.caps[capability]
	if c == nil {
		return Spec{}, false
	}
	return c.spec, true
}

// Adapter returns vendor's adapter for capability.
func (r *Registry) Adapter(capability, vendor string) (Adapter, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	c := r.caps[capability]
	if c == nil {
		return nil, false
	}
	a, ok := c.adapters[vendor]
	return a, ok
}

// Capabilities are the registered capabilities, sorted.
func (r *Registry) Capabilities() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]string, 0, len(r.caps))
	for name := range r.caps {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

// Vendors are capability's vendors with an adapter, sorted.
func (r *Registry) Vendors(capability string) []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	c := r.caps[capability]
	if c == nil {
		return nil
	}
	out := make([]string, 0, len(c.adapters))
	for v := range c.adapters {
		out = append(out, v)
	}
	sort.Strings(out)
	return out
}
