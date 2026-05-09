package server

import (
	"context"
	"encoding/json"
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
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/files"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/responses"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/scheduled"
	"github.com/futureagi/agentcc-gateway/internal/streaming"
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

	// Async inference support.
	asyncStore  *async.Store
	asyncWorker *async.Worker

	// Responses API store.
	responsesStore *responses.Store

	// Files API store.
	fileStore *files.Store

	// Video generation store.
	videoStore video.Store

	// Phase 12A: Advanced routing components.
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

func mergeModelObjects(globalModels, orgModels []models.ModelObject) []models.ModelObject {
	if len(orgModels) == 0 {
		return globalModels
	}
	merged := append([]models.ModelObject{}, globalModels...)
	indexByID := make(map[string]int, len(merged))
	for i, model := range merged {
		indexByID[model.ID] = i
	}
	for _, model := range orgModels {
		if idx, ok := indexByID[model.ID]; ok {
			merged[idx] = model
			continue
		}
		indexByID[model.ID] = len(merged)
		merged = append(merged, model)
	}
	return merged
}

func (h *Handlers) resolveProviderWithOrgFallback(ctx context.Context, rc *models.RequestContext, orgID string, orgCfg *tenant.OrgConfig, model string) (providers.Provider, error) {
	if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
		for providerID, provCfg := range orgCfg.Providers {
			if provCfg == nil || !provCfg.Enabled || !provCfg.HasCredentials() {
				continue
			}
			for _, m := range provCfg.Models {
				if orgModelMatches(m, model, providerID) {
					orgProvider, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, provCfg.APIKey, provCfg)
					if err == nil {
						rc.Provider = providerID
						rc.Metadata["org_provider_model_match"] = model
						return orgProvider, nil
					}
				}
			}
		}
	}

	provider, err := h.resolveProvider(ctx, rc, model)
	if err != nil {
		if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
			if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, model); orgP != nil {
				rc.Provider = providerID
				rc.Metadata["org_provider"] = "true"
				return orgP, nil
			}
		}
		return nil, err
	}

	if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}
	return provider, nil
}

