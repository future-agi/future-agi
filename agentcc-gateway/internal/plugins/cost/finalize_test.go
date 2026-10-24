package cost

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// fixedLookup is an injected price lookup that knows one card for every
// model, as the catalog's would.
func fixedLookup(card models.PriceCard) func() models.PriceLookup {
	return func() models.PriceLookup {
		return func(_, _, _ string) (models.PriceCard, bool) { return card, true }
	}
}

// billingPlugin prices managed attempts at 5% markup, 320 bps processing,
// 550 bps upstream fee on "openrouter" and none on "direct".
func billingPlugin(lookup func() models.PriceLookup) *Plugin {
	db := managedDB()
	p := New(true, func() *modeldb.ModelDB { return db }, nil, nil)
	p.SetManagedMarkup(5)
	p.SetFees(Fees{ProcessingFeeBps: 320, UpstreamFeeBps: map[string]int{"openrouter": 550, "direct": 0}})
	if lookup != nil {
		p.SetPricer(NewPricer(func() *modeldb.ModelDB { return db }, lookup))
	}
	return p
}

func managedRC(usage *models.Usage) *models.RequestContext {
	return &models.RequestContext{
		RequestID: "req-1",
		Model:     "anthropic/claude-sonnet-5.5",
		Provider:  "openrouter",
		Response:  &models.ChatCompletionResponse{Usage: usage},
		Metadata:  map[string]string{"billing_mode": "managed"},
		Timings:   map[string]time.Duration{},
	}
}

func addAttempt(rc *models.RequestContext, a models.Attempt) {
	if a.Provider == "" {
		a.Provider = "openrouter"
	}
	if a.Model == "" {
		a.Model = rc.Model
	}
	if a.CredentialType == "" {
		a.CredentialType = models.CredentialManaged
	}
	rc.AddAttempt(a)
}

func TestRequestBilledIsSumOfBillableAttempts(t *testing.T) {
	// $1/M input: a failed attempt the upstream counted 10 tokens for, and
	// the success, which the upstream says cost 10 micros. Each is billed
	// 10 × 1.05 = 10.5 → 11, so the request bills 22: rounding the 21-micro
	// sum at the end would bill 21.
	p := billingPlugin(fixedLookup(models.PriceCard{Prompt: 1e-6, Source: models.PriceSourceCatalog}))
	rc := managedRC(&models.Usage{PromptTokens: 7, Cost: usd(0.00001)})
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeError}) // nothing counted
	addAttempt(rc, models.Attempt{Kind: models.AttemptRetry, Outcome: models.OutcomeError, PromptTokens: 10})
	addAttempt(rc, models.Attempt{Kind: models.AttemptFailover, Outcome: models.OutcomeSuccess})

	p.ProcessResponse(context.Background(), rc)

	got := rc.AttemptsSnapshot()
	if got[1].BilledCostMicros != 11 || got[2].BilledCostMicros != 11 || got[0].BilledCostMicros != 0 {
		t.Fatalf("attempt billed = %d, %d, %d; want 0, 11, 11", got[0].BilledCostMicros, got[1].BilledCostMicros, got[2].BilledCostMicros)
	}
	b := rc.Billing
	if b.BilledMicros != 22 || b.UpstreamMicros != 20 || b.MarkupMicros != 2 || b.MarkupBps != 500 {
		t.Fatalf("billing = %+v; want billed 22 (never re-rounded), upstream 20, markup 2 at 500 bps", b)
	}
	if rc.Metadata["cost"] != "0.000022" || rc.Metadata["cost_base"] != "0.000020" || rc.Metadata["cost_markup_percent"] != "5" {
		t.Fatalf("metadata = %v; want cost to be exactly the billed sum", rc.Metadata)
	}
	// The retry was priced from the catalog, the success from the
	// upstream's report: the request's precise source is mixed, which the
	// log entry's enum lacks, so rc.Billing records the serving attempt's.
	if rc.Metadata["cost_source"] != "mixed" || b.CostSource != models.CostSourceProviderReported {
		t.Fatalf("cost_source = %q / billing %q; want mixed / provider_reported", rc.Metadata["cost_source"], b.CostSource)
	}
}

