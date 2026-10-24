package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/async"
	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/files"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/privacy"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/responses"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/video"
)

// maxSessionIDLen is the maximum allowed byte length for a session ID.
// Matches the VARCHAR(255) column in the backing store.
const maxSessionIDLen = 255

// Handlers holds dependencies for HTTP handlers.
// Routing fields use atomic.Pointer for hot-reload support.
type Handlers struct {
	registry       *providers.Registry
	engine         *pipeline.Engine
	healthMonitor  *routing.HealthMonitor
	maxBodySize    int64
	defaultTimeout time.Duration

	// maxTimeout is the ceiling resolveTimeout clamps to, the same one
	// middleware.Timeout puts on the request context. Zero means no clamp,
	// for a Handlers built without it.
	maxTimeout time.Duration

	// captureStreamContent reassembles streamed completions so post-plugins
	// see the text. Set when a sink is configured to record bodies.
	captureStreamContent bool

	// Streaming guardrail support.
	guardrailEngine    *guardrails.Engine
	policyStore        *policy.Store
	streamGuardrailCfg config.StreamingGuardrailConfig

	// Model metadata database (shared atomic pointer for hot-reload).
	modelDB *atomic.Pointer[modeldb.ModelDB]

	// Per-org multi-tenant support.
	tenantStore      *tenant.Store
	orgProviderCache *providers.OrgProviderCache

	// Key store for early org_id peek (before auth plugin runs).
	keyStore *authpkg.KeyStore

	// Managed models: the managed providers' catalogs and the markup billed
	// on what they serve (see managed.go).
	catalog               *catalog.Store
	managedMarkupPercent  float64
	orgLedger             *creditsplugin.OrgLedger
	requireManagedCredits bool
	modelOverrides        atomic.Pointer[map[string]modeldb.ModelOverride]
	// reserveDefaultOutputTokens is managed.reserve_default_output_tokens:
	// the output a hold assumes for a request with no max output, below the
	// model's own maximum (heldResponsesUsage, as the credits plugin's hold).
	reserveDefaultOutputTokens int

	// The model shutdown calendar (catalog/deprecations.json) and each
	// configured provider's calendar host (modeldb.DeprecationHost of its
	// api_format and base URL, or a managed provider's type), set at wiring.
	// Eligibility rule 4 and the image handler refuse a model past its
	// shutdown date with 410 model_expired.
	deprecations  *modeldb.Deprecations
	providerHosts map[string]string
	// thoughtSignatureModels is catalog.thought_signature_models as the
	// operator set it, passed into every eligibility input; nil (unset)
	// leaves the engine's own rule (eligibility.ThoughtSignatureFromGeneration).
	thoughtSignatureModels []string

	// Async inference support.
	asyncStore  *async.Store
	asyncWorker *async.Worker

	// Responses API store.
	responsesStore *responses.Store

	// Files API store.
	fileStore *files.Store

	// Video generation store.
	videoStore video.Store

	// Advanced routing components.
	complexityAnalyzer   *routing.ComplexityAnalyzer
	raceExecutor         *routing.RaceExecutor
	providerLockResolver *routing.ProviderLockResolver
	accessGroupChecker   *routing.AccessGroupChecker
	routingStrategy      string // "fastest", "adaptive", etc.

	// Scheduled completions.
	scheduledStore         scheduled.Store
	scheduledRetryAttempts int
	scheduledMaxAhead      time.Duration

	// Atomic pointers for hot-reloadable routing components.
	failover          atomic.Pointer[routing.Failover]
	modelFallbacks    atomic.Pointer[routing.ModelFallbacks]
	conditionalRouter atomic.Pointer[routing.ConditionalRouter]
	modelTimeouts     atomic.Pointer[map[string]time.Duration]
	mirror            atomic.Pointer[routing.Mirror]

	// Request-path seams: injected dependencies, billing hooks,
	// the generation recorder and the hook observer, each in its own file.
	injectedDepsHolder
	billingHolder
	generationHolder
	hookTrace
}

// orgModelMatches checks if an org provider's registered model matches the
// requested model. It first tries exact match, then prefix-stripping:
// "anthropic/claude-haiku-4-5" matches provider "anthropic" with model "claude-haiku-4-5".
func orgModelMatches(registeredModel, requestedModel, providerID string) bool {
	if registeredModel == requestedModel {
		return true
	}
	if idx := strings.IndexByte(requestedModel, '/'); idx > 0 {
		prefix := requestedModel[:idx]
		bareModel := requestedModel[idx+1:]
		if strings.EqualFold(prefix, providerID) && registeredModel == bareModel {
			return true
		}
	}
	return false
}

func setAuthMetadataFromRequest(rc *models.RequestContext, r *http.Request) {
	if authHeader := r.Header.Get("Authorization"); authHeader != "" {
		rc.Metadata["authorization"] = authHeader
		return
	}
	if apiKey := r.Header.Get("x-api-key"); apiKey != "" {
		rc.Metadata["authorization"] = "Bearer " + apiKey
	}
}

// readGuardrailPolicyHeader puts the request's X-Guardrail-Policy on rc,
// where the guardrail plugin applies it under the key's
// allow_policy_override (refused before the provider without it), on every
// route a check on the request reads (the read list of
// api_contracts/gateway/guardrail-request-routes.json).
func readGuardrailPolicyHeader(rc *models.RequestContext, r *http.Request) {
	if v := r.Header.Get("X-Guardrail-Policy"); v != "" {
		rc.Metadata["x-guardrail-policy"] = v
	}
}

func cloneRequestHeaders(r *http.Request) http.Header {
	headers := r.Header.Clone()
	if headers.Get("Authorization") == "" {
		if apiKey := headers.Get("x-api-key"); apiKey != "" {
			headers.Set("Authorization", "Bearer "+apiKey)
		}
	}
	return headers
}

func shouldApplyOrgProviderOverride(rc *models.RequestContext) bool {
	if rc == nil {
		return false
	}
	if rc.Metadata["key_type"] == "internal" {
		return false
	}
	if rc.Metadata["org_provider"] == "true" {
		return false
	}
	if rc.Metadata["org_provider_model_match"] != "" {
		return false
	}
	return true
}

func (h *Handlers) resolveProviderWithOrgFallback(ctx context.Context, rc *models.RequestContext, orgID string, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string, error) {
	provider, upstreamModel, err := h.resolveProviderOrgFirst(ctx, rc, orgID, orgCfg, model)
	if err != nil {
		return nil, "", err
	}

	if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}
	return provider, upstreamModel, nil
}

// resolveProviderOrgFirst resolves model to the org's own provider for it when
// there is one (orgRouteFor), and otherwise through resolveProvider. A chat
// request mapped to an alias of a one-model provider carries that model on
// rc.Request. When the org's providers for the model all fail to build and
// the key may use only those, it reports that failure without trying them
// again.
//
// The org's provider is sent the id the org registered for it, the one its
// host takes ("gpt-4o-mini" for "openai/gpt-4o-mini"), in the form its
// adapter needs (orgModelFor); chat gets it on rc.Request too, and rc.Model
// keeps the model the caller asked for. The registered id is recorded on rc
// when it differs (metaOrgUpstreamModel).
func (h *Handlers) resolveProviderOrgFirst(ctx context.Context, rc *models.RequestContext, orgID string, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string, error) {
	if orgID != "" {
		p, providerID, upstream, host, err := h.orgRouteFor(orgID, orgCfg, model)
		if p != nil {
			rc.Provider = providerID
			rc.Metadata["org_provider_model_match"] = model
			rc.Metadata["billing_mode"] = billingBYOK
			noteOrgUpstreamModel(rc, model, host)
			if rc.Request != nil {
				rc.Request.Model = upstream
			}
			return p, upstream, nil
		}
		if err != nil && h.orgProvidersOnly(ctx, rc) {
			return nil, "", orgProviderError(model, providerID, err)
		}
	}
	return h.resolveProviderFor(ctx, rc, orgCfg, model)
}

