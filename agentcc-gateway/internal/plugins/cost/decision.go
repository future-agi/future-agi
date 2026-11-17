package cost

import (
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/modeldb/orgdb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// decisionCard returns the card a decision call (EndpointType "decision") is
// priced on, and the cost source it records. A decision call runs on the
// caller's own provider key, which the vendor bills, so it is priced like a
// BYOK attempt: the org's own cost_tracking.custom_pricing entry under any
// name the call goes by wins (org_custom), else the model database's
// "decision:" price (modeldb.DecisionPricing), else nothing and the call is
// recorded as unpriced.
//
// It never reads a chat price: not the price card or model database entry of
// a chat model with the same name, not the org's model_database override
// (a chat override), not the requested-model fallback. A decision model can
// share a chat model's name (gpt-6-luna is both) at another price.
func (p *Plugin) decisionCard(rc *models.RequestContext, a *models.Attempt, usage *models.Usage) (models.PriceCard, models.CostSource) {
	if p.tenantStore != nil {
		if orgCfg := p.tenantStore.Get(rc.Metadata[tenant.MetadataKeyOrgID]); orgCfg != nil {
			for _, m := range orgDecisionNames(rc) {
				if cp := orgdb.CustomPrice(orgCfg, a.Provider, m); cp != nil {
					if usage != nil {
						usage.Cost = nil // usage is the attempt's own copy; the org's price is its own accounting
					}
					return orgdb.CustomCard(cp), models.CostSourceOrgCustom
				}
			}
		}
	}
	if db := p.pricer().modelDB(); db != nil {
		for _, m := range decisionModelNames(rc) {
			if pricing, ok := db.DecisionPricing(m); ok {
				return models.PriceCard{
					Prompt:     pricing.InputPerToken,
					Completion: pricing.OutputPerToken,
					Source:     models.PriceSourceModelDB,
				}, models.CostSourceModelDB
			}
		}
	}
	return models.PriceCard{}, models.CostSourceUnpriced
}

// orgDecisionNames are the names a decision call is looked up under in the
// org's custom pricing, in order: the model that answered (else the one the
// caller named), the one the caller named, then decisionModelNames.
func orgDecisionNames(rc *models.RequestContext) []string {
	names := make([]string, 0, 6)
	for _, m := range append([]string{rc.ResolvedModel, rc.Model}, decisionModelNames(rc)...) {
		if m != "" && !slices.Contains(names, m) {
			names = append(names, m)
		}
	}
	return names
}

// decisionModelNames lists the names a decision call's model goes by: the
// model the vendor reports, the one it was sent, the one the caller named,
// and the provider-qualified id GET /v1/decisions/models lists and prices
// ("typesafe/jev-latest"), so a bare "jev-latest" is charged the price the
// listing shows.
func decisionModelNames(rc *models.RequestContext) []string {
	names := make([]string, 0, 5)
	add := func(name string) {
		if name != "" && !slices.Contains(names, name) {
			names = append(names, name)
		}
	}
	add(rc.ResolvedModel)
	upstream := ""
	if rc.DecisionRequest != nil {
		upstream = rc.DecisionRequest.UpstreamModel
		add(upstream)
	}
	add(rc.Model)
	if rc.Provider != "" {
		for _, m := range []string{upstream, rc.Model} {
			if m != "" && !strings.HasPrefix(m, rc.Provider+"/") {
				add(rc.Provider + "/" + m)
			}
		}
	}
	return names
}
