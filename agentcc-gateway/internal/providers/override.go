package providers

import (
	"context"
	"fmt"
	"log/slog"
	"net"
	"net/url"
	"reflect"
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

	// queueTimeout is provider_defaults.queue_timeout, the queue wait of an
	// instance whose config leaves its own unset.
	queueTimeout time.Duration
}

// NewOrgProviderCache creates a cache pre-loaded with base provider configs.
// allowPrivateBaseURLs lets org base URLs point at private and LAN addresses.
func NewOrgProviderCache(baseCfgs map[string]config.ProviderConfig, allowPrivateBaseURLs bool) *OrgProviderCache {
	return &OrgProviderCache{
		providers:            make(map[string]Provider),
		baseCfgs:             baseCfgs,
		allowPrivateBaseURLs: allowPrivateBaseURLs && !config.IsCloudDeployment(),
	}
}

// SetDefaultQueueTimeout sets the queue wait (provider_defaults.queue_timeout)
// of the instances built from now on whose config leaves queue_timeout unset.
func (c *OrgProviderCache) SetDefaultQueueTimeout(d time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.queueTimeout = d
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
		}
		ok = true
	}
	if !ok {
		return nil, fmt.Errorf("no base config for provider %q", providerID)
	}

	// Refuse an unparseable inherited URL without publishing its userinfo in
	// a parser error. Never let net/http recover node authentication from it.
	if _, err := url.Parse(baseCfg.BaseURL); err != nil {
		return nil, &ConfigurationError{ProviderID: providerID, Problem: "base_url must be a valid URL"}
	}
	// Adapter settings use lowercase map lookups. Reject case-ambiguous
	// settings before normalization rather than picking a value by map order.
	seenHeaders := make(map[string]bool)
	for name := range baseCfg.Headers {
		if kept, ok := orgInheritedHeaderName(baseCfg.APIFormat, name); ok {
			key := strings.ToLower(kept)
			if seenHeaders[key] {
				return nil, &ConfigurationError{ProviderID: providerID, Problem: "inherited provider settings contain duplicate names"}
			}
			seenHeaders[key] = true
		}
	}
	orgCfg := resolveOrgConfig(baseCfg, apiKey, tenantCfg)
	if orgCfg.BaseURL == "" {
		return nil, &ConfigurationError{
			ProviderID: providerID,
			Problem:    "base_url is required because no upstream endpoint is configured for this provider",
		}
	}
	// After the org's own values, which win, as the config.yaml entry's do.
	applyConcurrencyDefaults(&orgCfg, c.queueTimeout)
	if tenantCfg != nil && tenantCfg.BaseURL != "" {
		orgCfg.DialContext = orgDialContext(c.allowPrivateBaseURLs)
	}

	p, err := createProvider(orgInstanceID(providerID, orgID), orgCfg)
	if err != nil {
		return nil, fmt.Errorf("creating org provider %s/%s: %w", orgID, providerID, err)
	}

	// Construction is the only authority for the BYOK publication exception.
	// Record it against this exact instance before the cache publishes it. The
	// instance keeps its concrete adapter type and full method set; a factory
	// result that cannot carry identity-keyed provenance fails closed.
	if !markOrgInstance(p, orgID) {
		_ = p.Close()
		return nil, &ConfigurationError{ProviderID: providerID, Problem: "provider does not support org credential provenance"}
	}
	c.providers[key] = p
	slog.Info("created per-org provider",
		"org_id", orgID,
		"provider", providerID,
		"base_url", resolveOrgConfig(orgCfg, "", nil).BaseURL,
		"api_format", orgCfg.APIFormat,
	)

	return p, nil
}

// orgInstanceID is the ID of the instance GetOrCreateWithTenantConfig builds
// from an org's own config for providerID. The gateway's own instances (the
// registry's) carry the provider ID alone.
func orgInstanceID(providerID, orgID string) string {
	return providerID + "_org_" + orgID
}

// orgCredentialProvenance is private construction evidence for the BYOK
// publication exception: instance identity -> the org whose credentials that
// instance sends. Only GetOrCreateWithTenantConfig writes it (before the cache
// publishes the instance) and Evict/EvictAll remove it. It is keyed by the
// instance itself, never by its ID string, so a registry adapter or a
// lookalike "<id>_org_<orgID>" provider can never hold an entry. The adapters
// are separate packages, so a field on the adapter is not available here;
// adapters keep their concrete types and method sets.
var orgCredentialProvenance = struct {
	sync.RWMutex
	owner map[Provider]string
}{owner: make(map[Provider]string)}

