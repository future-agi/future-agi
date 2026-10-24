package cost

// An org's Model Database overrides price its BYOK
// attempts, under this precedence (custom_pricing wins; model_database
// supplies the price where custom_pricing is absent; managed stays on
// FutureAGI's card).

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// customPricingOrg is the org config Django pushes for an org that prices
// gpt-4o itself (cost_tracking.custom_pricing, in dollars per million
// tokens), decoded from its JSON as the gateway stores it.
const customPricingOrg = `{"cost_tracking":{"enabled":true,"custom_pricing":{` +
	`"gpt-4o":{"input_per_mtok":1.0,"output_per_mtok":4.0},` +
	`"team-default":{"input_per_mtok":0.5,"output_per_mtok":2.0}}}}`

func orgConfigFromJSON(t *testing.T, raw string) *tenant.OrgConfig {
	t.Helper()
	var cfg tenant.OrgConfig
	if err := json.Unmarshal([]byte(raw), &cfg); err != nil {
		t.Fatalf("org config: %v", err)
	}
	return &cfg
}

// customPricingPlugin prices gpt-4o at the model database's $2.50/M input
// and $10/M output (testDB), with the orgs of store.
func customPricingPlugin(store *tenant.Store) *Plugin {
	db := testDB()
	return New(true, func() *modeldb.ModelDB { return db }, store, nil)
}

// byokRequest is a served BYOK chat request of org whose upstream reported
// 1,000 prompt and 500 completion tokens, with its attempts recorded on rc.
func byokRequest(org, model string, attempts ...models.Attempt) *models.RequestContext {
	rc := newRC(model, 1000, 500)
	rc.RequestID = "req-org-custom"
	rc.Provider = "openai"
	rc.EndpointType = "chat"
	rc.Metadata["billing_mode"] = "byok"
	rc.Metadata[tenant.MetadataKeyOrgID] = org
	for _, a := range attempts {
		rc.AddAttempt(a)
	}
	return rc
}

func byokServedAttempt(model string) models.Attempt {
	return models.Attempt{Provider: "openai", Model: model, CredentialType: models.CredentialBYOK, Outcome: models.OutcomeSuccess}
}

// TestOrgModelDatabasePricingOverridePricesAttempt: an org's per-model
// model_database override (tenant.ModelOverrideOrgConfig.Pricing, the
// console's OrgConfigEditor "model_database" block, stored by Django in
// AgentccOrgConfig.model_database and pushed to the gateway) prices the
// org's BYOK attempts.
func TestOrgModelDatabasePricingOverridePricesAttempt(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-mdb", orgConfigFromJSON(t,
		`{"model_database":{"overrides":{"gpt-4o":{"pricing":{"input_per_token":0.000001,"output_per_token":0.000004}}}}}`))
	p := customPricingPlugin(store)

	rc := byokRequest("org-mdb", "gpt-4o", byokServedAttempt("gpt-4o"))
	p.ProcessResponse(context.Background(), rc)

	// 1,000 × $1/M + 500 × $4/M = 3,000 micros at the override; 7,500 at the
	// model database's price.
	a := rc.AttemptsSnapshot()[0]
	if a.UpstreamCostMicros != 3000 {
		t.Fatalf("attempt priced %d micros (source %q); the org's model_database override prices it at 3000",
			a.UpstreamCostMicros, a.CostSource)
	}
	if a.CostSource != models.CostSourceOrgCustom {
		t.Fatalf("cost_source = %q, want %q", a.CostSource, models.CostSourceOrgCustom)
	}
}

// TestOrgCustomPricing: the org's custom_pricing prices its BYOK attempt.
func TestOrgCustomPricing(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-custom", orgConfigFromJSON(t, customPricingOrg))
	store.Set("org-plain", orgConfigFromJSON(t, `{"cost_tracking":{"enabled":true}}`))
	disabled := orgConfigFromJSON(t, customPricingOrg)
	disabled.CostTracking.Enabled = false
	store.Set("org-disabled", disabled)
	p := customPricingPlugin(store)

	const orgPrice, dbPrice = int64(3000), int64(7500)

	t.Run("the org's price prices the served attempt", func(t *testing.T) {
		rc := byokRequest("org-custom", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, orgPrice, models.CostSourceOrgCustom)
	})
	t.Run("the org's price wins over a cost the provider reported", func(t *testing.T) {
		rc := byokRequest("org-custom", "gpt-4o", byokServedAttempt("gpt-4o"))
		reported := 0.05
		rc.Response.Usage.Cost = &reported
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, orgPrice, models.CostSourceOrgCustom)
	})
	t.Run("a price set for the requested model prices the model it resolved to", func(t *testing.T) {
		rc := byokRequest("org-custom", "team-default", byokServedAttempt("gpt-4o"))
		rc.ResolvedModel = "gpt-4o"
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, orgPrice, models.CostSourceOrgCustom)

		rc = byokRequest("org-custom", "team-default", byokServedAttempt("vendor/unlisted"))
		rc.ResolvedModel = "vendor/unlisted"
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 1500, models.CostSourceOrgCustom)
	})
	t.Run("an org without a price for the model pays the model database's", func(t *testing.T) {
		rc := byokRequest("org-plain", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, dbPrice, models.CostSourceModelDB)
	})
	t.Run("another org's price never applies", func(t *testing.T) {
		rc := byokRequest("org-unknown", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, dbPrice, models.CostSourceModelDB)
	})
	t.Run("with cost tracking off the org's price is not used", func(t *testing.T) {
		rc := byokRequest("org-disabled", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, dbPrice, models.CostSourceModelDB)
	})
}