// NewHandlers creates a Handlers instance.
func NewHandlers(registry *providers.Registry, engine *pipeline.Engine, maxBodySize int64, defaultTimeout time.Duration, failover *routing.Failover, modelFallbacks *routing.ModelFallbacks, conditionalRouter *routing.ConditionalRouter, healthMonitor *routing.HealthMonitor, modelTimeouts map[string]time.Duration, mirror *routing.Mirror, guardrailEngine *guardrails.Engine, policyStore *policy.Store, streamGuardrailCfg config.StreamingGuardrailConfig, mdbPtr *atomic.Pointer[modeldb.ModelDB], tenantStore *tenant.Store, orgProviderCache *providers.OrgProviderCache, keyStore *authpkg.KeyStore) *Handlers {
	h := &Handlers{
		registry:           registry,
		engine:             engine,
		healthMonitor:      healthMonitor,
		maxBodySize:        maxBodySize,
		defaultTimeout:     defaultTimeout,
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
	return h
}

// ReloadRouting rebuilds all routing components from new config and atomically swaps them.
func (h *Handlers) ReloadRouting(cfg *config.Config, registry *providers.Registry) error {
	// Rebuild model database and update shared pointer.
	if h.modelDB != nil {
		newDB := modeldb.New(modeldb.BundledModels, ConvertModelOverrides(cfg.ModelDatabase.Overrides))
		h.modelDB.Store(newDB)
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

// peekKeyOrgID does a lightweight key lookup to extract org_id from the API key
// metadata BEFORE the auth plugin runs. This is needed because resolveOrgConfig
// depends on rc.Metadata["key_org_id"] which the auth plugin sets — but auth runs
// inside engine.Process, after org config resolution.
//
// The auth plugin still performs full validation (status, expiry, IP, model access).
// This peek is read-only and only copies the org_id; it does NOT authenticate.
func (h *Handlers) peekKeyOrgID(rc *models.RequestContext) {
	if h.keyStore == nil {
		return
	}
	authHeader := rc.Metadata["authorization"]
	if authHeader == "" || !strings.HasPrefix(authHeader, "Bearer ") {
		return
	}
	rawKey := strings.TrimPrefix(authHeader, "Bearer ")
	if rawKey == "" {
		return
	}
	apiKey := h.keyStore.Lookup(rawKey)
	if apiKey == nil {
		return
	}
	if orgID, ok := apiKey.Metadata["org_id"]; ok && orgID != "" {
		rc.Metadata["key_org_id"] = orgID
	}
	// Also propagate key_type early so provider resolution can distinguish
	// internal service keys from user keys before the auth plugin runs.
	if apiKey.KeyType != "" {
		rc.Metadata["key_type"] = apiKey.KeyType
	}
	// Propagate access_groups early so alias resolution (Phase 12A) works
	// for static config keys before the auth plugin copies full metadata.
	if ag, ok := apiKey.Metadata["access_groups"]; ok && ag != "" {
		rc.Metadata["key_access_groups"] = ag
	}
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

// resolveOrgProvider tries to find a provider for the given model in the org's
// provider config. It checks each enabled provider's model list and creates a
// cached provider instance with the org's API key.
func (h *Handlers) resolveOrgProvider(orgID string, orgCfg *tenant.OrgConfig, model string) (providers.Provider, string) {
	if orgCfg == nil || h.orgProviderCache == nil {
		return nil, ""
	}
	for providerID, pcfg := range orgCfg.Providers {
		if pcfg == nil || !pcfg.Enabled || !pcfg.HasCredentials() {
			continue
		}
		for _, m := range pcfg.Models {
			if m == model {
				p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, pcfg.APIKey, pcfg)
				if err != nil {
					slog.Warn("failed to create org provider for model",
						"org_id", orgID, "provider", providerID, "model", model, "error", err)
					continue
				}
				return p, providerID
			}
		}
	}
	return nil, ""
}

// applyOrgRoutingOverrides applies per-org routing config to the request context.
// This includes default model mapping and routing strategy metadata.
func (h *Handlers) applyOrgRoutingOverrides(orgCfg *tenant.OrgConfig, rc *models.RequestContext) {
	if orgCfg == nil || orgCfg.Routing == nil {
		return
	}

	routing := orgCfg.Routing

	// If the org has a default model and the request model isn't explicitly
	// mapped in the registry, use the org's default as a hint.
	if routing.DefaultModel != "" {
		rc.Metadata["org_default_model"] = routing.DefaultModel
	}

	// Record the org's preferred routing strategy for observability.
	if routing.Strategy != "" {
		rc.Metadata["org_routing_strategy"] = routing.Strategy
	}

	// Apply per-org model fallbacks: store in metadata for downstream use.
	if len(routing.ModelFallbacks) > 0 {
		rc.Metadata["org_has_fallbacks"] = "true"
		rc.OrgModelFallbacks = routing.ModelFallbacks
	}

	// Advanced routing: complexity routing override.
	if routing.Complexity != nil && routing.Complexity.Enabled {
		rc.Metadata["org_complexity_routing"] = "true"
	}

	// Advanced routing: fastest response / race mode.
	if routing.Fastest != nil && routing.Fastest.Enabled {
		rc.Metadata["org_fastest_response"] = "true"
	}

	// Advanced routing: adaptive routing.
	if routing.Adaptive != nil && routing.Adaptive.Enabled {
		rc.Metadata["org_adaptive_routing"] = "true"
	}

	// Advanced routing: provider lock.
	if routing.ProviderLock != nil && routing.ProviderLock.Enabled {
		rc.Metadata["org_provider_lock"] = "true"
	}

	// Reliability: circuit breaker.
	if routing.CircuitBreaker != nil && routing.CircuitBreaker.Enabled {
		rc.Metadata["org_circuit_breaker"] = "true"
	}

	// Reliability: retry.
	if routing.Retry != nil && routing.Retry.Enabled {
		rc.Metadata["org_retry"] = "true"
	}
}

func (h *Handlers) buildOrgFailoverConfig(r *tenant.RoutingConfig) config.FailoverConfig {
	cfg := config.FailoverConfig{
		Enabled:     true,
		MaxAttempts: 3,
		OnTimeout:   true,
	}

	if r.Failover != nil {
		if r.Failover.MaxAttempts > 0 {
			cfg.MaxAttempts = r.Failover.MaxAttempts
		}
		cfg.OnStatusCodes = r.Failover.OnStatusCodes
		cfg.OnTimeout = r.Failover.OnTimeout
		if r.Failover.PerAttemptTimeout != "" {
			if d, err := time.ParseDuration(r.Failover.PerAttemptTimeout); err == nil {
				cfg.PerAttemptTimeout = d
			}
		}
	}

	// Legacy flat fields as fallback.
	if len(cfg.OnStatusCodes) == 0 && len(r.FallbackStatusCodes) > 0 {
		cfg.OnStatusCodes = r.FallbackStatusCodes
	}
	if len(cfg.OnStatusCodes) == 0 {
		cfg.OnStatusCodes = routing.DefaultFailoverStatusCodes
	}

	return cfg
}

func (h *Handlers) buildOrgRetryConfig(r *tenant.RetryConfig) config.RetryConfig {
	cfg := config.RetryConfig{
		Enabled:    r.Enabled,
		MaxRetries: r.MaxRetries,
		Multiplier: r.Multiplier,
		OnTimeout:  r.OnTimeout,
	}
	if r.InitialDelay != "" {
		if d, err := time.ParseDuration(r.InitialDelay); err == nil {
			cfg.InitialDelay = d
		}
	}
	if r.MaxDelay != "" {
		if d, err := time.ParseDuration(r.MaxDelay); err == nil {
			cfg.MaxDelay = d
		}
	}
	if len(r.OnStatusCodes) > 0 {
		cfg.OnStatusCodes = r.OnStatusCodes
	}
	return cfg
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

// resolveTimeout returns the effective timeout for a request.
// Priority: x-agentcc-timeout header > model-specific > server default.
func (h *Handlers) resolveTimeout(rc *models.RequestContext, r *http.Request) time.Duration {
	// 1. Request header override (Go duration string, e.g. "30s", "2m", "500ms").
	if v := r.Header.Get("x-agentcc-timeout"); v != "" {
		if d, err := time.ParseDuration(v); err == nil && d > 0 {
			return d
		}
	}

	// 2. Per-model timeout from config.
	if mt := h.modelTimeouts.Load(); mt != nil {
		if d, ok := (*mt)[rc.Model]; ok && d > 0 {
			return d
		}
	}

	// 3. Server default.
	return h.defaultTimeout
}

// ChatCompletion handles POST /v1/chat/completions.
func (h *Handlers) ChatCompletion(w http.ResponseWriter, r *http.Request) {
	rc := models.AcquireRequestContext()
	defer rc.Release()

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
	// so post-plugins (cost, logging) have accurate token counts.
	if req.Stream {
		if req.StreamOptions == nil {
			req.StreamOptions = &models.StreamOptions{IncludeUsage: true}
		} else {
			req.StreamOptions.IncludeUsage = true
		}
	}

	// Extract client IP for IP ACL and audit.
	rc.Metadata["client_ip"] = extractClientIP(r)

	// Pass Authorization header for auth plugin.
	setAuthMetadataFromRequest(rc, r)

	// Extract Agentcc metadata from headers (with security key blocklist).
	if meta := r.Header.Get("x-agentcc-metadata"); meta != "" {
		var m map[string]string
		if err := json.Unmarshal([]byte(meta), &m); err == nil {
			for k, v := range m {
				if isBlockedMetadataKey(k) {
					continue
				}
				rc.Metadata[k] = v
			}
		}
	}
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
	if v := r.Header.Get("X-Guardrail-Policy"); v != "" {
		rc.Metadata["x-guardrail-policy"] = v
	}

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

	// Resolve effective timeout (header > model > default) and apply.
	timeout := h.resolveTimeout(rc, r)
	ctx, cancel := context.WithTimeout(r.Context(), timeout)
	defer cancel()
	rc.Metadata["timeout_ms"] = fmt.Sprintf("%d", timeout.Milliseconds())

	// Early org_id extraction: auth plugin hasn't run yet but resolveOrgConfig
	// needs key_org_id. Peek at the API key metadata to populate it.
	h.peekKeyOrgID(rc)

	orgID, orgCfg := h.resolveOrgConfig(rc)
	if orgID != "" {
		rc.Metadata["org_id"] = orgID
	}

	// Apply per-org overrides (routing, cache, rate limits).
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

	// --- Phase 12A: Advanced routing pipeline ---

	// 1. Model access group alias resolution and access check.
	keyGroups := splitCSV(rc.Metadata["key_access_groups"])
	if h.accessGroupChecker.IsEnabled() && len(keyGroups) > 0 {
		resolved := h.accessGroupChecker.ResolveAlias(rc.Model, keyGroups)
		if resolved != rc.Model {
			rc.SetMetadata("model_alias", rc.Model)
			rc.Model = resolved
			req.Model = resolved
		}
		matchedGroup, allowed := h.accessGroupChecker.Check(rc.Model, keyGroups)
		if !allowed {
			desc := h.accessGroupChecker.DescribeAllowed(keyGroups)
			models.WriteError(w, models.ErrForbidden(
				fmt.Sprintf("Model %q is not available for this API key. %s", rc.Model, desc)))
			return
		}
		if matchedGroup != "" {
			rc.SetMetadata("access_group_matched", matchedGroup)
		}
	}

	// 2. Provider locking — bypasses all routing if set.
	var providerLocked bool
	var lockedProvider providers.Provider
	if h.providerLockResolver.IsEnabled() {
		lockTarget := h.providerLockResolver.ExtractLock(rc, r.Header)
		if lockTarget != "" {
			if err := h.providerLockResolver.Validate(lockTarget); err != nil {
				models.WriteError(w, models.ErrForbidden(err.Error()))
				return
			}
			p, ok := h.registry.GetProvider(lockTarget)
			if !ok {
				models.WriteError(w, models.ErrNotFound("provider_not_found",
					fmt.Sprintf("Locked provider %q not found", lockTarget)))
				return
			}
			providerLocked = true
			lockedProvider = p
			rc.Provider = lockTarget
			rc.SetMetadata("provider_locked", "true")
			rc.SetMetadata("provider_lock_target", lockTarget)
		}
	}

	// 3. Complexity-based routing (only if not provider-locked).
	if !providerLocked && h.complexityAnalyzer.IsEnabled() && !strings.Contains(rc.Model, "/") {
		if override := r.Header.Get("x-agentcc-complexity-override"); override != "" {
			rc.SetMetadata("complexity_override", override)
		}
		result := h.complexityAnalyzer.Analyze(rc.Request)
		originalModel := rc.Model
		rc.Model = result.Model
		req.Model = result.Model
		routing.SetComplexityMetadata(rc, result, originalModel)
		if result.Provider != "" {
			rc.SetMetadata("complexity_provider", result.Provider)
		}
	}

	// Resolve provider (with load balancing and failover if configured).
	var provider providers.Provider
	var orgModelResolved bool

	// Check if the org has a provider that specifically registers this model.
	if !providerLocked && orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
		slog.Info("checking org providers for model", "model", req.Model, "org_providers_count", len(orgCfg.Providers))
		for providerID, provCfg := range orgCfg.Providers {
			if provCfg == nil || !provCfg.Enabled || !provCfg.HasCredentials() {
				slog.Info("skipping org provider", "provider", providerID, "nil", provCfg == nil, "enabled", provCfg != nil && provCfg.Enabled, "has_credentials", provCfg != nil && provCfg.HasCredentials())
				continue
			}
			for _, m := range provCfg.Models {
				if orgModelMatches(m, req.Model, providerID) {
					slog.Info("org provider model match, creating override", "provider", providerID, "model", m)
					orgProvider, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, providerID, provCfg.APIKey, provCfg)
					if err == nil {
						provider = orgProvider
						rc.Provider = providerID
						rc.Metadata["org_provider_model_match"] = req.Model
						orgModelResolved = true
						break
					}
				}
			}
			if orgModelResolved {
				break
			}
		}
	}

	if providerLocked {
		provider = lockedProvider
	} else if !orgModelResolved {
		var err error
		provider, err = h.resolveProvider(ctx, rc, req.Model)
		if err != nil {
			// Try org provider model lists before giving up.
			if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
				if orgP, providerID := h.resolveOrgProvider(orgID, orgCfg, req.Model); orgP != nil {
					provider = orgP
					rc.Provider = providerID
					rc.Metadata["org_provider"] = "true"
					err = nil
				}
			}
			if err != nil {
				models.WriteErrorFromError(w, err)
				return
			}
		}
	}

	// Internal service keys: skip org provider override and ACL enforcement.
	// They resolve from global config.yaml providers only.
	if shouldApplyOrgProviderOverride(rc) {
		provider = h.applyOrgProviderOverride(orgID, orgCfg, rc.Provider, provider)
	}
	if rc.Metadata["key_type"] != "internal" {
		// Enforce provider ACL after routing (auth plugin stores allowed providers).
		if allowedCSV, ok := rc.Metadata["auth_allowed_providers"]; ok && rc.Provider != "" {
			allowed := false
			for _, p := range splitCSV(allowedCSV) {
				if p == rc.Provider {
					allowed = true
					break
				}
			}
			if !allowed {
				models.WriteError(w, models.ErrForbidden("API key does not have access to provider: "+rc.Provider))
				return
			}
		}
	}

	// Check for async inference request.
	if h.AsyncMiddleware(w, r, rc) {
		return
	}

	if req.Stream {
		h.handleStream(ctx, w, rc, provider)
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
	if h.healthMonitor != nil && rc.Provider != "" {
		if len(rc.Errors) > 0 {
			h.healthMonitor.RecordError(rc.Provider, rc.Errors[0])
		} else {
			h.healthMonitor.RecordSuccess(rc.Provider)
		}
	}

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

// resolveProvider resolves the provider for a model, with failover support for non-streaming.
// For failover, it tries providers sequentially until one succeeds at the provider call level.
func (h *Handlers) resolveProvider(ctx context.Context, rc *models.RequestContext, model string) (providers.Provider, error) {
	// Non-internal keys must not resolve to global (FutureAGI-credentialed) providers.
	// This guard runs first so no code path (model map, conditional routes, registry)
	// can bypass it for user keys.
	if h.keyStore != nil && rc.Metadata["key_type"] != "internal" {
		return nil, models.ErrForbidden(
			fmt.Sprintf("model %q is not available for this API key", model),
		)
	}

	// Check per-org model map first — allows orgs to alias model names to providers.
	if len(rc.OrgModelMap) > 0 {
		if providerID, ok := rc.OrgModelMap[model]; ok {
			p, found := h.registry.GetProvider(providerID)
			if found {
				rc.Provider = providerID
				rc.Metadata["model_map_matched"] = model
				rc.Metadata["model_map_provider"] = providerID
				return p, nil
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
				if action.ModelOverride != "" {
					rc.Model = action.ModelOverride
					if rc.Request != nil {
						rc.Request.Model = action.ModelOverride
					}
				}
				return p, nil
			}
			slog.Warn("conditional route matched but provider not found",
				"rule", action.Name, "provider", action.Provider)
		}
	}

	// Try primary model first.
	// Non-internal keys must not resolve to global (FutureAGI-credentialed) providers —
	// they should only use org-configured providers (resolved above) or be rejected.
	if rc.Metadata["key_type"] != "internal" {
		return nil, fmt.Errorf("model %q is not available for this API key: configure provider access via the control plane", model)
	}

	result, err := h.registry.ResolveWithRouting(model)
	if err == nil {
		rc.Provider = result.Provider.ID()
		if result.StrategyName != "" {
			rc.Metadata["routing_strategy"] = result.StrategyName
		}
		if result.ModelOverride != "" {
			rc.Model = result.ModelOverride
			if rc.Request != nil {
				rc.Request.Model = result.ModelOverride
			}
		}
		return result.Provider, nil
	}

	// Try model fallbacks: org-level first, then global.
	var effectiveFallbacks *routing.ModelFallbacks
	if len(rc.OrgModelFallbacks) > 0 {
		effectiveFallbacks = routing.NewModelFallbacks(rc.OrgModelFallbacks)
	} else if mf := h.modelFallbacks.Load(); mf != nil {
		effectiveFallbacks = mf
	}

	if effectiveFallbacks != nil && effectiveFallbacks.HasFallbacks(model) {
		for _, fbModel := range effectiveFallbacks.GetChain(model) {
			fbResult, fbErr := h.registry.ResolveWithRouting(fbModel)
			if fbErr == nil {
				rc.Provider = fbResult.Provider.ID()
				rc.Model = fbModel
				if rc.Request != nil {
					rc.Request.Model = fbModel
				}
				rc.Flags.FallbackUsed = true
				rc.Metadata["original_model"] = model
				rc.Metadata["fallback_model"] = fbModel
				if fbResult.StrategyName != "" {
					rc.Metadata["routing_strategy"] = fbResult.StrategyName
				}
				if fbResult.ModelOverride != "" {
					rc.Model = fbResult.ModelOverride
					if rc.Request != nil {
						rc.Request.Model = fbResult.ModelOverride
					}
				}
				return fbResult.Provider, nil
			}
		}
	}

	return nil, err
}

func (h *Handlers) handleNonStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider, orgCfg *tenant.OrgConfig) {
	// Determine effective failover: org routing config takes priority over global.
	var effectiveFailover *routing.Failover
	if orgCfg != nil && orgCfg.Routing != nil && (orgCfg.Routing.FallbackEnabled || (orgCfg.Routing.Failover != nil && orgCfg.Routing.Failover.Enabled)) {
		foCfg := h.buildOrgFailoverConfig(orgCfg.Routing)
		var retryer *routing.Retryer
		if orgCfg.Routing.Retry != nil && orgCfg.Routing.Retry.Enabled {
			retryer = routing.NewRetryer(h.buildOrgRetryConfig(orgCfg.Routing.Retry))
		}
		effectiveFailover = routing.NewFailover(foCfg, h.registry.Router(), retryer, nil)
	} else if fo := h.failover.Load(); fo != nil {
		effectiveFailover = fo
	}

	if effectiveFailover != nil && effectiveFailover.IsEnabled() && h.registry.Router() != nil && h.registry.Router().HasTargets(rc.Model) {
		h.handleNonStreamWithFailover(ctx, w, rc, effectiveFailover, orgCfg)
		return
	}

	// Resolve model fallback chain (org-level first, then global).
	var modelChain []string
	if len(rc.OrgModelFallbacks) > 0 {
		modelChain = rc.OrgModelFallbacks[rc.Model]
	} else if mf := h.modelFallbacks.Load(); mf != nil {
		modelChain = mf.GetChain(rc.Model)
	}

	originalModel := rc.Request.Model
	orgID := rc.Metadata["org_id"]

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		resp, err := provider.ChatCompletion(ctx, rc.Request)
		if err == nil {
			rc.Response = resp
			rc.ResolvedModel = resp.Model
			return nil
		}

		if len(modelChain) == 0 || effectiveFailover == nil || !effectiveFailover.ShouldFailover(err) {
			return err
		}

		for _, fbModel := range modelChain {
			var fbProvider providers.Provider
			var fbProviderID string

			if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
				for pid, pcfg := range orgCfg.Providers {
					if pcfg == nil || !pcfg.Enabled || !pcfg.HasCredentials() {
						continue
					}
					for _, m := range pcfg.Models {
						if orgModelMatches(m, fbModel, pid) {
							op, opErr := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, pid, pcfg.APIKey, pcfg)
							if opErr == nil {
								fbProvider = op
								fbProviderID = pid
							}
							break
						}
					}
					if fbProvider != nil {
						break
					}
				}
			}

			if fbProvider == nil {
				fbResult, fbErr := h.registry.ResolveWithRouting(fbModel)
				if fbErr != nil {
					continue
				}
				fbProvider = fbResult.Provider
				if shouldApplyOrgProviderOverride(rc) {
					fbProvider = h.applyOrgProviderOverride(orgID, orgCfg, fbResult.Provider.ID(), fbResult.Provider)
				}
				fbProviderID = fbResult.Provider.ID()
				if fbResult.ModelOverride != "" {
					fbModel = fbResult.ModelOverride
				}
			}

			rc.Provider = fbProviderID
			rc.Request.Model = fbModel

			fbResp, fbCallErr := fbProvider.ChatCompletion(ctx, rc.Request)
			if fbCallErr == nil {
				rc.Response = fbResp
				rc.ResolvedModel = fbResp.Model
				rc.Flags.FallbackUsed = true
				rc.Metadata["original_model"] = originalModel
				rc.Metadata["fallback_model"] = fbModel
				return nil
			}
		}

		return err
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.Response == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.Response)
}

