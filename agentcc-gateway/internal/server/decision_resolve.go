package server

import (
	"context"
	"crypto/sha256"
	"fmt"
	"log/slog"
	"net/http"
	"slices"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/middleware"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/systemone"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Third-party decision models run only on the customer's own key. Nothing in
// this file reaches a provider that holds a Future AGI key: it never calls
// resolveProvider, so no registry fallback, conditional route, model fallback,
// failover or mirror applies to a decision call.

// decisionTarget is where one decision call goes.
type decisionTarget struct {
	providerID    string
	upstreamModel string
	send          func(ctx context.Context, req *models.DecisionRequest) (*models.DecisionResponse, error)
}

// decisionCandidate is a provider that lists the requested model.
type decisionCandidate struct {
	id       string
	provider providers.Provider
	// format is the decision format the provider speaks, or "" for none.
	format string
	// apiFormat is the api_format it was saved with, for the error that
	// explains why it cannot serve a decision. "" when not known.
	apiFormat string
}

// resolveDecision picks the provider and key for a decision call. In order:
//
//  1. A licence token is refused: decision models are never a managed service.
//  2. With auth off (a self-host with no keystore) only an exact registry match
//     is used; config.yaml's key there is the operator's, who is the customer.
//  3. An internal key must carry the caller's own key in x-agentcc-provider-key
//     and name the vendor as the model prefix. That key is used for this one
//     call; config.yaml's key for the same vendor never is.
//  4. Any other key resolves to its org's own providers only, and may not send
//     x-agentcc-provider-key.
func (h *Handlers) resolveDecision(ctx context.Context, header http.Header, rc *models.RequestContext, req *models.DecisionRequest, providerKey string, hasProviderKey bool, orgID string, orgCfg *tenant.OrgConfig) (*decisionTarget, error) {
	if middleware.IsLicenseAuthorized(ctx) || rc.Metadata["auth_type"] == "license" {
		return nil, errBYOKRequired("Decision models run only on your own provider key; a managed service token cannot call them.")
	}
	if h.keyStore == nil {
		if hasProviderKey {
			return nil, errProviderKeyHeaderNotAllowed()
		}
		return h.registryDecisionTarget(rc, header, req)
	}

	key := h.lookupRequestKey(rc)
	if key == nil {
		return nil, models.ErrUnauthorized("Invalid or missing API key")
	}
	if key.KeyType == "internal" {
		if !hasProviderKey || providerKey == "" {
			return nil, errBYOKRequired("Decision models run only on the customer's own provider key. " +
				"An internal caller must send that key in " + models.ProviderKeyHeader + ".")
		}
		return h.internalDecisionTarget(req, providerKey)
	}
	if hasProviderKey {
		return nil, errProviderKeyHeaderNotAllowed()
	}
	return h.orgDecisionTarget(rc, header, req, orgID, orgCfg)
}

// internalDecisionTarget serves an internal caller with the key it brought.
// The vendor is the model's prefix; its upstream is the dialect's own, or the
// base_url of a config.yaml provider of that dialect. Nothing else of that
// provider is used: not its api_key, headers or prefix.
func (h *Handlers) internalDecisionTarget(req *models.DecisionRequest, providerKey string) (*decisionTarget, error) {
	prefix, upstream, ok := strings.Cut(req.Model, "/")
	dialect := strings.ToLower(strings.TrimSpace(prefix))
	format, known := providers.DecisionDialectFormat(dialect)
	if !ok || upstream == "" || !known {
		return nil, models.ErrBadRequest("decision_model_prefix_required",
			"model must name its vendor: typesafe/<model> or perplexity/<model> for System One, openai/<model> for OpenAI Decisions")
	}
	if format != req.Format {
		return nil, errFormatMismatch(req.Model, dialect, format)
	}
	p, err := h.keyedDecisionProviders.get(dialect, h.decisionBaseURLs[dialect], providerKey)
	if err != nil {
		// The error names the dialect and never the key.
		slog.Warn("could not build decision provider for internal caller", "dialect", dialect, "error", err)
		return nil, models.ErrInternal("could not set up the decision provider")
	}
	return newDecisionTarget(dialect, upstream, p, format), nil
}

// orgDecisionProviderEnabled allows keyless endpoints only for Generic System
// One with an explicit, valid org URL. Known vendors still require BYOK.
func (h *Handlers) orgDecisionProviderEnabled(id string, cfg *tenant.ProviderConfig) bool {
	if cfg == nil || !cfg.Enabled {
		return false
	}
	if cfg.HasCredentials() {
		return true
	}
	if cfg.BaseURL == "" || h.orgProviderCache == nil {
		return false
	}
	format, ok := h.orgProviderCache.APIFormat(id, cfg)
	if !ok || !providers.IsSystemOneFormat(format) {
		return false
	}
	dialect, err := systemone.DialectFor(id, cfg.BaseURL)
	return err == nil && !dialect.KeyRequired
}

// orgDecisionTarget serves a byok or managed key from its org's own providers:
// those enabled with credentials (or keyless Generic endpoints) that list the
// model, in provider ID order.
// When none does but one holds the model's vendor key, the refusal says to add
// the model to it, not to add a key the org already has.
func (h *Handlers) orgDecisionTarget(rc *models.RequestContext, header http.Header, req *models.DecisionRequest, orgID string, orgCfg *tenant.OrgConfig) (*decisionTarget, error) {
	if orgID == "" || orgCfg == nil || h.orgProviderCache == nil {
		return nil, errDecisionNotConfigured(req.Model)
	}
	ids := make([]string, 0, len(orgCfg.Providers))
	for id := range orgCfg.Providers {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	only, err := h.decisionNarrowing(rc, header, req.Model, ids)
	if err != nil {
		return nil, err
	}

	var candidates []decisionCandidate
	var failedID, unlistedID string
	var buildErr error
	vendor := decisionVendor(req.Model)
	for _, id := range ids {
		pcfg := orgCfg.Providers[id]
		if (only != "" && id != only) || !h.orgDecisionProviderEnabled(id, pcfg) {
			continue
		}
		if !listsModel(pcfg.Models, req.Model, id) {
			// The request names this provider, or it holds the model's vendor key.
			if unlistedID == "" && (only != "" || orgProviderVendor(id, pcfg.BaseURL) == vendor) {
				unlistedID = id
			}
			continue
		}
		p, err := h.orgProviderCache.GetOrCreateWithTenantConfig(orgID, id, pcfg.APIKey, pcfg)
		if err != nil {
			slog.Warn("failed to create org decision provider", "org_id", orgID, "provider", id, "error", err)
			if buildErr == nil {
				failedID, buildErr = id, err
			}
			continue
		}
		apiFormat := pcfg.APIFormat
		if apiFormat == "" {
			apiFormat = "unset"
		}
		candidates = append(candidates, decisionCandidate{
			id: id, provider: p, apiFormat: apiFormat,
			format: decisionFormatOf(p, id, pcfg.BaseURL),
		})
	}
	if len(candidates) == 0 {
		if buildErr != nil {
			return nil, orgProviderError(req.Model, failedID, buildErr)
		}
		if unlistedID != "" {
			return nil, errDecisionModelNotListed(req.Model, unlistedID, orgProviderVendor(unlistedID, orgCfg.Providers[unlistedID].BaseURL))
		}
		return nil, errDecisionNotConfigured(req.Model)
	}
	return chooseDecisionCandidate(req, candidates)
}

// registryDecisionTarget serves a gateway with auth off from config.yaml's
// providers that list the model exactly, or "<provider>/<model>".
func (h *Handlers) registryDecisionTarget(rc *models.RequestContext, header http.Header, req *models.DecisionRequest) (*decisionTarget, error) {
	ids := h.registry.ProviderIDs()
	sort.Strings(ids)
	only, err := h.decisionNarrowing(rc, header, req.Model, ids)
	if err != nil {
		return nil, err
	}
	exact := h.registry.ProvidersFor(req.Model)
	var bare []string
	prefix, bareModel, hasPrefix := strings.Cut(req.Model, "/")
	if hasPrefix {
		bare = h.registry.ProvidersFor(bareModel)
	}

	var candidates []decisionCandidate
	for _, id := range ids {
		if only != "" && id != only {
			continue
		}
		if !slices.Contains(exact, id) && !(hasPrefix && strings.EqualFold(prefix, id) && slices.Contains(bare, id)) {
			continue
		}
		p, ok := h.registry.GetProvider(id)
		if !ok {
			continue
		}
		candidates = append(candidates, decisionCandidate{id: id, provider: p, format: decisionFormatOf(p, id, "")})
	}
	if len(candidates) == 0 {
		return nil, models.ErrNotFound("model_not_found",
			fmt.Sprintf("model %q is not listed by any decision provider in the gateway config", req.Model))
	}
	return chooseDecisionCandidate(req, candidates)
}

// decisionNarrowing returns the one provider a request is narrowed to, or ""
// for none: a provider lock (header or metadata) if there is one, else the
// provider a "<provider>/" model prefix names. A lock only filters the
// request's own providers; one naming any other provider is refused.
func (h *Handlers) decisionNarrowing(rc *models.RequestContext, header http.Header, model string, ids []string) (string, error) {
	if h.providerLockResolver.IsEnabled() {
		if lock := h.providerLockResolver.ExtractLock(rc, header); lock != "" {
			if err := h.providerLockResolver.Validate(lock); err != nil {
				return "", models.ErrForbidden(err.Error())
			}
			if !slices.Contains(ids, lock) {
				return "", &models.APIError{
					Status:  http.StatusForbidden,
					Type:    models.ErrTypePermission,
					Code:    "provider_lock_not_allowed",
					Message: fmt.Sprintf("Provider lock %q names a provider this API key cannot use for decisions; decisions run only on your organization's own providers.", lock),
				}
			}
			return lock, nil
		}
	}
	if prefix, _, ok := strings.Cut(model, "/"); ok {
		for _, id := range ids {
			if strings.EqualFold(id, prefix) {
				return id, nil
			}
		}
	}
	return "", nil
}

// chooseDecisionCandidate returns the first candidate that speaks the body's
// format. When none does, it says why: another format (the caller sent the
// body to the wrong route, and nothing translates) or no decision format at
// all (the provider was saved with the wrong api_format).
func chooseDecisionCandidate(req *models.DecisionRequest, candidates []decisionCandidate) (*decisionTarget, error) {
	for _, c := range candidates {
		if c.format == req.Format {
			return newDecisionTarget(c.id, stripProviderPrefix(req.Model, c.id), c.provider, c.format), nil
		}
	}
	for _, c := range candidates {
		if c.format != "" {
			return nil, errFormatMismatch(req.Model, c.id, c.format)
		}
	}
	return nil, errDecisionWrongFormat(candidates[0], req.Format)
}

func newDecisionTarget(id, upstreamModel string, p providers.Provider, format string) *decisionTarget {
	t := &decisionTarget{providerID: id, upstreamModel: upstreamModel}
	if format == models.DecisionFormatOpenAI {
		t.send = p.(providers.DecisionsProvider).CreateDecision
	} else {
		t.send = p.(providers.SystemOneProvider).SystemOne
	}
	return t
}

// decisionFormatOf returns the decision format a built provider speaks. An
// OpenAI-format provider whose ID or base_url names a System One vendor is a
// System One credential saved with the wrong api_format, not an OpenAI one.
func decisionFormatOf(p providers.Provider, id, baseURL string) string {
	if _, ok := p.(providers.SystemOneProvider); ok {
		return models.DecisionFormatSystemOne
	}
	if _, ok := p.(providers.DecisionsProvider); ok && !systemone.IsVendor(id, baseURL) {
		return models.DecisionFormatOpenAI
	}
	return ""
}

// apiFormatDecisionFormat is decisionFormatOf for a provider not built yet:
// a System One api_format builds a System One provider, "openai" the only
// provider that serves OpenAI Decisions.
func apiFormatDecisionFormat(apiFormat, id, baseURL string) string {
	switch {
	case providers.IsSystemOneFormat(apiFormat):
		return models.DecisionFormatSystemOne
	case apiFormat == "openai" && !systemone.IsVendor(id, baseURL):
		return models.DecisionFormatOpenAI
	default:
		return ""
	}
}

// listsModel reports whether a provider's model list has the requested model,
// as written or without a leading "<providerID>/".
func listsModel(registered []string, model, providerID string) bool {
	bare := stripProviderPrefix(model, providerID)
	for _, m := range registered {
		if m == model || (bare != model && m == bare) {
			return true
		}
	}
	return false
}

// stripProviderPrefix removes a leading "<providerID>/" from model, matching
// the ID case-insensitively. Only that one prefix goes: a vendor whose own
// model IDs contain a slash keeps them.
func stripProviderPrefix(model, providerID string) string {
	if prefix, rest, ok := strings.Cut(model, "/"); ok && strings.EqualFold(prefix, providerID) {
		return rest
	}
	return model
}

// decisionVendor guesses whose key a model needs, for the message that tells
// the caller to add it.
func decisionVendor(model string) string {
	lower := strings.ToLower(model)
	prefix, bare, ok := strings.Cut(lower, "/")
	if !ok {
		prefix, bare = "", lower
	}
	switch {
	case strings.HasPrefix(prefix, "typesafe") || strings.HasPrefix(bare, "jev-"):
		return "TypeSafe"
	case strings.HasPrefix(prefix, "perplexity") || strings.HasPrefix(bare, "pplx-"):
		return "Perplexity"
	case prefix == "openai" || strings.HasPrefix(bare, "gpt-"):
		return "OpenAI"
	default:
		return "decision provider"
	}
}

// orgProviderVendor names whose key an org provider holds, as decisionVendor
// names a model's, from its ID or base_url; "" when they say nothing.
func orgProviderVendor(id, baseURL string) string {
	if d, err := systemone.DialectFor(id, baseURL); err == nil && d.Name != systemone.Generic.Name {
		return d.Vendor
	}
	if strings.EqualFold(id, providers.DecisionDialectOpenAI) {
		return "OpenAI"
	}
	return ""
}

func errBYOKRequired(message string) *models.APIError {
	return &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Code: models.CodeBYOKRequired, Message: message}
}

func errProviderKeyHeaderNotAllowed() *models.APIError {
	return models.ErrBadRequest(models.CodeProviderKeyHeaderNotAllowed,
		models.ProviderKeyHeader+" is accepted only from internal callers. Save your provider key under Gateway > Providers instead.")
}

func errDecisionNotConfigured(model string) *models.APIError {
	return &models.APIError{
		Status: http.StatusForbidden,
		Type:   models.ErrTypePermission,
		Code:   models.CodeDecisionProviderNotConfigured,
		Message: fmt.Sprintf("No provider in your organization serves %q. Add your %s key under Gateway > Providers.",
			model, decisionVendor(model)),
	}
}

// errDecisionModelNotListed refuses a model the org's provider holds the key
// for but leaves out of its model list. The code is the not-configured one:
// either way the fix is under Gateway > Providers.
func errDecisionModelNotListed(model, providerID, vendor string) *models.APIError {
	holds := "a key"
	if vendor != "" {
		holds = "your " + vendor + " key"
	}
	return &models.APIError{
		Status: http.StatusForbidden,
		Type:   models.ErrTypePermission,
		Code:   models.CodeDecisionProviderNotConfigured,
		Message: fmt.Sprintf("Provider %q has %s but does not list %q. Add the model to it under Gateway > Providers.",
			providerID, holds, stripProviderPrefix(model, providerID)),
	}
}

func errDecisionWrongFormat(c decisionCandidate, want string) *models.APIError {
	saved := ""
	if c.apiFormat != "" {
		saved = fmt.Sprintf(" is saved with api_format %q, which", c.apiFormat)
	}
	var message string
	if want == models.DecisionFormatOpenAI {
		message = fmt.Sprintf("Provider %q%s does not serve OpenAI Decisions. OpenAI Decisions need an openai provider; System One models go to /v1/systemone.", c.id, saved)
	} else {
		message = fmt.Sprintf("Provider %q%s does not serve System One decisions. Edit it under Gateway > Providers and choose System One (api_format systemone).", c.id, saved)
	}
	return &models.APIError{Status: http.StatusForbidden, Type: models.ErrTypePermission, Code: models.CodeDecisionProviderWrongFormat, Message: message}
}

// errFormatMismatch refuses a body whose format the resolved provider does
// not speak. Nothing translates between the formats, so the hint names the
// route and shape the provider does take.
func errFormatMismatch(model, providerID, providerFormat string) *models.APIError {
	hint := "send questions as an object keyed by question id to /v1/systemone"
	name := "System One"
	if providerFormat == models.DecisionFormatOpenAI {
		hint = "send questions as an array to /v1/decisions"
		name = "OpenAI Decisions"
	}
	return &models.APIError{
		Status: http.StatusUnprocessableEntity,
		Type:   models.ErrTypeInvalidRequest,
		Code:   models.CodeFormatMismatch,
		Message: fmt.Sprintf("Model %q is served by provider %q in the %s format: %s. The gateway does not translate between decision formats.",
			model, providerID, name, hint),
	}
}

// keyedDecisionProviders holds the providers built for internal callers' own
// keys, so each call does not open a new connection pool. An entry is found by
// a SHA-256 of the dialect, base URL and key: the key is never a map key or a
// log field, and is kept only inside the provider that sends it.
type keyedDecisionProviders struct {
	mu        sync.Mutex
	providers map[[sha256.Size]byte]*keyedDecisionProvider
	// sweep drops idle entries when no call comes to do it; nil while the
	// cache is empty.
	sweep *time.Timer
	// now is time.Now, replaced in tests.
	now func() time.Time
}

type keyedDecisionProvider struct {
	provider providers.Provider
	lastUsed time.Time
}

// maxKeyedDecisionProviders bounds the cache; past it the least recently used
// entry is dropped. An entry idle for keyedDecisionIdleTTL is dropped too, by
// the next call or within another TTL by the sweep, so a key used for one call
// isn't kept in memory until 256 others push it out.
const (
	maxKeyedDecisionProviders = 256
	keyedDecisionIdleTTL      = 10 * time.Minute
)

func (c *keyedDecisionProviders) clock() time.Time {
	if c.now != nil {
		return c.now()
	}
	return time.Now()
}

func (c *keyedDecisionProviders) get(dialect, baseURL, apiKey string) (providers.Provider, error) {
	sum := sha256.Sum256([]byte(dialect + "\x00" + baseURL + "\x00" + apiKey))

	c.mu.Lock()
	defer c.mu.Unlock()
	now := c.clock()
	c.dropIdle(now)
	if entry, ok := c.providers[sum]; ok {
		entry.lastUsed = now
		return entry.provider, nil
	}
	p, err := providers.NewKeyedDecisionProvider(dialect, baseURL, apiKey)
	if err != nil {
		return nil, err
	}
	if c.providers == nil {
		c.providers = make(map[[sha256.Size]byte]*keyedDecisionProvider)
	}
	if len(c.providers) >= maxKeyedDecisionProviders {
		var oldestKey [sha256.Size]byte
		var oldest *keyedDecisionProvider
		for key, entry := range c.providers {
			if oldest == nil || entry.lastUsed.Before(oldest.lastUsed) {
				oldestKey, oldest = key, entry
			}
		}
		oldest.provider.Close()
		delete(c.providers, oldestKey)
	}
	c.providers[sum] = &keyedDecisionProvider{provider: p, lastUsed: now}
	if c.sweep == nil {
		c.sweep = time.AfterFunc(keyedDecisionIdleTTL, c.sweepIdle)
	}
	return p, nil
}

// dropIdle drops the entries idle past the TTL. Closing only drops idle
// connections; a call in flight finishes. The caller holds c.mu.
func (c *keyedDecisionProviders) dropIdle(now time.Time) {
	for key, entry := range c.providers {
		if now.Sub(entry.lastUsed) > keyedDecisionIdleTTL {
			entry.provider.Close()
			delete(c.providers, key)
		}
	}
}

// sweepIdle drops idle entries when no call has, and runs again while any are
// left.
func (c *keyedDecisionProviders) sweepIdle() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.dropIdle(c.clock())
	if len(c.providers) == 0 {
		c.sweep = nil
	} else if c.sweep != nil {
		c.sweep.Reset(keyedDecisionIdleTTL)
	}
}

// size is how many providers the cache holds.
func (c *keyedDecisionProviders) size() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.providers)
}
