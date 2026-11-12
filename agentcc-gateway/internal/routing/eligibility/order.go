package eligibility

import (
	"sort"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing/reqopts"
)

// The caller's provider.order, provider.sort and provider.allow_fallbacks,
// OpenRouter's meaning on every route:
//   - order names upstream provider slugs to try first, in that order;
//   - sort ranks the endpoints by price, throughput or latency;
//   - allow_fallbacks false keeps the request on the providers order names
//     (or, with no order, on the first-ranked one).
//
// A BYOK aggregator (OpenRouter) is forwarded the caller's provider object
// and applies them itself. On a managed candidate the gateway applies them
// to the endpoints it pins (EndpointSet), from the catalogue's per-endpoint
// prices, throughput and latency; a managed candidate with no endpoint list,
// or none with the figure a sort ranks by, cannot honour order or sort and
// is refused rather than served unsorted. Any other candidate is one
// endpoint, its gateway provider: order and sort are met as they are, and
// allow_fallbacks false with an order that does not name it refuses it.
// sort.partition "none" (one ranking across the models[] chain) is refused
// off an aggregator: the gateway ranks endpoints within each model.

// CodeProviderPreferenceUnavailable refuses a provider.order or
// provider.sort the gateway cannot honour for the candidate (400).
const CodeProviderPreferenceUnavailable = "provider_preference_unavailable"

func init() {
	for _, f := range []string{
		reqopts.FieldProviderOrder,
		reqopts.FieldProviderSort,
		reqopts.FieldProviderAllowFallbacks,
	} {
		reqopts.MarkSupported(f)
	}
}

// noFallbacks reports whether p sets allow_fallbacks false.
func noFallbacks(p *models.ProviderPrefs) bool {
	return p != nil && p.AllowFallbacks != nil && !*p.AllowFallbacks
}

// ordersEndpoint reports whether order names e: its upstream provider slug,
// or its tag ("<upstream_provider>/<variant>"), as provider.only matches.
func ordersEndpoint(order []string, e *catalog.Endpoint) bool {
	return orderIndex(order, e) < len(order)
}

// orderIndex is the position in order of the first entry naming e,
// len(order) when none does.
func orderIndex(order []string, e *catalog.Endpoint) int {
	for i, s := range order {
		s = strings.TrimSpace(s)
		if (e.UpstreamProvider != "" && strings.EqualFold(s, e.UpstreamProvider)) || (e.Tag != "" && strings.EqualFold(s, e.Tag)) {
			return i
		}
	}
	return len(order)
}

// sortFigure is e's figure for a sort by by, lower first, and whether e has
// one: its blended price (3 prompt to 1 completion, as auto weighs them),
// its median latency, or its median throughput negated.
func sortFigure(by string, e *catalog.Endpoint) (float64, bool) {
	switch by {
	case "price":
		if e.DynamicPrice {
			return 0, false
		}
		return (3*e.Prices.Prompt + e.Prices.Completion) / 4, true
	case "latency":
		if e.LatencyP50Ms == nil {
			return 0, false
		}
		return *e.LatencyP50Ms, true
	case "throughput":
		if e.ThroughputP50 == nil {
			return 0, false
		}
		return -*e.ThroughputP50, true
	}
	return 0, false
}

// checkProviderOrder is the rule for provider.order, provider.sort and
// provider.allow_fallbacks (see above).
func checkProviderOrder(c *checker) (string, string) {
	p := c.in.Prefs
	if p == nil || c.forwarded() || (len(p.Order) == 0 && p.Sort == nil && p.AllowFallbacks == nil) {
		return "", ""
	}
	if p.Sort != nil && p.Sort.Partition == "none" {
		return CodeProviderPreferenceUnavailable,
			"provider.sort.partition none is not supported here: endpoints are sorted within each model"
	}
	const unmatched = "no provider of this model is in the request's provider.order, and provider.allow_fallbacks is false"
	switch {
	case c.hasEndpoints:
		if noFallbacks(p) && len(p.Order) > 0 && !c.narrow(func(e *catalog.Endpoint) bool { return ordersEndpoint(p.Order, e) }) {
			return CodeNoProviderMatches, unmatched
		}
		if p.Sort != nil && !c.anyEndpoint(func(e *catalog.Endpoint) bool { _, ok := sortFigure(p.Sort.By, e); return ok }) {
			return CodeProviderPreferenceUnavailable,
				"provider.sort " + p.Sort.By + " cannot be honoured: the gateway has no " + p.Sort.By + " figures for this model's providers"
		}
	case c.unpinnable():
		if len(p.Order) > 0 || p.Sort != nil {
			return CodeProviderPreferenceUnavailable,
				"provider.order and provider.sort cannot be honoured for this model: the gateway has no list of its providers"
		}
	default:
		if noFallbacks(p) && len(p.Order) > 0 && !equalFoldAny(p.Order, c.in.ProviderID) {
			return CodeNoProviderMatches, unmatched
		}
	}
	return "", ""
}

// anyEndpoint reports whether an eligible endpoint satisfies ok.
func (c *checker) anyEndpoint(ok func(e *catalog.Endpoint) bool) bool {
	eps := c.in.Entry.Endpoints
	for i := range eps {
		if c.alive.has(i) && ok(&eps[i]) {
			return true
		}
	}
	return false
}

// arrange applies p's sort and order to set, already ranked: sort re-ranks
// it by its figure (an endpoint without one keeps its rank after those with
// one), then order moves the endpoints it names to the front, in its order;
// allow_fallbacks false with no order keeps only the first. The rule has
// already narrowed the set to order's endpoints when allow_fallbacks is
// false.
func arrange(set []*catalog.Endpoint, p *models.ProviderPrefs) []*catalog.Endpoint {
	if p == nil || len(set) == 0 {
		return set
	}
	if p.Sort != nil {
		by := p.Sort.By
		sort.SliceStable(set, func(i, j int) bool {
			fi, oki := sortFigure(by, set[i])
			fj, okj := sortFigure(by, set[j])
			if oki != okj {
				return oki
			}
			return oki && fi < fj
		})
	}
	if len(p.Order) > 0 {
		sort.SliceStable(set, func(i, j int) bool {
			return orderIndex(p.Order, set[i]) < orderIndex(p.Order, set[j])
		})
	} else if noFallbacks(p) {
		set = set[:1]
	}
	return set
}