func (h *Handlers) handleNonStreamWithFailover(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, fo *routing.Failover, orgCfg *tenant.OrgConfig) {
	originalModel := rc.Request.Model
	orgID := rc.Metadata["org_id"]

	// Wrap the failover loop inside engine.Process so pre-plugins run once,
	// then the failover retries only the provider call, then post-plugins run once.
	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		foResult, err := fo.Execute(ctx, rc.Model, func(ctx context.Context, providerID string, modelOverride string) error {
			p, ok := h.registry.GetProvider(providerID)
			if !ok {
				return fmt.Errorf("provider %q not found", providerID)
			}

			if shouldApplyOrgProviderOverride(rc) {
				p = h.applyOrgProviderOverride(orgID, orgCfg, providerID, p)
			}

			// Reset for this attempt.
			rc.Provider = providerID
			rc.Response = nil
			rc.ResolvedModel = ""
			if modelOverride != "" {
				rc.Request.Model = modelOverride
			} else {
				rc.Request.Model = originalModel
			}

			resp, err := p.ChatCompletion(ctx, rc.Request)
			if err != nil {
				return err
			}
			rc.Response = resp
			rc.ResolvedModel = resp.Model
			return nil
		})

		if err != nil {
			// Provider failover exhausted — try model fallback chain.
			var modelChain []string
			if len(rc.OrgModelFallbacks) > 0 {
				modelChain = rc.OrgModelFallbacks[originalModel]
			} else if mf := h.modelFallbacks.Load(); mf != nil {
				modelChain = mf.GetChain(originalModel)
			}

			for _, fbModel := range modelChain {
				// Resolve fallback model: check org providers first, then global registry.
				var fbProvider providers.Provider
				var fbProviderID string

				if orgCfg != nil && orgID != "" && h.orgProviderCache != nil {
					for pid, pcfg := range orgCfg.Providers {
						if pcfg == nil || !pcfg.Enabled || !pcfg.HasCredentials() {
							continue
						}
						for _, m := range pcfg.Models {
							if orgModelMatches(m, fbModel, pid) {
								op, opErr := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, pid, pcfg.APIKey, pcfg)
								if opErr == nil {
									fbProvider = op
									fbProviderID = pid
								}
								break
							}
						}
						if fbProvider != nil {
							break
						}
					}
				}

				if fbProvider == nil {
					fbResult, fbErr := h.registry.ResolveWithRouting(fbModel)
					if fbErr != nil {
						continue
					}
					fbProvider = fbResult.Provider
					if shouldApplyOrgProviderOverride(rc) {
						fbProvider = h.applyOrgProviderOverride(orgID, orgCfg, fbResult.Provider.ID(), fbResult.Provider)
					}
					fbProviderID = fbResult.Provider.ID()
					if fbResult.ModelOverride != "" {
						fbModel = fbResult.ModelOverride
					}
				}

				rc.Provider = fbProviderID
				rc.Request.Model = fbModel

				fbResp, fbCallErr := fbProvider.ChatCompletion(ctx, rc.Request)
				if fbCallErr == nil {
					rc.Response = fbResp
					rc.ResolvedModel = fbResp.Model
					rc.Flags.FallbackUsed = true
					rc.Metadata["original_model"] = originalModel
					rc.Metadata["fallback_model"] = fbModel
					return nil
				}
			}

			return err
		}

		// Record failover metadata.
		if foResult.FallbackUsed {
			rc.Flags.FallbackUsed = true
			rc.Metadata["failover_original_provider"] = foResult.OriginalProvider
		}
		rc.Metadata["failover_attempts"] = fmt.Sprintf("%d", foResult.Attempts)
		if foResult.StrategyName != "" {
			rc.Metadata["routing_strategy"] = foResult.StrategyName
		}
		return nil
	}

	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteErrorFromError(w, err)
		return
	}

	if rc.Response == nil {
		models.WriteError(w, models.ErrInternal("no response from provider"))
		return
	}

	h.setAgentccHeaders(w, rc)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(rc.Response)
}

