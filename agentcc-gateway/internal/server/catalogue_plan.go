package server

import (
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// mixedCataloguePlan rejects a declared mixed plan before the primary's hold
// or I/O. Hop admission remains a second fence for undeclared provider failover
// and catalogue changes. This is read-only resolution: no credential creation,
// ledger operation, policy mutation, or upstream request.
func (h *Handlers) mixedCataloguePlan(rc *models.RequestContext, orgCfg *tenant.OrgConfig, primary *models.PriceCard) bool {
	if !h.catalog.Load().MixedCatalogueDisabled() || rc.AttemptCount() != 0 || orgCfg == nil || !orgCfg.ManagedModelsEnabled() || rc.Metadata["key_type"] == "internal" || h.registry == nil {
		return false
	}
	// The plan dispatch would walk, from the one resolver dispatch uses, on
	// the route routing fixed just before this admission. At pool admission
	// auto has chosen no route yet (the zero route), so only the caller's
	// declared plan is judged; resolveRoute judges the chosen model again
	// after auto installs its actual (possibly empty) fallback plan.
	chain := h.effectiveFallbackPlan(rc, rc.FallbackRoute())
	if len(chain) == 0 {
		return false
	}
	inclusive, token := false, false
	see := func(card *models.PriceCard) {
		if card == nil {
			return
		}
		if card.Inclusive != nil {
			inclusive = true
		} else {
			token = true
		}
	}
	see(primary)
	for _, name := range chain {
		// An org-owned listing or alias takes precedence over the catalogue.
		if _, _, ok := orgMappedRoute(orgCfg, name); ok {
			continue
		}
		if _, pc := nextOrgProviderListing(orgCfg, name, "", false); pc != nil {
			continue
		}
		p, upstream, ok := h.registry.ResolveManaged(name, h.catalogHas)
		if !ok {
			if id, _ := h.managedCatalogID(name); id != "" {
				p, upstream, ok = h.registry.ResolveManaged(id, h.catalogHas)
			}
		}
		if !ok || orgHasCredentials(orgCfg, p.ID()) {
			continue
		}
		card, priced := h.catalog.Load().PriceCard(p.ID(), upstream, "")
		if priced && card.Billable() {
			see(&card)
		}
		if inclusive && token {
			return true
		}
	}
	return false
}