func TestFailedAttemptBillableOnlyWithUsage(t *testing.T) {
	p := billingPlugin(fixedLookup(models.PriceCard{Prompt: 2e-6, Completion: 10e-6, Source: models.PriceSourceCatalog}))
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeTimeout})
	addAttempt(rc, models.Attempt{Kind: models.AttemptFailover, Outcome: models.OutcomeError, PromptTokens: 1000})
	addAttempt(rc, models.Attempt{Kind: models.AttemptFailover, Outcome: models.OutcomeSkipped})
	addAttempt(rc, models.Attempt{Kind: models.AttemptModelFallback, Outcome: models.OutcomeSuccess})

	p.ProcessResponse(context.Background(), rc)

	got := rc.AttemptsSnapshot()
	wantBillable := []bool{false, true, false, true}
	wantUpstream := []int64{0, 2000, 0, 7000}
	for i, a := range got {
		if a.Billable != wantBillable[i] || a.UpstreamCostMicros != wantUpstream[i] {
			t.Errorf("attempt %d (%s): billable %v upstream %d; want %v %d", i+1, a.Outcome, a.Billable, a.UpstreamCostMicros, wantBillable[i], wantUpstream[i])
		}
	}
	if rc.Billing.UpstreamMicros != 9000 || rc.Billing.BilledMicros != 2100+7350 {
		t.Fatalf("billing = %+v; want upstream 9000, billed 9450", rc.Billing)
	}

	// A request whose only attempt failed with nothing counted bills nothing.
	rc = managedRC(nil)
	rc.Response = nil
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeError})
	p.ProcessResponse(context.Background(), rc)
	if a := rc.AttemptsSnapshot()[0]; a.Billable || rc.Billing.BilledMicros != 0 || rc.Metadata["cost"] != "" {
		t.Fatalf("a failed attempt with no usage was billed: %+v, billing %+v, metadata %v", a, rc.Billing, rc.Metadata)
	}
}

func TestLandedCostRecordedPerManagedAttempt(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{Cost: usd(1.0)})
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess})

	p.ProcessResponse(context.Background(), rc)

	a := rc.AttemptsSnapshot()[0]
	if a.LandedCostMicros != 1_088_600 || a.UpstreamFeeBps != 550 || a.ProcessingFeeBps != 320 || a.BilledCostMicros != 1_050_000 {
		t.Fatalf("attempt = %+v; want landed 1088600 at 550/320 bps, billed 1050000", a)
	}
	b := rc.Billing
	if b.LandedMicros != 1_088_600 || b.UpstreamFeeBps != 550 || b.ProcessingFeeBps != 320 {
		t.Fatalf("billing = %+v; want the serving attempt's landed cost and rates", b)
	}
}

func TestDirectSourceNoUpstreamFee(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{Cost: usd(1.0)})
	rc.Provider = "direct"
	addAttempt(rc, models.Attempt{Provider: "direct", Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess})

	p.ProcessResponse(context.Background(), rc)

	// No upstream fee: landed is upstream 1,000,000 plus processing only,
	// 1,050,000 × 3.2% = 33,600.
	a := rc.AttemptsSnapshot()[0]
	if a.LandedCostMicros != 1_033_600 || a.UpstreamFeeBps != 0 || rc.Billing.LandedMicros != 1_033_600 {
		t.Fatalf("attempt = %+v, billing %+v; want landed 1033600 with no upstream fee", a, rc.Billing)
	}
}

