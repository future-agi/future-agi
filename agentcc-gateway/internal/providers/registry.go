package providers

import (
	"encoding/json"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"reflect"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers/anthropic"
	"github.com/futureagi/agentcc-gateway/internal/providers/azure"
	"github.com/futureagi/agentcc-gateway/internal/providers/bedrock"
	"github.com/futureagi/agentcc-gateway/internal/providers/cohere"
	"github.com/futureagi/agentcc-gateway/internal/providers/gemini"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/providers/speech"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// Registry manages provider instances and model-to-provider mapping.
type Registry struct {
	mu             sync.RWMutex
	providers      map[string]Provider
	modelMap       map[string]string   // model name → provider ID
	modelProviders map[string][]string // model name → all provider IDs that serve it
	router         *routing.Router     // nil if no routing configured
	managed        map[string]ManagedProvider

	// configs holds each config.yaml provider's config as its instance was
	// built from it (presets, local defaults and discovered models applied),
	// so key rotation can rebuild the instance with a new key.
	configs map[string]config.ProviderConfig

	// retired maps each instance a key rotation replaced to its provider id,
	// so CurrentInstance can hand work that captured it the instance serving
	// now. One entry per promote; an operator action, so it stays small.
	retired map[Provider]string
}

// ManagedProvider is a global provider FutureAGI supplies the credentials
// for. Orgs that opt in to managed models may use it.
type ManagedProvider struct {
	ID   string
	Type string // the config.yaml type, e.g. "openrouter"; selects the catalog format
	// CatalogURL is where the provider lists its models.
	CatalogURL string
}

// NewRegistry creates a registry from config.
func NewRegistry(cfg *config.Config) (*Registry, error) {
	r := &Registry{
		providers:      make(map[string]Provider),
		modelMap:       make(map[string]string),
		modelProviders: make(map[string][]string),
		managed:        make(map[string]ManagedProvider),
		configs:        make(map[string]config.ProviderConfig),
		retired:        make(map[Provider]string),
	}

	for name, pcfg := range cfg.Providers {
		// Apply provider presets (auto-fill base_url, api_format from type).
		applyProviderPreset(&pcfg)

		// Apply optimized defaults for local/self-hosted providers.
		applyLocalDefaults(&pcfg)

		// Auto-discover models if list is empty and discovery is enabled.
		if discovered := autoDiscoverModels(name, &pcfg); len(discovered) > 0 {
			pcfg.Models = append(pcfg.Models, discovered...)
		}

		p, err := createProvider(name, pcfg)
		if err != nil {
			return nil, fmt.Errorf("creating provider %q: %w", name, err)
		}
		r.providers[name] = p
		r.configs[name] = pcfg
		if pcfg.Managed {
			r.managed[name] = ManagedProvider{
				ID:         name,
				Type:       pcfg.Type,
				CatalogURL: config.JoinEndpoint(strings.TrimRight(pcfg.BaseURL, "/"), pcfg.EffectiveAPIPathPrefix(), "/v1/models"),
			}
		}

		attrs := []any{"name", name, "format", pcfg.APIFormat, "base_url", pcfg.BaseURL}
		if pcfg.Managed {
			attrs = append(attrs, "managed", true)
		}
		if IsLocalProvider(pcfg) {
			attrs = append(attrs, "local", true)
		}
		slog.Info("provider registered", attrs...)

		// Register models from provider config.
		for _, model := range pcfg.Models {
			r.modelMap[model] = name
			r.modelProviders[model] = append(r.modelProviders[model], name)
		}

		// Non-blocking connectivity check for local providers.
		if IsLocalProvider(pcfg) {
			go connectivityCheck(name, pcfg.BaseURL, pcfg.EffectiveAPIPathPrefix())
		}
	}

	// Apply explicit model map from config (overrides provider-level).
	for model, providerID := range cfg.ModelMap {
		r.modelMap[model] = providerID
	}

	// Create router if routing config is present or if models have multiple providers.
	if cfg.Routing.DefaultStrategy != "" || len(cfg.Routing.Targets) > 0 || r.hasMultiProviderModels() {
		providerIDs := make(map[string]bool, len(r.providers))
		for id := range r.providers {
			providerIDs[id] = true
		}
		router, err := routing.NewRouter(cfg.Routing, providerIDs, r.modelProviders)
		if err != nil {
			return nil, fmt.Errorf("creating router: %w", err)
		}
		r.router = router
	}

	slog.Info("provider registry initialized",
		"providers", len(r.providers),
		"models_mapped", len(r.modelMap),
		"router_enabled", r.router != nil,
	)

	return r, nil
}

// hasMultiProviderModels returns true if any model has more than one provider.
func (r *Registry) hasMultiProviderModels() bool {
	for _, providers := range r.modelProviders {
		if len(providers) > 1 {
			return true
		}
	}
	return false
}

func createProvider(name string, cfg config.ProviderConfig) (Provider, error) {
	switch cfg.APIFormat {
	case "openai":
		return openai.New(name, cfg)
	case "anthropic":
		return anthropic.New(name, cfg)
	case "gemini", "google":
		return gemini.New(name, cfg)
	case "bedrock":
		return bedrock.New(name, cfg)
	case "cohere":
		return cohere.New(name, cfg)
	case "azure":
		return azure.New(name, cfg)
	case "deepgram", "cartesia":
		return speech.New(name, cfg)
	default:
		return nil, fmt.Errorf("unsupported api_format %q (supported: openai, anthropic, gemini, bedrock, cohere, azure, deepgram, cartesia)", cfg.APIFormat)
	}
}

// RegisterProvider adds a provider at runtime (e.g., a2a provider for agent delegation).
// Models are registered with the "providerID/" prefix for resolution.
func (r *Registry) RegisterProvider(id string, p Provider, modelNames []string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.providers[id] = p
	for _, model := range modelNames {
		r.modelMap[model] = id
		r.modelProviders[model] = append(r.modelProviders[model], id)
	}
	slog.Info("provider registered (runtime)", "id", id, "models", len(modelNames))
}

// GetProvider returns a specific provider by ID.
func (r *Registry) GetProvider(id string) (Provider, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	p, ok := r.providers[id]
	return p, ok
}

// ProviderIDs returns all registered provider IDs.
func (r *Registry) ProviderIDs() []string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	ids := make([]string, 0, len(r.providers))
	for id := range r.providers {
		ids = append(ids, id)
	}
	return ids
}

