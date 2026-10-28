package providers

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/netguard"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// OrgProviderCache caches per-org provider instances built from the provider
// configs orgs push through the control plane.
//
// An org that states its own base_url gets a provider built from its config
// alone. One that does not inherits the upstream (base_url, api_format,
// headers) of the config.yaml entry with the same provider ID, but never that
// entry's credentials: an org's requests authenticate as the org.
//
// Thread-safe: protects the cache with a RWMutex.
type OrgProviderCache struct {
	mu        sync.RWMutex
	providers map[string]Provider              // key: "orgID:providerID"
	baseCfgs  map[string]config.ProviderConfig // providerID → base config

	// allowPrivateBaseURLs lets an org base_url point at private and LAN
	// addresses; see validateBaseURL.
	allowPrivateBaseURLs bool
}

// NewOrgProviderCache creates a cache pre-loaded with base provider configs.
// allowPrivateBaseURLs lets org base URLs point at private and LAN addresses.
func NewOrgProviderCache(baseCfgs map[string]config.ProviderConfig, allowPrivateBaseURLs bool) *OrgProviderCache {
	return &OrgProviderCache{
		providers:            make(map[string]Provider),
		baseCfgs:             baseCfgs,
		allowPrivateBaseURLs: allowPrivateBaseURLs,
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

// GetOrCreateWithTenantConfig is like GetOrCreate but accepts the org's
// tenant.ProviderConfig. The provider is built from that config alone when it
// states a base_url or service account, or when config.yaml has no entry for
// providerID (managed mode); otherwise it is layered onto that entry.
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
	// An org that names its own upstream must get exactly that upstream.
	// Layering it onto a config.yaml entry that shares the provider ID would
	// send the org's key to the operator's base_url instead.
	if tenantCfg != nil && (!ok || tenantCfg.BaseURL != "" || tenantCfg.ServiceAccountJSON != "") {
		// Validate base URL to prevent SSRF via tenant-supplied config.
		if err := validateBaseURL(tenantCfg.BaseURL, c.allowPrivateBaseURLs); err != nil {
			return nil, fmt.Errorf("org %s provider %s: %w", orgID, providerID, err)
		}

		// Managed mode: the org's upstream, with defaults for what it leaves
		// unset. resolveOrgConfig applies the rest of the org's config.
		apiFormat := tenantCfg.APIFormat
		if apiFormat == "" {
			apiFormat = inferAPIFormat(providerID)
		}
		baseCfg = config.ProviderConfig{
			BaseURL:        tenantCfg.BaseURL,
			APIFormat:      apiFormat,
			DefaultTimeout: 60 * time.Second,
			MaxConcurrent:  100,
			ConnPoolSize:   100,
		}
		ok = true
	}
	if !ok {
		return nil, fmt.Errorf("no base config for provider %q", providerID)
	}

	orgCfg := resolveOrgConfig(baseCfg, apiKey, tenantCfg)
	if tenantCfg != nil && tenantCfg.BaseURL != "" {
		orgCfg.DialContext = orgDialContext(c.allowPrivateBaseURLs)
	}

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

// resolveOrgConfig clones a base provider config for one org. The upstream it
// names (base_url, api_format, headers, TLS) stays; everything the org states
// about itself wins over the rest, including a path prefix stated as empty.
// None of the operator's credentials carry over: the org authenticates with
// its own or not at all.
func resolveOrgConfig(baseCfg config.ProviderConfig, apiKey string, tenantCfg *tenant.ProviderConfig) config.ProviderConfig {
	orgCfg := baseCfg
	orgCfg.APIKey = apiKey
	orgCfg.AWSAccessKeyID = ""
	orgCfg.AWSSecretAccessKey = ""
	orgCfg.AWSSessionToken = ""
	orgCfg.CredentialsFile = ""
	orgCfg.ServiceAccountJSON = ""
	if tenantCfg == nil {
		return orgCfg
	}

	orgCfg.AWSAccessKeyID = tenantCfg.AWSAccessKeyID
	orgCfg.AWSSecretAccessKey = tenantCfg.AWSSecretAccessKey
	orgCfg.AWSSessionToken = tenantCfg.AWSSessionToken
	orgCfg.ServiceAccountJSON = tenantCfg.ServiceAccountJSON
	if tenantCfg.AWSRegion != "" {
		orgCfg.AWSRegion = tenantCfg.AWSRegion
	}
	if tenantCfg.APIPathPrefix != nil {
		orgCfg.APIPathPrefix = tenantCfg.APIPathPrefix
	}
	if len(tenantCfg.Models) > 0 {
		orgCfg.Models = tenantCfg.Models
	}
	if tenantCfg.Timeout > 0 {
		orgCfg.DefaultTimeout = time.Duration(tenantCfg.Timeout) * time.Second
	}
	if tenantCfg.MaxConcurrent > 0 {
		orgCfg.MaxConcurrent = tenantCfg.MaxConcurrent
	}
	if tenantCfg.ConnPoolSize > 0 {
		orgCfg.ConnPoolSize = tenantCfg.ConnPoolSize
	}
	return orgCfg
}

// BaseURLRejection says why validateBaseURL refused an org base_url.
type BaseURLRejection int

const (
	// BaseURLInvalid: not an http(s) URL with a host.
	BaseURLInvalid BaseURLRejection = iota
	// BaseURLUnresolvable: the host does not resolve.
	BaseURLUnresolvable
	// BaseURLPrivate: a private or LAN address. The operator can allow these.
	BaseURLPrivate
	// BaseURLLoopback: the gateway's own host. Never allowed.
	BaseURLLoopback
	// BaseURLForbidden: link-local, cloud metadata, multicast or unspecified.
	// Never allowed.
	BaseURLForbidden
)

// BaseURLError reports an org base_url the gateway refuses to call.
type BaseURLError struct {
	BaseURL string
	Host    string
	IP      net.IP // the offending address; nil when rejected before resolving
	Reason  BaseURLRejection
	Err     error // the parse or lookup failure, if any
}

func (e *BaseURLError) Error() string {
	switch e.Reason {
	case BaseURLUnresolvable:
		return fmt.Sprintf("cannot resolve base_url host %q: %v", e.Host, e.Err)
	case BaseURLPrivate:
		return fmt.Sprintf("base_url host %q resolves to private address %s: %s (set %s=true to allow private/LAN provider URLs)",
			e.Host, e.IP, e.BaseURL, config.EnvAllowPrivateProviderURLs)
	case BaseURLLoopback:
		return fmt.Sprintf("base_url host %q resolves to loopback address %s, which is never allowed: %s", e.Host, e.IP, e.BaseURL)
	case BaseURLForbidden:
		if e.IP == nil {
			return fmt.Sprintf("base_url host %q is a cloud metadata endpoint, which is never allowed: %s", e.Host, e.BaseURL)
		}
		return fmt.Sprintf("base_url host %q resolves to link-local, metadata, multicast or unspecified address %s, which is never allowed: %s",
			e.Host, e.IP, e.BaseURL)
	default:
		return fmt.Sprintf("invalid base_url %q: %v", e.BaseURL, e.Err)
	}
}

func (e *BaseURLError) Unwrap() error { return e.Err }

// PublicMessage explains the refusal to an API caller. It leaves out the
// resolved address, which would map the operator's internal DNS for them.
func (e *BaseURLError) PublicMessage() string {
	switch e.Reason {
	case BaseURLUnresolvable:
		return fmt.Sprintf("its base_url host %q does not resolve from the gateway", e.Host)
	case BaseURLPrivate:
		return "its base_url points to a private network address, which this gateway refuses by default. " +
			"A self-hosted deployment can allow private/LAN provider URLs by setting " +
			config.EnvAllowPrivateProviderURLs + "=true on the gateway and the backend"
	case BaseURLLoopback:
		return "its base_url points to a loopback address, which is never allowed: from the gateway that is the gateway itself. " +
			"Use the model server's host or service name (host.docker.internal for a server on the Docker host)"
	case BaseURLForbidden:
		return "its base_url points to a link-local or cloud metadata address, which is never allowed"
	default:
		return "its base_url is not a valid http(s) URL"
	}
}

var (
	// Metadata endpoints by name, refused before any lookup.
	metadataHosts = map[string]bool{
		"metadata":                   true,
		"metadata.google.internal":   true,
		"metadata.goog":              true,
		"instance-data":              true,
		"instance-data.ec2.internal": true,
	}

	// lookupIP resolves a base_url host; a variable so tests can stub DNS.
	lookupIP = net.LookupIP

	// orgDialContext opens the connections of an org provider that names its
	// own base_url; a variable so tests can reach a test server on loopback.
	orgDialContext = func(allowPrivate bool) func(ctx context.Context, network, addr string) (net.Conn, error) {
		return netguard.DialContext(net.Dialer{}, allowPrivate)
	}

	baseURLRejections = map[netguard.Class]BaseURLRejection{
		netguard.Private:   BaseURLPrivate,
		netguard.Loopback:  BaseURLLoopback,
		netguard.Forbidden: BaseURLForbidden,
	}
)

// validateBaseURL checks an org-supplied base URL before the org's key is
// sent there, to refuse it early with the reason. Every address the host
// resolves to must pass netguard: private and LAN addresses only when
// allowPrivate is set; loopback, link-local, cloud metadata, multicast and
// unspecified addresses never. The provider's dialer checks again on every
// connection. Returns a *BaseURLError.
func validateBaseURL(baseURL string, allowPrivate bool) error {
	if baseURL == "" {
		return nil
	}
	parsed, err := url.Parse(baseURL)
	if err != nil {
		return &BaseURLError{BaseURL: baseURL, Reason: BaseURLInvalid, Err: err}
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return &BaseURLError{BaseURL: baseURL, Reason: BaseURLInvalid, Err: fmt.Errorf("scheme must be http or https")}
	}
	host := strings.TrimSuffix(strings.ToLower(parsed.Hostname()), ".")
	if host == "" {
		return &BaseURLError{BaseURL: baseURL, Reason: BaseURLInvalid, Err: fmt.Errorf("no hostname")}
	}
	if metadataHosts[host] {
		return &BaseURLError{BaseURL: baseURL, Host: host, Reason: BaseURLForbidden}
	}

	ips := []net.IP{net.ParseIP(host)}
	if ips[0] == nil {
		ips, err = lookupIP(host)
		if err == nil && len(ips) == 0 {
			err = fmt.Errorf("no addresses")
		}
		if err != nil {
			return &BaseURLError{BaseURL: baseURL, Host: host, Reason: BaseURLUnresolvable, Err: err}
		}
	}
	for _, ip := range ips {
		if class := netguard.Classify(ip); !class.Allowed(allowPrivate) {
			return &BaseURLError{BaseURL: baseURL, Host: host, IP: ip, Reason: baseURLRejections[class]}
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