func (h *Handlers) handleStream(ctx context.Context, w http.ResponseWriter, rc *models.RequestContext, provider providers.Provider) {
	sseWriter := streaming.NewSSEWriter(w)
	if sseWriter == nil {
		models.WriteError(w, models.ErrInternal("streaming not supported by server"))
		return
	}

	// For streaming, we call the provider directly (pre/post plugins run but provider is streaming).
	var chunks <-chan models.StreamChunk
	var errCh <-chan error

	providerCall := func(ctx context.Context, rc *models.RequestContext) error {
		chunks, errCh = provider.StreamChatCompletion(ctx, rc.Request)
		return nil
	}

	// Run pre-plugins, initiate streaming, then post-plugins will run after.
	if err := h.engine.Process(ctx, rc, providerCall); err != nil {
		models.WriteError(w, models.ErrInternal(err.Error()))
		return
	}

	if rc.Flags.ShortCircuited && rc.Response != nil {
		// Short-circuited (e.g., cache hit) - return as JSON even if stream was requested.
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(rc.Response)
		return
	}

	if chunks == nil {
		models.WriteError(w, models.ErrInternal("no stream from provider"))
		return
	}

	// Set Prism headers after pre-plugins have run but before the streaming body starts.
	h.setAgentccHeaders(w, rc)
	sseWriter.WriteHeaders()

	// Create a cancellable context for stream guardrail checks.
	// Cancelling this ensures spawned guardrail goroutines are stopped on exit.
	streamCtx, streamCancel := context.WithCancel(ctx)
	defer streamCancel()

	// Track the last usage from stream chunks so we can populate rc.Response
	// after the stream completes (providers send usage in the final chunk).
	var lastUsage *models.Usage
	var streamID string
	var streamCreated int64

	// finalizeStream populates rc.Response with accumulated usage and runs
	// post-plugins (cost, credits, logging, otel, prometheus). Must be called
	// before every return from the streaming loop. When detach is true, uses
	// a background context so post-plugins run even after client disconnect.
	finalizeStream := func(detach bool) *models.StreamChunk {
		rc.Response = &models.ChatCompletionResponse{
			Model: rc.ResolvedModel,
			Usage: lastUsage, // nil is OK — means provider didn't send usage
		}
		pluginCtx := ctx
		if detach {
			pluginCtx = context.Background()
		}
		h.engine.RunPostPlugins(pluginCtx, rc)
		if detach {
			return nil
		}
		return h.buildStreamingMetadataChunk(rc, streamID, streamCreated, lastUsage)
	}

	// Set up streaming guardrail checker if enabled.
	var streamChecker *guardrails.StreamGuardrailChecker
	if h.streamGuardrailCfg.Enabled && h.guardrailEngine != nil && h.guardrailEngine.PostCount() > 0 {
		var keyPolicy *policy.Policy
		if keyID := rc.Metadata["auth_key_id"]; keyID != "" && h.policyStore != nil {
			keyPolicy = h.policyStore.Get(keyID)
		}
		rp := policy.RequestPolicyNone
		if rpStr := rc.Metadata["x-guardrail-policy"]; rpStr != "" {
			if parsed, ok := policy.ParseRequestPolicy(rpStr); ok {
				rp = parsed
			}
		}
		streamChecker = guardrails.NewStreamChecker(
			h.guardrailEngine, h.streamGuardrailCfg,
			keyPolicy, rp, rc.Request, rc.Metadata,
		)
	}

	for {
		select {
		case chunk, ok := <-chunks:
			if !ok {
				// Stream complete — run final guardrail check.
				if streamChecker != nil {
					if res := streamChecker.Finish(streamCtx); res.Blocked {
						sseWriter.WriteError(models.ErrGuardrailBlocked("stream_blocked", res.Message))
					} else if res.Disclaimer != "" {
						disclaimer := res.Disclaimer
						disclaimerChunk := models.StreamChunk{
							Choices: []models.StreamChoice{
								{Delta: models.Delta{Content: &disclaimer}},
							},
						}
						sseWriter.WriteChunk(disclaimerChunk)
					}
				}
				metadataChunk := finalizeStream(false)
				if metadataChunk != nil {
					if err := sseWriter.WriteChunk(*metadataChunk); err != nil {
						slog.Warn("error writing final stream metadata chunk",
							"request_id", rc.RequestID,
							"error", err,
						)
					}
				}
				sseWriter.WriteDone()
				return
			}
			if streamID == "" && chunk.ID != "" {
				streamID = chunk.ID
			}
			if streamCreated == 0 && chunk.Created != 0 {
				streamCreated = chunk.Created
			}
			if len(chunk.Choices) > 0 && rc.ResolvedModel == "" {
				rc.ResolvedModel = chunk.Model
			}
			if chunk.Usage != nil {
				lastUsage = chunk.Usage
			}

			// Run streaming guardrail check.
			if streamChecker != nil {
				if res := streamChecker.ProcessChunk(streamCtx, chunk); res.Blocked {
					rc.Flags.GuardrailTriggered = true
					sseWriter.WriteError(models.ErrGuardrailBlocked("stream_blocked", res.Message))
					finalizeStream(false)
					return
				}
			}

			if err := sseWriter.WriteChunk(chunk); err != nil {
				slog.Warn("error writing stream chunk",
					"request_id", rc.RequestID,
					"error", err,
				)
				finalizeStream(false)
				return
			}

		case err, ok := <-errCh:
			if ok && err != nil {
				if apiErr, isAPI := err.(*models.APIError); isAPI {
					sseWriter.WriteError(apiErr)
				} else {
					sseWriter.WriteError(models.ErrInternal(err.Error()))
				}
				finalizeStream(false)
				return
			}

		case <-ctx.Done():
			// Client disconnected — streamCancel() in defer will stop guardrail goroutines.
			// Drain remaining chunks to unblock the provider goroutine.
			go func() {
				for range chunks {
				}
			}()
			// Run post-plugins synchronously with detached context to avoid
			// data race with defer rc.Release() in the caller.
			finalizeStream(true)
			return
		}
	}
}