// IsManaged reports whether id is a managed provider.
func (r *Registry) IsManaged(id string) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	_, ok := r.managed[id]
	return ok
}

// ManagedProviders returns the managed providers, sorted by ID.
func (r *Registry) ManagedProviders() []ManagedProvider {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]ManagedProvider, 0, len(r.managed))
	for _, mp := range r.managed {
		out = append(out, mp)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// ResolveManaged finds the managed provider that serves model and the model
// ID to send it. In order: a model the config maps to a managed provider; a
// model in a managed provider's catalog (inCatalog); "<managed id>/<model>",
// sent upstream as <model>. ok is false when no managed provider serves it.
func (r *Registry) ResolveManaged(model string, inCatalog func(providerID, model string) bool) (p Provider, upstreamModel string, ok bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if len(r.managed) == 0 {
		return nil, "", false
	}
	if id, found := r.modelMap[model]; found {
		if _, managed := r.managed[id]; managed {
			return r.providers[id], model, true
		}
	}
	if inCatalog != nil {
		for _, id := range r.sortedManagedIDs() {
			if inCatalog(id, model) {
				return r.providers[id], model, true
			}
		}
	}
	if idx := strings.Index(model, "/"); idx > 0 && idx < len(model)-1 {
		if _, managed := r.managed[model[:idx]]; managed {
			return r.providers[model[:idx]], model[idx+1:], true
		}
	}
	return nil, "", false
}

func (r *Registry) sortedManagedIDs() []string {
	ids := make([]string, 0, len(r.managed))
	for id := range r.managed {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// ResolveResult contains the resolved provider and optional routing metadata.
type ResolveResult struct {
	Provider      Provider
	StrategyName  string // Non-empty if routing was used
	ModelOverride string // Non-empty if model should be overridden for this provider
}

// Resolve finds the provider for a given model name.
func (r *Registry) Resolve(model string) (Provider, error) {
	result, err := r.ResolveWithRouting(model)
	if err != nil {
		return nil, err
	}
	return result.Provider, nil
}

// ResolveWithRouting finds the provider for a model, using load balancing if available.
func (r *Registry) ResolveWithRouting(model string) (*ResolveResult, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	// Try routing first if the model has routing targets. A single target
	// routes too: it can name the provider and the model_override to send.
	if r.router != nil && r.router.TargetCount(model) > 0 {
		var (
			routingResult *routing.RoutingResult
			err           error
		)

		_, explicitModelRequested := r.modelMap[model]
		if explicitModelRequested {
			routingResult, err = r.router.SelectExactModel(model)
		} else {
			routingResult, err = r.router.Select(model)
		}
		if err == nil {
			if p, ok := r.providers[routingResult.Target.ProviderID]; ok {
				return &ResolveResult{
					Provider:      p,
					StrategyName:  routingResult.StrategyName,
					ModelOverride: routingResult.Target.ModelOverride,
				}, nil
			}
			return nil, models.ErrInternal(fmt.Sprintf("routed to provider %q but not found", routingResult.Target.ProviderID))
		}

		if !explicitModelRequested {
			return nil, models.ErrServiceUnavailable(err.Error())
		}
	}

	// Direct lookup.
	if providerID, ok := r.modelMap[model]; ok {
		if p, ok := r.providers[providerID]; ok {
			return &ResolveResult{Provider: p}, nil
		}
		return nil, models.ErrInternal(fmt.Sprintf("provider %q configured for model %q but not found", providerID, model))
	}

	// Try prefix match: "openai/gpt-4o" → provider "openai", model "gpt-4o".
	if idx := strings.Index(model, "/"); idx > 0 {
		providerID := model[:idx]
		if p, ok := r.providers[providerID]; ok {
			return &ResolveResult{Provider: p}, nil
		}
	}

	// If only one provider is configured, use it as default.
	if len(r.providers) == 1 {
		for _, p := range r.providers {
			return &ResolveResult{Provider: p}, nil
		}
	}

	return nil, models.ErrNotFound("model_not_found",
		fmt.Sprintf("model %q not found in any configured provider. Configure model_map or use 'provider/model' format.", model))
}

// ResolveModelName extracts the actual model name (strips provider prefix if present).
func ResolveModelName(model string) string {
	if idx := strings.Index(model, "/"); idx > 0 {
		return model[idx+1:]
	}
	return model
}

// ListAllModels aggregates models from all providers.
func (r *Registry) ListAllModels() []models.ModelObject {
	r.mu.RLock()
	defer r.mu.RUnlock()

	var all []models.ModelObject

	for model := range r.modelMap {
		all = append(all, models.ModelObject{
			ID:      model,
			Object:  "model",
			Created: 0,
			OwnedBy: r.modelMap[model],
		})
	}

	return all
}

// Close shuts down all providers.
func (r *Registry) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()

	for name, p := range r.providers {
		if err := p.Close(); err != nil {
			slog.Warn("error closing provider", "name", name, "error", err)
		}
	}
	return nil
}

// ProviderCount returns the number of registered providers.
func (r *Registry) ProviderCount() int {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return len(r.providers)
}

// Router returns the load balancing router, or nil if not configured.
func (r *Registry) Router() *routing.Router {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.router
}

// IsLocalProvider returns true if the provider is targeting a local/self-hosted endpoint.
func IsLocalProvider(cfg config.ProviderConfig) bool {
	if cfg.Local != nil {
		return *cfg.Local
	}
	return isLocalURL(cfg.BaseURL)
}

// isLocalURL checks if a URL points to a local or private network address.
func isLocalURL(rawURL string) bool {
	u, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	host := u.Hostname()

	if host == "localhost" || host == "::1" {
		return true
	}

	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}

	// Check loopback (127.x.x.x) and private ranges (10.x, 172.16-31.x, 192.168.x).
	return ip.IsLoopback() || ip.IsPrivate()
}

// applyLocalDefaults sets optimized defaults for local/self-hosted providers.
// Only sets values that are zero (not explicitly configured).
func applyLocalDefaults(cfg *config.ProviderConfig) {
	if !IsLocalProvider(*cfg) {
		return
	}
	if cfg.DefaultTimeout == 0 {
		cfg.DefaultTimeout = 120 * time.Second
	}
	if cfg.MaxConcurrent == 0 {
		cfg.MaxConcurrent = 10
	}
	if cfg.ConnPoolSize == 0 {
		cfg.ConnPoolSize = 10
	}
}

// autoDiscoverModels calls GET /v1/models to discover available models from a provider.
// Returns discovered model IDs, or nil if discovery is disabled or fails.
func autoDiscoverModels(name string, cfg *config.ProviderConfig) []string {
	// Determine if auto-discover is enabled.
	if cfg.AutoDiscover != nil {
		if !*cfg.AutoDiscover {
			return nil
		}
	} else if len(cfg.Models) > 0 {
		// Models already configured; skip auto-discovery unless explicitly requested.
		return nil
	}

	client := &http.Client{Timeout: 5 * time.Second}
	baseURL := strings.TrimRight(cfg.BaseURL, "/")
	// Built the same way the provider builds its own URLs, or a provider that
	// serves no /v1 would be probed at a path it does not have.
	modelsURL := config.JoinEndpoint(baseURL, cfg.EffectiveAPIPathPrefix(), "/v1/models")
	resp, err := client.Get(modelsURL)
	if err != nil {
		slog.Warn("auto-discover: failed to reach provider",
			"provider", name, "url", modelsURL, "error", err)
		return nil
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		slog.Warn("auto-discover: unexpected status from provider",
			"provider", name, "status", resp.StatusCode)
		return nil
	}

	var body struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		slog.Warn("auto-discover: failed to parse models response",
			"provider", name, "error", err)
		return nil
	}

	modelIDs := make([]string, 0, len(body.Data))
	for _, m := range body.Data {
		if m.ID != "" {
			modelIDs = append(modelIDs, m.ID)
		}
	}

	if len(modelIDs) > 0 {
		slog.Info("auto-discover: found models",
			"provider", name, "count", len(modelIDs), "models", modelIDs)
	}
	return modelIDs
}