func TestByokAttemptsCarryNoMarkupOrLandedCost(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{Cost: usd(0.0068)})
	rc.Metadata["billing_mode"] = "byok"
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess, CredentialType: models.CredentialBYOK})

	p.ProcessResponse(context.Background(), rc)

	a := rc.AttemptsSnapshot()[0]
	if a.UpstreamCostMicros != 6800 || a.BilledCostMicros != 6800 || a.LandedCostMicros != 0 || a.ProcessingFeeBps != 0 {
		t.Fatalf("BYOK attempt = %+v; want billed equal to upstream, nothing landed", a)
	}
	if b := rc.Billing; b.BilledMicros != 0 || b.UpstreamMicros != 6800 || b.MarkupBps != 0 || b.LandedMicros != 0 {
		t.Fatalf("billing = %+v; a BYOK request bills FutureAGI nothing", b)
	}
	if rc.Metadata["cost"] != "0.006800" || rc.Metadata["cost_base"] != "" {
		t.Fatalf("metadata = %v", rc.Metadata)
	}
}

func TestServedEndpointPriced(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Billing.EndpointSet = []models.EndpointRef{
		{ID: "openrouter:acme:m", UpstreamProvider: "acme", Price: models.PriceCard{Prompt: 4e-6, Completion: 20e-6, Source: models.PriceSourceCatalog}},
		{ID: "openrouter:bolt:m", UpstreamProvider: "bolt", Price: models.PriceCard{Prompt: 1e-6, Completion: 5e-6, Source: models.PriceSourceCatalog}},
	}
	rc.Billing.ReservePrice = &rc.Billing.EndpointSet[0].Price
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess, ServedUpstreamProvider: "bolt"})

	p.ProcessResponse(context.Background(), rc)

	// bolt served: 1000 × $1/M + 500 × $5/M = 3500 micros, not acme's 14000.
	a := rc.AttemptsSnapshot()[0]
	if a.UpstreamCostMicros != 3500 || a.CostSource != models.CostSourceCatalog || rc.Billing.CostSource != models.CostSourceCatalog {
		t.Fatalf("attempt = %+v; want bolt's price, 3500 micros, source catalog", a)
	}

	// The request-level served upstream stands in when the attempt has none.
	rc = managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Billing.EndpointSet = []models.EndpointRef{
		{UpstreamProvider: "acme", Price: models.PriceCard{Prompt: 4e-6, Completion: 20e-6, Source: models.PriceSourceCatalog}},
		{UpstreamProvider: "bolt", Price: models.PriceCard{Prompt: 1e-6, Completion: 5e-6, Source: models.PriceSourcePriceCard}},
	}
	rc.Billing.ServedUpstreamProvider = "bolt"
	p.ProcessResponse(context.Background(), rc)
	if rc.Billing.UpstreamMicros != 3500 || rc.Billing.CostSource != models.CostSourcePriceCard {
		t.Fatalf("billing = %+v; want bolt's price card, 3500 micros", rc.Billing)
	}
}

func TestUnknownServedUsesReservePrice(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	reserve := models.PriceCard{Prompt: 4e-6, Completion: 20e-6, Source: models.PriceSourceCatalog}
	rc.Billing.EndpointSet = []models.EndpointRef{
		{UpstreamProvider: "acme", Price: reserve},
		{UpstreamProvider: "bolt", Price: models.PriceCard{Prompt: 1e-6, Completion: 5e-6, Source: models.PriceSourceCatalog}},
	}
	rc.Billing.ReservePrice = &reserve
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess, ServedUpstreamProvider: "zeta"})

	p.ProcessResponse(context.Background(), rc)

	// zeta is not in the set: priced at the reserve card, the highest.
	a := rc.AttemptsSnapshot()[0]
	if a.UpstreamCostMicros != 14000 || a.CostSource != models.CostSourceCatalogMax {
		t.Fatalf("attempt = %+v; want the reserve price, 14000 micros, catalog_max", a)
	}
	if rc.Billing.CostSource != models.CostSourceCatalogMax || rc.Metadata["cost_source"] != "catalog_max" {
		t.Fatalf("billing source %q, metadata %q; want catalog_max", rc.Billing.CostSource, rc.Metadata["cost_source"])
	}
}