// SetCaptureStreamContent enables reassembly of streamed completions. Off by
// default: without a sink that records bodies, the assembly is wasted work.
func (h *Handlers) SetCaptureStreamContent(v bool) { h.captureStreamContent = v }

// NewHandlers creates a Handlers instance.
func NewHandlers(registry *providers.Registry, engine *pipeline.Engine, maxBodySize int64, defaultTimeout, maxTimeout time.Duration, failover *routing.Failover, modelFallbacks *routing.ModelFallbacks, conditionalRouter *routing.ConditionalRouter, healthMonitor *routing.HealthMonitor, modelTimeouts map[string]time.Duration, mirror *routing.Mirror, guardrailEngine *guardrails.Engine, policyStore *policy.Store, streamGuardrailCfg config.StreamingGuardrailConfig, mdbPtr *atomic.Pointer[modeldb.ModelDB], tenantStore *tenant.Store, orgProviderCache *providers.OrgProviderCache, keyStore *authpkg.KeyStore) *Handlers {
	h := &Handlers{
		registry:           registry,
		engine:             engine,
		healthMonitor:      healthMonitor,
		maxBodySize:        maxBodySize,
		defaultTimeout:     defaultTimeout,
		maxTimeout:         maxTimeout,
		guardrailEngine:    guardrailEngine,
		policyStore:        policyStore,
		streamGuardrailCfg: streamGuardrailCfg,
		modelDB:            mdbPtr,
		tenantStore:        tenantStore,
		orgProviderCache:   orgProviderCache,
		keyStore:           keyStore,
	}
	h.failover.Store(failover)
	h.modelFallbacks.Store(modelFallbacks)
	h.conditionalRouter.Store(conditionalRouter)
	if len(modelTimeouts) > 0 {
		h.modelTimeouts.Store(&modelTimeouts)
	}
	h.mirror.Store(mirror)
	h.deps.Store(h.defaultInjectedDeps())
	return h
}

// ReloadRouting rebuilds all routing components from new config and atomically swaps them.
func (h *Handlers) ReloadRouting(cfg *config.Config, registry *providers.Registry) error {
	// Rebuild model database and update shared pointer.
	if h.modelDB != nil {
		overrides := ConvertModelOverrides(cfg.ModelDatabase.Overrides)
		h.modelOverrides.Store(&overrides)
		h.modelDB.Store(BuildModelDB(overrides, h.catalog))
	}

	// Rebuild router in registry.
	if err := registry.ReloadRouter(cfg.Routing); err != nil {
		return fmt.Errorf("reload router: %w", err)
	}

	// Rebuild conditional router.
	if len(cfg.Routing.ConditionalRoutes) > 0 {
		cr, err := routing.NewConditionalRouter(cfg.Routing.ConditionalRoutes)
		if err != nil {
			return fmt.Errorf("reload conditional routes: %w", err)
		}
		h.conditionalRouter.Store(cr)
	} else {
		h.conditionalRouter.Store(nil)
	}

	// Rebuild model fallbacks.
	if len(cfg.Routing.ModelFallbacks) > 0 {
		mf := routing.NewModelFallbacks(cfg.Routing.ModelFallbacks)
		h.modelFallbacks.Store(mf)
	} else {
		h.modelFallbacks.Store(nil)
	}

	// Rebuild failover chain (retry → circuit breaker → failover).
	var retryer *routing.Retryer
	if cfg.Routing.Retry.Enabled {
		retryer = routing.NewRetryer(cfg.Routing.Retry)
	}

	var cbReg *routing.CircuitBreakerRegistry
	if cfg.Routing.CircuitBreaker.Enabled && registry.Router() != nil {
		cbReg = routing.NewCircuitBreakerRegistry(cfg.Routing.CircuitBreaker, registry.Router().SetHealthy)
	}

	if registry.Router() != nil && cfg.Routing.Failover.Enabled {
		fo := routing.NewFailover(cfg.Routing.Failover, registry.Router(), retryer, cbReg)
		h.failover.Store(fo)
	} else {
		h.failover.Store(nil)
	}

	// Rebuild model timeouts.
	if len(cfg.Routing.ModelTimeouts) > 0 {
		mt := cfg.Routing.ModelTimeouts
		h.modelTimeouts.Store(&mt)
	} else {
		h.modelTimeouts.Store(nil)
	}

	// Rebuild mirror.
	if cfg.Routing.Mirror.Enabled && len(cfg.Routing.Mirror.Rules) > 0 {
		mirrorLookup := func(providerID string) (routing.MirrorProvider, bool) {
			return registry.GetProvider(providerID)
		}
		m := routing.NewMirror(cfg.Routing.Mirror, mirrorLookup)
		h.mirror.Store(m)
	} else {
		h.mirror.Store(nil)
	}

	return nil
}

// resolveOrgConfig extracts the org config from the tenant store based on
// the API key's org_id metadata (set by the auth plugin).
func (h *Handlers) resolveOrgConfig(rc *models.RequestContext) (string, *tenant.OrgConfig) {
	if h.tenantStore == nil {
		return "", nil
	}
	return h.tenantStore.ResolveOrgConfig(rc.Metadata)
}

// lookupRequestKey returns the API key the request presented, or nil. It does
// not authenticate and writes nothing.
func (h *Handlers) lookupRequestKey(rc *models.RequestContext) *authpkg.APIKey {
	if h.keyStore == nil {
		return nil
	}
	authHeader := rc.Metadata["authorization"]
	if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
		return nil
	}
	rawKey := strings.TrimPrefix(authHeader, "Bearer ")
	if rawKey == "" {
		return nil
	}
	return h.keyStore.Lookup(rawKey)
}

// callerOrgID is the org of the API key that KeyAuth authenticated for this
// request, as KeyAuth recorded it: the owner of the jobs, files and stored
// responses that key creates and the only org whose objects it may read. It is
// "" for keys without an org, for license-authorized requests, and when auth
// is off (single tenant).
//
// When auth is on and KeyAuth authenticated nothing for this request, it
// writes 401 and returns false: such a request must not fall into the no-org
// tenant. It never looks the key up again, so a key revoked, expired or
// re-synced after KeyAuth cannot change the answer.
func (h *Handlers) callerOrgID(w http.ResponseWriter, r *http.Request) (string, bool) {
	id, ok := h.callerIdentity(r)
	if !ok {
		models.WriteError(w, models.ErrUnauthorized("Invalid or missing API key"))
		return "", false
	}
	return id.OrgID, true
}

