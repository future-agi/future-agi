package cost

import (
	"slices"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/modeldb/orgdb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// cardFor returns the card a token-priced attempt is priced on, and the cost
// source it records when the upstream reported no charge (BPC#11): the
// served endpoint's card, else the reserve card when the served endpoint of
// an endpoint set is unknown (catalog_max), else the provider's price for
// the model. A BYOK attempt with the org's own price for the model
// (orgCard: custom_pricing, then the model_database override) is priced on
// it, ahead of any reported charge: that price is the org's own accounting.
// A managed attempt never is: it stays on FutureAGI's card. An empty card
// leaves the attempt unpriced.
//
// A BYOK hop down a model fallback chain (movesDownModelChain) is looked up
// in the catalog under its own model only, so it takes its own listed price
// or none. modelCandidates would add the model asked for when the hop's host
// answers naming the id it was sent, which makes the hop's model the
// resolved one: the hop, another model than the one asked for, would take
// that model's listed price. A managed attempt's lookup is not changed here.
func (p *Plugin) cardFor(rc *models.RequestContext, a *models.Attempt, serving, managed bool, usage *models.Usage) (models.PriceCard, models.CostSource) {
	if !managed {
		if card, ok := p.orgCard(rc, a, endpointID(rc, a)); ok {
			if usage != nil {
				usage.Cost = nil // usage is the attempt's own copy
			}
			return card, models.CostSourceOrgCustom
		}
	}
	if managed {
		if card, source, ok := endpointSetCard(rc, a, serving); ok {
			return card, source
		}
		if a.Pricing != nil && a.Pricing.ReservePrice != nil {
			card := a.Pricing.ReservePrice
			return *card, costSourceOf(card.Source)
		}
		if a.Pricing == nil && serving && rc.Billing.ReservePrice != nil {
			return *rc.Billing.ReservePrice, costSourceOf(rc.Billing.ReservePrice.Source)
		}
	}
	var candidates []string
	if !managed && movesDownModelChain(a.Kind) {
		candidates = []string{a.Model}
	} else {
		candidates = modelCandidates(rc, a.Model)
	}
	for _, model := range candidates {
		if card, source, ok := p.pricer().Card(a.Provider, model, endpointID(rc, a), managed); ok {
			if !managed {
				card = scaleCard(card, p.aliasFactor(rc))
			}
			return card, source
		}
	}
	return models.PriceCard{}, models.CostSourceUnpriced
}

// endpointSetCard prices a managed attempt inside an endpoint set: the card
// of the endpoint that served (the set's only endpoint when the upstream
// names none), or, when that is unknown, the reserve card (the highest in the
// set), recorded as catalog_max.
func endpointSetCard(rc *models.RequestContext, a *models.Attempt, serving bool) (models.PriceCard, models.CostSource, bool) {
	set, reserve := rc.Billing.EndpointSet, rc.Billing.ReservePrice
	if a.Pricing != nil {
		set, reserve = a.Pricing.EndpointSet, a.Pricing.ReservePrice
	}
	if len(set) == 0 {
		return models.PriceCard{}, "", false
	}
	served := a.ServedUpstreamProvider
	if served == "" && serving && a.Pricing == nil {
		served = rc.Billing.ServedUpstreamProvider
	}
	if served != "" {
		var matched *models.PriceCard
		ambiguous := false
		for _, ref := range set {
			if strings.EqualFold(strings.TrimSpace(ref.UpstreamProvider), strings.TrimSpace(served)) ||
				(ref.ProviderName != "" && strings.EqualFold(strings.TrimSpace(ref.ProviderName), strings.TrimSpace(served))) {
				if matched != nil && *matched != ref.Price {
					ambiguous = true
				}
				card := ref.Price
				matched = &card
			}
		}
		if matched != nil && !ambiguous {
			return *matched, costSourceOf(matched.Source), true
		}
	} else if len(set) == 1 {
		// A set of one endpoint names the endpoint that served when the
		// upstream does not say: a direct price-card source reports no served
		// provider, and its route's set is its card's one endpoint (W2
		// integration: G10's endpoint sets on direct managed routes).
		return set[0].Price, costSourceOf(set[0].Price.Source), true
	}
	if reserve != nil {
		return *reserve, models.CostSourceCatalogMax, true
	}
	return models.PriceCard{}, "", false
}

// orgCard is the org's own price for the model a called (orgdb's
// precedence, NG-14), tried on orgCardModels in order: its
// cost_tracking.custom_pricing entry while cost tracking is on; else its
// model_database override's prices merged field by field on top of the
// price the model database has for that model (an override that prices only
// input keeps the database's output price). Either is recorded as
// org_custom.
func (p *Plugin) orgCard(rc *models.RequestContext, a *models.Attempt, endpoint string) (models.PriceCard, bool) {
	if p.tenantStore == nil {
		return models.PriceCard{}, false
	}
	orgCfg := p.tenantStore.Get(rc.Metadata[tenant.MetadataKeyOrgID])
	if orgCfg == nil || !orgHasPrices(orgCfg) {
		return models.PriceCard{}, false
	}
	candidates := orgCardModels(rc, a)
	for _, m := range candidates {
		if cp := orgdb.CustomPrice(orgCfg, a.Provider, m); cp != nil {
			return orgdb.CustomCard(cp), true
		}
	}
	for _, m := range candidates {
		if ov := orgdb.Override(orgCfg, a.Provider, m); orgdb.HasPrice(ov) {
			base, _, _ := p.pricer().Card(a.Provider, m, endpoint, false)
			return orgdb.PriceCard(ov, base)
		}
	}
	return models.PriceCard{}, false
}

// orgHasPrices reports whether orgCfg holds any own price orgCard reads: a
// custom_pricing entry while cost tracking is on, or a model_database
// override. Without one, orgCard looks nothing up (and builds no candidates).
func orgHasPrices(orgCfg *tenant.OrgConfig) bool {
	ct := orgCfg.CostTracking
	if ct != nil && ct.Enabled && len(ct.CustomPricing) > 0 {
		return true
	}
	return orgCfg.ModelDatabase != nil && len(orgCfg.ModelDatabase.Overrides) > 0
}

// orgCardModels are the models a BYOK attempt is looked up under in the org's
// own prices. An org's provider serves "<provider>/<id>" when the org
// registered <id> for it, and is sent <id> (REL-OWNKEY), so the attempt
// records <id>, or "<provider>/<id>" for an adapter that cuts the prefix
// itself from an id with a slash of its own, while the org may have priced
// either the id asked, which the request log records as model, or the id it
// registered. In order:
//
//   - the model the client asked for, when it names a's model on a's
//     provider: with both ids priced, the asked id's price wins, as it did
//     when the attempt recorded the asked id;
//   - a's model;
//   - a's model in the other form an org provider match accepts
//     (otherModelForm), which is how a model fallback hop, whose asked id
//     the request does not keep, finds its own price in either form;
//   - the model the client asked for when a called the resolved one
//     (modelCandidates): an alias the org priced. Never for a hop down a
//     model fallback chain (movesDownModelChain): the hop is another model
//     than the one asked for, and when its host answers naming the id it was
//     sent, the hop's model is the resolved one, so this step would price the
//     hop at the primary's price.
func orgCardModels(rc *models.RequestContext, a *models.Attempt) []string {
	out := make([]string, 0, 4)
	add := func(m string) {
		if m != "" && !slices.Contains(out, m) {
			out = append(out, m)
		}
	}
	if namesModel(rc.Model, a.Provider, a.Model) {
		add(rc.Model)
	}
	add(a.Model)
	add(otherModelForm(a.Provider, a.Model))
	if !movesDownModelChain(a.Kind) {
		for _, m := range modelCandidates(rc, a.Model) {
			add(m)
		}
	}
	return out
}

// movesDownModelChain reports whether an attempt of kind moved down a model
// fallback chain (routing.FallbackAttemptKind): a model fallback hop, or an
// auto-routed request's runner-up, whose rc.Model is the candidate chosen
// first.
func movesDownModelChain(kind models.AttemptKind) bool {
	return kind == models.AttemptModelFallback || kind == models.AttemptAutoFallback
}

// namesModel reports whether asked names model on provider the way an org
// provider match does (the server's orgModelMatches): asked is model, or
// "<provider>/<model>" with the prefix in any case.
func namesModel(asked, provider, model string) bool {
	if asked == model {
		return true
	}
	prefix, rest, ok := strings.Cut(asked, "/")
	return ok && provider != "" && strings.EqualFold(prefix, provider) && rest == model
}

// otherModelForm is model's other form on provider: model without its
// "<provider>/" prefix when it has one, else "<provider>/<model>". It is ""
// when provider is unknown.
func otherModelForm(provider, model string) string {
	if provider == "" || model == "" {
		return ""
	}
	if prefix, rest, ok := strings.Cut(model, "/"); ok && strings.EqualFold(prefix, provider) {
		return rest
	}
	return provider + "/" + model
}

// aliasFactor is the configured cost factor of the request's model alias,
// or 1.
func (p *Plugin) aliasFactor(rc *models.RequestContext) float64 {
	alias := rc.Metadata["model_alias"]
	if alias == "" {
		return 1
	}
	for prefix, factor := range p.aliasCostFactors {
		if strings.HasPrefix(alias, prefix) {
			return factor
		}
	}
	return 1
}

func scaleCard(c models.PriceCard, f float64) models.PriceCard {
	if f == 1 {
		return c
	}
	c.Prompt, c.Completion, c.CacheRead, c.CacheWrite = c.Prompt*f, c.Completion*f, c.CacheRead*f, c.CacheWrite*f
	c.Request, c.Image, c.WebSearch, c.Reasoning = c.Request*f, c.Image*f, c.WebSearch*f, c.Reasoning*f
	return c
}

// modelCandidates are the models an attempt's price is looked up under: the
// model it called, then the model the client asked for when the attempt
// called the resolved one (an alias the price table does not know).
func modelCandidates(rc *models.RequestContext, model string) []string {
	if model == rc.ResolvedModel && rc.Model != "" && rc.Model != model {
		return []string{model, rc.Model}
	}
	return []string{model}
}

func endpointID(rc *models.RequestContext, a *models.Attempt) string {
	if a.EndpointID != "" {
		return a.EndpointID
	}
	return rc.Billing.EndpointID
}
