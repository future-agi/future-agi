package credits

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

const testOrg = "org-1"

// sonnet is the fixture card: $3 / $15 per million tokens.
var sonnet = models.PriceCard{Prompt: 3e-6, Completion: 15e-6, Source: models.PriceSourceCatalog}

// managedGateway is the plugin over an in-memory ledger, pricing every model
// with the fixture card.
type managedGateway struct {
	plugin *Plugin
	mem    *billing.MemoryLedger
	ledger *OrgLedger
}

func newManagedGateway(t *testing.T, grantedMicros int64) *managedGateway {
	t.Helper()
	mem := billing.NewMemoryLedger()
	ledger := NewOrgLedger(mem)
	if grantedMicros >= 0 {
		ledger.SetMicros(testOrg, grantedMicros)
	}
	p := New(true, nil)
	p.SetOrgLedger(ledger, true)
	p.SetPrices(func(providerID, model, endpointID string) (models.PriceCard, bool) { return sonnet, true })
	return &managedGateway{plugin: p, mem: mem, ledger: ledger}
}

// managedRequest is a managed chat request for "hello world" (10 input
// tokens) with max_tokens maxTokens.
func managedRequest(id string, maxTokens int) *models.RequestContext {
	rc := models.AcquireRequestContext()
	rc.RequestID = id
	rc.Provider = "openrouter"
	rc.Request = &models.ChatCompletionRequest{Model: "anthropic/claude-sonnet-5.5",
		Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"hello world"`)}}, MaxTokens: &maxTokens}
	rc.Metadata["billing_mode"] = models.BillingModeManaged
	rc.Metadata["org_id"] = testOrg
	rc.Metadata["auth_key_id"] = "key-1"
	return rc
}

func (g *managedGateway) account(t *testing.T) billing.Account {
	t.Helper()
	a, err := g.ledger.Account(context.Background(), testOrg)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// writeBody renders an API error as the client gets it.
func writeBody(t *testing.T, err *models.APIError) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	w := httptest.NewRecorder()
	models.WriteErrorFromError(w, err)
	var body struct {
		Error struct {
			Code     string         `json:"code"`
			Metadata map[string]any `json:"metadata"`
		} `json:"error"`
	}
	if jerr := json.Unmarshal(w.Body.Bytes(), &body); jerr != nil {
		t.Fatalf("body %s: %v", w.Body, jerr)
	}
	body.Error.Metadata["code"] = body.Error.Code
	return w, body.Error.Metadata
}

func TestLimitErrorCarriesScopeAndReset(t *testing.T) {
	g := newManagedGateway(t, 0)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	res := g.plugin.ProcessRequest(context.Background(), rc)
	if res.Error == nil {
		t.Fatal("an org granted $0 was not refused")
	}
	w, meta := writeBody(t, res.Error)
	if w.Code != http.StatusPaymentRequired || meta["code"] != "managed_credits_exhausted" {
		t.Fatalf("status %d, body %s", w.Code, w.Body)
	}
	if meta["scope"] != "org" || meta["limit_source"] != models.LimitSourceCredits {
		t.Fatalf("metadata = %v, want scope org and limit_source agentcc_credits", meta)
	}
	if v, ok := meta["resets_at"]; !ok || v != nil {
		t.Fatalf("resets_at must be present and null: %s", w.Body)
	}
	if w.Header().Get("Retry-After") != "" {
		t.Fatal("an exhausted 402 sends no Retry-After")
	}
}

func TestReserveHoldsTheWorstCaseEstimate(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := managedRequest("req-1", 1000)
	defer rc.Release()
	if res := g.plugin.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatal(res.Error)
	}
	// (10 × 3 + 1000 × 15) micros × 1.05 = 15,781.5, rounded up.
	if a := g.account(t); a.HeldMicros != 15_782 || rc.Billing.HoldMicros != 15_782 || !rc.Billing.Reserved {
		t.Fatalf("held %d (rc %d, reserved %v), want 15782", a.HeldMicros, rc.Billing.HoldMicros, rc.Billing.Reserved)
	}
	if rc.Billing.CreditsRemainingMicros == nil || *rc.Billing.CreditsRemainingMicros != 1_000_000-15_782 {
		t.Fatalf("credits remaining = %v", rc.Billing.CreditsRemainingMicros)
	}
}

func TestReserveUsesTheHighestEndpointCard(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := managedRequest("req-1", 1000)
	defer rc.Release()
	rc.Billing.EndpointSet = []models.EndpointRef{
		{ID: "e1", UpstreamProvider: "one", Price: models.PriceCard{Prompt: 1e-6, Completion: 1e-6}},
		{ID: "e3", UpstreamProvider: "three", Price: models.PriceCard{Prompt: 3e-6, Completion: 3e-6}},
		{ID: "e2", UpstreamProvider: "two", Price: models.PriceCard{Prompt: 2e-6, Completion: 2e-6}},
	}
	g.plugin.ProcessRequest(context.Background(), rc)
	// (10 + 1000) × 3 micros × 1.05 = 3,181.5, rounded up.
	if a := g.account(t); a.HeldMicros != 3_182 {
		t.Fatalf("held %d, want 3182 at the highest card (price 3)", a.HeldMicros)
	}
	if rc.Billing.ReservePrice == nil || *rc.Billing.ReservePrice != rc.Billing.EndpointSet[1].Price {
		t.Fatalf("ReservePrice = %+v, want the price-3 card", rc.Billing.ReservePrice)
	}
}

func TestNoAccountIs503AndAsksForAPush(t *testing.T) {
	g := newManagedGateway(t, -1) // opted in, never pushed
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	res := g.plugin.ProcessRequest(context.Background(), rc)
	w, meta := writeBody(t, res.Error)
	if w.Code != http.StatusServiceUnavailable || meta["code"] != "managed_billing_unavailable" ||
		meta["reason"] != models.ReasonAccountStateMissing || w.Header().Get("Retry-After") != "30" {
		t.Fatalf("status %d, Retry-After %q, body %s", w.Code, w.Header().Get("Retry-After"), w.Body)
	}
	if _, ok := meta["limit_source"]; ok {
		t.Fatalf("the 503 names no limit_source: %s", w.Body)
	}
	if !g.mem.NeedsPush(testOrg) {
		t.Fatal("the org was not added to mc:needs_push")
	}
}

func TestRefusalKinds(t *testing.T) {
	for _, tc := range []struct {
		name               string
		granted, otherHold int64
		code, limitSource  string
		retryAfter         string
	}{
		// The request holds 15,782 micros.
		{"exhausted", 0, 0, "managed_credits_exhausted", models.LimitSourceCredits, ""},
		{"insufficient", 10_000, 0, "managed_credits_insufficient", models.LimitSourceCredits, ""},
		{"in_flight", 20_000, 10_000, "managed_credits_insufficient", models.LimitSourceInFlight, "2"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			g := newManagedGateway(t, tc.granted)
			if tc.otherHold > 0 {
				g.ledger.Reserve(context.Background(), testOrg, "other", tc.otherHold, time.Minute, billing.ReserveNew)
			}
			rc := managedRequest("req-1", 1000)
			defer rc.Release()
			res := g.plugin.ProcessRequest(context.Background(), rc)
			if res.Error == nil {
				t.Fatal("not refused")
			}
			w, meta := writeBody(t, res.Error)
			if w.Code != http.StatusPaymentRequired || meta["code"] != tc.code || meta["limit_source"] != tc.limitSource ||
				w.Header().Get("Retry-After") != tc.retryAfter || meta["required_usd"] != 0.015782 {
				t.Fatalf("status %d, Retry-After %q, body %s", w.Code, w.Header().Get("Retry-After"), w.Body)
			}
			if rc.Billing.Reserved {
				t.Fatal("a refused request is marked reserved")
			}
		})
	}
}

func TestCacheHitVoidsAndRecordsNoSpend(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	g.plugin.ProcessRequest(context.Background(), rc)
	rc.Flags.CacheHit = true
	rc.Billing.BilledMicros = 5_000 // what a stored response once cost; a hit is never billed
	if g.plugin.ShouldSkipOnCacheHit() {
		t.Fatal("the credits plugin skips cache hits, so a held request would never be voided")
	}
	g.plugin.ProcessResponse(context.Background(), rc)
	out := g.mem.Outbox(testOrg)
	if len(out) != 1 || out[0]["kind"] != billing.KindVoid || out[0]["billed_micros"] != "0" {
		t.Fatalf("outbox = %v, want one void record", out)
	}
	if a := g.account(t); a.HeldMicros != 0 || a.SpentMicros != 0 {
		t.Fatalf("account = %+v", a)
	}
}

func TestSettleBilledAmountOnce(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := managedRequest("req-1", 100)
	g.plugin.ProcessRequest(context.Background(), rc)
	rc.Billing.BilledMicros, rc.Billing.UpstreamMicros, rc.Billing.MarkupMicros, rc.Billing.MarkupBps = 5_670, 5_400, 270, 500
	rc.Billing.CostSource = models.CostSourceProviderReported
	rc.AddAttempt(models.Attempt{Kind: models.AttemptPrimary, Provider: "openrouter", Model: "anthropic/claude-sonnet-5.5",
		CredentialType: models.CredentialManaged, Outcome: models.OutcomeSuccess, UpstreamCostMicros: 5_400, BilledCostMicros: 5_670})
	g.plugin.ProcessResponse(context.Background(), rc)
	rc.Release() // runs the finalizer: the hold is settled, so nothing more is written

	a := g.account(t)
	if a.SpentMicros != 5_670 || a.HeldMicros != 0 {
		t.Fatalf("account = %+v, want 5670 spent", a)
	}
	out := g.mem.Outbox(testOrg)
	if len(out) != 1 {
		t.Fatalf("outbox = %v, want exactly one spend record", out)
	}
	rec := out[0]
	if rec["kind"] != "spend" || rec["request_id"] != "req-1" || rec["generation_id"] != "req-1" || rec["api_key_id"] != "key-1" ||
		rec["billed_micros"] != "5670" || rec["hold_micros"] != "1607" || rec["cost_source"] != "provider_reported" ||
		rec["model"] != "anthropic/claude-sonnet-5.5" || rec["provider"] != "openrouter" {
		t.Fatalf("spend record = %v", rec)
	}
	var attempts []billing.AttemptSummary
	if err := json.Unmarshal([]byte(rec["attempts"]), &attempts); err != nil || len(attempts) != 1 || attempts[0].BilledMicros != 5_670 {
		t.Fatalf("attempts = %s (%v)", rec["attempts"], err)
	}
}

func TestSettleSetsCreditsRemaining(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	g.plugin.ProcessRequest(context.Background(), rc)
	rc.Billing.BilledMicros = 250_000
	g.plugin.ProcessResponse(context.Background(), rc)
	if rc.Metadata[metaCreditsRemaining] != "0.750000" || *rc.Billing.CreditsRemainingMicros != 750_000 {
		t.Fatalf("remaining = %q / %v", rc.Metadata[metaCreditsRemaining], *rc.Billing.CreditsRemainingMicros)
	}
}

func TestFinalizerVoidsOnPanic(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	func() {
		defer func() { _ = recover() }()
		defer rc.RunFinalizers() // every public handler defers it
		g.plugin.ProcessRequest(context.Background(), rc)
		panic("handler blew up before the post-plugins ran")
	}()
	if a := g.account(t); a.HeldMicros != 0 {
		t.Fatalf("held %d after the panic, want the hold voided", a.HeldMicros)
	}
	if out := g.mem.Outbox(testOrg); len(out) != 1 || out[0]["kind"] != billing.KindVoid {
		t.Fatalf("outbox = %v, want one void record", out)
	}
}

// ctxCheckingLedger fails any operation handed a context that is already
// done, as a real store would.
type ctxCheckingLedger struct{ billing.Ledger }

func (l ctxCheckingLedger) Void(ctx context.Context, org, holdID string, rec billing.Settlement) (billing.SettleResult, error) {
	if ctx.Err() != nil {
		return billing.SettleResult{}, ctx.Err()
	}
	return l.Ledger.Void(ctx, org, holdID, rec)
}

func TestFinalizerVoidsOnTimeout(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	g.plugin.SetOrgLedger(NewOrgLedger(ctxCheckingLedger{g.mem}), true)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	g.plugin.ProcessRequest(ctx, rc)
	<-ctx.Done() // the request timed out; its handler exits without post-plugins
	rc.RunFinalizers()
	if a := g.account(t); a.HeldMicros != 0 {
		t.Fatalf("held %d after the timeout, want the hold voided", a.HeldMicros)
	}

	// Through the pipeline: a request that times out before its provider
	// call runs the post-plugins, which void the hold.
	engine := pipeline.NewEngine(g.plugin)
	rc2 := managedRequest("req-2", 100)
	defer rc2.Release()
	ctx2, cancel2 := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel2()
	err := engine.Process(ctx2, rc2, func(ctx context.Context, _ *models.RequestContext) error {
		<-ctx.Done()
		return ctx.Err()
	})
	if err == nil {
		t.Fatal("the provider call did not time out")
	}
	if a := g.account(t); a.HeldMicros != 0 {
		t.Fatalf("held %d after a timed-out provider call", a.HeldMicros)
	}
	if out := g.mem.Outbox(testOrg); len(out) != 2 || out[1]["request_id"] != "req-2" || out[1]["kind"] != billing.KindVoid {
		t.Fatalf("outbox = %v", out)
	}
}

func TestFailsClosedWhenRedisIsUnreachable(t *testing.T) {
	for name, ledger := range map[string]*OrgLedger{
		"unreachable redis": NewOrgLedger(redisstate.NewManagedCreditStore(nil, "")),
		"no redis at all":   NewUnavailableOrgLedger(),
	} {
		t.Run(name, func(t *testing.T) {
			p := New(true, nil)
			p.SetOrgLedger(ledger, true)
			rc := managedRequest("req-1", 100)
			defer rc.Release()
			res := p.ProcessRequest(context.Background(), rc)
			if res.Error == nil {
				t.Fatal("a managed request was admitted with no reachable ledger")
			}
			w, meta := writeBody(t, res.Error)
			if w.Code != http.StatusServiceUnavailable || meta["code"] != "managed_billing_unavailable" ||
				meta["reason"] != models.ReasonRedisUnavailable || w.Header().Get("Retry-After") != "" {
				t.Fatalf("status %d, Retry-After %q, body %s", w.Code, w.Header().Get("Retry-After"), w.Body)
			}
			if ok, err := p.ReserveAdd(rc, "openrouter", "anthropic/claude-sonnet-5.5"); ok || !errors.Is(err, billing.ErrUnavailable) {
				t.Fatalf("a fallback hop was admitted with no reachable ledger: %v %v", ok, err)
			}
		})
	}
}

func TestMemoryModeWorksWithoutRedis(t *testing.T) {
	p := New(true, nil)
	ledger := NewOrgLedger(nil) // managed.require_redis=false
	ledger.SetMicros(testOrg, 1_000_000)
	p.SetOrgLedger(ledger, true)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	if res := p.ProcessRequest(context.Background(), rc); res.Error != nil {
		t.Fatal(res.Error)
	}
	rc.Billing.BilledMicros = 1_234
	p.ProcessResponse(context.Background(), rc)
	if ledger.Kind() != billing.KindMemory || ledger.BalanceMicros(testOrg) != 1_000_000-1_234 {
		t.Fatalf("memory ledger %q balance %d", ledger.Kind(), ledger.BalanceMicros(testOrg))
	}
}

func TestUnpricedModelHoldsTheUnpricedReserve(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	g.plugin.SetPrices(func(string, string, string) (models.PriceCard, bool) { return models.PriceCard{}, false })
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	g.plugin.ProcessRequest(context.Background(), rc)
	if a := g.account(t); a.HeldMicros != 100_000 || rc.Metadata["billing_alert"] != "unpriced" {
		t.Fatalf("held %d, billing_alert %q; want managed.unpriced_reserve_usd and the alert", a.HeldMicros, rc.Metadata["billing_alert"])
	}
}

func TestDefaultOutputIsTheSmallerOfModelMaxAndConfig(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	g.plugin.SetMaxOutput(func(string, string) int { return 2000 })
	rc := managedRequest("req-1", 0)
	defer rc.Release()
	rc.Request.MaxTokens = nil
	g.plugin.ProcessRequest(context.Background(), rc)
	// (10 × 3 + 2000 × 15) × 1.05 = 31,531.5 → 31,532: the model's 2000 under the default 4096.
	if a := g.account(t); a.HeldMicros != 31_532 {
		t.Fatalf("held %d, want 31532", a.HeldMicros)
	}
	g.plugin.SetOrgMaxTokens(func(string) int { return 500 })
	rc2 := managedRequest("req-2", 0)
	defer rc2.Release()
	rc2.Request.MaxTokens = nil
	g.plugin.ProcessRequest(context.Background(), rc2)
	// (10 × 3 + 500 × 15) × 1.05 = 7,906.5 → 7,907: the org's estimate wins.
	if a := g.account(t); a.HeldMicros != 31_532+7_907 {
		t.Fatalf("held %d, want the org's 500-token estimate on top", a.HeldMicros)
	}
}

func TestReserveAddForFallbackHops(t *testing.T) {
	g := newManagedGateway(t, 100_000)
	rc := managedRequest("req-1", 1000)
	defer rc.Release()

	rc.Metadata["billing_mode"] = models.BillingModeBYOK
	if ok, err := g.plugin.ReserveAdd(rc, "operator", "gpt-4o"); !ok || err != nil || g.account(t).HeldMicros != 0 {
		t.Fatal("a hop on the org's own credentials took a hold")
	}

	rc.Metadata["billing_mode"] = models.BillingModeManaged
	if ok, err := g.plugin.ReserveAdd(rc, "openrouter", "anthropic/claude-sonnet-5.5"); !ok || err != nil {
		t.Fatalf("managed hop: %v %v", ok, err)
	}
	if ok, _ := g.plugin.ReserveAdd(rc, "openrouter", "anthropic/claude-sonnet-5.5"); !ok {
		t.Fatal("second managed hop refused")
	}
	if a := g.account(t); a.HeldMicros != 2*15_782 || rc.Billing.HoldMicros != 2*15_782 {
		t.Fatalf("held %d (rc %d), want two hop holds on the request's hold", a.HeldMicros, rc.Billing.HoldMicros)
	}
	rc.Request.MaxTokens = ptr(100_000)
	if ok, err := g.plugin.ReserveAdd(rc, "openrouter", "anthropic/claude-sonnet-5.5"); ok || err != nil {
		t.Fatalf("a hop that does not fit: %v %v, want refused", ok, err)
	}
	rc.RunFinalizers()
	if a := g.account(t); a.HeldMicros != 0 {
		t.Fatalf("held %d after the request ended, want the finalizer's one void", a.HeldMicros)
	}
	if out := g.mem.Outbox(testOrg); len(out) != 1 {
		t.Fatalf("outbox = %v, want one void for the request", out)
	}

	g.mem.DropAccount(testOrg)
	rc2 := managedRequest("req-2", 100)
	defer rc2.Release()
	if ok, err := g.plugin.ReserveAdd(rc2, "openrouter", "anthropic/claude-sonnet-5.5"); ok || err != nil || !g.mem.NeedsPush(testOrg) {
		t.Fatalf("hop with no account: %v %v needs_push %v", ok, err, g.mem.NeedsPush(testOrg))
	}
}

func TestRequireCreditsOffSettlesWithoutAHold(t *testing.T) {
	g := newManagedGateway(t, 0)
	g.plugin.SetOrgLedger(g.ledger, false)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	if res := g.plugin.ProcessRequest(context.Background(), rc); res.Error != nil || rc.Billing.Reserved {
		t.Fatalf("require_credits off: %v, reserved %v", res.Error, rc.Billing.Reserved)
	}
	rc.Billing.BilledMicros = 900
	g.plugin.ProcessResponse(context.Background(), rc)
	if a := g.account(t); a.SpentMicros != 900 {
		t.Fatalf("spent %d, want the request's spend settled", a.SpentMicros)
	}
}

// failingSettleLedger cannot settle, as a store that drops mid-request.
type failingSettleLedger struct{ billing.Ledger }

func (failingSettleLedger) Settle(context.Context, string, string, billing.Settlement) (billing.SettleResult, error) {
	return billing.SettleResult{}, billing.ErrUnavailable
}

// A settlement that fails is never followed by a void: the spend may have
// been written, so the hold is left to expire rather than released as
// unbilled.
func TestFailedSettleIsNeverVoided(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	g.plugin.SetOrgLedger(NewOrgLedger(failingSettleLedger{g.mem}), true)
	rc := managedRequest("req-1", 100)
	g.plugin.ProcessRequest(context.Background(), rc)
	rc.Billing.BilledMicros = 1_000
	g.plugin.ProcessResponse(context.Background(), rc)
	rc.Release() // runs the finalizer
	if out := g.mem.Outbox(testOrg); len(out) != 0 {
		t.Fatalf("outbox = %v, want no void after a failed settle", out)
	}
	if a := g.account(t); a.HeldMicros != 1_607 {
		t.Fatalf("held %d, want the hold left to expire", a.HeldMicros)
	}
}

func TestZeroHoldTTLKeepsTheDefault(t *testing.T) {
	g := newManagedGateway(t, 1_000_000)
	s := DefaultSettings()
	s.HoldTTL = 0
	g.plugin.SetSettings(s)
	rc := managedRequest("req-1", 100)
	defer rc.Release()
	g.plugin.ProcessRequest(context.Background(), rc)
	// The hold keeps the default TTL: the next RESERVE leaves it held.
	g.ledger.Reserve(context.Background(), testOrg, "next", 1, time.Minute, billing.ReserveNew)
	if a := g.account(t); a.HeldMicros != 1_607+1 {
		t.Fatalf("held %d, want the first hold still held", a.HeldMicros)
	}
}

func TestManagedKeyNotChargedOnCacheHit(t *testing.T) {
	ks := newKeyStore()
	key := findKey(ks, "managed")
	p := New(true, ks)
	rc := makeRC(key.ID, "managed")
	defer rc.Release()
	rc.Flags.CacheHit = true
	rc.Metadata["cost"] = "0.500000"
	before := key.BalanceUSD()
	p.ProcessResponse(context.Background(), rc)
	if key.BalanceUSD() != before {
		t.Fatalf("a cache hit charged the managed key: %v -> %v", before, key.BalanceUSD())
	}
}

func ptr[T any](v T) *T { return &v }