// callerIdentity is the identity of the API key KeyAuth authenticated for this
// request, under callerOrgID's rule: the zero identity (no key, no org) when
// auth is off or a license authorized the request, and ok false when auth is
// on and KeyAuth authenticated nothing. It never looks the key up again.
func (h *Handlers) callerIdentity(r *http.Request) (authpkg.Identity, bool) {
	if h.keyStore == nil {
		return authpkg.Identity{}, true
	}
	if id, ok := middleware.KeyIdentity(r.Context()); ok {
		return id, true
	}
	if middleware.IsLicenseAuthorized(r.Context()) {
		return authpkg.Identity{}, true
	}
	return authpkg.Identity{}, false
}

// peekKeyOrgID does a lightweight key lookup to extract org_id from the API key
// metadata BEFORE the auth plugin runs. This is needed because resolveOrgConfig
// depends on rc.Metadata["key_org_id"] which the auth plugin sets — but auth runs
// inside engine.Process, after org config resolution.
//
// The auth plugin still performs full validation (status, expiry, IP, model access).
// This peek is read-only and does NOT authenticate. Besides the org_id it
// copies the key's provider and model ACL (recordKeyACL), so the provider-ACL
// exits and eligibility apply them before the provider is called.
func (h *Handlers) peekKeyOrgID(rc *models.RequestContext) {
	found := h.lookupRequestKey(rc)
	if found == nil {
		return
	}
	// Read under the store lock: an admin key update may run alongside.
	apiKey := h.keyStore.Peek(found)
	recordKeyACL(rc, apiKey)
	if orgID, ok := apiKey.Metadata["org_id"]; ok && orgID != "" {
		rc.Metadata["key_org_id"] = orgID
	}
	// Also propagate key_type early so provider resolution can distinguish
	// internal service keys from user keys before the auth plugin runs.
	if apiKey.KeyType != "" {
		rc.Metadata["key_type"] = apiKey.KeyType
	}
	// Propagate access_groups early so alias resolution works
	// for static config keys before the auth plugin copies full metadata.
	if ag, ok := apiKey.Metadata["access_groups"]; ok && ag != "" {
		rc.Metadata["key_access_groups"] = ag
	}
	// The key's content mode, for step 5. Copied whenever the key is
	// found, empty included, so resolveContentMode never looks the key up
	// again. Step 5 and the logging plugin keep an empty copy out of the log.
	rc.Metadata[privacy.KeyLogContentRequestMetadata] = apiKey.Metadata[privacy.KeyLogContentMetadata]
}

// applyOrgProviderOverride checks if the org has a custom API key for the
// resolved provider and returns a provider instance with the org's key.
// If no override exists, returns the original provider unchanged.
func (h *Handlers) applyOrgProviderOverride(orgID string, orgCfg *tenant.OrgConfig, providerID string, provider providers.Provider) providers.Provider {
	if orgCfg == nil || h.orgProviderCache == nil || orgID == "" {
		return provider
	}

	// Check if the org has a custom config for this provider.
	provCfg, ok := orgCfg.Providers[providerID]
	if !ok || provCfg == nil || !provCfg.HasCredentials() {
		return provider
	}

	// Check if the provider is enabled for this org.
	if !provCfg.Enabled {
		return provider
	}

	// Get or create a cached provider instance with the org's API key.
	orgProvider, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, provCfg.APIKey, provCfg)
	if err != nil {
		slog.Warn("failed to create org provider override, using base provider",
			"org_id", orgID,
			"provider", providerID,
			"error", err,
		)
		return provider
	}

	slog.Debug("using per-org provider API key",
		"org_id", orgID,
		"provider", providerID,
	)
	return orgProvider
}

// resolveOrgProvider finds the org's own provider for model (orgRouteFor)
// for a handler that does not apply the org's model_map: a model_map alias
// whose provider would be sent another model name is not taken here, so it
// resolves as it would with no model_map. It returns the provider, its ID and
// the model to send it (the id the org registered, in the form its adapter
// needs: orgModelFor), which the caller puts on its request, and records the
// id the org registered on rc when it differs from model
// (metaOrgUpstreamModel).
func (h *Handlers) resolveOrgProvider(rc *models.RequestContext, orgID string, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string, string) {
	p, providerID, upstream, host, _ := h.orgRouteFor(orgID, orgCfg, model)
	if p == nil {
		return nil, "", ""
	}
	if host == "" && upstream != model {
		return nil, "", ""
	}
	noteOrgUpstreamModel(rc, model, host)
	return p, providerID, upstream
}

// orgProviderFor returns the org's own provider for model (orgRouteFor's
// rule), its ID, and the id it lists that model matched (orgModelMatches):
// the id its host takes, model itself or model without its "<provider>/"
// prefix; "" for a model_map route to a provider that does not list model.
// When it cannot be built, it returns the failure and that provider's ID
// instead.
func (h *Handlers) orgProviderFor(orgID string, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string, string, error) {
	p, providerID, _, host, err := h.orgRouteFor(orgID, orgCfg, model)
	return p, providerID, host, err
}

// orgRouteFor returns the org's own provider for model, its ID, the model
// name to send it and the id the org registered that model matched:
//  1. When the org's model_map maps model to one of its own enabled
//     providers with credentials, that provider: model is sent as named when
//     the provider lists it or lists several models, and as the provider's
//     only model when it lists exactly one and not this name (an alias). A
//     map entry naming any other provider (one of the gateway's) is not the
//     org's and is skipped here.
//  2. Else the org's enabled providers with credentials whose model list
//     matches model, in sorted provider-id order: the first that builds.
//
// A provider that lists model is sent the id it registered for it
// (orgProviderListing), in the form its adapter needs (orgModelFor), and
// that id is the fourth result; it is "" when the provider does not list
// model (rule 1's alias and several-models cases).
//
// When the provider it chose (or every one that lists model) fails to build,
// it returns the first failure and that provider's ID instead.
func (h *Handlers) orgRouteFor(orgID string, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string, string, string, error) {
	if orgCfg == nil || h.orgProviderCache == nil {
		return nil, "", model, "", nil
	}
	if providerID, upstream, ok := orgMappedRoute(orgCfg, model); ok {
		pcfg := orgCfg.Providers[providerID]
		p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, pcfg.APIKey, pcfg)
		if err != nil {
			slog.Warn("failed to create org provider for mapped model",
				"org_id", orgID, "provider", providerID, "model", model, "error", err)
			return nil, providerID, model, "", err
		}
		if host, ok := orgProviderListing(pcfg, providerID, model); ok {
			return p, providerID, orgModelFor(p, model, host), host, nil
		}
		return p, providerID, upstream, "", nil
	}
	var failedID string
	var firstErr error
	for after, started := "", false; ; started = true {
		providerID, pcfg := nextOrgProviderListing(orgCfg, model, after, started)
		if pcfg == nil {
			return nil, failedID, model, "", firstErr
		}
		after = providerID
		p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, pcfg.APIKey, pcfg)
		if err != nil {
			slog.Warn("failed to create org provider for model",
				"org_id", orgID, "provider", providerID, "model", model, "error", err)
			if firstErr == nil {
				failedID, firstErr = providerID, err
			}
			continue
		}
		host, _ := orgProviderListing(pcfg, providerID, model)
		return p, providerID, orgModelFor(p, model, host), host, nil
	}
}

// orgProviderUsable reports whether an org provider can serve a customer
// key: enabled and with credentials.
func orgProviderUsable(pcfg *tenant.ProviderConfig) bool {
	return pcfg != nil && pcfg.Enabled && pcfg.HasCredentials()
}