func (h *Handlers) buildStreamingMetadataChunk(rc *models.RequestContext, streamID string, streamCreated int64, usage *models.Usage) *models.StreamChunk {
	model := rc.ResolvedModel
	if model == "" && rc.Request != nil {
		model = rc.Request.Model
	}
	if streamID == "" {
		streamID = rc.RequestID
	}
	if streamCreated == 0 {
		streamCreated = time.Now().Unix()
	}

	var cost float64
	if rawCost, ok := rc.Metadata["cost"]; ok {
		if parsed, err := strconv.ParseFloat(rawCost, 64); err == nil {
			cost = parsed
		}
	}

	chunk := &models.StreamChunk{
		ID:      streamID,
		Object:  "chat.completion.chunk",
		Created: streamCreated,
		Model:   model,
		Choices: []models.StreamChoice{},
		Usage:   usage,
	}
	if include := rc.RequestHeaders.Get("x-agentcc-include-metadata"); include == "1" || strings.EqualFold(include, "true") {
		chunk.AgentccMetadata = &models.AgentccStreamMetadata{
			Cost:      cost,
			LatencyMs: rc.Elapsed().Milliseconds(),
		}
	}
	return chunk
}

func (h *Handlers) setAgentccHeaders(w http.ResponseWriter, rc *models.RequestContext) {
	w.Header().Set("x-agentcc-provider", rc.Provider)
	if rc.ResolvedModel != "" {
		w.Header().Set("x-agentcc-model-used", rc.ResolvedModel)
	}

	latency := rc.Elapsed()
	w.Header().Set("x-agentcc-latency-ms", latencyString(latency))

	if cost, ok := rc.Metadata["cost"]; ok {
		w.Header().Set("x-agentcc-cost", cost)
	}
	if status, ok := rc.Metadata["cache_status"]; ok && status != "" {
		w.Header().Set("x-agentcc-cache", status)
	} else if rc.Flags.CacheHit {
		w.Header().Set("x-agentcc-cache", "hit")
	}
	if rc.Flags.GuardrailTriggered {
		w.Header().Set("x-agentcc-guardrail-triggered", "true")
	}
	if rc.Flags.FallbackUsed {
		w.Header().Set("x-agentcc-fallback-used", "true")
	}
	if strategy, ok := rc.Metadata["routing_strategy"]; ok {
		w.Header().Set("x-agentcc-routing-strategy", strategy)
	}
	if timeout, ok := rc.Metadata["timeout_ms"]; ok {
		w.Header().Set("x-agentcc-timeout-ms", timeout)
	}

	// Advanced routing headers.
	if _, ok := rc.Metadata["provider_locked"]; ok {
		w.Header().Set("x-agentcc-provider-locked", rc.Metadata["provider_lock_target"])
	}
	if tier, ok := rc.Metadata["complexity_tier"]; ok {
		w.Header().Set("x-agentcc-complexity-tier", tier)
	}
	if winner, ok := rc.Metadata["race_winner"]; ok {
		w.Header().Set("x-agentcc-race-winner", winner)
	}

	// Credit headers for managed keys.
	if v, ok := rc.Metadata["credits_used"]; ok {
		w.Header().Set("x-agentcc-credits-used", v)
	}
	if v, ok := rc.Metadata["credits_remaining"]; ok {
		w.Header().Set("x-agentcc-credits-remaining", v)
	}

	// Rate limit headers.
	if v, ok := rc.Metadata["ratelimit_limit"]; ok {
		w.Header().Set("x-ratelimit-limit-requests", v)
	}
	if v, ok := rc.Metadata["ratelimit_remaining"]; ok {
		w.Header().Set("x-ratelimit-remaining-requests", v)
	}
	if v, ok := rc.Metadata["ratelimit_reset"]; ok {
		w.Header().Set("x-ratelimit-reset-requests", v)
	}
}