// connectivityCheck performs a non-blocking health check against a provider.
// Runs in a goroutine; logs result but never blocks startup.
func connectivityCheck(name, baseURL, pathPrefix string) {
	client := &http.Client{Timeout: 3 * time.Second}
	url := config.JoinEndpoint(baseURL, pathPrefix, "/v1/models")
	resp, err := client.Get(url)
	if err != nil {
		slog.Warn("connectivity check: provider unreachable (may start later)",
			"provider", name, "url", url, "error", err)
		return
	}
	resp.Body.Close()
	slog.Info("connectivity check: provider reachable", "provider", name, "status", resp.StatusCode)
}

// ReloadRouter rebuilds the load balancing router from new routing config.
func (r *Registry) ReloadRouter(routingCfg config.RoutingConfig) error {
	r.mu.RLock()
	providerIDs := make(map[string]bool, len(r.providers))
	for id := range r.providers {
		providerIDs[id] = true
	}
	modelProviders := r.modelProviders
	r.mu.RUnlock()

	// Build new router (may be nil if no routing config).
	var newRouter *routing.Router
	if routingCfg.DefaultStrategy != "" || len(routingCfg.Targets) > 0 {
		var err error
		newRouter, err = routing.NewRouter(routingCfg, providerIDs, modelProviders)
		if err != nil {
			return err
		}
	}

	r.mu.Lock()
	r.router = newRouter
	r.mu.Unlock()
	return nil
}

