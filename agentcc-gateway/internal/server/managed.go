package server

import (
	"math"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// Managed models: providers FutureAGI holds the credentials for (config.yaml
// providers with managed: true). A customer key may use one only when its org
// has opted in (org config managed_models.enabled); those requests are billed
// with billing_mode "managed" — upstream cost plus the managed markup, drawn
// from the org's managed-credit balance. A request an org's own provider
// serves is billing_mode "byok". Every other global provider stays reserved
// for internal keys.

const (
	billingManaged = "managed"
	billingBYOK    = "byok"
)

// ManagedSettings is what the server needs to offer managed models.
type ManagedSettings struct {
	Catalog        *catalog.Store
	Ledger         *creditsplugin.OrgLedger
	MarkupPercent  float64
	RequireCredits bool
}

// EnableManaged turns on managed models. Call after New and before Start.
func (s *Server) EnableManaged(ms ManagedSettings) {
	s.managed = ms
	s.handlers.catalog = ms.Catalog
	s.handlers.managedMarkupPercent = ms.MarkupPercent
	s.handlers.orgLedger = ms.Ledger
	s.handlers.requireManagedCredits = ms.RequireCredits
	overrides := ConvertModelOverrides(s.cfg.ModelDatabase.Overrides)
	s.handlers.modelOverrides.Store(&overrides)
}

// ManagedCatalogSources lists the managed providers whose catalogs the
// gateway keeps current.
func ManagedCatalogSources(registry *providers.Registry) []catalog.Source {
	mps := registry.ManagedProviders()
	out := make([]catalog.Source, 0, len(mps))
	for _, mp := range mps {
		out = append(out, catalog.Source{ProviderID: mp.ID, Type: mp.Type, URL: mp.CatalogURL})
	}
	return out
}

func (h *Handlers) catalogHas(providerID, model string) bool {
	return h.catalog.Load().Has(providerID, model)
}

func roundPrice(f float64) float64 { return math.Round(f*1e6) / 1e6 }

// --- Admin endpoints -------------------------------------------------------

// managedRoute wraps an admin handler that needs managed models configured.
func (s *Server) managedRoute(h http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.managed.Catalog == nil || s.managed.Ledger == nil {
			if !s.checkAdminAuth(w, r) {
				return
			}
			models.WriteError(w, models.ErrNotFound("managed_models_disabled",
				"this gateway has no managed providers (config.yaml providers with managed: true)"))
			return
		}
		h(w, r)
	}
}

func ptr[T any](v T) *T { return &v }

// errProviderNotPermitted refuses a global provider to a key that may not use it.
var errProviderNotPermitted = models.ErrForbidden("provider is not available for this API key")

// BuildModelDB is the model database: the bundled data, the managed
// providers' catalogs, and config.yaml's overrides on top.
func BuildModelDB(overrides map[string]modeldb.ModelOverride, cat *catalog.Store) *modeldb.ModelDB {
	bundled := modeldb.BundledModels
	if c := cat.Load(); c != nil {
		bundled = modeldb.WithExtra(bundled, c.ModelInfos())
	}
	return modeldb.New(bundled, overrides)
}

// RefreshModelDB rebuilds the model database after a catalog refresh, keeping
// the overrides of the config last loaded.
func (s *Server) RefreshModelDB() {
	h := s.handlers
	if h.modelDB == nil {
		return
	}
	var overrides map[string]modeldb.ModelOverride
	if ov := h.modelOverrides.Load(); ov != nil {
		overrides = *ov
	}
	h.modelDB.Store(BuildModelDB(overrides, h.catalog))
}

// errDeferredRunUnavailable refuses a deferred run (async, scheduled) to a key
// limited to its org's providers: the run resolves its provider from the
// global registry, which such a key may not use.
func errDeferredRunUnavailable(what string) *models.APIError {
	return &models.APIError{
		Status:  http.StatusBadRequest,
		Type:    models.ErrTypeInvalidRequest,
		Code:    "deferred_run_unavailable",
		Message: what + " is not available for organization API keys; send the request synchronously",
	}
}
