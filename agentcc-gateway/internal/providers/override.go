package providers

import (
	"fmt"
	"log/slog"
	"net"
	"net/url"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// OrgProviderCache caches per-org provider instances that use org-specific
// API keys.  The base provider configs (base_url, api_format, etc.) come
// from the gateway's config.yaml; only the API key is overridden.
//
// Thread-safe: protects the cache with a RWMutex.
type OrgProviderCache struct {
	mu        sync.RWMutex
	providers map[string]Provider // key: "orgID:providerID"
	baseCfgs  map[string]config.ProviderConfig // providerID → base config
}

// NewOrgProviderCache creates a cache pre-loaded with base provider configs.
func NewOrgProviderCache(baseCfgs map[string]config.ProviderConfig) *OrgProviderCache {
	return &OrgProviderCache{
		providers: make(map[string]Provider),
		baseCfgs:  baseCfgs,
	}
}

// Get returns a cached provider for the org+provider combo, or nil if not cached.
func (c *OrgProviderCache) Get(orgID, providerID string) Provider {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.providers[orgID+":"+providerID]
}

// GetOrCreate returns an existing cached provider or creates a new one
// with the given API key.  Returns the provider and any creation error.
func (c *OrgProviderCache) GetOrCreate(orgID, providerID, apiKey string) (Provider, error) {
	return c.GetOrCreateWithTenantConfig(orgID, providerID, apiKey, nil)
}

// GetOrCreateWithTenantConfig is like GetOrCreate but accepts an optional
// tenant.ProviderConfig to build the provider when no config.yaml base exists
// (managed mode).
func (c *OrgProviderCache) GetOrCreateWithTenantConfig(orgID, providerID, apiKey string, tenantCfg *tenant.ProviderConfig) (Provider, error) {
	key := orgID + ":" + providerID

	// Fast path: already cached.
	c.mu.RLock()
	if p, ok := c.providers[key]; ok {
		c.mu.RUnlock()
		return p, nil
	}
	c.mu.RUnlock()

	// Slow path: create new provider instance.
	c.mu.Lock()
	defer c.mu.Unlock()

	// Double-check under write lock.
	if p, ok := c.providers[key]; ok {
		return p, nil
	}

	baseCfg, ok := c.baseCfgs[providerID]
	if !ok && tenantCfg != nil {
		// Validate base URL to prevent SSRF via tenant-supplied config.
		if err := validateBaseURL(tenantCfg.BaseURL); err != nil {
			return nil, fmt.Errorf("org %s provider %s: %w", orgID, providerID, err)
		}

		// Managed mode: build base config from tenant's provider config.
		timeout := time.Duration(tenantCfg.Timeout) * time.Second
		if timeout <= 0 {
			timeout = 60 * time.Second
		}
		maxConc := tenantCfg.MaxConcurrent
		if maxConc <= 0 {
			maxConc = 100
		}
		poolSize := tenantCfg.ConnPoolSize
		if poolSize <= 0 {
			poolSize = 100
		}
		apiFormat := tenantCfg.APIFormat
		if apiFormat == "" {
			apiFormat = inferAPIFormat(providerID)
		}
		baseCfg = config.ProviderConfig{
			BaseURL:            tenantCfg.BaseURL,
			APIKey:             apiKey,
			APIFormat:          apiFormat,
			DefaultTimeout:     timeout,
			MaxConcurrent:      maxConc,
			ConnPoolSize:       poolSize,
			Models:             tenantCfg.Models,
			AWSAccessKeyID:     tenantCfg.AWSAccessKeyID,
			AWSSecretAccessKey: tenantCfg.AWSSecretAccessKey,
			AWSRegion:          tenantCfg.AWSRegion,
			AWSSessionToken:    tenantCfg.AWSSessionToken,
		}
		ok = true
	}
	if !ok {
		return nil, fmt.Errorf("no base config for provider %q", providerID)
	}

	// Clone the config with the org's API key.
	orgCfg := baseCfg
	orgCfg.APIKey = apiKey

	p, err := createProvider(providerID+"_org_"+orgID, orgCfg)
	if err != nil {
		return nil, fmt.Errorf("creating org provider %s/%s: %w", orgID, providerID, err)
	}

	c.providers[key] = p
	slog.Info("created per-org provider",
		"org_id", orgID,
		"provider", providerID,
		"base_url", orgCfg.BaseURL,
		"api_format", orgCfg.APIFormat,
	)

	return p, nil
}

// isPrivateIP returns true if the given IP is in a private/reserved range.
func isPrivateIP(ip net.IP) bool {
	privateRanges := []struct {
		network string
	}{
		{"0.0.0.0/8"},
		{"10.0.0.0/8"},
		{"172.16.0.0/12"},
		{"192.168.0.0/16"},
		{"169.254.0.0/16"},
		{"127.0.0.0/8"},
		{"::1/128"},
		{"fc00::/7"},
		{"fe80::/10"},
	}
	for _, r := range privateRanges {
		_, cidr, _ := net.ParseCIDR(r.network)
		if cidr.Contains(ip) {
			return true
		}
	}
	return false
}

// validateBaseURL checks that a base URL does not resolve to a private/internal IP.
// Returns an error if the URL is invalid, unresolvable, or points to a private address.
func validateBaseURL(baseURL string) error {
	if baseURL == "" {
		return nil
	}
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return fmt.Errorf("invalid base_url: %w", err)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("base_url must use http or https scheme: %s", baseURL)
	}
	host := parsed.Hostname()
	if host == "" {
		return fmt.Errorf("base_url has no hostname: %s", baseURL)
	}

	// If the host is a raw IP, check it directly.
	if ip := net.ParseIP(host); ip != nil {
		if isPrivateIP(ip) {
			return fmt.Errorf("base_url resolves to private IP (%s): %s", ip, baseURL)
		}
		return nil
	}

	// Resolve hostname and check all IPs.
	addrs, err := net.LookupHost(host)
	if err != nil {
		return fmt.Errorf("cannot resolve base_url host %q: %w", host, err)
	}
	for _, addr := range addrs {
		ip := net.ParseIP(addr)
		if ip != nil && isPrivateIP(ip) {
			return fmt.Errorf("base_url host %q resolves to private IP (%s): %s", host, ip, baseURL)
		}
	}
	return nil
}

// inferAPIFormat guesses the API format from the provider name.
func inferAPIFormat(providerID string) string {
	switch providerID {
	case "anthropic":
		return "anthropic"
	case "google", "gemini":
		return "gemini"
	case "cohere":
		return "cohere"
	default:
		return "openai"
	}
}

// Evict removes all cached providers for an org (called when org config changes).
func (c *OrgProviderCache) Evict(orgID string) {
	c.mu.Lock()
	defer c.mu.Unlock()

	for key, p := range c.providers {
		// Keys are formatted as "orgID:providerID".
		if len(key) > len(orgID) && key[:len(orgID)+1] == orgID+":" {
			p.Close()
			delete(c.providers, key)
		}
	}
}

// EvictAll removes all cached providers (called on config reload).
func (c *OrgProviderCache) EvictAll() {
	c.mu.Lock()
	defer c.mu.Unlock()

	for key, p := range c.providers {
		p.Close()
		delete(c.providers, key)
	}
}

// Count returns the number of cached provider instances.
func (c *OrgProviderCache) Count() int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.providers)
}