func latencyString(d time.Duration) string {
	return strconv.FormatInt(d.Milliseconds(), 10)
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

// isBlockedMetadataKey returns true for metadata keys that must not be
// set by external callers via x-agentcc-metadata header. These keys are
// reserved for internal use by auth, budget, rate-limiting, and other plugins.
func isBlockedMetadataKey(key string) bool {
	lower := strings.ToLower(key)
	for _, prefix := range []string{
		"auth_", "key_", "org_", "budget_", "ratelimit_",
		"cost", "cache_", "guardrail_", "credit_",
	} {
		if strings.HasPrefix(lower, prefix) {
			return true
		}
	}
	// Block specific keys that don't follow a prefix pattern.
	switch lower {
	case "authorization", "client_ip", "timeout_ms":
		return true
	}
	return false
}

// extractClientIP extracts the client IP address from the request.
// It checks X-Forwarded-For, X-Real-IP, and falls back to RemoteAddr.
func extractClientIP(r *http.Request) string {
	// Check X-Forwarded-For (may contain multiple IPs: client, proxy1, proxy2).
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		parts := strings.Split(xff, ",")
		if ip := strings.TrimSpace(parts[0]); ip != "" {
			return ip
		}
	}

	// Check X-Real-IP.
	if xri := r.Header.Get("X-Real-IP"); xri != "" {
		return xri
	}

	// Fall back to RemoteAddr (strip port).
	addr := r.RemoteAddr
	if idx := strings.LastIndex(addr, ":"); idx > 0 {
		// Handle IPv6 in brackets: [::1]:8080
		if addr[0] == '[' {
			if bracketIdx := strings.Index(addr, "]"); bracketIdx > 0 {
				return addr[1:bracketIdx]
			}
		}
		return addr[:idx]
	}
	return addr
}

