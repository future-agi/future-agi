package modeldb

import (
	"regexp"
	"slices"
	"strings"
	"time"
)

// DecisionKeyPrefix namespaces decision-model prices. A decision model can
// share its name with a chat model (gpt-6-luna is both) at a different price,
// so the two never look each other up.
const DecisionKeyPrefix = "decision:"

// decisionModels are the decision models with a vendor-confirmed price, in
// USD per million input tokens. Every one of them bills nothing for output.
var decisionModels = map[string]struct {
	provider     string
	inputPerMTok float64
}{
	"jev-1.13.0":            {"typesafe", 0.042},
	"jev-1.14.0":            {"typesafe", 0.042},
	"jev-latest":            {"typesafe", 0.042},
	"pplx-decider-v1.1-27b": {"perplexity", 0.02},
	"pplx-decider-v1-27b":   {"perplexity", 0.02},
	"gpt-6-luna":            {"openai", 0.10},
}

// addDecisionModels adds the decision models to bundled under their
// "decision:" keys.
func addDecisionModels(bundled map[string]*ModelInfo) {
	for id, m := range decisionModels {
		key := DecisionKeyPrefix + id
		bundled[key] = &ModelInfo{
			ID:       key,
			Provider: m.provider,
			Mode:     ModeDecision,
			Pricing:  PricingInfo{InputPerToken: m.inputPerMTok / 1_000_000},
		}
	}
}

// DecisionPricing returns a decision model's price. It is DecisionPricingAny
// for a model known by one name.
func (db *ModelDB) DecisionPricing(model string) (*PricingInfo, bool) {
	return db.DecisionPricingAny(model)
}

// DecisionPricingAny returns the price of a decision model that goes by
// several names, most specific first (a call's dated resolved name, the name
// it was sent upstream, the caller's provider-qualified name). It looks only
// under "decision:" keys and never falls back to a chat model of the same
// name.
//
// Its exact candidates are every name as given, in order, then every name's
// last path segment ("jev-1.13.0" from "typesafe/jev-1.13.0"), in order. For Jev
// only, its normalized candidates are each last segment without its release
// date, then the version it names, so OpenRouter's "typesafe/jev-1.13" and the
// dated "typesafe/jev-1.13-20260917" it answers with are priced as jev-1.13.0
// (see jevVersionCandidates). It tries, stopping at the first price:
//  1. operator overrides under the exact candidates;
//  2. operator overrides under the normalized candidates;
//  3. any price under the exact candidates;
//  4. any price under the normalized candidates.
//
// So an operator's price for any name the call goes by, such as
// "decision:typesafe/jev-1.13", "decision:openrouter/typesafe/jev-1.13",
// "decision:jev-latest" or the resolved version's "decision:jev-1.14.0", always
// wins over a bundled price, and an exact name wins over a normalized one.
// Within a tier, every full name wins over every last segment, so a generic
// "decision:jev-1.13.0" never outranks the provider-specific
// "decision:openrouter/typesafe/jev-1.13" whatever form the answer's name has.
//
// GET /v1/decisions/models prices an entry from its upstream and listed names
// only: it cannot know what an alias resolves to. A "~typesafe/jev-latest" call
// answered as "typesafe/jev-1.14-20261015" is charged an override on
// "decision:jev-1.14.0", while the listing shows jev-latest's price.
//
// Known limitation (an owner decision, pinned by
// TestDecisionPricingTwoShortNameOverridesFollowTheAnswersForm): with
// operator overrides on two short names of one model, the charge depends on
// the form of the answer. With A on "decision:jev-1.13" and B on
// "decision:jev-1.13.0", a call sent as "typesafe/jev-1.13" is charged A when
// the answer is dated ("typesafe/jev-1.13-20260917": its last segment is not
// an exact key, and "jev-1.13" is) and B when it is undated
// ("typesafe/jev-1.13.0": its last segment is). The rule today: an override on
// the resolved version wins over a sent name's short-name override when the
// answer is undated. The listing shows A. The alias pair "jev-latest" and
// "jev-1.14.0" behaves the same way.
func (db *ModelDB) DecisionPricingAny(names ...string) (*PricingInfo, bool) {
	exact := slices.Clone(names)
	var normalized []string
	for _, name := range names {
		last := lastPathSegment(name)
		if last != name {
			exact = append(exact, last)
		}
		normalized = append(normalized, jevVersionCandidates(last)...)
	}
	for _, overriddenOnly := range []bool{true, false} {
		for _, ids := range [][]string{exact, normalized} {
			for _, id := range ids {
				key := DecisionKeyPrefix + id
				if overriddenOnly && !db.priceOverridden[key] {
					continue
				}
				if m, ok := db.models[key]; ok && m.Pricing.HasPricing() {
					return &m.Pricing, true
				}
			}
		}
	}
	return nil, false
}