// orgProviderListing returns the id the org provider providerID lists that
// model matches (orgModelMatches: model itself or model without its
// "<provider>/" prefix), and whether it lists model.
func orgProviderListing(pcfg *tenant.ProviderConfig, providerID, model string) (string, bool) {
	for _, m := range pcfg.Models {
		if orgModelMatches(m, model, providerID) {
			return m, true
		}
	}
	return "", false
}

// orgProviderLists reports whether the org provider providerID lists model.
func orgProviderLists(pcfg *tenant.ProviderConfig, providerID, model string) bool {
	_, ok := orgProviderListing(pcfg, providerID, model)
	return ok
}

// orgMappedRoute is model_map's entry for model when it names one of the
// org's own usable providers: that provider's ID and the model to send it
// (orgRouteFor rule 1).
func orgMappedRoute(orgCfg *tenant.OrgConfig, model string) (string, string, bool) {
	providerID, ok := orgCfg.ModelMap[model]
	if !ok {
		return "", "", false
	}
	pcfg := orgCfg.Providers[providerID]
	if !orgProviderUsable(pcfg) {
		return "", "", false
	}
	if len(pcfg.Models) == 1 && !orgProviderLists(pcfg, providerID, model) {
		return providerID, pcfg.Models[0], true
	}
	return providerID, model, true
}

// nextOrgProviderListing returns the usable org provider with the smallest
// ID that lists model and sorts after after (any ID when started is false),
// and nil when there is none: sorted provider-id order without allocating.
func nextOrgProviderListing(orgCfg *tenant.OrgConfig, model, after string, started bool) (string, *tenant.ProviderConfig) {
	var bestID string
	var best *tenant.ProviderConfig
	for providerID, pcfg := range orgCfg.Providers {
		if (started && providerID <= after) || (best != nil && providerID >= bestID) {
			continue
		}
		if orgProviderUsable(pcfg) && orgProviderLists(pcfg, providerID, model) {
			bestID, best = providerID, pcfg
		}
	}
	return bestID, best
}

// applyOrgCacheOverrides applies per-org cache config to the request context.
// Cache plugin reads these metadata keys to honor per-org cache settings.
func (h *Handlers) applyOrgCacheOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	// Cache is opt-in: if the org has no cache config or hasn't explicitly enabled it, disable.
	if orgCfg == nil || orgCfg.Cache == nil || !orgCfg.Cache.Enabled {
		rc.Metadata["org_cache_disabled"] = "true"
		return
	}

	cache := orgCfg.Cache

	// Per-org default TTL override (in seconds).
	if cache.DefaultTTL > 0 {
		rc.Metadata["org_cache_ttl"] = fmt.Sprintf("%ds", cache.DefaultTTL)
	}

	// Per-org cache backend info for observability.
	if cache.Backend != "" {
		rc.Metadata["org_cache_backend"] = cache.Backend
	}
}

// applyOrgRateLimitOverrides applies per-org rate limit config to the request context.
// Rate limiter plugin reads these metadata keys to apply per-org limits.
func (h *Handlers) applyOrgRateLimitOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.RateLimiting == nil {
		return
	}

	rl := orgCfg.RateLimiting
	if !rl.Enabled {
		return
	}

	if rl.GlobalRPM > 0 {
		rc.Metadata["org_rate_limit_rpm"] = fmt.Sprintf("%d", rl.GlobalRPM)
	}
	if rl.GlobalTPM > 0 {
		rc.Metadata["org_rate_limit_tpm"] = fmt.Sprintf("%d", rl.GlobalTPM)
	}
	if rl.PerKeyRPM > 0 {
		rc.Metadata["org_rate_limit_per_key_rpm"] = fmt.Sprintf("%d", rl.PerKeyRPM)
	}
	if rl.PerUserRPM > 0 {
		rc.Metadata["org_rate_limit_per_user_rpm"] = fmt.Sprintf("%d", rl.PerUserRPM)
	}
	// Org rate-limit fields (the rate-limit plugin reads these; only the handler stamps them).
	if rl.PerKeyTPM > 0 {
		rc.Metadata["org_rate_limit_per_key_tpm"] = strconv.Itoa(rl.PerKeyTPM)
	}
	if rl.PerModelRPM > 0 {
		rc.Metadata["org_rate_limit_per_model_rpm"] = perModelRPMStamp(rc.Model, rl.PerModelRPM)
	}
}

// perModelRPMStamp is org_rate_limit_per_model_rpm: a compact JSON object of
// model -> requests per minute, keys sorted. The org config's per_model_rpm
// is one limit that applies to each model, so the object holds the
// request's model (rc.Model when the overrides are applied).
func perModelRPMStamp(model string, rpm int) string {
	name, err := json.Marshal(model)
	if err != nil { // a string always marshals
		name = []byte(`""`)
	}
	return "{" + string(name) + ":" + strconv.Itoa(rpm) + "}"
}

// applyOrgBudgetOverrides applies per-org budget config to the request context.
func (h *Handlers) applyOrgBudgetOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.Budgets == nil || !orgCfg.Budgets.Enabled {
		return
	}
	b := orgCfg.Budgets
	rc.Metadata["org_budget_enabled"] = "true"
	if b.OrgLimit > 0 {
		rc.Metadata["org_budget_limit"] = fmt.Sprintf("%.2f", b.OrgLimit)
	}
	if b.OrgPeriod != "" {
		rc.Metadata["org_budget_period"] = b.OrgPeriod
	} else if b.DefaultPeriod != "" {
		rc.Metadata["org_budget_period"] = b.DefaultPeriod
	}
	if b.WarnThreshold > 0 {
		rc.Metadata["org_budget_warn_threshold"] = fmt.Sprintf("%.2f", b.WarnThreshold)
	}
	if b.HardLimit {
		rc.Metadata["org_budget_hard"] = "true"
	}
}

// applyOrgCostTrackingOverrides applies per-org cost tracking config to the request context.
func (h *Handlers) applyOrgCostTrackingOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.CostTracking == nil || !orgCfg.CostTracking.Enabled {
		return
	}
	rc.Metadata["org_cost_tracking_enabled"] = "true"
	if len(orgCfg.CostTracking.CustomPricing) > 0 {
		rc.Metadata["org_has_custom_pricing"] = "true"
	}
}

// applyOrgIPACLOverrides applies per-org IP ACL config to the request context.
func (h *Handlers) applyOrgIPACLOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.IPACL == nil || !orgCfg.IPACL.Enabled {
		return
	}
	rc.Metadata["org_ipacl_enabled"] = "true"
	if len(orgCfg.IPACL.Allow) > 0 {
		rc.Metadata["org_ipacl_has_allow"] = "true"
	}
	if len(orgCfg.IPACL.Deny) > 0 {
		rc.Metadata["org_ipacl_has_deny"] = "true"
	}
}

// applyOrgAlertingOverrides applies per-org alerting config to the request context.
func (h *Handlers) applyOrgAlertingOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.Alerting == nil || !orgCfg.Alerting.Enabled {
		return
	}
	rc.Metadata["org_alerting_enabled"] = "true"
	if len(orgCfg.Alerting.Rules) > 0 {
		rc.Metadata["org_alerting_rules"] = fmt.Sprintf("%d", len(orgCfg.Alerting.Rules))
	}
}

