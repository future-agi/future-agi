package cost

import (
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// searchCost prices a search request (EndpointType "search"), in USD: one
// query at its search provider's per-query price (modeldb.SearchPrice). A
// search has no model; the price table lists it under
// "<search provider>/search". ok is false when that entry has no flat
// per-query price: the search is then recorded as unpriced, never as free.
func (p *Plugin) searchCost(rc *models.RequestContext) (float64, models.CostSource, bool) {
	if rc.SearchRequest == nil {
		return 0, models.CostSourceUnpriced, false
	}
	price, ok := p.pricer().modelDB().SearchPrice(rc.SearchRequest.SearchProvider)
	if !ok {
		return 0, models.CostSourceUnpriced, false
	}
	return price, models.CostSourceModelDB, true
}

// unpricedModel names the price an unpriced attempt is missing, for the
// operator's warning: a search's "<search provider>/search" entry (a search
// names no model), else the attempt's model.
func unpricedModel(rc *models.RequestContext, a *models.Attempt) string {
	if rc.EndpointType == "search" && rc.SearchRequest != nil {
		return modeldb.SearchPriceKey(rc.SearchRequest.SearchProvider)
	}
	return a.Model
}
