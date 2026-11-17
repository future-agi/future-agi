package server

import (
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"maps"
	"net/http"
	"slices"

	gatewayadmin "github.com/futureagi/agentcc-gateway/internal/contracts/generated"
	"github.com/futureagi/agentcc-gateway/internal/mcp/credtemplate"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
	"github.com/futureagi/agentcc-gateway/internal/toolcatalog"
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
			if cp.ServiceAccountJSON != "" {
				cp.ServiceAccountJSON = "****"
			}
			rp[name] = &cp
		}
		redacted.Providers = rp
	}
	redacted.ManagedModels = redactManagedModels(redacted.ManagedModels)
	if r := redacted.Routing; r != nil && r.Rollout != nil && r.Rollout.ManagedModels != nil &&
		len(r.Rollout.ManagedModels.UpstreamKeys) > 0 {
		routing := *r
		rollout := *r.Rollout
		rollout.ManagedModels = redactManagedModels(rollout.ManagedModels)
		routing.Rollout = &rollout
		redacted.Routing = &routing
	}
	redacted.MCP = redactMCP(redacted.MCP)
	return &redacted
}

// mcpSecretMask replaces an MCP secret in every admin GET.
const mcpSecretMask = "****"

// redactMCP masks every MCP secret an org config can carry: a server's
// legacy auth.token and auth.key, and the push-only credential.secret and
// managed_tools.credentials.*.secret values (assembled at push, never
// returned). It copies before masking, so the live config is never changed.
func redactMCP(m *tenant.MCPOrgConfig) *tenant.MCPOrgConfig {
	if m == nil {
		return nil
	}
	cp := *m
	if len(m.Servers) > 0 {
		cp.Servers = make(map[string]*tenant.MCPServerOrgConfig, len(m.Servers))
		for id, srv := range m.Servers {
			if srv == nil {
				cp.Servers[id] = nil
				continue
			}
			s := *srv
			if srv.Auth != nil {
				a := *srv.Auth
				a.Token, a.Key = maskSet(a.Token), maskSet(a.Key)
				s.Auth = &a
			}
			if srv.Credential != nil {
				c := *srv.Credential
				c.Secret = maskSecret(c.Secret)
				s.Credential = &c
			}
			cp.Servers[id] = &s
		}
	}
	if mt := m.ManagedTools; mt != nil && len(mt.Credentials) > 0 {
		tools := *mt
		tools.Credentials = make(map[string]*tenant.MCPManagedToolCredential, len(mt.Credentials))
		for id, cred := range mt.Credentials {
			if cred == nil {
				tools.Credentials[id] = nil
				continue
			}
			c := *cred
			c.Secret = maskSecret(c.Secret)
			tools.Credentials[id] = &c
		}
		cp.ManagedTools = &tools
	}
	return &cp
}

func maskSecret(sec *tenant.MCPCredentialSecret) *tenant.MCPCredentialSecret {
	if sec == nil {
		return nil
	}
	c := *sec
	c.Value = maskSet(c.Value)
	if len(sec.Parts) > 0 {
		// A copy: the live config's parts keep their values.
		c.Parts = make([]tenant.MCPCredentialSecretPart, len(sec.Parts))
		for i, part := range sec.Parts {
			part.Value = maskSet(part.Value)
			c.Parts[i] = part
		}
	}
	return &c
}

// maskSet masks a set value and leaves an empty one empty.
func maskSet(v string) string {
	if v == "" {
		return ""
	}
	return mcpSecretMask
}

// redactManagedModels masks every pushed per-org upstream key (contract 6.14:
// redacted from every GET) and keeps key_hash. It copies before masking, so
// the live config is never changed.
func redactManagedModels(mm *tenant.ManagedModelsConfig) *tenant.ManagedModelsConfig {
	if mm == nil || len(mm.UpstreamKeys) == 0 {
		return mm
	}
	cp := *mm
	cp.UpstreamKeys = make(map[string]*tenant.ManagedUpstreamKey, len(mm.UpstreamKeys))
	for id, k := range mm.UpstreamKeys {
		if k == nil {
			continue
		}
		masked := *k
		masked.APIKey = redactAPIKey(masked.APIKey)
		cp.UpstreamKeys[id] = &masked
	}
	return &cp
}