func TestUnpricedManagedFlagged(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Model = "vendor/unknown"
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess})
	before := UnpricedManagedAttempts()

	p.ProcessResponse(context.Background(), rc)

	if got := UnpricedManagedAttempts() - before; got != 1 {
		t.Fatalf("%s moved by %d, want 1", UnpricedManagedAttemptsMetric, got)
	}
	a := rc.AttemptsSnapshot()[0]
	if a.CostSource != models.CostSourceUnpriced || !a.Billable || a.BilledCostMicros != 0 {
		t.Fatalf("attempt = %+v; want a billable attempt marked unpriced at 0", a)
	}
	if rc.Billing.CostSource != models.CostSourceUnpriced || rc.Billing.BilledMicros != 0 || rc.Metadata["cost"] != "" {
		t.Fatalf("billing = %+v, metadata %v; want unpriced and nothing billed", rc.Billing, rc.Metadata)
	}

	// A BYOK request that cannot be priced is unpriced too, at no cost, and
	// does not count as a managed attempt served on FutureAGI's credit.
	rc = managedRC(&models.Usage{PromptTokens: 1000})
	rc.Model = "vendor/unknown"
	rc.Metadata["billing_mode"] = "byok"
	before = UnpricedManagedAttempts()
	p.ProcessResponse(context.Background(), rc)
	if rc.Metadata["cost_source"] != "unpriced" || rc.Billing.CostSource != models.CostSourceUnpriced || rc.Billing.UpstreamMicros != 0 || UnpricedManagedAttempts() != before {
		t.Fatalf("BYOK unpriced: metadata %v, billing %+v", rc.Metadata, rc.Billing)
	}
}

func TestCacheHitIsNotBillable(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{PromptTokens: 1000, Cost: usd(0.007)})
	rc.Flags.CacheHit = true

	p.ProcessResponse(context.Background(), rc)

	// cache_hit stays in the metadata; the log entry's enum has no such
	// value, so rc.Billing records none.
	if rc.Metadata["cost_source"] != "cache_hit" || rc.Billing.CostSource != "" || rc.Billing.BilledMicros != 0 || rc.Metadata["cost"] != "" {
		t.Fatalf("metadata %v, billing %+v; want an unbilled cache hit", rc.Metadata, rc.Billing)
	}
}

func TestEstimatedStreamUsageFlagged(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})
	rc.Metadata["usage_estimated"] = "true"
	addAttempt(rc, models.Attempt{Kind: models.AttemptPrimary, Outcome: models.OutcomeSuccess})

	p.ProcessResponse(context.Background(), rc)

	if !rc.Billing.UsageEstimated || !rc.AttemptsSnapshot()[0].UsageEstimated || rc.Billing.BilledMicros != 7350 {
		t.Fatalf("billing = %+v; want an estimated, billed request", rc.Billing)
	}
}

func TestUnrecordedRequestPricedAsOneAttempt(t *testing.T) {
	p := billingPlugin(nil)
	rc := managedRC(&models.Usage{PromptTokens: 1000, CompletionTokens: 500})

	p.ProcessResponse(context.Background(), rc)

	// With no attempts recorded, the request is one implicit attempt,
	// priced from OpenRouter's own catalog entry: 7000 micros.
	if rc.AttemptCount() != 0 {
		t.Fatal("pricing must not record attempts")
	}
	b := rc.Billing
	if b.UpstreamMicros != 7000 || b.BilledMicros != 7350 || b.LandedMicros != 7385+235 || b.CostSource != models.CostSourceCatalog {
		t.Fatalf("billing = %+v; want 7000 upstream, 7350 billed, catalog", b)
	}
}