// identityKeyed is true only for pointer-typed instances. Their identity is a
// stable, hashable key; any other dynamic type is never trusted.
func identityKeyed(p Provider) bool {
	return p != nil && reflect.TypeOf(p).Kind() == reflect.Pointer && !reflect.ValueOf(p).IsNil()
}

func markOrgInstance(p Provider, orgID string) bool {
	if orgID == "" || !identityKeyed(p) {
		return false
	}
	orgCredentialProvenance.Lock()
	orgCredentialProvenance.owner[p] = orgID
	orgCredentialProvenance.Unlock()
	return true
}

// unmarkOrgInstance drops the record when the cache closes and removes the
// instance. A request still holding it is then treated as platform-owned,
// which is the fail-closed direction.
func unmarkOrgInstance(p Provider) {
	if !identityKeyed(p) {
		return
	}
	orgCredentialProvenance.Lock()
	delete(orgCredentialProvenance.owner, p)
	orgCredentialProvenance.Unlock()
}

// IsOrgInstance reports whether p is an instance built from orgID's own
// provider config, and so calls its upstream with the org's credentials
// rather than the gateway's. The answer is construction provenance, never an
// ID naming convention.
func IsOrgInstance(p Provider, orgID string) bool {
	if orgID == "" || !identityKeyed(p) {
		return false
	}
	orgCredentialProvenance.RLock()
	owner, ok := orgCredentialProvenance.owner[p]
	orgCredentialProvenance.RUnlock()
	return ok && owner == orgID
}

// orgInheritedHeaderName is an exact-name, case-insensitive allowlist. The
// common entries are reviewed non-secret wire defaults; the adapter-specific
// entries are settings, normalized for the adapters' lowercase map lookups.
// All credential-capable headers (and unknown names) are deliberately absent.
func orgInheritedHeaderName(apiFormat, name string) (string, bool) {
	key := strings.ToLower(name)
	switch key {
	case "anthropic-version", "anthropic-beta", "user-agent":
		return name, true // Preserve wire-header spelling and deployed defaults.
	}
	switch apiFormat {
	case "azure":
		return key, key == "auth-type" || key == "api-version"
	case "gemini", "google":
		return key, key == "x-gcp-project" || key == "x-gcp-location"
	case "bedrock":
		return key, key == "x-aws-region"
	case "cohere":
		return key, key == "x-cohere-input-type"
	default:
		return "", false
	}
}

// resolveOrgConfig clones a base provider config for one org. The endpoint
// (without inherited userinfo), api_format, reviewed headers and TLS stay;
// everything the org states about itself wins, including an empty path prefix.
// None of the operator's credentials carry over: the org authenticates with
// its own or not at all.
func resolveOrgConfig(baseCfg config.ProviderConfig, apiKey string, tenantCfg *tenant.ProviderConfig) config.ProviderConfig {
	orgCfg := baseCfg
	if tenantCfg == nil || tenantCfg.BaseURL == "" {
		// BaseURL is copied, not mutated in the platform config. Without this,
		// net/http can synthesize node Basic auth alongside a tenant API key.
		if endpoint, err := url.Parse(orgCfg.BaseURL); err == nil && endpoint.User != nil {
			endpoint.User = nil
			orgCfg.BaseURL = endpoint.String()
		}
	}
	// An inherited header must never replace the tenant credential. This is
	// an allowlist, not a growing denylist of auth header spellings. Keep a
	// separate map so an org instance cannot mutate the operator's defaults.
	orgCfg.Headers = nil
	for name, value := range baseCfg.Headers {
		if kept, ok := orgInheritedHeaderName(baseCfg.APIFormat, name); ok {
			if orgCfg.Headers == nil {
				orgCfg.Headers = make(map[string]string)
			}
			orgCfg.Headers[kept] = value
		}
	}
	// tenant.ProviderConfig has no Headers field on this deployed tree.
	// Do not add a new tenant auth-header surface as part of this backport.
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
	if metadataHosts[host] || (config.IsCloudDeployment() &&
		(host == "host.docker.internal" || host == "localhost" || strings.HasSuffix(host, ".localhost"))) {
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
			// Revoke and unpublish together under c.mu, before Close can
			// block or let a still-running request observe a closed instance.
			unmarkOrgInstance(p)
			delete(c.providers, key)
			p.Close()
		}
	}
}

// EvictAll removes all cached providers (called on config reload).
func (c *OrgProviderCache) EvictAll() {
	c.mu.Lock()
	defer c.mu.Unlock()

	for key, p := range c.providers {
		// Same lock and revoke-before-close order as Evict.
		unmarkOrgInstance(p)
		delete(c.providers, key)
		p.Close()
	}
}

// Count returns the number of cached provider instances.
func (c *OrgProviderCache) Count() int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.providers)
}
