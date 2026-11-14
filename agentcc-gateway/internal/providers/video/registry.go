package video

import (
	"fmt"
	"reflect"
	"sort"
	"sync"
)

// Registry is deliberately empty until adapters are explicitly registered.
type Registry struct {
	mu       sync.RWMutex
	adapters map[string]Adapter
}

func NewRegistry() *Registry { return &Registry{adapters: make(map[string]Adapter)} }
func (r *Registry) Register(a Adapter) error {
	if a == nil || (reflect.ValueOf(a).Kind() == reflect.Ptr && reflect.ValueOf(a).IsNil()) {
		return fmt.Errorf("nil video adapter")
	}
	service := a.Capabilities().Service
	if service == "" {
		return fmt.Errorf("video adapter service required")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.adapters == nil {
		r.adapters = make(map[string]Adapter)
	}
	if _, ok := r.adapters[service]; ok {
		return fmt.Errorf("video adapter already registered: %s", service)
	}
	r.adapters[service] = a
	return nil
}
func (r *Registry) Get(service string) (Adapter, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	a, ok := r.adapters[service]
	return a, ok
}
func (r *Registry) Services() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]string, 0, len(r.adapters))
	for k := range r.adapters {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