// checkServed asserts that rc's last attempt, its billing and its cost
// metadata all carry the BYOK cost micros from source.
func checkServed(t *testing.T, rc *models.RequestContext, micros int64, source models.CostSource) {
	t.Helper()
	attempts := rc.AttemptsSnapshot()
	if len(attempts) == 0 {
		t.Fatal("no attempt recorded")
	}
	a := attempts[len(attempts)-1]
	if !a.Billable || a.UpstreamCostMicros != micros || a.BilledCostMicros != micros || a.CostSource != source {
		t.Fatalf("served attempt = billable %v, upstream %d, billed %d, source %q; want billable, %d, %d, %q",
			a.Billable, a.UpstreamCostMicros, a.BilledCostMicros, a.CostSource, micros, micros, source)
	}
	if a.LandedCostMicros != 0 {
		t.Fatalf("a BYOK attempt lands nothing; landed = %d", a.LandedCostMicros)
	}
	if rc.Billing.UpstreamMicros != micros || rc.Billing.BilledMicros != 0 || rc.Billing.CostSource != source {
		t.Fatalf("billing = upstream %d, billed %d, source %q; want %d, 0 (BYOK), %q",
			rc.Billing.UpstreamMicros, rc.Billing.BilledMicros, rc.Billing.CostSource, micros, source)
	}
	if got, want := rc.Metadata["cost"], formatMicros(micros); got != want {
		t.Fatalf("cost metadata = %q, want %q", got, want)
	}
	if rc.Metadata["cost_source"] != string(source) {
		t.Fatalf("cost_source metadata = %q, want %q", rc.Metadata["cost_source"], source)
	}
}

// The model_database override and custom_pricing as the console writes them:
// model_database prices gpt-4o at $2/M in, $8/M out (6,000 micros for the
// request's 1,000 + 500 tokens); custom_pricing at $1/M, $4/M (3,000).
const (
	mdbOnly   = `{"model_database":{"overrides":{"gpt-4o":{"pricing":{"input_per_token":0.000002,"output_per_token":0.000008}}}}}`
	bothPrice = `{"cost_tracking":{"enabled":true,"custom_pricing":{"gpt-4o":{"input_per_mtok":1.0,"output_per_mtok":4.0}}},` +
		`"model_database":{"overrides":{"gpt-4o":{"pricing":{"input_per_token":0.000002,"output_per_token":0.000008}}}}}`
	bothTrackingOff = `{"cost_tracking":{"enabled":false,"custom_pricing":{"gpt-4o":{"input_per_mtok":1.0,"output_per_mtok":4.0}}},` +
		`"model_database":{"overrides":{"gpt-4o":{"pricing":{"input_per_token":0.000002,"output_per_token":0.000008}}}}}`
	inputOnly   = `{"model_database":{"overrides":{"gpt-4o":{"pricing":{"input_per_token":0.000001}}}}}`
	unknownMDB  = `{"model_database":{"overrides":{"my-llama-70b":{"pricing":{"input_per_token":0.0000001,"output_per_token":0.0000002}}}}}`
	cachedMDB   = `{"model_database":{"overrides":{"gpt-4o":{"pricing":{"input_per_token":0.000002,"output_per_token":0.000008,"cached_input_per_token":0.0000005}}}}}`
	limitsOnly  = `{"model_database":{"overrides":{"gpt-4o":{"max_output_tokens":5,"capabilities":{"function_calling":false}}}}}`
	prefixedMDB = `{"model_database":{"overrides":{"openai/gpt-4o":{"pricing":{"input_per_token":0.000002,"output_per_token":0.000008}}}}}`
)

