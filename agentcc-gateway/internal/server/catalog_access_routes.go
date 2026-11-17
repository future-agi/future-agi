package server

import (
	"math"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Access channels of a model for one org.
const (
	accessManaged = "managed"
	accessBYOK    = "byok"
	accessBoth    = "both"
	// stateUnknown is a model only the org's own key reaches: no catalog
	// lists it, so the gateway knows no state for it.
	stateUnknown = "unknown"
)

// catalogAccess is GET /-/orgs/{org_id}/catalog/access: one row per
// canonical model the org can reach, through managed access, its own keys
// (BYOK), or both.
type catalogAccess struct {
	Data  []catalogAccessRow `json:"data"`
	Stats catalogAccessStats `json:"stats"`
}

type catalogAccessRow struct {
	GenerationPricing *models.GenerationPricingAPI `json:"generation_pricing,omitempty"`
	ID                string                       `json:"id"`
	Name              string                       `json:"name"`
	// Access is the channels that offer the model to the org: managed (a
	// managed source sells it and the org's managed policy allows it), byok
	// (one of the org's providers with credentials serves it), or both.
	Access        string   `json:"access"`
	BYOKProviders []string `json:"byok_providers"`
	// Managed is true when the org can send the model managed now: it has
	// opted in, and the model is offered and servable.
	Managed bool `json:"managed"`
	// ManagedAvailableOnOptIn is true when it would be, were the org to opt in.
	ManagedAvailableOnOptIn bool `json:"managed_available_on_opt_in"`
	// ManagedSources are the managed providers that sell the model to this
	// org; empty when managed access does not offer it (access byok).
	ManagedSources []string `json:"managed_sources"`
	// State is the catalog state (active, degraded, deprecated, blocked), or
	// unknown for a model no catalog lists.
	State string `json:"state"`
	// Pricing is what the org pays per token through managed access, at the
	// cheapest source (list price plus the markup); null when no managed
	// source has a fixed price for it.
	Pricing       *catalog.PricingView `json:"pricing"`
	ContextLength int                  `json:"context_length"`
}

type catalogAccessStats struct {
	CanonicalModels int `json:"canonical_models"`
	BYOKOnly        int `json:"byok_only"`
	ManagedOnly     int `json:"managed_only"`
	Both            int `json:"both"`
}

// handleCatalogAccess serves GET /-/orgs/{org_id}/catalog/access (admin).
func (s *Server) handleCatalogAccess(w http.ResponseWriter, r *http.Request) {
	if !s.checkAdminAuth(w, r) {
		return
	}
	orgID := r.URL.Query().Get("org_id")
	if orgID == "" {
		models.WriteError(w, models.ErrBadRequest("missing_org_id", "org_id is required"))
		return
	}
	var orgCfg *tenant.OrgConfig
	if s.handlers.tenantStore != nil {
		orgCfg = s.handlers.tenantStore.Get(orgID)
	}
	now := time.Now()
	if s.managed.Catalog != nil {
		now = s.managed.Catalog.Now()
	}
	writeJSON(w, http.StatusOK, buildCatalogAccess(s.managed.Catalog.Load(), orgCfg, s.managed.MarkupPercent, now, s.handlers))
}

// buildCatalogAccess is the access view of cat for an org with orgCfg (nil:
// an org with no config).
func buildCatalogAccess(cat *catalog.Catalog, orgCfg *tenant.OrgConfig, markupPercent float64, now time.Time, handlers ...*Handlers) catalogAccess {
	merged := cat.CanonicalModels()
	byok := orgBYOKModels(merged, orgCfg)
	optedIn := orgCfg.ManagedModelsEnabled()
	factor := 1 + markupPercent/100

	view := catalogAccess{Data: []catalogAccessRow{}}
	listed := make(map[string]bool, len(merged))
	for _, cm := range merged {
		listed[cm.ID] = true
		row := catalogAccessRow{ID: cm.ID, Name: cm.Name, ContextLength: cm.ContextLength, State: bestState(cm.Listings, false),
			BYOKProviders: sortedKeys(byok[cm.ID]), ManagedSources: []string{}}
		offered := false
		if id, listings, endpoints := accessOffer(cm, orgCfg, now, handlers...); id != "" {
			if id != cm.ID && len(row.BYOKProviders) > 0 {
				// Keep the org's own canonical channel separate from a variant
				// whose exact ID is the only managed offer.
				own := row
				own.Access = accessBYOK
				view.Data = append(view.Data, own)
				row.BYOKProviders = []string{}
			}
			row.ID = id
			offered, row.State = true, bestState(listings, true)
			set := map[string]bool{}
			for _, l := range listings {
				set[l.ProviderID] = true
			}
			row.ManagedSources = sortedKeys(set)
			row.Managed, row.ManagedAvailableOnOptIn = optedIn, !optedIn
			row.Pricing = cheapestPricing(endpoints, factor)
			for _, l := range listings {
				if q := l.Model.InclusivePrice(l.Source); q != nil && (q.Generation != "" || q.RouterUniverse != "") {
					row.Pricing = nil
					row.GenerationPricing = generationPrice(l.Model, l.Source)
					break
				}
			}
		}
		if row.Access = accessOf(offered, len(row.BYOKProviders) > 0); row.Access != "" {
			view.Data = append(view.Data, row)
		}
	}
	for id, providers := range byok {
		if !listed[id] {
			view.Data = append(view.Data, catalogAccessRow{ID: id, Name: id, Access: accessBYOK, State: stateUnknown,
				BYOKProviders: sortedKeys(providers), ManagedSources: []string{}})
		}
	}
	sort.Slice(view.Data, func(i, j int) bool { return view.Data[i].ID < view.Data[j].ID })
	for _, row := range view.Data {
		switch row.Access {
		case accessBoth:
			view.Stats.Both++
		case accessManaged:
			view.Stats.ManagedOnly++
		default:
			view.Stats.BYOKOnly++
		}
	}
	view.Stats.CanonicalModels = len(view.Data)
	return view
}

// orgBYOKModels maps each canonical model to the org's providers with
// credentials that serve it: the models a provider lists, or, when it lists
// none, every catalog model of the vendor its id names ("anthropic") or of
// the managed provider whose id it reuses with the org's own key.
func orgBYOKModels(merged []*catalog.CanonicalModel, orgCfg *tenant.OrgConfig) map[string]map[string]bool {
	out := map[string]map[string]bool{}
	add := func(model, provider string) {
		if out[model] == nil {
			out[model] = map[string]bool{}
		}
		out[model][provider] = true
	}
	if orgCfg == nil {
		return out
	}
	for id, pc := range orgCfg.Providers {
		if !orgHasCredentials(orgCfg, id) {
			continue
		}
		if len(pc.Models) > 0 {
			for _, m := range pc.Models {
				add(catalog.Canonical(id, m), id)
			}
			continue
		}
		vendor, isVendor := catalog.ProviderVendor(id)
		for _, cm := range merged {
			if isVendor && strings.HasPrefix(cm.ID, vendor+"/") || listsProvider(cm, id) {
				add(cm.ID, id)
			}
		}
	}
	return out
}

func listsProvider(cm *catalog.CanonicalModel, providerID string) bool {
	for _, l := range cm.Listings {
		if l.ProviderID == providerID {
			return true
		}
	}
	return false
}

func anyGlob(patterns []string, values ...string) bool {
	for _, p := range patterns {
		for _, v := range values {
			if catalog.MatchGlob(p, v) {
				return true
			}
		}
	}
	return false
}

// stateRank orders the states from most to least usable.
var stateRank = map[string]int{catalog.StateActive: 0, catalog.StateDegraded: 1, catalog.StateDeprecated: 2, catalog.StateBlocked: 3}

// bestState is the most usable state among the listings (only the ones
// sold managed, at a fixed price, when sellable is set).
func bestState(listings []catalog.Listing, sellable bool) string {
	best := ""
	for _, l := range listings {
		if sellable && !l.Model.ManagedBillable(l.Source) {
			continue
		}
		if r, ok := stateRank[l.Model.State]; ok && (best == "" || r < stateRank[best]) {
			best = l.Model.State
		}
	}
	if best == "" {
		return stateUnknown
	}
	return best
}

// cheapestPricing is the admitted endpoint with the lowest prompt plus
// completion price, at the price the org pays. Callers supply only endpoints
// admitted for the exact advertised listing; variants cannot lend it prices.
func cheapestOfferPrices(endpoints []catalog.Endpoint) *catalog.Prices {
	var best *catalog.Endpoint
	for i := range endpoints {
		e := &endpoints[i]
		if !e.DynamicPrice && (best == nil || e.Prices.Prompt+e.Prices.Completion < best.Prices.Prompt+best.Prices.Completion) {
			best = e
		}
	}
	if best == nil {
		return nil
	}
	return &best.Prices
}

func cheapestPricing(endpoints []catalog.Endpoint, factor float64) *catalog.PricingView {
	p := cheapestOfferPrices(endpoints)
	if p == nil {
		return nil
	}
	view := catalog.Prices{
		Prompt: billedPrice(p.Prompt, factor), Completion: billedPrice(p.Completion, factor),
		CacheRead: billedPrice(p.CacheRead, factor), CacheWrite: billedPrice(p.CacheWrite, factor),
		Request: billedPrice(p.Request, factor), Image: billedPrice(p.Image, factor),
		WebSearch: billedPrice(p.WebSearch, factor), Reasoning: billedPrice(p.Reasoning, factor),
	}.View()
	return &view
}

// billedPrice is a list price with the markup factor, rounded to 1e-12 USD
// so it prints as the decimal it is ("0.0000021", not "0.0000021000000000000002").
func billedPrice(list, factor float64) float64 { return math.Round(list*factor*1e12) / 1e12 }

func accessOf(managed, byok bool) string {
	switch {
	case managed && byok:
		return accessBoth
	case managed:
		return accessManaged
	case byok:
		return accessBYOK
	}
	return ""
}

func sortedKeys(set map[string]bool) []string {
	out := make([]string, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