// applyOrgPrivacyOverrides applies per-org privacy/redaction config to the request context.
func (h *Handlers) applyOrgPrivacyOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.Privacy == nil || !orgCfg.Privacy.Enabled {
		return
	}
	rc.Metadata["org_privacy_enabled"] = "true"
	if orgCfg.Privacy.Mode != "" {
		rc.Metadata["org_privacy_mode"] = orgCfg.Privacy.Mode
	}
}

// applyOrgToolPolicyOverrides applies per-org tool policy config to the request context.
func (h *Handlers) applyOrgToolPolicyOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.ToolPolicy == nil || !orgCfg.ToolPolicy.Enabled {
		return
	}
	rc.Metadata["org_tool_policy_enabled"] = "true"
	if orgCfg.ToolPolicy.DefaultAction != "" {
		rc.Metadata["org_tool_policy_action"] = orgCfg.ToolPolicy.DefaultAction
	}
	if orgCfg.ToolPolicy.MaxToolsPerRequest > 0 {
		rc.Metadata["org_tool_policy_max_tools"] = fmt.Sprintf("%d", orgCfg.ToolPolicy.MaxToolsPerRequest)
	}
}

// applyOrgMCPOverrides applies per-org MCP config to the request context.
func (h *Handlers) applyOrgMCPOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.MCP == nil || !orgCfg.MCP.Enabled {
		return
	}
	rc.Metadata["org_mcp_enabled"] = "true"
	if len(orgCfg.MCP.Servers) > 0 {
		rc.Metadata["org_mcp_servers"] = fmt.Sprintf("%d", len(orgCfg.MCP.Servers))
	}
	if orgCfg.MCP.Guardrails != nil && orgCfg.MCP.Guardrails.Enabled {
		rc.Metadata["org_mcp_guardrails_enabled"] = "true"
		if len(orgCfg.MCP.Guardrails.BlockedTools) > 0 {
			rc.Metadata["org_mcp_blocked_tools"] = fmt.Sprintf("%d", len(orgCfg.MCP.Guardrails.BlockedTools))
		}
		if len(orgCfg.MCP.Guardrails.AllowedServers) > 0 {
			rc.Metadata["org_mcp_allowed_servers"] = fmt.Sprintf("%d", len(orgCfg.MCP.Guardrails.AllowedServers))
		}
		if len(orgCfg.MCP.Guardrails.ToolRateLimits) > 0 {
			rc.Metadata["org_mcp_tool_rate_limits"] = fmt.Sprintf("%d", len(orgCfg.MCP.Guardrails.ToolRateLimits))
		}
	}
}

// applyOrgAuditOverrides applies per-org audit logging config to the request context.
func (h *Handlers) applyOrgAuditOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.Audit == nil || !orgCfg.Audit.Enabled {
		return
	}
	rc.Metadata["org_audit_enabled"] = "true"
	if orgCfg.Audit.MinSeverity != "" {
		rc.Metadata["org_audit_min_severity"] = orgCfg.Audit.MinSeverity
	}
	if len(orgCfg.Audit.Categories) > 0 {
		rc.Metadata["org_audit_categories"] = fmt.Sprintf("%d", len(orgCfg.Audit.Categories))
	}
	if len(orgCfg.Audit.Sinks) > 0 {
		rc.Metadata["org_audit_sinks"] = fmt.Sprintf("%d", len(orgCfg.Audit.Sinks))
	}
}

// applyOrgA2AOverrides applies per-org A2A config to the request context.
func (h *Handlers) applyOrgA2AOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.A2A == nil || !orgCfg.A2A.Enabled {
		return
	}
	rc.Metadata["org_a2a_enabled"] = "true"
	if len(orgCfg.A2A.Agents) > 0 {
		rc.Metadata["org_a2a_agents"] = fmt.Sprintf("%d", len(orgCfg.A2A.Agents))
	}
}

// applyOrgModelDatabaseOverrides applies per-org model database overrides to the request context.
func (h *Handlers) applyOrgModelDatabaseOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.ModelDatabase == nil || len(orgCfg.ModelDatabase.Overrides) == 0 {
		return
	}
	rc.Metadata["org_model_database_enabled"] = "true"
	rc.Metadata["org_model_database_overrides"] = fmt.Sprintf("%d", len(orgCfg.ModelDatabase.Overrides))
}

// applyOrgModelMapOverrides applies per-org model map to the request context.
// The model map allows orgs to alias model names to specific providers.
func (h *Handlers) applyOrgModelMapOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil {
		return
	}

	// Start with explicit model map if present.
	if len(orgCfg.ModelMap) > 0 {
		rc.OrgModelMap = make(map[string]string, len(orgCfg.ModelMap))
		for k, v := range orgCfg.ModelMap {
			rc.OrgModelMap[k] = v
		}
	}

	// Also populate from org provider model lists so that models declared
	// under a provider are automatically routable without an explicit model_map entry.
	for providerID, pcfg := range orgCfg.Providers {
		if pcfg == nil || !pcfg.Enabled || len(pcfg.Models) == 0 {
			continue
		}
		if rc.OrgModelMap == nil {
			rc.OrgModelMap = make(map[string]string)
		}
		for _, m := range pcfg.Models {
			// Don't override explicit model_map entries.
			if _, exists := rc.OrgModelMap[m]; !exists {
				rc.OrgModelMap[m] = providerID
			}
		}
	}

	if len(rc.OrgModelMap) > 0 {
		rc.Metadata["org_model_map_enabled"] = "true"
		rc.Metadata["org_model_map_count"] = fmt.Sprintf("%d", len(rc.OrgModelMap))
	}
}

// orgModelTimeout returns the timeout the request's org set for rc.Model in
// its stored config. Handlers resolve the timeout before they apply org
// overrides, so this reads the org from the request's key instead of from
// rc, and writes nothing.
func (h *Handlers) orgModelTimeout(rc *models.RequestContext) (time.Duration, bool) {
	if h.tenantStore == nil {
		return 0, false
	}
	apiKey := h.lookupRequestKey(rc)
	if apiKey == nil {
		return 0, false
	}
	return orgModelTimeoutFor(h.tenantStore.Get(apiKey.Metadata["org_id"]), rc.Model)
}

// orgModelTimeoutFor returns the timeout orgCfg sets for model: the
// effective org config on chat (call-order step 4 may have chosen a rollout
// candidate).
func orgModelTimeoutFor(orgCfg *tenant.OrgConfig, model string) (time.Duration, bool) {
	if orgCfg == nil || orgCfg.Routing == nil {
		return 0, false
	}
	raw, ok := orgCfg.Routing.ModelTimeouts[model]
	if !ok {
		return 0, false
	}
	d, err := time.ParseDuration(raw)
	if err != nil || d <= 0 {
		slog.Warn("org model timeout is not a positive duration, ignoring it",
			"model", model,
			"value", raw,
		)
		return 0, false
	}
	return d, true
}