// TestModelDBOverlayPricingPrecedence pins the precedence both ways.
func TestModelDBOverlayPricingPrecedence(t *testing.T) {
	store := tenant.NewStore()
	for org, raw := range map[string]string{
		"org-mdb": mdbOnly, "org-both": bothPrice, "org-both-off": bothTrackingOff,
		"org-input": inputOnly, "org-unknown-model": unknownMDB, "org-cached": cachedMDB,
		"org-limits": limitsOnly, "org-prefixed": prefixedMDB,
	} {
		store.Set(org, orgConfigFromJSON(t, raw))
	}
	p := customPricingPlugin(store)

	t.Run("model_database alone prices the BYOK attempt", func(t *testing.T) {
		rc := byokRequest("org-mdb", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 6000, models.CostSourceOrgCustom)
	})
	t.Run("custom_pricing wins over model_database", func(t *testing.T) {
		rc := byokRequest("org-both", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 3000, models.CostSourceOrgCustom)
	})
	t.Run("with cost tracking off, model_database supplies the price", func(t *testing.T) {
		rc := byokRequest("org-both-off", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 6000, models.CostSourceOrgCustom)
	})
	t.Run("a partial override is merged on top of the model database", func(t *testing.T) {
		// 1,000 × $1/M (override) + 500 × $10/M (model database) = 6,000.
		rc := byokRequest("org-input", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 6000, models.CostSourceOrgCustom)
	})
	t.Run("a model the database does not know is priced by the override alone", func(t *testing.T) {
		// 1,000 × $0.10/M + 500 × $0.20/M = 200 micros.
		rc := byokRequest("org-unknown-model", "my-llama-70b", byokServedAttempt("my-llama-70b"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 200, models.CostSourceOrgCustom)
	})
	t.Run("the cached input price prices cached tokens", func(t *testing.T) {
		// 600 uncached × $2/M + 400 cached × $0.5/M + 500 × $8/M = 1,200 + 200 + 4,000.
		rc := byokRequest("org-cached", "gpt-4o", byokServedAttempt("gpt-4o"))
		rc.Response.Usage.PromptDetails.CachedTokens = 400
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 5400, models.CostSourceOrgCustom)
	})
	t.Run("an override without prices leaves the model database's price", func(t *testing.T) {
		rc := byokRequest("org-limits", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 7500, models.CostSourceModelDB)
	})
	t.Run("an override keyed provider/model prices the bare model the provider served", func(t *testing.T) {
		rc := byokRequest("org-prefixed", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 6000, models.CostSourceOrgCustom)
	})
	t.Run("the override wins over a cost the provider reported", func(t *testing.T) {
		rc := byokRequest("org-mdb", "gpt-4o", byokServedAttempt("gpt-4o"))
		reported := 0.05
		rc.Response.Usage.Cost = &reported
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 6000, models.CostSourceOrgCustom)
	})
	t.Run("another org's override never applies", func(t *testing.T) {
		rc := byokRequest("org-other", "gpt-4o", byokServedAttempt("gpt-4o"))
		p.ProcessResponse(context.Background(), rc)
		checkServed(t, rc, 7500, models.CostSourceModelDB)
	})
}

// TestManagedPriceIgnoresOrgPrices: an org's model_database and custom_pricing
// entries never change what a managed attempt costs; it stays on FutureAGI's
// card (here OpenRouter's own catalog price, 7,000 micros, plus the 5%
// markup).
func TestManagedPriceIgnoresOrgPrices(t *testing.T) {
	store := tenant.NewStore()
	store.Set("org-m", orgConfigFromJSON(t, `{"cost_tracking":{"enabled":true,"custom_pricing":{`+
		`"anthropic/claude-sonnet-5.5":{"input_per_mtok":0.01,"output_per_mtok":0.01}}},`+
		`"model_database":{"overrides":{"anthropic/claude-sonnet-5.5":{"pricing":{"input_per_token":0.00000001,"output_per_token":0.00000001}},`+
		`"claude-sonnet-5.5":{"pricing":{"input_per_token":0.00000001,"output_per_token":0.00000001}}}}}`))
	p := managedPlugin(store)

	for _, withOrg := range []bool{false, true} {
		rc := newRC("anthropic/claude-sonnet-5.5", 1000, 500)
		rc.Provider = "openrouter"
		rc.EndpointType = "chat"
		rc.Metadata["billing_mode"] = "managed"
		if withOrg {
			rc.Metadata[tenant.MetadataKeyOrgID] = "org-m"
		}
		rc.AddAttempt(models.Attempt{Provider: "openrouter", Model: "anthropic/claude-sonnet-5.5",
			CredentialType: models.CredentialManaged, Outcome: models.OutcomeSuccess})
		p.ProcessResponse(context.Background(), rc)
		a := rc.AttemptsSnapshot()[0]
		if a.UpstreamCostMicros != 7000 || a.BilledCostMicros != 7350 || a.CostSource != models.CostSourceCatalog {
			t.Fatalf("org overrides set %v: managed attempt = upstream %d, billed %d, source %q; want 7000, 7350, catalog",
				withOrg, a.UpstreamCostMicros, a.BilledCostMicros, a.CostSource)
		}
	}
}
