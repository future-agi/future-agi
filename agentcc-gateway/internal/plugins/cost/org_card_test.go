package cost

import (
	"context"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An own-key request for "<provider>/<id>" is served by the org's provider
// that registered <id>, and that provider is sent <id>: the
// attempt records <id>, rc.Model keeps the asked "<provider>/<id>" (the
// request log's model), and a real host answers with a dated id
// (rc.ResolvedModel). The org's own price for the model
// (cost_tracking.custom_pricing) applies whether the org keyed it by the id
// asked or by the id registered; when it keyed both, the asked id's price
// wins. A model fallback
// hop is priced on its own model in either form, never on the primary's,
// even when its host answers with the id it was sent, which makes the hop's
// model the request's resolved one. With no org price for its model, a hop
// is priced at its own listed price, or left unpriced when the catalog does
// not list its model, never at the listed price of the model asked for. A
// price keyed "<provider>/<model>" prices a bare <model> served by that
// provider, and no other provider's.
func TestOrgPriceAppliesToTheAskedAndTheRegisteredID(t *testing.T) {
	const (
		haiku      = "claude-haiku-4-5"
		haikuAsked = "anthropic/" + haiku
		haikuDated = "claude-haiku-4-5-20251001"
		down       = "claude-down"
		custom     = "claude-custom-x" // a model the catalog does not list
	)
	byPrice := func(prices map[string]float64) map[string]*tenant.CustomPricing {
		m := make(map[string]*tenant.CustomPricing, len(prices))
		for k, v := range prices {
			m[k] = &tenant.CustomPricing{InputPerMTok: v, OutputPerMTok: v}
		}
		return m
	}
	type attempt struct {
		kind    models.AttemptKind
		model   string
		outcome models.AttemptOutcome
	}
	primary := func(model string) attempt { return attempt{models.AttemptPrimary, model, models.OutcomeSuccess} }
	for _, c := range []struct {
		name          string
		asked         string
		provider      string // the provider of every attempt, unless firstProvider
		firstProvider string // the first attempt's provider, when a later one moved to provider
		managed       bool   // a managed request: every attempt on the platform's credential
		attempts      []attempt
		prices        map[string]float64 // per million tokens, input and output alike
		resolved      string             // the id the host answered with; "" is haikuDated
		cost          string             // 1,000 tokens in and 1,000 out
		source        string
	}{
		{name: "prefixed ask, priced on the asked id", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{haikuAsked: 100},
			cost: "0.200000", source: "org_custom"},
		{name: "prefixed ask, priced on the registered id", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{haiku: 100},
			cost: "0.200000", source: "org_custom"},
		{name: "prefixed ask, both priced: the asked id's price", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{haikuAsked: 100, haiku: 50},
			cost: "0.200000", source: "org_custom"},
		{name: "bare ask, both priced: the bare id's price", asked: haiku, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{haikuAsked: 100, haiku: 50},
			cost: "0.100000", source: "org_custom"},
		{name: "prefix in another case", asked: "Anthropic/" + haiku, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{"Anthropic/" + haiku: 100},
			cost: "0.200000", source: "org_custom"},
		// An Anthropic-format host whose id has a slash of its own is handed
		// "<provider>/<id>", which its adapter cuts once: the attempt records
		// the asked id, and the org may have priced the registered one.
		{name: "slash id, priced on the registered id", asked: "myrouter/anthropic/claude-sonnet-4", provider: "myrouter",
			attempts: []attempt{primary("myrouter/anthropic/claude-sonnet-4")}, prices: map[string]float64{"anthropic/claude-sonnet-4": 100},
			cost: "0.200000", source: "org_custom"},
		{name: "fallback hop, priced on its own asked id", asked: "anthropic/" + down, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, down, models.OutcomeError}, {models.AttemptModelFallback, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{haikuAsked: 100, "anthropic/" + down: 50},
			cost:     "0.200000", source: "org_custom"},
		{name: "fallback hop with no org price of its own: its listed price, not the primary's org price", asked: "anthropic/" + down, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, down, models.OutcomeError}, {models.AttemptModelFallback, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{"anthropic/" + down: 50},
			cost:     "0.006000", source: "modeldb"}, // claude-haiku-4-5: $1 and $5 a million tokens
		{name: "an attempt on another model is not priced on the asked id", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{primary("claude-sonnet-4")}, prices: map[string]float64{haikuAsked: 100},
			cost: "0.006000", source: "modeldb"},
		// A host that answers with the id it was sent makes the serving
		// attempt's model the resolved one. A model fallback hop is another
		// model than the one asked for, so the asked id's price never covers
		// it; an auto-routed request's runner-up is such a hop too, and its
		// rc.Model is the candidate chosen first.
		{name: "echoing host: fallback hop with no org price of its own: its listed price, not the primary's org price", asked: "anthropic/" + down, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, down, models.OutcomeError}, {models.AttemptModelFallback, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{"anthropic/" + down: 50}, resolved: haiku,
			cost: "0.006000", source: "modeldb"},
		{name: "echoing host: bare fallback hop with no org price of its own: its listed price, not the primary's org price", asked: down, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, down, models.OutcomeError}, {models.AttemptModelFallback, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{down: 50}, resolved: haiku,
			cost: "0.006000", source: "modeldb"},
		{name: "echoing host: auto runner-up with no org price of its own: its listed price, not the first candidate's org price", asked: "anthropic/" + down, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, down, models.OutcomeError}, {models.AttemptAutoFallback, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{"anthropic/" + down: 50}, resolved: haiku,
			cost: "0.006000", source: "modeldb"},
		{name: "echoing host: fallback hop priced on its own registered id", asked: "anthropic/" + down, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, down, models.OutcomeError}, {models.AttemptModelFallback, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{"anthropic/" + down: 50, haiku: 100}, resolved: haiku,
			cost: "0.200000", source: "org_custom"},
		// The asked id still prices the model it resolves to on the request's
		// own model: an alias the org priced, on the first try and on a retry.
		{name: "echoing host: a primary that called the resolved model is priced on the asked alias", asked: "my-haiku", provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{"my-haiku": 100}, resolved: haiku,
			cost: "0.200000", source: "org_custom"},
		{name: "echoing host: a retry that called the resolved model is priced on the asked alias", asked: "my-haiku", provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptRetry, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{"my-haiku": 100}, resolved: haiku,
			cost: "0.200000", source: "org_custom"},
		// An org's price keyed "<provider>/<model>" names that model on that
		// provider: it prices the model asked for in either form when that
		// provider serves it, and never when another provider does (custom_pricing
		// wins over the listed price).
		{name: "bare ask on the provider the key names, priced on \"<provider>/<id>\" only: the org's price", asked: haiku, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{haikuAsked: 100},
			cost: "0.200000", source: "org_custom"},
		{name: "bare ask on another provider than the key names: its listed price", asked: haiku, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{"myrouter/" + haiku: 100},
			cost: "0.006000", source: "modeldb"},
		{name: "prefixed ask on another provider than the key names: its listed price", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{"myrouter/" + haiku: 100},
			cost: "0.006000", source: "modeldb"},
		{name: "bare ask served by another provider than the key names: its listed price", asked: haiku, provider: "myrouter",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{haikuAsked: 100},
			cost: "0.006000", source: "modeldb"},
		{name: "prefixed ask served by another provider than the key names: its listed price", asked: "myrouter/" + haiku, provider: "myrouter",
			attempts: []attempt{primary(haiku)}, prices: map[string]float64{haikuAsked: 100},
			cost: "0.006000", source: "modeldb"},
		// A key that is both an id an org provider registered (myrouter's
		// "anthropic/claude-sonnet-4") and "<provider>/<model>" (claude-sonnet-4
		// on anthropic) is read both ways.
		{name: "a key that is also another provider's registered id prices the bare model on the provider it names", asked: "claude-sonnet-4", provider: "anthropic",
			attempts: []attempt{primary("claude-sonnet-4")}, prices: map[string]float64{"anthropic/claude-sonnet-4": 100},
			cost: "0.200000", source: "org_custom"},
		// A provider failover serves the model asked for on another
		// provider. A price keyed by the id asked reaches it only through the
		// step for an alias the org priced (modelCandidates), which runs when
		// the attempt called the resolved model: so a failover of a
		// "<provider>/<model>" ask takes that price when its host answers
		// naming the id it was sent, and its listed price when its host
		// answers a dated id. These pin that behaviour; the
		// failover serves the model asked for, so no other model's price is
		// borrowed. A bare ask's failover never takes a price keyed
		// "<provider>/<model>".
		{name: "provider failover of a prefixed ask, echoing host: the price keyed by the id asked", asked: haikuAsked, provider: "myrouter", firstProvider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptFailover, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{haikuAsked: 100}, resolved: haiku,
			cost: "0.200000", source: "org_custom"},
		{name: "provider failover of a prefixed ask, dated host: its listed price", asked: haikuAsked, provider: "myrouter", firstProvider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptFailover, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{haikuAsked: 100},
			cost:     "0.006000", source: "modeldb"},
		{name: "provider failover of a bare ask, echoing host: a price keyed \"<provider>/<model>\" for the first provider does not price it", asked: haiku, provider: "myrouter", firstProvider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptFailover, haiku, models.OutcomeSuccess}},
			prices:   map[string]float64{haikuAsked: 100}, resolved: haiku,
			cost: "0.006000", source: "modeldb"},
		// With no org price, the catalog's price follows the same rule: a
		// model fallback hop is priced at its own listed price, or left
		// unpriced when the catalog does not list its model, never at the
		// listed price of the model asked for, even when its host answers
		// with the id it was sent. That covers an auto-routed runner-up and a
		// hop onto another of the org's providers.
		{name: "echoing host, no org price: prefixed fallback hop on a model the catalog does not list: unpriced, not the primary's listed price", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptModelFallback, custom, models.OutcomeSuccess}},
			resolved: custom, cost: "", source: "unpriced"},
		{name: "echoing host, no org price: bare fallback hop on a model the catalog does not list: unpriced, not the primary's listed price", asked: haiku, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptModelFallback, custom, models.OutcomeSuccess}},
			resolved: custom, cost: "", source: "unpriced"},
		{name: "echoing host, no org price: auto runner-up on a model the catalog does not list: unpriced, not the first candidate's listed price", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptAutoFallback, custom, models.OutcomeSuccess}},
			resolved: custom, cost: "", source: "unpriced"},
		{name: "echoing host, no org price: prefixed fallback hop onto another org provider's unlisted model: unpriced, not the primary's listed price", asked: "openai/gpt-4o", provider: "myvllm", firstProvider: "openai",
			attempts: []attempt{{models.AttemptPrimary, "gpt-4o", models.OutcomeError}, {models.AttemptModelFallback, "llama-local", models.OutcomeSuccess}},
			resolved: "llama-local", cost: "", source: "unpriced"},
		{name: "echoing host, no org price: bare fallback hop onto another org provider's unlisted model: unpriced, not the primary's listed price", asked: "gpt-4o", provider: "myvllm", firstProvider: "openai",
			attempts: []attempt{{models.AttemptPrimary, "gpt-4o", models.OutcomeError}, {models.AttemptModelFallback, "llama-local", models.OutcomeSuccess}},
			resolved: "llama-local", cost: "", source: "unpriced"},
		// Controls: a host that answers with a dated id never reached the
		// asked id, and a hop on a listed model takes its own listed price,
		// not the primary's (gpt-4o's $50 against claude-haiku-4-5's $1 and
		// $5 a million tokens).
		{name: "dated host, no org price: fallback hop on a model the catalog does not list: unpriced", asked: haikuAsked, provider: "anthropic",
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptModelFallback, custom, models.OutcomeSuccess}},
			resolved: custom + "-20251001", cost: "", source: "unpriced"},
		{name: "echoing host, no org price: fallback hop on a listed model onto another provider: its own listed price, not the primary's", asked: "openai/gpt-4o", provider: "anthropic", firstProvider: "openai",
			attempts: []attempt{{models.AttemptPrimary, "gpt-4o", models.OutcomeError}, {models.AttemptModelFallback, haiku, models.OutcomeSuccess}},
			resolved: haiku, cost: "0.006000", source: "modeldb"},
		// A managed request is looked up under the model it called, then the model
		// asked for (modelCandidates): a managed hop on a model the catalog does not
		// list takes the listed price of the model asked for.
		{name: "managed, echoing host: fallback hop on a model the catalog does not list takes the asked model's listed price", asked: haiku, provider: "anthropic", managed: true,
			attempts: []attempt{{models.AttemptPrimary, haiku, models.OutcomeError}, {models.AttemptModelFallback, custom, models.OutcomeSuccess}},
			resolved: custom, cost: "0.006000", source: "modeldb"},
	} {
		t.Run(c.name, func(t *testing.T) {
			db := modeldb.New(map[string]*modeldb.ModelInfo{
				haiku:             {Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: 1e-6, OutputPerToken: 5e-6}},
				"claude-sonnet-4": {Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: 1e-6, OutputPerToken: 5e-6}},
				"gpt-4o":          {Mode: modeldb.ModeChat, Pricing: modeldb.PricingInfo{InputPerToken: 50e-6, OutputPerToken: 50e-6}},
			}, nil)
			store := tenant.NewStore()
			store.Set("org-1", &tenant.OrgConfig{CostTracking: &tenant.CostTrackingConfig{Enabled: true, CustomPricing: byPrice(c.prices)}})
			rc := newRC(c.asked, 1000, 1000)
			rc.Provider = c.provider
			rc.ResolvedModel = haikuDated // what a real host answers for the alias it was sent
			if c.resolved != "" {
				rc.ResolvedModel = c.resolved
			}
			mode, credential := "byok", models.CredentialBYOK
			if c.managed {
				mode, credential = models.BillingModeManaged, models.CredentialManaged
			}
			rc.Metadata["billing_mode"] = mode
			rc.Metadata[tenant.MetadataKeyOrgID] = "org-1"
			for i, a := range c.attempts {
				provider := c.provider
				if i == 0 && c.firstProvider != "" {
					provider = c.firstProvider
				}
				rc.AddAttempt(models.Attempt{Kind: a.kind, Provider: provider, Model: a.model,
					CredentialType: credential, Outcome: a.outcome})
			}

			New(true, func() *modeldb.ModelDB { return db }, store, nil).ProcessResponse(context.Background(), rc)

			if rc.Metadata["cost"] != c.cost || rc.Metadata["cost_source"] != c.source {
				t.Fatalf("cost = %q from %q, want %s from %s (attempts %+v)",
					rc.Metadata["cost"], rc.Metadata["cost_source"], c.cost, c.source, rc.AttemptsSnapshot())
			}
		})
	}
}
