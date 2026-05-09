package policy

import (
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// Override defines per-guardrail overrides for a specific key.
type Override struct {
	Disabled  bool
	Action    string   // "" means use global default
	Threshold *float64 // nil means use global default
}

// Policy is a per-key guardrail policy.
type Policy struct {
	Overrides map[string]Override // guardrail name → override
}

// RequestPolicy represents a per-request policy override.
type RequestPolicy int

const (
	// RequestPolicyNone means no per-request override.
	RequestPolicyNone RequestPolicy = iota
	// RequestPolicyLogOnly runs all guardrails in log mode.
	RequestPolicyLogOnly
	// RequestPolicyDisabled skips all guardrails.
	RequestPolicyDisabled
	// RequestPolicyStrict runs all guardrails in block mode with threshold 0.
	RequestPolicyStrict
)

// ParseRequestPolicy parses a request policy header value.
func ParseRequestPolicy(s string) (RequestPolicy, bool) {
	switch s {
	case "log-only":
		return RequestPolicyLogOnly, true
	case "disabled":
		return RequestPolicyDisabled, true
	case "strict":
		return RequestPolicyStrict, true
	case "":
		return RequestPolicyNone, true
	default:
		return RequestPolicyNone, false
	}
}

// Store manages per-key guardrail policies.
type Store struct {
	mu      sync.RWMutex
	byKeyID map[string]*Policy
}

// NewStore creates an empty PolicyStore.
func NewStore() *Store {
	return &Store{
		byKeyID: make(map[string]*Policy),
	}
}

// Register adds a policy for a key ID.
func (s *Store) Register(keyID string, policyCfg *config.KeyGuardrailPolicyConfig) {
	if policyCfg == nil || len(policyCfg.Overrides) == 0 {
		return
	}

	p := &Policy{
		Overrides: make(map[string]Override, len(policyCfg.Overrides)),
	}
	for _, o := range policyCfg.Overrides {
		p.Overrides[o.Name] = Override{
			Disabled:  o.Disabled,
			Action:    o.Action,
			Threshold: o.Threshold,
		}
	}

	s.mu.Lock()
	s.byKeyID[keyID] = p
	s.mu.Unlock()
}

// Get returns the policy for a key ID, or nil if none.
func (s *Store) Get(keyID string) *Policy {
	if s == nil {
		return nil
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.byKeyID[keyID]
}