// DecisionPricingExact returns the price under "decision:<model>" only: no
// last path segment, no Jev normalization, so "openai/gpt-6-luna" is not
// priced as the decision model "gpt-6-luna".
func (db *ModelDB) DecisionPricingExact(model string) (*PricingInfo, bool) {
	if m, ok := db.models[DecisionKeyPrefix+model]; ok && m.Pricing.HasPricing() {
		return &m.Pricing, true
	}
	return nil, false
}

// lastPathSegment returns what follows a model name's last "/", or the name
// itself when there is no "/" or nothing follows it.
func lastPathSegment(model string) string {
	if idx := strings.LastIndexByte(model, '/'); idx >= 0 && idx < len(model)-1 {
		return model[idx+1:]
	}
	return model
}

// jevVersion matches the Jev names OpenRouter uses: "jev-<major>.<minor>",
// optionally with a ".<patch>", optionally with a "-YYYYMMDD" release date.
var jevVersion = regexp.MustCompile(`^jev-(\d+)\.(\d+)(?:\.(\d+))?(?:-(\d{8}))?$`)

// jevVersionCandidates returns the other names a Jev model name may be priced
// under, most specific first: the name without its release date, then the
// "jev-<major>.<minor>.<patch>" version it names, with a missing patch read as
// ".0". So "jev-1.13-20260917" gives "jev-1.13" and "jev-1.13.0". A patch that
// is given is kept as is ("jev-1.13.2" stays "jev-1.13.2"), so a name is never
// priced as another version. A name that is not a Jev version, including one
// whose suffix is not a calendar date, gives none.
func jevVersionCandidates(name string) []string {
	m := jevVersion.FindStringSubmatch(name)
	if m == nil {
		return nil
	}
	var out []string
	if date := m[4]; date != "" {
		if _, err := time.Parse("20060102", date); err != nil {
			return nil
		}
		out = append(out, strings.TrimSuffix(name, "-"+date))
	}
	patch := m[3]
	if patch == "" {
		patch = "0"
	}
	if normalized := "jev-" + m[1] + "." + m[2] + "." + patch; normalized != name && !slices.Contains(out, normalized) {
		out = append(out, normalized)
	}
	return out
}

// CalculateDecisionCost prices a decision call from the vendor's token counts.
func (db *ModelDB) CalculateDecisionCost(model string, inputTokens, outputTokens int) (float64, bool) {
	return db.CalculateDecisionCostAny([]string{model}, inputTokens, outputTokens)
}

// CalculateDecisionCostAny prices a decision call whose model goes by several
// names, as DecisionPricingAny does, from the vendor's token counts.
func (db *ModelDB) CalculateDecisionCostAny(names []string, inputTokens, outputTokens int) (float64, bool) {
	pricing, ok := db.DecisionPricingAny(names...)
	if !ok {
		return 0, false
	}
	return pricing.Calculate(inputTokens, outputTokens, CostOptions{}), true
}