// extractBodySessionID extracts session_id from the request body's "metadata"
// field (which lands in Extra for chat completion requests).
func extractBodySessionID(extra map[string]json.RawMessage) string {
	if extra == nil {
		return ""
	}
	raw, ok := extra["metadata"]
	if !ok {
		return ""
	}
	var meta map[string]interface{}
	if err := json.Unmarshal(raw, &meta); err != nil {
		return ""
	}
	if sid, ok := meta["session_id"].(string); ok && sid != "" {
		return sid
	}
	return ""
}

// ModelDB returns the current model database.
func (h *Handlers) ModelDB() *modeldb.ModelDB {
	if h.modelDB == nil {
		return nil
	}
	return h.modelDB.Load()
}

// ListModels handles GET /v1/models.
func (h *Handlers) ListModels(w http.ResponseWriter, r *http.Request) {
	allModels := h.registry.ListAllModels()
	db := h.ModelDB()

	if scopedModels, ok := h.orgScopedModels(r); ok {
		allModels = mergeModelObjects(allModels, scopedModels)
	}

	enriched := make([]models.EnrichedModelObject, 0, len(allModels))
	for _, m := range allModels {
		em := models.EnrichedModelObject{
			ID:      m.ID,
			Object:  m.Object,
			Created: m.Created,
			OwnedBy: m.OwnedBy,
		}

		if db != nil {
			if info, ok := db.Get(m.ID); ok {
				em.Mode = string(info.Mode)
				em.MaxInputTokens = info.MaxInputTokens
				em.MaxOutputTokens = info.MaxOutputTokens
				if info.DeprecationDate != "" {
					em.Deprecated = true
				}
				if info.Pricing.HasPricing() {
					em.Pricing = &models.ModelPricingAPI{
						InputPerMTokens:       info.Pricing.InputPerMTok(),
						OutputPerMTokens:      info.Pricing.OutputPerMTok(),
						CachedInputPerMTokens: info.Pricing.CachedInputPerToken * 1_000_000,
					}
				}
				em.Capabilities = &models.ModelCapsAPI{
					Vision:          info.Capabilities.Vision,
					FunctionCalling: info.Capabilities.FunctionCalling,
					Streaming:       info.Capabilities.Streaming,
					ResponseSchema:  info.Capabilities.ResponseSchema,
					PromptCaching:   info.Capabilities.PromptCaching,
					Reasoning:       info.Capabilities.Reasoning,
				}
			}
		}

		enriched = append(enriched, em)
	}

	resp := models.EnrichedModelListResponse{
		Object: "list",
		Data:   enriched,
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(resp)
}

func (h *Handlers) orgScopedModels(r *http.Request) ([]models.ModelObject, bool) {
	if h.tenantStore == nil || h.keyStore == nil {
		return nil, false
	}
	rawKey := ""
	if authHeader := r.Header.Get("Authorization"); strings.HasPrefix(authHeader, "Bearer ") {
		rawKey = strings.TrimPrefix(authHeader, "Bearer ")
	} else if apiKey := r.Header.Get("x-api-key"); apiKey != "" {
		rawKey = apiKey
	}
	if rawKey == "" {
		return nil, false
	}
	ki := h.keyStore.Lookup(rawKey)
	if ki == nil {
		return nil, false
	}
	orgID := ki.Metadata["org_id"]
	if orgID == "" {
		return nil, false
	}
	orgCfg := h.tenantStore.Get(orgID)
	if orgCfg == nil || orgCfg.Providers == nil {
		return nil, false
	}

	seen := make(map[string]bool)
	result := make([]models.ModelObject, 0)
	for providerName, pc := range orgCfg.Providers {
		if !pc.Enabled {
			continue
		}
		for _, modelID := range pc.Models {
			if seen[modelID] {
				continue
			}
			seen[modelID] = true
			result = append(result, models.ModelObject{
				ID:      modelID,
				Object:  "model",
				OwnedBy: providerName,
			})
		}
	}
	if len(result) == 0 {
		return nil, false
	}
	return result, true
}

// GetModel handles GET /v1/models/{model}.
func (h *Handlers) GetModel(w http.ResponseWriter, r *http.Request) {
	modelID := r.URL.Query().Get("model")
	if modelID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_model", "model ID is required"))
		return
	}

	if scopedModels, ok := h.orgScopedModels(r); ok {
		for _, m := range scopedModels {
			if m.ID == modelID {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusOK)
				json.NewEncoder(w).Encode(m)
				return
			}
		}
	}

	// Check global registry for non-tenant deployments.
	allModels := h.registry.ListAllModels()
	for _, m := range allModels {
		if m.ID == modelID {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(m)
			return
		}
	}

	models.WriteError(w, models.ErrNotFound("model_not_found", "Model '"+modelID+"' not found"))
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