// appliedTimeoutMs narrows the resolved timeout to the one that really bounded
// the call. The provider holds its own client timeout, which covers the whole
// exchange including a streamed body, so the shorter of the two is what the
// caller gets. Reporting the resolved value alone advertises a limit nothing
// enforces, which is how a per-model timeout above a provider's own came to
// look like it had been applied.
//
// resolved is already formatted; it is returned unchanged when the provider
// states no timeout. Callers that retry pass the instance that served the response.
func appliedTimeoutMs(provider providers.Provider, resolved string) string {
	ms, err := strconv.ParseInt(resolved, 10, 64)
	if err != nil || ms <= 0 {
		return resolved
	}
	reporter, ok := provider.(providers.ClientTimeoutReporter)
	if !ok {
		return resolved
	}
	ceiling := reporter.ClientTimeout()
	if ceiling <= 0 || ceiling.Milliseconds() >= ms {
		return resolved
	}
	return strconv.FormatInt(ceiling.Milliseconds(), 10)
}

// orgInstanceServed reports whether the org's own provider instance served the
// request, rather than the operator's base provider.
//
// Two of the three resolve paths leave a marker. The third,
// applyOrgProviderOverride, leaves none — but it runs exactly when
// shouldApplyOrgProviderOverride allows it, so asking that same question here
// keeps one answer in one place. The case it rules out is an internal key: it
// keeps the base provider even when its org has an instance of its own.
func orgInstanceServed(rc *models.RequestContext) bool {
	return rc.Metadata["org_provider"] == "true" ||
		rc.Metadata["org_provider_model_match"] != "" ||
		shouldApplyOrgProviderOverride(rc)
}

// servingProvider resolves the instance for handlers without chat retries.
// Chat response writers retain the successful instance instead, since retries
// can switch between base and org providers with the same ID.
func (h *Handlers) servingProvider(rc *models.RequestContext) (providers.Provider, bool) {
	if rc.Provider == "" {
		return nil, false
	}
	if h.orgProviderCache != nil && orgInstanceServed(rc) {
		if orgID := rc.Metadata["org_id"]; orgID != "" {
			if p := h.orgProviderCache.Get(orgID, rc.Provider); p != nil {
				return p, true
			}
		}
	}
	if h.registry == nil {
		return nil, false
	}
	return h.registry.GetProvider(rc.Provider)
}

// requestTimeout is the timeout a request asks for under the stored config
// of its key's org, before any ceiling. Priority: request header > the org's
// model timeout for rc.Model > the node's own timeout for it
// (nodeModelTimeout: config.yaml model timeout > server default).
// resolveTimeout holds it at server.max_request_timeout; an async job runs to
// it (AsyncMiddleware).
func (h *Handlers) requestTimeout(rc *models.RequestContext, r *http.Request) time.Duration {
	if d, ok := middleware.RequestTimeoutHeader(r); ok {
		return d
	}
	if d, ok := h.orgModelTimeout(rc); ok {
		return d
	}
	return h.nodeModelTimeout(rc.Model)
}

// requestTimeoutFor is requestTimeout under orgCfg, the org config the
// request is routed by: the effective one on chat.
func (h *Handlers) requestTimeoutFor(rc *models.RequestContext, r *http.Request, orgCfg *tenant.OrgConfig) time.Duration {
	if d, ok := middleware.RequestTimeoutHeader(r); ok {
		return d
	}
	if d, ok := orgModelTimeoutFor(orgCfg, rc.Model); ok {
		return d
	}
	return h.nodeModelTimeout(rc.Model)
}

// resolveTimeout returns the effective timeout for a request under the
// stored config of its key's org: requestTimeout, never above
// server.max_request_timeout. The clamp is what keeps the answer honest: the
// request context carries that same maximum, so a larger value here would be
// a number nothing enforces.
func (h *Handlers) resolveTimeout(rc *models.RequestContext, r *http.Request) time.Duration {
	return h.clampTimeout(h.requestTimeout(rc, r))
}

// resolveTimeoutFor is resolveTimeout under orgCfg, the org config the
// request is routed by: the effective one on chat. It is held at the same
// ceiling.
func (h *Handlers) resolveTimeoutFor(rc *models.RequestContext, r *http.Request, orgCfg *tenant.OrgConfig) time.Duration {
	return h.clampTimeout(h.requestTimeoutFor(rc, r, orgCfg))
}

// clampTimeout holds d at server.max_request_timeout, the ceiling
// middleware.Timeout puts on every request context. A zero maxTimeout means
// no clamp, for a Handlers built without it.
func (h *Handlers) clampTimeout(d time.Duration) time.Duration {
	if h.maxTimeout > 0 {
		return min(d, h.maxTimeout)
	}
	return d
}

