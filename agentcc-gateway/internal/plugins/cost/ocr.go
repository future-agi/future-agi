package cost

import (
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// priceEndpoint prices the attempt that served rc on its endpoint's own
// units, in USD: OCR per page here, every other unit-priced endpoint through
// priceModality. unit is false for an endpoint priced on tokens.
//
// Like a token-priced attempt (cardFor), it is priced on the model it called,
// else on the model the client asked for (modelCandidates): a route or a
// routing target may send a model the gateway has no price for, a deployment
// name say, and the request then takes the requested model's price, so its
// cost estimate and the budgets that count it are not left empty. source is
// the source of the price used.
func (p *Plugin) priceEndpoint(rc *models.RequestContext, a *models.Attempt) (usd float64, source models.CostSource, ok, unit bool) {
	if usd, source, ok, unit = p.priceUnits(rc, a); ok || !unit {
		return usd, source, ok, unit
	}
	if candidates := modelCandidates(rc, a.Model); len(candidates) > 1 {
		asked := *a
		asked.Model = candidates[1]
		return p.priceUnits(rc, &asked)
	}
	return usd, source, ok, unit
}

// priceUnits prices a on its endpoint's own units (priceEndpoint).
func (p *Plugin) priceUnits(rc *models.RequestContext, a *models.Attempt) (usd float64, source models.CostSource, ok, unit bool) {
	if rc.EndpointType == "ocr" {
		usd, source, ok = p.ocrCost(rc, a)
		return usd, source, ok, true
	}
	return p.priceModality(rc, a)
}

// ocrCost is the processed pages times the model's per-page price, plus the
// annotated pages times its per-annotated-page price:
// usage_info.pages_processed × ocr_cost_per_page + annotated pages ×
// annotation_cost_per_page. Pages are OCR's usage unit, so the processed
// pages are recorded on the request whether or not the model has a price,
// BYOK included (free but metered).
func (p *Plugin) ocrCost(rc *models.RequestContext, a *models.Attempt) (float64, models.CostSource, bool) {
	resp := rc.OCRResponse
	pages, _ := resp.PagesProcessed()
	rc.Metadata[models.OCRPagesMetadataKey] = strconv.Itoa(pages)
	perPage, perAnnotated, ok := p.pricer().modelDB().OCRPrices(a.Provider, a.Model)
	if !ok {
		return 0, models.CostSourceUnpriced, false
	}
	return float64(pages)*perPage + float64(resp.AnnotatedPages())*perAnnotated, models.CostSourceModelDB, true
}
