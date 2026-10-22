package server

import (
	"log/slog"
	"net/http"
	"sort"
	"time"

	authpkg "github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/capabilities"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/modeldb/orgdb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// capabilityRoutes serves the capability registry. It keeps the provider
// config the registry was built from: a config reload does not rebuild the
// registry, so the startup config is the one that describes it.
type capabilityRoutes struct {
	srv               *Server
	providerCfg       map[string]config.ProviderConfig
	qualificationFile string
}

func newCapabilityRoutes(s *Server) *capabilityRoutes {
	c := &capabilityRoutes{srv: s}
	if s.cfg != nil {
		c.providerCfg = s.cfg.Providers
		c.qualificationFile = s.cfg.Capabilities.QualificationFile
	}
	return c
}

// handleKey serves GET /v1/capabilities: the providers the request's key can
// reach, or with ?model=<id> that model's flags through each of them.
func (c *capabilityRoutes) handleKey(w http.ResponseWriter, r *http.Request) {
	ps, audience, orgCfg := c.reachable(r)
	in := c.inputs(ps, audience)
	if orgCfg != nil && orgCfg.ModelDatabase != nil && len(orgCfg.ModelDatabase.Overrides) > 0 {
		// The org's Model Database limits and capability switches,
		// as the validation plugin applies them to the org's calls.
		in.OrgView = func(providerID, model string, base *modeldb.ModelInfo) *modeldb.ModelInfo {
			info, _ := orgdb.Info(orgCfg, providerID, model, base, false)
			return info
		}
	}
	c.write(w, r, in)
}

// handleAdmin serves GET /-/capabilities: every provider, for the admin.
func (c *capabilityRoutes) handleAdmin(w http.ResponseWriter, r *http.Request) {
	if !c.srv.checkAdminAuth(w, r) {
		return
	}
	ps := capabilities.FromRegistry(c.srv.registry, c.providerCfg)
	c.write(w, r, c.inputs(ps, capabilities.AudienceAdmin))
}

func (c *capabilityRoutes) write(w http.ResponseWriter, r *http.Request, in capabilities.Inputs) {
	model := r.URL.Query().Get("model")
	if model == "" {
		m := capabilities.Build(in)
		if in.Audience != capabilities.AudienceAdmin {
			omitResaleTerms(m)
		}
		writeJSON(w, http.StatusOK, m)
		return
	}
	v := capabilities.BuildModel(in, model)
	if len(v.Providers) == 0 {
		models.WriteError(w, models.ErrNotFound("model_not_found", "No provider available to this caller serves model '"+model+"'"))
		return
	}
	writeJSON(w, http.StatusOK, v)
}

// omitResaleTerms clears every cell's managed_resale_terms: it is internal
// legal status, shown on /-/capabilities only.
func omitResaleTerms(m *capabilities.Matrix) {
	for _, row := range m.Endpoints {
		for id, c := range row.Providers {
			c.ManagedResaleTerms = ""
			row.Providers[id] = c
		}
	}
}

// inputs gathers what the matrix reads, at call time: the catalog and model
// database as they are now, and the priced function the wiring injected
// (cost.Priced once wiring_pricing has run).
func (c *capabilityRoutes) inputs(ps []capabilities.Provider, audience capabilities.Audience) capabilities.Inputs {
	h := c.srv.handlers
	// Read per request, so an import shows without a reload. A file that
	// cannot be used is no evidence: nothing is marked qualified from it.
	q, err := capabilities.LoadQualification(c.qualificationFile)
	if err != nil {
		slog.Warn("capabilities: qualification file ignored", "path", c.qualificationFile, "error", err)
	}
	return capabilities.Inputs{
		Providers:        ps,
		Catalog:          c.srv.managed.Catalog.Load(),
		ModelDB:          h.ModelDB(),
		Priced:           h.Deps().Priced,
		ManagedServes:    managedServes,
		Qualification:    q,
		Audience:         audience,
		ScheduledEnabled: h.scheduledStore != nil,
		Now:              time.Now(),
	}
}

// capabilityEndpointTypes maps a launch endpoint to the request endpoint type
// its handler sets.
var capabilityEndpointTypes = map[string]string{
	capabilities.EndpointChat:       "chat",
	capabilities.EndpointResponses:  "responses",
	capabilities.EndpointMessages:   "anthropic_messages",
	capabilities.EndpointEmbeddings: "embedding",
}

// managedServes reports whether the gateway serves managed traffic on a
// capability endpoint, by asking managedEndpoint about its handler.
func managedServes(endpoint string) bool {
	et, ok := capabilityEndpointTypes[endpoint]
	return ok && managedEndpoint(&models.RequestContext{EndpointType: et})
}

// reachable is the providers the request's key can call, as the request path
// resolves them (providerForKey): a key limited to its org's providers gets
// the org's own providers (BYOK) and, when the org opted in, the managed
// ones; any other key gets every global provider. The key's provider ACL
// applies to both. Nothing is built: an org's provider is described from its
// config, never instantiated.
func (c *capabilityRoutes) reachable(r *http.Request) ([]capabilities.Provider, capabilities.Audience, *tenant.OrgConfig) {
	h := c.srv.handlers
	global := capabilities.FromRegistry(c.srv.registry, c.providerCfg)

	rc := models.AcquireRequestContext()
	defer rc.Release()
	setAuthMetadataFromRequest(rc, r)
	key := h.lookupRequestKey(rc)
	if key != nil {
		rc.Metadata["key_type"] = key.KeyType
	}
	if !h.orgProvidersOnly(r.Context(), rc) {
		return keyACL(global, key), capabilities.AudienceGlobalKey, nil
	}

	var orgCfg *tenant.OrgConfig
	if key != nil && h.tenantStore != nil {
		orgCfg = h.tenantStore.Get(key.Metadata["org_id"])
	}
	var out []capabilities.Provider
	seen := map[string]bool{}
	for _, p := range global {
		seen[p.ID] = true
		switch {
		case orgHasCredentials(orgCfg, p.ID):
			out = append(out, orgBYOKProvider(p.ID, orgCfg.Providers[p.ID], &p))
		case p.Managed && orgCfg.ManagedModelsEnabled():
			out = append(out, p)
		}
	}
	if orgCfg != nil {
		for id := range orgCfg.Providers {
			if !seen[id] && orgHasCredentials(orgCfg, id) {
				out = append(out, orgBYOKProvider(id, orgCfg.Providers[id], nil))
			}
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return keyACL(out, key), capabilities.AudienceOrgKey, orgCfg
}

// orgBYOKProvider describes an org's own provider the way
// OrgProviderCache.GetOrCreateWithTenantConfig builds it: the org's upstream
// and api_format when it names a base URL or a service account (or the ID is
// not a global provider), else the global provider's adapter with the org's
// key.
func orgBYOKProvider(id string, pc *tenant.ProviderConfig, global *capabilities.Provider) capabilities.Provider {
	if global != nil && pc.BaseURL == "" && pc.ServiceAccountJSON == "" {
		p := *global
		p.Managed, p.ResaleTermsRef, p.CredentialType = false, "", capabilities.CredentialBYOK
		if len(pc.Models) > 0 {
			p.Models = pc.Models
		}
		return p
	}
	format := pc.APIFormat
	if format == "" {
		format = inferOrgAPIFormat(id)
	}
	return capabilities.Provider{
		ID:             id,
		Interfaces:     capabilities.FormatInterfaces(format),
		APIFormat:      format,
		Upstream:       capabilities.UpstreamOf("", pc.BaseURL),
		Models:         pc.Models,
		CredentialType: capabilities.CredentialBYOK,
	}
}

// inferOrgAPIFormat is the api_format OrgProviderCache gives an org provider
// that names none (providers.inferAPIFormat, which is unexported).
func inferOrgAPIFormat(providerID string) string {
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

// keyACL keeps the providers key's provider ACL allows.
func keyACL(ps []capabilities.Provider, key *authpkg.APIKey) []capabilities.Provider {
	if key == nil {
		return ps
	}
	out := make([]capabilities.Provider, 0, len(ps))
	for _, p := range ps {
		if key.CanAccessProvider(p.ID) {
			out = append(out, p)
		}
	}
	return out
}