// ChatCompletion handles POST /v1/chat/completions.
func (h *Handlers) ChatCompletion(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	defer rc.RunFinalizers()

	rc.RequestID = models.GetRequestID(r.Context())
	rc.TraceID = w.Header().Get("x-agentcc-trace-id")
	rc.RequestHeaders = cloneRequestHeaders(r)
	body, err := io.ReadAll(io.LimitReader(r.Body, h.maxBodySize+1))
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("read_error", "Failed to read request body"))
		return
	}
	if int64(len(body)) > h.maxBodySize {
		models.WriteError(w, &models.APIError{
			Status:  http.StatusRequestEntityTooLarge,
			Type:    models.ErrTypeInvalidRequest,
			Code:    "request_too_large",
			Message: fmt.Sprintf("Request body exceeds maximum size of %d bytes", h.maxBodySize),
		})
		return
	}

	var req models.ChatCompletionRequest
	if err := json.Unmarshal(body, &req); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON in request body: "+err.Error()))
		return
	}

	// Validate required fields.
	if req.Model == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model is required"))
		return
	}
	if len(req.Messages) == 0 {
		models.WriteError(w, models.ErrBadRequest("missing_messages", "messages is required and must not be empty"))
		return
	}

	rc.Model = req.Model
	rc.Request = &req
	rc.IsStream = req.Stream
	rc.UserID = req.User

	// Ensure streaming requests always include usage in the final chunk
	// so post-plugins (cost, logging) have accurate token counts. Whether
	// the client asked for it is kept first: only then does the client get
	// the usage chunk; another client gets the usage on the finish chunk
	// (handleStream, finishHold).
	if req.Stream {
		rc.Flags.ClientIncludeUsage = req.StreamOptions != nil && req.StreamOptions.IncludeUsage
		if req.StreamOptions == nil {
			req.StreamOptions = &models.StreamOptions{IncludeUsage: true}
		} else {
			req.StreamOptions.IncludeUsage = true
		}
	}

	// Extract client IP for IP ACL and audit.
	rc.Metadata["client_ip"] = middleware.ClientIP(r)

	// Pass Authorization header for auth plugin.
	setAuthMetadataFromRequest(rc, r)

	// Caller dimensions, from the x-agentcc-metadata header and the body's
	// own metadata field (with security key blocklist).
	applyCallerMetadata(rc, r, req.Extra["metadata"])
	applyCallerExtras(rc, req.Extra)
	if sid := r.Header.Get("x-agentcc-session-id"); sid != "" {
		if len(sid) > maxSessionIDLen {
			models.WriteError(w, models.ErrBadRequest("session_id_too_long",
				fmt.Sprintf("x-agentcc-session-id exceeds maximum length of %d characters", maxSessionIDLen)))
			return
		}
		rc.SessionID = sid
	}
	// Also extract session_id from request body metadata (fallback).
	if rc.SessionID == "" {
		if sid := extractBodySessionID(req.Extra); sid != "" {
			if len(sid) > maxSessionIDLen {
				models.WriteError(w, models.ErrBadRequest("session_id_too_long",
					fmt.Sprintf("metadata.session_id exceeds maximum length of %d characters", maxSessionIDLen)))
				return
			}
			rc.SessionID = sid
		}
	}

	// Extract guardrail policy override header.
	readGuardrailPolicyHeader(rc, r)

	// Extract cache-related headers into metadata for cache plugin.
	if v := r.Header.Get("x-agentcc-cache-ttl"); v != "" {
		rc.Metadata["cache_ttl"] = v
	}
	if v := r.Header.Get("x-agentcc-cache-namespace"); v != "" {
		rc.Metadata["cache_namespace"] = v
	}
	if r.Header.Get("x-agentcc-cache-force-refresh") == "true" {
		rc.Metadata["cache_force_refresh"] = "true"
	}
	if v := r.Header.Get("Cache-Control"); v != "" {
		rc.Metadata["cache_control"] = v
	}

	// Request retry headers: they only tighten the org's retries and
	// reach the retry loop on the request context, for stream and non-stream.
	retryOverrides, apiErr := routing.ParseRetryOverrides(r.Header)
	if apiErr != nil {
		models.WriteError(w, apiErr)
		return
	}

	// The stored org config's timeout stands through steps 1 to 10, so
	// gateway headers rendered inside them carry its x-agentcc-timeout-ms.
	// requested is that timeout before the
	// ceiling, which an async job of this request runs to (AsyncMiddleware).
	// Both are resolved on the model the client asked for, before routing
	// (step 11) can rename it.
	requested := h.requestTimeout(rc, r)
	timeout := h.clampTimeout(requested)
	rc.Metadata["timeout_ms"] = strconv.FormatInt(timeout.Milliseconds(), 10)

	// Call-order steps 1 to 10 (hook_chain.go): runtime identity, the org,
	// the agent context, the effective org config and its overrides, content
	// mode, routing options, presets, idempotency and the run budget.
	orgCfg, ok := h.chatPreRoute(w, r, rc, &req, body)
	if !ok {
		return
	}

	// The effective config's timeout (header > its model timeout > the
	// node's) bounds everything from routing on; steps 1 to 10 ran on
	// r.Context(). Only a rollout candidate's config differs from the stored
	// one the stable timeout was read from.
	if rc.Evidence.PolicyVariant == tenant.PolicyVariantCandidate {
		requested = h.requestTimeoutFor(rc, r, orgCfg)
		if effective := h.clampTimeout(requested); effective != timeout {
			timeout = effective
			rc.Metadata["timeout_ms"] = strconv.FormatInt(timeout.Milliseconds(), 10)
		}
	}
	ctx, cancel := context.WithTimeout(routing.WithRetryOverrides(r.Context(), retryOverrides), timeout)
	defer cancel()

	// --- Advanced routing pipeline ---
	provider, _, err := h.resolveRoute(ctx, r, rc, orgCfg, "chat")
	if err != nil {
		h.failChat(w, rc, err)
		return
	}
	if apiErr := h.checkRoutedRequest(rc); apiErr != nil { // managed routing fields and allowlist
		h.failChat(w, rc, apiErr)
		return
	}

	// Check for async inference request. The job runs on the provider
	// resolved above, as this request would, and to the timeout resolved
	// before routing.
	if h.AsyncMiddleware(w, r, rc, provider, orgCfg, requested) {
		return
	}

	if req.Stream {
		h.handleStream(ctx, w, rc, provider, orgCfg)
	} else {
		h.handleNonStream(ctx, w, rc, provider, orgCfg)
	}

	// Check if timeout occurred.
	if ctx.Err() == context.DeadlineExceeded {
		rc.Flags.Timeout = true
	}

	// Note: x-agentcc-timeout-ms is set via setAgentccHeaders (before WriteHeader)
	// using rc.Metadata["timeout_ms"] which was populated above.

	// Record provider latency for routing (after response).
	if router := h.registry.Router(); router != nil {
		if d, ok := rc.Timings["provider"]; ok {
			router.RecordLatency(rc.Provider, d)
		}
	}

	// Record health metrics.
	h.recordProviderHealth(rc)

	// Traffic mirroring (async, fire-and-forget).
	// Copy request fields before rc is released to the pool.
	if m := h.mirror.Load(); m != nil && len(rc.Errors) == 0 && !rc.IsStream {
		reqCopy := *rc.Request

		// Build production info for shadow result capture.
		var prod *routing.ProductionInfo
		if m.Store != nil {
			prodTokens := 0
			prodResponse := ""
			if rc.Response != nil {
				if rc.Response.Usage != nil {
					prodTokens = rc.Response.Usage.TotalTokens
				}
				prodResponse = routing.ExtractResponseText(rc.Response)
			}
			prod = &routing.ProductionInfo{
				RequestID:  rc.RequestID,
				Model:      rc.Model,
				Response:   prodResponse,
				LatencyMs:  time.Since(rc.StartTime).Milliseconds(),
				Tokens:     prodTokens,
				StatusCode: 200,
			}
		}
		m.ExecuteAsync(&reqCopy, rc.Provider, rc.Model, prod)
	}
}

// applyOrgOverrides applies the org config's per-org overrides (routing,
// cache, rate limits, budgets, cost tracking, IP ACL, alerting, privacy, tool
// policy, MCP, audit, A2A, model database and model map) to rc.
func (h *Handlers) applyOrgOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	h.applyOrgRoutingOverrides(orgCfg, rc)
	h.applyOrgCacheOverrides(orgCfg, rc)
	h.applyOrgRateLimitOverrides(orgCfg, rc)
	h.applyOrgBudgetOverrides(orgCfg, rc)
	h.applyOrgCostTrackingOverrides(orgCfg, rc)
	h.applyOrgIPACLOverrides(orgCfg, rc)
	h.applyOrgAlertingOverrides(orgCfg, rc)
	h.applyOrgPrivacyOverrides(orgCfg, rc)
	h.applyOrgToolPolicyOverrides(orgCfg, rc)
	h.applyOrgMCPOverrides(orgCfg, rc)
	h.applyOrgAuditOverrides(orgCfg, rc)
	h.applyOrgA2AOverrides(orgCfg, rc)
	h.applyOrgModelDatabaseOverrides(orgCfg, rc)
	h.applyOrgModelMapOverrides(orgCfg, rc)
}

// unavailableModelError explains why a non-internal key cannot use model
// under orgCfg, the org config the request is routed by. When one of the
// org's own providers lists the model but cannot be built (its base_url was
// refused, say) that is the reason to report: the generic answer would send
// the caller looking at their API key instead.
func (h *Handlers) unavailableModelError(rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) error {
	if orgID := rc.Metadata[tenant.MetadataKeyOrgID]; orgID != "" {
		if _, providerID, _, err := h.orgProviderFor(orgID, orgCfg, model); err != nil {
			return orgProviderError(model, providerID, err)
		}
	}
	return errModelNotAvailable(model)
}

// errModelNotAvailable is the 403 for a model no provider the key may use
// serves, as chat answers it when none of the org's providers lists the
// model (unavailableModelError).
func errModelNotAvailable(model string) *models.APIError {
	return models.ErrForbidden(fmt.Sprintf("model %q is not available for this API key", model))
}