func gatewayContractOrgConfigToTenant(cfg *gatewayadmin.OrgConfig) (*tenant.OrgConfig, error) {
	body, err := json.Marshal(cfg)
	if err != nil {
		return nil, err
	}
	var out tenant.OrgConfig
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func gatewayContractOrgConfigMapToTenant(configs map[string]*gatewayadmin.OrgConfig) (map[string]*tenant.OrgConfig, error) {
	body, err := json.Marshal(configs)
	if err != nil {
		return nil, err
	}
	var out map[string]*tenant.OrgConfig
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// OrgConfigHandlers provides admin CRUD for per-org gateway configurations.
type OrgConfigHandlers struct {
	store            *tenant.Store
	adminToken       string
	orgProviderCache *providers.OrgProviderCache // nil-safe
	onConfigChange   func(orgID string)          // called after org config changes (e.g. invalidate caches)
	// toolCatalog is the tool catalog the gateway loads (tools on); nil skips the
	// managed opt-in check.
	toolCatalog *toolcatalog.Catalog
	// connectorCatalog is the connector catalog the gateway embeds
	// (dist/catalog.json): a catalog_ref server's shared secret is checked
	// against the parts its provider declares, as the MCP pool checks it;
	// nil refuses such a server.
	connectorCatalog credtemplate.Catalog
}

// NewOrgConfigHandlers creates org config admin handlers.
func NewOrgConfigHandlers(store *tenant.Store, adminToken string, orgProviderCache *providers.OrgProviderCache) *OrgConfigHandlers {
	return &OrgConfigHandlers{
		store:            store,
		adminToken:       adminToken,
		orgProviderCache: orgProviderCache,
	}
}

// SetConnectorCatalog gives the handlers the connector catalog, so a config
// pushed by any path is refused at the PUT or bulk load when the MCP pool
// would refuse one of its catalog_ref servers. The
// wiring calls it at startup, whether or not MCP is on.
func (h *OrgConfigHandlers) SetConnectorCatalog(cat credtemplate.Catalog) {
	h.connectorCatalog = cat
}

// SetToolCatalog gives the handlers the tool catalog, so a config pushed by
// any path (not only Django's save) cannot give a byok_only vendor a managed
// opt-in. The wiring calls it when tools are enabled.
func (h *OrgConfigHandlers) SetToolCatalog(cat *toolcatalog.Catalog) {
	h.toolCatalog = cat
}

// refuseManagedByokOnly answers 400 capability_managed_not_allowed when the
// config's mcp.managed_tools allows a byok_only vendor; with no catalog set
// nothing is checked.
func (h *OrgConfigHandlers) refuseManagedByokOnly(w http.ResponseWriter, orgID string, cfg *tenant.OrgConfig) bool {
	if h.toolCatalog == nil {
		return false
	}
	if err := h.toolCatalog.CheckOrgConfig(cfg); err != nil {
		slog.Warn("org config refused: managed opt-in for a byok_only vendor", "org_id", orgID, "error", err)
		models.WriteError(w, models.ErrBadRequest(toolcatalog.CodeManagedNotAllowed, "org "+orgID+": "+err.Error()))
		return true
	}
	return false
}

// refuseUnappliedPlacement answers 400 mcp_credential_placement_unsupported,
// with the field as param, when a pushed MCP credential secret of the config
// cannot be placed as it states (the credential templates, and the
// header-only rule for a managed tool credential): the config is not stored,
// so the credential is never sent elsewhere or in part.
func (h *OrgConfigHandlers) refuseUnappliedPlacement(w http.ResponseWriter, orgID string, cfg *tenant.OrgConfig) bool {
	perr := cfg.UnappliedMCPCredentialPlacement(h.connectorCatalog)
	if perr == nil {
		return false
	}
	slog.Warn("org config refused: an MCP credential placement this gateway does not apply", "org_id", orgID, "field", perr.Field)
	apiErr := models.ErrBadRequest(tenant.CodeMCPCredentialPlacementUnsupported, "org "+orgID+": "+perr.Error())
	field := perr.Field
	apiErr.Param = &field
	models.WriteError(w, apiErr)
	return true
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

	var contractCfg searchOrgContract
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&contractCfg); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+models.DescribeJSONError(err)))
		return
	}
	cfg, err := contractCfg.runtime()
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_contract", "Invalid gateway org config: "+err.Error()))
		return
	}
	if h.refuseManagedByokOnly(w, orgID, cfg) || h.refuseUnappliedPlacement(w, orgID, cfg) {
		return
	}

	if err := h.store.Set(orgID, cfg); err != nil {
		writeOrgPublicationError(w, err)
		return
	}

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

	if err := h.store.Delete(orgID); err != nil {
		writeOrgPublicationError(w, err)
		return
	}

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

	var contractConfigs map[string]*searchOrgContract
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&contractConfigs); err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_json", "Invalid JSON: "+models.DescribeJSONError(err)))
		return
	}
	configs, err := searchOrgContractsRuntime(contractConfigs)
	if err != nil {
		models.WriteError(w, models.ErrBadRequest("invalid_contract", "Invalid gateway org config map: "+err.Error()))
		return
	}
	for _, orgID := range slices.Sorted(maps.Keys(configs)) {
		if h.refuseManagedByokOnly(w, orgID, configs[orgID]) || h.refuseUnappliedPlacement(w, orgID, configs[orgID]) {
			return
		}
	}

	if err := h.store.LoadBulk(configs); err != nil {
		writeOrgPublicationError(w, err)
		return
	}

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

// SetOrgSectionsEnforced records the org config sections this node enforces,
// as cmd/agentcc reads them off the plugins it installed: GET /-/config
// reports them as org_sections_enforced. Call after New and
// before Start. A server it is never called on leaves the field out, which the
// console reads as "enforcement unknown".
func (s *Server) SetOrgSectionsEnforced(sections []string) {
	s.orgSectionsEnforced = append([]string{}, sections...)
}

// SetOrgSectionsNotEnforced records, for GET /-/config's
// org_sections_not_enforced, each org config section this node does not
// enforce with the reason (ip_acl: middleware.OrgIPACLClientIPUnknown). An
// empty or nil map leaves the field out. Call after New and before Start.
func (s *Server) SetOrgSectionsNotEnforced(reasons map[string]string) {
	s.orgSectionsNotEnforced = make(map[string]string, len(reasons))
	for section, reason := range reasons {
		s.orgSectionsNotEnforced[section] = reason
	}
}
