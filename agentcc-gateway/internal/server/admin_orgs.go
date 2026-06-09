package server

import (
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// redactAPIKey masks an API key, showing only the first 8 and last 4 characters.
func redactAPIKey(key string) string {
	if len(key) <= 12 {
		return "****"
	}
	return key[:8] + "****" + key[len(key)-4:]
}

// redactOrgConfig returns a shallow copy of the config with API keys masked.
func redactOrgConfig(cfg *tenant.OrgConfig) *tenant.OrgConfig {
	if cfg == nil {
		return nil
	}
	redacted := *cfg
	if len(redacted.Providers) > 0 {
		rp := make(map[string]*tenant.ProviderConfig, len(redacted.Providers))
		for name, pc := range redacted.Providers {
			cp := *pc
			if cp.APIKey != "" {
				cp.APIKey = redactAPIKey(cp.APIKey)
			}
			if cp.AWSAccessKeyID != "" {
				cp.AWSAccessKeyID = redactAPIKey(cp.AWSAccessKeyID)
			}
			if cp.AWSSecretAccessKey != "" {
				cp.AWSSecretAccessKey = redactAPIKey(cp.AWSSecretAccessKey)
			}
			if cp.AWSSessionToken != "" {
				cp.AWSSessionToken = redactAPIKey(cp.AWSSessionToken)
			}
			rp[name] = &cp
		}
		redacted.Providers = rp
	}
	return &redacted
}

// OrgConfigHandlers provides admin CRUD for per-org gateway configurations.
type OrgConfigHandlers struct {
	store            *tenant.Store
	adminToken       string
	orgProviderCache *providers.OrgProviderCache // nil-safe
	onConfigChange   func(orgID string)          // called after org config changes (e.g. invalidate caches)
}

// NewOrgConfigHandlers creates org config admin handlers.
func NewOrgConfigHandlers(store *tenant.Store, adminToken string, orgProviderCache *providers.OrgProviderCache) *OrgConfigHandlers {
	return &OrgConfigHandlers{
		store:            store,
		adminToken:       adminToken,
		orgProviderCache: orgProviderCache,
	}
}

// SetOnConfigChange registers a callback invoked after any org config change.
func (h *OrgConfigHandlers) SetOnConfigChange(fn func(orgID string)) {
	h.onConfigChange = fn
}

func (h *OrgConfigHandlers) requireAdmin(w http.ResponseWriter, r *http.Request) bool {
	if h.adminToken == "" {
		slog.Warn("admin request denied: no admin token configured")
		http.Error(w, `{"error":"admin token not configured"}`, http.StatusForbidden)
		return false
	}
	token := r.Header.Get("Authorization")
	expected := "Bearer " + h.adminToken
	if subtle.ConstantTimeCompare([]byte(token), []byte(expected)) != 1 {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error":"unauthorized - admin token required"}`))
		return false
	}
	return true
}

// SetOrgConfig handles PUT /-/orgs/{org_id}/config — upsert an org config.
func (h *OrgConfigHandlers) SetOrgConfig(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	orgID := r.URL.Query().Get("org_id")
	if orgID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
		return
	}

	var cfg tenant.OrgConfig
	if err := json.NewDecoder(r.Body).Decode(&cfg); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+err.Error()))
		return
	}

	h.store.Set(orgID, &cfg)

	// Evict cached provider instances so they pick up new API keys.
	if h.orgProviderCache != nil {
		h.orgProviderCache.Evict(orgID)
	}
	// Invalidate dynamic guardrail cache so guardrails pick up new config.
	if h.onConfigChange != nil {
		h.onConfigChange(orgID)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status": "ok",
		"org_id": orgID,
	})
}

// GetOrgConfig handles GET /-/orgs/{org_id}/config — get an org config.
func (h *OrgConfigHandlers) GetOrgConfig(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	orgID := r.URL.Query().Get("org_id")
	if orgID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
		return
	}

	cfg := h.store.Get(orgID)
	if cfg == nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error": "org config not found",
		})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(redactOrgConfig(cfg))
}

// DeleteOrgConfig handles DELETE /-/orgs/{org_id}/config — remove an org config.
func (h *OrgConfigHandlers) DeleteOrgConfig(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	orgID := r.URL.Query().Get("org_id")
	if orgID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
		return
	}

	h.store.Delete(orgID)

	// Evict cached provider instances for this org.
	if h.orgProviderCache != nil {
		h.orgProviderCache.Evict(orgID)
	}
	if h.onConfigChange != nil {
		h.onConfigChange(orgID)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status":  "ok",
		"org_id":  orgID,
		"deleted": true,
	})
}

// ListOrgConfigs handles GET /-/orgs/configs — list all org configs.
func (h *OrgConfigHandlers) ListOrgConfigs(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	all := h.store.GetAll()

	redacted := make(map[string]*tenant.OrgConfig, len(all))
	for orgID, cfg := range all {
		redacted[orgID] = redactOrgConfig(cfg)
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"object": "list",
		"count":  len(redacted),
		"data":   redacted,
	})
}

// BulkLoadOrgConfigs handles POST /-/orgs/configs/bulk — replace all org configs at once.
func (h *OrgConfigHandlers) BulkLoadOrgConfigs(w http.ResponseWriter, r *http.Request) {
	if !h.requireAdmin(w, r) {
		return
	}

	var configs map[string]*tenant.OrgConfig
	if err := json.NewDecoder(r.Body).Decode(&configs); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+err.Error()))
		return
	}

	h.store.LoadBulk(configs)

	// Evict all cached provider instances since all configs changed.
	if h.orgProviderCache != nil {
		h.orgProviderCache.EvictAll()
	}
	// Invalidate all dynamic guardrail caches.
	if h.onConfigChange != nil {
		for orgID := range configs {
			h.onConfigChange(orgID)
		}
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"status": "ok",
		"loaded": len(configs),
	})
}