// orgProviderError turns an org provider that could not be built into the
// error the caller sees. Only a refused base_url is explained in detail; other
// build errors can carry config the caller should not see (orgProviderFor
// logs them).
func orgProviderError(model, providerID string, err error) *models.APIError {
	var urlErr *providers.BaseURLError
	if errors.As(err, &urlErr) {
		status, errType, code := http.StatusForbidden, models.ErrTypePermission, "provider_base_url_blocked"
		if urlErr.Reason == providers.BaseURLInvalid || urlErr.Reason == providers.BaseURLUnresolvable {
			status, errType, code = http.StatusBadGateway, models.ErrTypeServer, "provider_base_url_unusable"
		}
		return &models.APIError{
			Status: status,
			Type:   errType,
			Code:   code,
			Message: fmt.Sprintf("model %q is served by your organization's provider %q, but the gateway will not call it: %s.",
				model, providerID, urlErr.PublicMessage()),
		}
	}
	return &models.APIError{
		Status:  http.StatusBadGateway,
		Type:    models.ErrTypeServer,
		Code:    "provider_unavailable",
		Message: fmt.Sprintf("model %q is served by your organization's provider %q, but the gateway could not set that provider up; check its configuration.", model, providerID),
	}
}

// credentialSourceOf names whose credentials p calls its upstream with, for
// a request of orgID: "org" when p was built from that org's own provider
// config, "platform" when it is one of the gateway's own providers.
func credentialSourceOf(p providers.Provider, orgID string) string {
	if providers.IsOrgInstance(p, orgID) {
		return "org"
	}
	return "platform"
}

// orgProvidersOnly reports whether rc's key may use only its org's own
// providers: any key but an internal one, unless a license authorized the
// request.
func (h *Handlers) orgProvidersOnly(ctx context.Context, rc *models.RequestContext) bool {
	return h.keyStore != nil && rc.Metadata["key_type"] != "internal" && !middleware.IsLicenseAuthorized(ctx)
}

// resolveProvider resolves the provider for a model under the stored config
// of the request's org: resolveProviderFor for the handlers that do not run
// call-order step 4.
func (h *Handlers) resolveProvider(ctx context.Context, rc *models.RequestContext, model string) (providers.Provider, string, error) {
	var orgCfg *tenant.OrgConfig
	if h.orgProvidersOnly(ctx, rc) {
		_, orgCfg = h.resolveOrgConfig(rc)
	}
	return h.resolveProviderFor(ctx, rc, orgCfg, model)
}

// resolveProviderFor resolves the provider for a model and the model name to
// send it. The two names differ when a managed model, a conditional route or
// a routing target overrides the requested model, or a chat request takes a
// model fallback. Chat also gets the name on rc.Request; every other endpoint
// has no rc.Request and puts the returned name on its own request.
// orgCfg decides managed models for a key limited to its org's providers:
// the effective org config on chat, so a rollout candidate's managed_models
// applies to candidate traffic.
func (h *Handlers) resolveProviderFor(ctx context.Context, rc *models.RequestContext, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string, error) {
	// Non-internal keys must not resolve to global (FutureAGI-credentialed) providers.
	// This guard runs first so no code path (model map, conditional routes, registry)
	// can bypass it for user keys.
	if h.orgProvidersOnly(ctx, rc) {
		if p, upstream, ok := h.resolveManaged(rc, orgCfg, model); ok {
			return p, upstream, nil
		}
		return nil, "", h.unavailableModelError(rc, orgCfg, model)
	}

	// Check per-org model map first — allows orgs to alias model names to providers.
	if len(rc.OrgModelMap) > 0 {
		if providerID, ok := rc.OrgModelMap[model]; ok {
			p, found := h.registry.GetProvider(providerID)
			if found {
				rc.Provider = providerID
				rc.Metadata["model_map_matched"] = model
				rc.Metadata["model_map_provider"] = providerID
				return p, model, nil
			}
			slog.Warn("org model map matched but provider not found, falling through",
				"model", model, "provider", providerID)
		}
	}

	// Check conditional routes first.
	if cr := h.conditionalRouter.Load(); cr != nil {
		if action := cr.Evaluate(rc); action != nil {
			p, ok := h.registry.GetProvider(action.Provider)
			if ok {
				rc.Provider = action.Provider
				rc.Metadata["routing_rule"] = action.Name
				rc.SetMetadata(routing.RouteReasonConditional, action.Name)
				if action.ModelOverride != "" {
					if rc.Request != nil {
						rc.Request.Model = action.ModelOverride
					}
					return p, action.ModelOverride, nil
				}
				return p, model, nil
			}
			slog.Warn("conditional route matched but provider not found",
				"rule", action.Name, "provider", action.Provider)
		}
	}

	// Try primary model first.
	result, err := h.registry.ResolveWithRouting(model)
	if err == nil {
		rc.Provider = result.Provider.ID()
		if result.StrategyName != "" {
			rc.Metadata["routing_strategy"] = result.StrategyName
		}
		if result.ModelOverride != "" {
			if rc.Request != nil {
				rc.Request.Model = result.ModelOverride
			}
			return result.Provider, result.ModelOverride, nil
		}
		return result.Provider, model, nil
	}

	// Try model fallbacks: org-level first, then global.
	var effectiveFallbacks *routing.ModelFallbacks
	if len(rc.OrgModelFallbacks) > 0 {
		effectiveFallbacks = routing.NewModelFallbacks(rc.OrgModelFallbacks)
	} else if mf := h.modelFallbacks.Load(); mf != nil {
		effectiveFallbacks = mf
	}

	// Only chat takes a model fallback here: a non-chat request (embeddings,
	// rerank, images, audio, OCR) served by another model than the one it
	// named would answer in another model's terms, and an embedding would
	// land in another vector space.
	if rc.Request != nil && effectiveFallbacks != nil && effectiveFallbacks.HasFallbacks(model) {
		for _, fbModel := range effectiveFallbacks.GetChain(model) {
			fbResult, fbErr := h.registry.ResolveWithRouting(fbModel)
			if fbErr == nil {
				rc.Provider = fbResult.Provider.ID()
				upstreamModel := fbModel
				if fbResult.ModelOverride != "" {
					upstreamModel = fbResult.ModelOverride
				}
				rc.Request.Model = upstreamModel
				rc.Flags.FallbackUsed = true
				rc.Metadata["original_model"] = model
				rc.Metadata["fallback_model"] = fbModel
				if fbResult.StrategyName != "" {
					rc.Metadata["routing_strategy"] = fbResult.StrategyName
				}
				return fbResult.Provider, upstreamModel, nil
			}
		}
	}

	return nil, "", err
}

func splitCSV(s string) []string {
	parts := strings.Split(s, ",")
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		if t := strings.TrimSpace(p); t != "" {
			result = append(result, t)
		}
	}
	return result
}

// ModelDB returns the current model database.
func (h *Handlers) ModelDB() *modeldb.ModelDB {
	if h.modelDB == nil {
		return nil
	}
	return h.modelDB.Load()
}

// NotImplemented returns 501 for endpoints that are registered but not yet built.
func NotImplemented(w http.ResponseWriter, r *http.Request) {
	models.WriteError(w, &models.APIError{
		Status:  http.StatusNotImplemented,
		Type:    models.ErrTypeServer,
		Code:    "not_implemented",
		Message: "This endpoint is not yet implemented",
	})
}