// RotationCandidate builds, without registering it, an instance of the
// config.yaml provider id that authenticates with key instead of its
// configured one, for a key rotation to validate the key on before it is
// applied. It also returns the provider's api_format and models. The caller
// closes the instance.
func (r *Registry) RotationCandidate(id, key string) (p Provider, apiFormat string, models []string, err error) {
	r.mu.RLock()
	pcfg, ok := r.configs[id]
	r.mu.RUnlock()
	if !ok {
		return nil, "", nil, fmt.Errorf("provider %q is not a config.yaml provider", id)
	}
	pcfg.APIKey = key
	p, err = createProvider(id, pcfg)
	if err != nil {
		return nil, "", nil, err
	}
	return p, pcfg.APIFormat, append([]string(nil), pcfg.Models...), nil
}

// RotateProviderKey rebuilds the config.yaml provider id from its config with
// key and swaps it in, under the registry lock: every lookup from then on
// returns the new instance, so every new upstream call (a retry included,
// since each attempt looks the provider up) sends key. A call already in
// flight finishes on the instance it holds. It returns that previous
// instance, which the caller closes once its drain period has passed.
func (r *Registry) RotateProviderKey(id, key string) (Provider, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	pcfg, ok := r.configs[id]
	if !ok {
		return nil, fmt.Errorf("provider %q is not a config.yaml provider", id)
	}
	old, ok := r.providers[id]
	if !ok {
		return nil, fmt.Errorf("provider %q is not registered", id)
	}
	pcfg.APIKey = key
	p, err := createProvider(id, pcfg)
	if err != nil {
		return nil, err
	}
	r.providers[id] = p
	r.configs[id] = pcfg
	if usableAsKey(old) {
		r.retired[old] = id
	}
	return old, nil
}

// CurrentInstance returns the instance that serves p's provider now when p is
// one a key rotation replaced (through any number of later rotations), and p
// unchanged otherwise: the current instance, an org's own provider (built
// apart from the registry with the org's key), a rotation candidate, nil.
// Work that captured an instance and runs later, such as an async job queued
// before a promote, calls it when it starts, so it sends the key in force
// then and never a rotated-out one, while an org's key stays the org's.
func (r *Registry) CurrentInstance(p Provider) Provider {
	if !usableAsKey(p) {
		return p
	}
	r.mu.RLock()
	defer r.mu.RUnlock()
	id, ok := r.retired[p]
	if !ok {
		return p
	}
	if cur, ok := r.providers[id]; ok {
		return cur
	}
	return p
}

// usableAsKey reports whether p can be a map key: every provider this
// registry builds is a pointer, but RegisterProvider takes any Provider, and
// a map lookup with an uncomparable dynamic type panics.
func usableAsKey(p Provider) bool {
	return p != nil && reflect.TypeOf(p).Comparable()
}
