package server

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/modeldb/orgdb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// ListModels handles GET /v1/models. A key limited to its org's providers
// (orgProvidersOnly: every customer key) lists only what it can call
// (orgModelObjects); any other caller (an internal key, a
// license-authorized request, a gateway without keys) lists every configured
// model.
func (h *Handlers) ListModels(w http.ResponseWriter, r *http.Request) {
	if key, orgCfg, scoped := h.modelsScope(r); scoped {
		writeModelList(w, h.orgModelObjects(key, orgCfg))
		return
	}

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
				enrichModelObject(&em, info)
			}
		}
		enriched = append(enriched, em)
	}

	enriched = append(enriched, h.extraModelObjects(r, enriched)...)
	writeModelList(w, enriched)
}

func writeModelList(w http.ResponseWriter, data []models.EnrichedModelObject) {
	resp := models.EnrichedModelListResponse{
		Object: "list",
		Data:   data,
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(resp)
}

// enrichModelObject fills em's mode, limits, prices and capabilities from
// info.
func enrichModelObject(em *models.EnrichedModelObject, info *modeldb.ModelInfo) {
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

// modelsScope reports whether the request's key is limited to its org's
// providers, as the request path decides it (orgProvidersOnly, the same
// decision capabilityRoutes.reachable makes), with the key and its org's
// config.
func (h *Handlers) modelsScope(r *http.Request) (*authpkg.APIKey, *tenant.OrgConfig, bool) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	setAuthMetadataFromRequest(rc, r)
	key := h.lookupRequestKey(rc)
	if key != nil {
		rc.Metadata["key_type"] = key.KeyType
	}
	if !h.orgProvidersOnly(r.Context(), rc) {
		return key, nil, false
	}
	var orgCfg *tenant.OrgConfig
	if key != nil && h.tenantStore != nil {
		orgCfg = h.tenantStore.Get(key.Metadata["org_id"])
	}
	return key, orgCfg, true
}

// orgModelObjects is what a key limited to its org's providers can call:
// the models of the org's own providers that hold credentials and
// that the key's provider and model ACLs allow, described as the org sees
// them (its Model Database overrides and Cost Tracking prices on top of the
// model database, orgdb.Info); "auto"; and the managed catalog when the org
// has opted in to managed models. No operator (global) provider's model is
// listed: chat refuses them to this key.
func (h *Handlers) orgModelObjects(key *authpkg.APIKey, orgCfg *tenant.OrgConfig) []models.EnrichedModelObject {
	var out []models.EnrichedModelObject
	seen := map[string]bool{}
	if orgCfg != nil {
		ids := make([]string, 0, len(orgCfg.Providers))
		for id := range orgCfg.Providers {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		db := h.ModelDB()
		for _, providerID := range ids {
			if !orgHasCredentials(orgCfg, providerID) || (key != nil && !key.CanAccessProvider(providerID)) {
				continue
			}
			for _, m := range orgCfg.Providers[providerID].Models {
				if m == "" || seen[m] || (key != nil && !key.CanAccessModel(m)) {
					continue
				}
				seen[m] = true
				em := models.EnrichedModelObject{ID: m, Object: "model", OwnedBy: providerID}
				base, _ := db.ForProvider(providerID, m)
				if info, _ := orgdb.Info(orgCfg, providerID, m, base, true); info != nil {
					enrichModelObject(&em, info)
				}
				out = append(out, em)
			}
		}
	}
	if auto := routing.AutoModelNames[0]; !seen[auto] {
		seen[auto] = true
		out = append(out, models.EnrichedModelObject{ID: auto, Object: "model", OwnedBy: "agentcc", Mode: "chat"})
	}
	if h.catalog != nil && orgCfg.ManagedModelsEnabled() {
		for _, m := range h.managedModelObjects() {
			if seen[m.ID] || (key != nil && (!key.CanAccessProvider(m.OwnedBy) || !key.CanAccessModel(m.ID))) {
				continue
			}
			seen[m.ID] = true
			applyOrgLimitsAndSwitches(&m, orgdb.Override(orgCfg, m.OwnedBy, m.ID))
			out = append(out, m)
		}
	}
	return out
}

// applyOrgLimitsAndSwitches puts ov's token limits and capability switches on
// a managed model's listing: the validation plugin holds the org's managed
// calls to them. Its prices are left alone: a managed call is billed at
// FutureAGI's catalog price.
func applyOrgLimitsAndSwitches(em *models.EnrichedModelObject, ov *tenant.ModelOverrideOrgConfig) {
	if ov == nil {
		return
	}
	if n := orgdb.MaxInputTokens(ov); n > 0 {
		em.MaxInputTokens = n
	}
	if n := orgdb.MaxOutputTokens(ov); n > 0 {
		em.MaxOutputTokens = n
	}
	c := ov.Capabilities
	if c == nil || em.Capabilities == nil {
		return
	}
	caps := *em.Capabilities // the listing's own copy
	for _, s := range []struct {
		from *bool
		to   *bool
	}{
		{c.Vision, &caps.Vision}, {c.FunctionCalling, &caps.FunctionCalling}, {c.Streaming, &caps.Streaming},
		{c.ResponseSchema, &caps.ResponseSchema}, {c.PromptCaching, &caps.PromptCaching}, {c.Reasoning, &caps.Reasoning},
	} {
		if s.from != nil {
			*s.to = *s.from
		}
	}
	em.Capabilities = &caps
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

	if key, orgCfg, scoped := h.modelsScope(r); scoped {
		for _, m := range h.orgModelObjects(key, orgCfg) {
			if m.ID == modelID {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusOK)
				json.NewEncoder(w).Encode(models.ModelObject{ID: m.ID, Object: m.Object, Created: m.Created, OwnedBy: m.OwnedBy})
				return
			}
		}
		models.WriteError(w, models.ErrNotFound("model_not_found", "Model '"+modelID+"' not found"))
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

// managedModelObjects lists the managed catalog for /v1/models, priced as the
// org pays for it.
func (h *Handlers) managedModelObjects() []models.EnrichedModelObject {
	cat := h.catalog.Load()
	if cat == nil {
		return nil
	}
	factor := 1 + h.managedMarkupPercent/100
	var out []models.EnrichedModelObject
	for _, snap := range cat.Snapshots() {
		for _, m := range snap.Sorted() {
			em := models.EnrichedModelObject{
				ID:              m.ID,
				Object:          "model",
				Created:         m.Created,
				OwnedBy:         snap.ProviderID,
				Mode:            "chat",
				MaxInputTokens:  m.ContextLength,
				MaxOutputTokens: m.MaxOutputTokens,
			}
			info := m.ModelInfo(m.ID, snap.ProviderID)
			if !m.DynamicPrice {
				em.Pricing = &models.ModelPricingAPI{
					InputPerMTokens:       roundPrice(m.InputPerToken * 1e6 * factor),
					OutputPerMTokens:      roundPrice(m.OutputPerToken * 1e6 * factor),
					CachedInputPerMTokens: roundPrice(m.CacheReadPerToken * 1e6 * factor),
				}
			}
			em.Capabilities = &models.ModelCapsAPI{
				Vision:          info.Capabilities.Vision,
				FunctionCalling: info.Capabilities.FunctionCalling,
				Streaming:       true,
				ResponseSchema:  info.Capabilities.ResponseSchema,
				PromptCaching:   info.Capabilities.PromptCaching,
				Reasoning:       info.Capabilities.Reasoning,
			}
			out = append(out, em)
		}
	}
	return out
}

// extraModelObjects adds to /v1/models what the gateway serves beyond its
// configured models: "auto", and the managed catalog for a key whose org has
// opted in.
func (h *Handlers) extraModelObjects(r *http.Request, listed []models.EnrichedModelObject) []models.EnrichedModelObject {
	seen := make(map[string]bool, len(listed))
	for _, m := range listed {
		seen[m.ID] = true
	}
	var out []models.EnrichedModelObject
	if !seen[routing.AutoModelNames[0]] {
		out = append(out, models.EnrichedModelObject{ID: routing.AutoModelNames[0], Object: "model", OwnedBy: "agentcc", Mode: "chat"})
	}
	if h.catalog == nil || !h.keyHasManagedModels(r) {
		return out
	}
	for _, m := range h.managedModelObjects() {
		if !seen[m.ID] {
			out = append(out, m)
		}
	}
	return out
}

// keyHasManagedModels reports whether the request's key belongs to an org
// that has opted in to managed models.
func (h *Handlers) keyHasManagedModels(r *http.Request) bool {
	if h.tenantStore == nil || h.keyStore == nil {
		return false
	}
	rawKey := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	if rawKey == "" {
		rawKey = r.Header.Get("x-api-key")
	}
	ki := h.keyStore.Lookup(rawKey)
	if ki == nil || ki.Metadata["org_id"] == "" {
		return false
	}
	return h.tenantStore.Get(ki.Metadata["org_id"]).ManagedModelsEnabled()
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
