package routing

import (
	"fmt"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ProviderLockResolver validates and resolves provider lock requests.
// Immutable after creation — safe for concurrent use.
type ProviderLockResolver struct {
	enabled bool
	allowed map[string]bool // nil = all allowed
	denied  map[string]bool
}

// NewProviderLockResolver creates a resolver from config.
func NewProviderLockResolver(cfg config.ProviderLockConfig) *ProviderLockResolver {
	// Default to enabled if not explicitly set.
	enabled := cfg.Enabled
	if !cfg.Enabled && len(cfg.AllowedProviders) == 0 && len(cfg.DenyProviders) == 0 {
		enabled = true // enabled by default
	}

	var allowed map[string]bool
	if len(cfg.AllowedProviders) > 0 {
		allowed = make(map[string]bool, len(cfg.AllowedProviders))
		for _, p := range cfg.AllowedProviders {
			allowed[p] = true
		}
	}

	denied := make(map[string]bool, len(cfg.DenyProviders))
	for _, p := range cfg.DenyProviders {
		denied[p] = true
	}

	return &ProviderLockResolver{
		enabled: enabled,
		allowed: allowed,
		denied:  denied,
	}
}

// IsEnabled returns true if provider locking is active.
func (r *ProviderLockResolver) IsEnabled() bool {
	return r != nil && r.enabled
}

// ExtractLock checks request headers and metadata for a provider lock target.
// Returns empty string if no lock is present.
func (r *ProviderLockResolver) ExtractLock(rc *models.RequestContext, header http.Header) string {
	// Header takes priority.
	if v := header.Get("x-agentcc-provider-lock"); v != "" {
		return v
	}
	// Metadata fallback.
	if v, ok := rc.Metadata["provider_lock"]; ok && v != "" {
		return v
	}
	return ""
}

// Validate checks if the lock target is allowed by access control rules.
func (r *ProviderLockResolver) Validate(lockTarget string) error {
	if r.denied[lockTarget] {
		return fmt.Errorf("provider %q is in the deny list and cannot be locked to", lockTarget)
	}
	if r.allowed != nil && !r.allowed[lockTarget] {
		return fmt.Errorf("provider %q is not in the allowed providers list", lockTarget)
	}
	return nil
}
