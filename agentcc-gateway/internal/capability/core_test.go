package capability

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Each fallback class: stateless_cross_vendor moves on after a 5xx (never
// after an input 4xx), same_model_only and none never move, and
// create_time_only moves only before a session exists.
func TestFallbackClasses(t *testing.T) {
	sessionFails := AdapterFunc(func(context.Context, VendorCall) (VendorResponse, error) {
		return VendorResponse{}, &VendorError{Status: http.StatusBadGateway, Message: "lost the session", SessionCreated: true}
	})
	for _, tt := range []struct {
		name     string
		class    Class
		status   int
		adapters map[string]Adapter
		attempts int
		served   bool
	}{
		{"stateless 503", ClassStatelessCrossVendor, http.StatusServiceUnavailable, nil, 2, true},
		{"stateless 429", ClassStatelessCrossVendor, http.StatusTooManyRequests, nil, 2, true},
		{"stateless input 400", ClassStatelessCrossVendor, http.StatusBadRequest, nil, 1, false},
		{"same model only", ClassSameModelOnly, http.StatusServiceUnavailable, nil, 1, false},
		{"none", ClassNone, http.StatusServiceUnavailable, nil, 1, false},
		{"create time, before the session", ClassCreateTimeOnly, http.StatusServiceUnavailable, nil, 2, true},
		{"create time, after the session", ClassCreateTimeOnly, 0, map[string]Adapter{"echo-a": sessionFails}, 1, false},
	} {
		t.Run(tt.name, func(t *testing.T) {
			tc := newTestCore(t, coreSetup{capability: "echo", class: tt.class, cards: echoFixtureCards(t), adapters: tt.adapters})
			if tt.status != 0 {
				tc.vendors.fail("echo-a", tt.status)
			}
			org := withBYOK(orgConfig("echo", []string{"echo-a", "echo-b"}, false), "echo-a", "echo-b")
			rc, res, err := tc.call(t, "req-class", "echo", Request{Org: org})
			if tt.served != (err == nil) {
				t.Fatalf("served = %v (%v), want %v", err == nil, err, tt.served)
			}
			call, _ := CallOf(rc)
			if got := len(call.attemptsCopy()); got != tt.attempts {
				t.Fatalf("%d attempts, want %d", got, tt.attempts)
			}
			if tt.served && (res.Attempts[1].Kind != models.AttemptFailover || res.Attempts[1].Reason == "") {
				t.Fatalf("second attempt = %+v, want a failover with its reason", res.Attempts[1])
			}
			if tt.status == http.StatusBadRequest {
				wantAPIError(t, err, http.StatusBadGateway, "provider_400")
			}
		})
	}
}

// At most four vendors are tried.
func TestFourCandidateCap(t *testing.T) {
	var cards []*Card
	var vendors []string
	for _, v := range []string{"echo-a", "echo-b", "echo-c", "echo-d", "echo-e", "echo-f"} {
		cards = append(cards, card("echo", v, 0.001))
		vendors = append(vendors, v)
	}
	tc := newTestCore(t, coreSetup{capability: "echo", cards: cards})
	for _, v := range vendors {
		tc.vendors.fail(v, http.StatusServiceUnavailable)
	}
	_, _, err := tc.call(t, "req-cap", "echo", Request{Org: withBYOK(orgConfig("echo", vendors, false), vendors...)})
	apiErr := wantAPIError(t, err, http.StatusServiceUnavailable, CodeCapabilityProviderUnavailable)
	if refs := apiErr.Metadata.(map[string]any)["attempts"].([]attemptRef); len(refs) != MaxCandidates {
		t.Fatalf("%d attempts, want %d", len(refs), MaxCandidates)
	}
	if tc.vendors.count("echo-e")+tc.vendors.count("echo-f") != 0 {
		t.Fatal("a fifth vendor was called")
	}
}

// A managed call holds units × price × (10000 + bps) / 10000, rounded up,
// keyed by its attempt id, and settles exactly what it billed, as one
// managed-settlement-v1 record.
func TestReserveSettleExactMicros(t *testing.T) {
	ledger := &recordingLedger{Ledger: testLedger(t)}
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t), managed: []string{"echo-a"}, ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 100_000)

	rc, res, err := tc.call(t, "req-exact", "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true)})
	if err != nil {
		t.Fatal(err)
	}
	if len(ledger.holds) != 1 || ledger.holds[0].id != "req-exact.1" || ledger.holds[0].micros != 1050 {
		t.Fatalf("holds = %+v, want one of 1050 micros on req-exact.1", ledger.holds)
	}
	acct := account(t, ledger, testOrg)
	if acct.SpentMicros != 1050 || acct.HeldMicros != 0 {
		t.Fatalf("account = %+v, want 1050 spent and nothing held", acct)
	}
	out := outbox(t, ledger.Ledger, testOrg)
	if len(out) != 1 {
		t.Fatalf("outbox = %v, want one record", out)
	}
	want := map[string]string{"kind": "spend", "request_id": "req-exact.1", "generation_id": "req-exact", "org_id": testOrg,
		"api_key_id": "key-1", "billed_micros": "1050", "upstream_micros": "1000", "markup_micros": "50", "markup_bps": "500",
		"hold_micros": "1050", "landed_micros": "1034", "upstream_fee_bps": "0", "processing_fee_bps": "320",
		"provider": "echo-a", "model": "echo/echo.call", "cost_source": "price_card", "usage_estimated": "0"}
	for k, v := range want {
		if out[0][k] != v {
			t.Errorf("record %s = %q, want %q", k, out[0][k], v)
		}
	}
	if res.Usage.ChargedUSD != 0.00105 || res.Usage.UpstreamCostUSD != 0.001 || res.Usage.CostSource != CostRateCard {
		t.Fatalf("usage = %+v", res.Usage)
	}
	Publish(rc)
	if b := rc.Billing; b.BilledMicros != 1050 || b.UpstreamMicros != 1000 || b.MarkupMicros != 50 || b.Mode != BillingManaged ||
		b.LandedMicros != 1034 || b.CostSource != models.CostSourcePriceCard {
		t.Fatalf("rc.Billing = %+v", b)
	}
}

// A failure the vendor does not bill (free_on_failure, or no answer at all)
// voids the hold; one it bills settles at the rate card.
func TestVoidWhenVendorDoesNotBillFailure(t *testing.T) {
	ledger := testLedger(t)
	billsFailure := card("echo", "echo-b", 0.002, func(c *Card) { c.SKUs[0].FreeOnFailure = false })
	cards := []*Card{echoFixtureCards(t)[0], billsFailure}
	tc := newTestCore(t, coreSetup{capability: "echo", class: ClassNone, cards: cards, managed: []string{"echo-a", "echo-b"},
		ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 100_000)
	tc.vendors.fail("echo-a", http.StatusServiceUnavailable)
	tc.vendors.fail("echo-b", http.StatusInternalServerError)

	org := orgConfig("echo", []string{"echo-a"}, true)
	if _, _, err := tc.call(t, "req-free", "echo", Request{Org: org}); err == nil {
		t.Fatal("a failed call succeeded")
	}
	org.Tools.Capabilities["echo"].Providers = []string{"echo-b"}
	if _, _, err := tc.call(t, "req-billed", "echo", Request{Org: org}); err == nil {
		t.Fatal("a failed call succeeded")
	}
	out := outbox(t, ledger, testOrg)
	if len(out) != 2 || out[0]["kind"] != "void" || out[0]["request_id"] != "req-free.1" || out[1]["kind"] != "spend" ||
		out[1]["billed_micros"] != "2100" || out[1]["request_id"] != "req-billed.1" {
		t.Fatalf("outbox = %v, want a void for the free failure and a 2100-micro spend for the billed one", out)
	}
	if acct := account(t, ledger, testOrg); acct.SpentMicros != 2100 || acct.HeldMicros != 0 {
		t.Fatalf("account = %+v", acct)
	}

	// A vendor that never answered billed nothing, whatever the card says.
	unreachable := AdapterFunc(func(context.Context, VendorCall) (VendorResponse, error) {
		return VendorResponse{}, &VendorError{Message: "connection refused"}
	})
	ledger2 := testLedger(t)
	tc2 := newTestCore(t, coreSetup{capability: "echo", class: ClassNone, cards: []*Card{billsFailure}, managed: []string{"echo-b"},
		ledger: ledger2, markupBps: 500, adapters: map[string]Adapter{"echo-b": unreachable}})
	grant(t, ledger2, testOrg, 100_000)
	if _, _, err := tc2.call(t, "req-dial", "echo", Request{Org: orgConfig("echo", []string{"echo-b"}, true)}); err == nil {
		t.Fatal("a failed call succeeded")
	}
	if out := outbox(t, ledger2, testOrg); len(out) != 1 || out[0]["kind"] != "void" {
		t.Fatalf("outbox = %v, want one void", out)
	}
}

// A charge below a micro accrues on its run and settles once the run owes a
// whole micro: three calls at 0.42 micros settle 0, 0 and 1.
func TestSubMicroAccrual(t *testing.T) {
	ledger := testLedger(t)
	perChar := card("echo", "echo-a", 0.0000004, func(c *Card) { c.SKUs[0].Unit = "char_1k" })
	tc := newTestCore(t, coreSetup{capability: "echo", cards: []*Card{perChar}, managed: []string{"echo-a"}, ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 100_000)
	org := orgConfig("echo", []string{"echo-a"}, true)

	var billed []int64
	for i := 1; i <= 3; i++ {
		rc := newRC(t, "req-sub-"+strconv.Itoa(i))
		rc.Agent.RunID = "run-1"
		res, err := tc.Do(context.Background(), rc, "echo", Request{Org: org, Body: json.RawMessage(`{}`)})
		if err != nil {
			t.Fatal(err)
		}
		billed = append(billed, res.Attempts[0].BilledCostMicros)
	}
	if billed[0] != 0 || billed[1] != 0 || billed[2] != 1 {
		t.Fatalf("billed per call = %v, want [0 0 1]", billed)
	}
	if acct := account(t, ledger, testOrg); acct.SpentMicros != 1 || acct.HeldMicros != 0 {
		t.Fatalf("account = %+v, want 1 micro spent", acct)
	}
	rc := newRC(t, "req-other-run")
	rc.Agent.RunID = "run-2"
	if res, err := tc.Do(context.Background(), rc, "echo", Request{Org: org, Body: json.RawMessage(`{}`)}); err != nil ||
		res.Attempts[0].BilledCostMicros != 0 {
		t.Fatalf("another run's first call billed %v (%v), want 0: runs accrue apart", res.Attempts, err)
	}
}

// Results are cached per org only: another org's identical call reaches the
// vendor, and a cacheable=none vendor is never cached.
func TestCacheOrgOnly(t *testing.T) {
	exa, parallel := goldenCard(t, "exa"), goldenCard(t, "parallel") // cacheable org; cacheable none
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{exa, parallel}, cacheTTL: time.Minute})
	org := withBYOK(orgConfig("search", []string{"exa", "parallel"}, false), "exa", "parallel")
	body := json.RawMessage(`{"q":"same"}`)

	for i := 0; i < 2; i++ {
		if _, _, err := tc.call(t, "req-a-"+strconv.Itoa(i), "search", Request{Org: org, Body: body}); err != nil {
			t.Fatal(err)
		}
	}
	if n := tc.vendors.count("exa"); n != 1 {
		t.Fatalf("exa called %d times for one org's identical calls, want 1", n)
	}
	other := newRC(t, "req-b")
	other.Metadata["org_id"] = "org-other"
	if _, err := tc.Do(context.Background(), other, "search", Request{Org: org, Body: body}); err != nil {
		t.Fatal(err)
	}
	if n := tc.vendors.count("exa"); n != 2 {
		t.Fatalf("exa called %d times, want 2: another org never reads this org's cache", n)
	}
	for i := 0; i < 2; i++ {
		if _, _, err := tc.call(t, "req-p-"+strconv.Itoa(i), "search", Request{Org: org, Body: body, Providers: []string{"parallel"}}); err != nil {
			t.Fatal(err)
		}
	}
	if n := tc.vendors.count("parallel"); n != 2 {
		t.Fatalf("parallel called %d times, want 2: cacheable=none is never cached", n)
	}
	tc.cache.mu.Lock()
	defer tc.cache.mu.Unlock()
	for key := range tc.cache.entries {
		if strings.Contains(key, "|parallel|") {
			t.Fatalf("a cacheable=none result was stored (%s): its terms allow no copy at all", key)
		}
	}
}

// A cache hit is free: no hold, no settlement, cost_source cache_hit, and
// the vendor is not called.
func TestCacheHitFree(t *testing.T) {
	ledger := testLedger(t)
	exa := goldenCard(t, "exa")
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{exa}, managed: []string{"exa"}, ledger: ledger,
		markupBps: 500, cacheTTL: time.Minute})
	grant(t, ledger, testOrg, 100_000)
	org := orgConfig("search", []string{"exa"}, true)
	body := json.RawMessage(`{"q":"cached"}`)

	if _, _, err := tc.call(t, "req-miss", "search", Request{Org: org, Body: body}); err != nil {
		t.Fatal(err)
	}
	spent := account(t, ledger, testOrg).SpentMicros
	rc, res, err := tc.call(t, "req-hit", "search", Request{Org: org, Body: body})
	if err != nil {
		t.Fatal(err)
	}
	if res.Usage.CostSource != CostCacheHit || res.Usage.ChargedUSD != 0 || res.Usage.UpstreamCostUSD != 0 {
		t.Fatalf("hit usage = %+v, want $0 cache_hit", res.Usage)
	}
	if n := tc.vendors.count("exa"); n != 1 {
		t.Fatalf("exa called %d times, want 1", n)
	}
	if acct := account(t, ledger, testOrg); acct.SpentMicros != spent || acct.HeldMicros != 0 {
		t.Fatalf("account after the hit = %+v, want spent unchanged at %d", acct, spent)
	}
	if out := outbox(t, ledger, testOrg); len(out) != 1 {
		t.Fatalf("outbox = %v, want only the miss's record", out)
	}
	Publish(rc)
	if a := rc.AttemptsSnapshot(); len(a) != 1 || a[0].Billable || a[0].BilledCostMicros != 0 {
		t.Fatalf("hit attempts = %+v, want one unbillable attempt", a)
	}
}

// Retention is the most restrictive of the content mode, the card's
// results_retention and the org's tighten-only override.
func TestRetentionMostRestrictive(t *testing.T) {
	for _, tt := range []struct {
		mode     models.ContentMode
		card     string
		override string
		want     models.ContentMode
		withCard bool
		withOrg  bool
	}{
		{"", "full", "", models.ContentModeFull, true, false},
		{models.ContentModeFull, "metadata", "", models.ContentModeMetadata, true, false},
		{models.ContentModeMetadata, "full", "", models.ContentModeMetadata, true, false},
		{models.ContentModeFull, "none", "", models.ContentModeNone, true, false},
		{models.ContentModeNone, "full", "", models.ContentModeNone, true, false},
		{models.ContentModeFull, "full", "metadata", models.ContentModeMetadata, true, true},
		{models.ContentModeFull, "full", "full", models.ContentModeFull, true, true},
		{models.ContentModeFull, "forever", "", models.ContentModeNone, true, false},
		{models.ContentModeMetadata, "", "", models.ContentModeMetadata, false, false},
	} {
		rc := newRC(t, "req-retention")
		rc.Privacy.ContentMode = tt.mode
		if tt.withOrg {
			org := orgConfig("echo", nil, false)
			org.Tools.Policy = &tenant.ToolsPolicyConfig{RetentionOverride: tt.override}
			attachCall(rc, "echo", Request{Org: org})
		}
		var c *Card
		if tt.withCard {
			c = card("echo", "echo-a", 0.001, func(c *Card) { c.ResultsRetention = tt.card })
		}
		if got := RetentionFor(rc, c); got != tt.want {
			t.Errorf("mode %q, card %q, override %q: %q, want %q", tt.mode, tt.card, tt.override, got, tt.want)
		}
		calls.Delete(rc)
	}
}

// A vendor whose terms forbid analysing its results flags the request's log
// entry eval_export=forbidden, through SetLogEvidence; an allowed one does
// not.
func TestEvalExportFlag(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{goldenCard(t, "tavily"), goldenCard(t, "exa")}})
	org := withBYOK(orgConfig("search", []string{"tavily", "exa"}, false), "tavily", "exa")

	rc, _, err := tc.call(t, "req-forbidden", "search", Request{Org: org, Providers: []string{"tavily"}})
	if err != nil {
		t.Fatal(err)
	}
	if got := string(rc.SnapshotV2().LogEvidence["eval_export"]); got != `"forbidden"` {
		t.Fatalf("eval_export evidence = %s, want \"forbidden\"", got)
	}
	rc, _, err = tc.call(t, "req-allowed", "search", Request{Org: org, Providers: []string{"exa"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, set := rc.SnapshotV2().LogEvidence["eval_export"]; set {
		t.Fatal("an allowed vendor's call set eval_export")
	}
}

// A call from an MCP built-in carries _meta io.futureagi/billing.
func TestBillingMetaForMCP(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t), managed: []string{"echo-a"}, markupBps: 500})
	grant(t, tc.ledger, testOrg, 100_000)
	org := orgConfig("echo", []string{"echo-a"}, true)

	_, res, err := tc.call(t, "req-mcp", "echo", Request{Org: org, MCP: true})
	if err != nil {
		t.Fatal(err)
	}
	meta, _ := res.Meta[MetaBillingKey].(map[string]any)
	if meta["billing_mode"] != BillingManaged || meta["upstream_micros"] != int64(1000) || meta["billed_micros"] != int64(1050) ||
		meta["cost_source"] != CostRateCard {
		t.Fatalf("billing meta = %v", res.Meta)
	}
	if _, res, _ = tc.call(t, "req-rest", "echo", Request{Org: org}); res.Meta != nil {
		t.Fatalf("a REST call carries meta %v", res.Meta)
	}
}

// WorstCaseMicros is the hold Do takes, through the installed core too.
func TestWorstCaseMicrosIsTheReserve(t *testing.T) {
	ledger := &recordingLedger{Ledger: billing.NewMemoryLedger()}
	cheap := echoFixtureCards(t)[0]
	dear := card("echo", "echo-b", 0.004)
	tc := newTestCore(t, coreSetup{capability: "echo", cards: []*Card{cheap, dear}, managed: []string{"echo-a", "echo-b"},
		ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 1_000_000)
	org := orgConfig("echo", []string{"echo-a"}, true)
	req := Request{Org: org, Units: 3, Body: json.RawMessage(`{}`)}

	rc := newRC(t, "req-worst")
	want := tc.WorstCaseMicros(rc, "echo", req)
	if _, err := tc.Do(context.Background(), rc, "echo", req); err != nil {
		t.Fatal(err)
	}
	if want != 3150 || len(ledger.holds) != 1 || ledger.holds[0].micros != want {
		t.Fatalf("WorstCaseMicros = %d, holds = %+v, want one hold of 3150", want, ledger.holds)
	}
	Install(tc.Core)
	t.Cleanup(func() { Install(nil) })
	org.Tools.Capabilities["echo"].Providers = []string{"echo-a", "echo-b"}
	if got := WorstCaseMicros(newRC(t, "req-worst-2"), "echo", req); got != 12600 {
		t.Fatalf("installed WorstCaseMicros = %d, want 12600, the dearer candidate's hold", got)
	}
	if got := WorstCaseMicros(newRC(t, "req-worst-3"), "echo", Request{Org: withBYOK(orgConfig("echo", []string{"echo-a"}, false), "echo-a")}); got != 0 {
		t.Fatalf("BYOK-only WorstCaseMicros = %d, want 0", got)
	}
}

// recordingLedger records every RESERVE it passes on.
type recordingLedger struct {
	billing.Ledger
	holds []struct {
		id     string
		micros int64
	}
}

func (l *recordingLedger) Reserve(ctx context.Context, org, holdID string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	l.holds = append(l.holds, struct {
		id     string
		micros int64
	}{holdID, micros})
	return l.Ledger.Reserve(ctx, org, holdID, micros, ttl, mode)
}

// Three vendor failures in a row open the vendor's circuit, which the ranking
// then puts last; the input's own 4xx says nothing of the vendor and never
// opens it.
func TestInputErrorsLeaveTheCircuitClosed(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "echo", class: ClassNone, cards: echoFixtureCards(t)})
	org := withBYOK(orgConfig("echo", []string{"echo-a"}, false), "echo-a")
	tc.vendors.fail("echo-a", http.StatusBadRequest)
	for i := 0; i < breakerFailures; i++ {
		tc.call(t, "req-400-"+strconv.Itoa(i), "echo", Request{Org: org})
	}
	if tc.health.view("echo", "echo-a").open {
		t.Fatal("input errors opened the vendor's circuit")
	}
	tc.vendors.fail("echo-a", http.StatusServiceUnavailable)
	for i := 0; i < breakerFailures; i++ {
		tc.call(t, "req-503-"+strconv.Itoa(i), "echo", Request{Org: org})
	}
	if !tc.health.view("echo", "echo-a").open {
		t.Fatal("three 503s did not open the vendor's circuit")
	}
	for name, order := range map[string][]string{"auto pool": nil, "org order": {"echo-a", "echo-b"}} {
		pool := withBYOK(orgConfig("echo", order, false), "echo-a", "echo-b")
		cands, _, apiErr := tc.selectCandidates(newRC(t, "req-rank"), "echo", Request{Org: pool})
		if apiErr != nil || len(cands) != 2 || cands[0].vendor != "echo-b" {
			t.Fatalf("%s: candidates %+v (%v), want echo-b first while echo-a's circuit is open", name, cands, apiErr)
		}
	}
}

// An adapter that panics never leaves its managed hold behind: the request's
// finalizer voids it.
func TestPanickingAdapterHoldIsVoided(t *testing.T) {
	ledger := testLedger(t)
	panics := AdapterFunc(func(context.Context, VendorCall) (VendorResponse, error) { panic("vendor adapter bug") })
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t)[:1], managed: []string{"echo-a"}, ledger: ledger,
		markupBps: 500, adapters: map[string]Adapter{"echo-a": panics}})
	grant(t, ledger, testOrg, 100_000)

	rc := newRC(t, "req-panic")
	func() {
		defer func() { _ = recover() }()
		tc.Do(context.Background(), rc, "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true), Body: json.RawMessage(`{}`)})
	}()
	if acct := account(t, ledger, testOrg); acct.HeldMicros != 1050 {
		t.Fatalf("held %d before the request ended, want the 1050 hold", acct.HeldMicros)
	}
	rc.RunFinalizers()
	if acct := account(t, ledger, testOrg); acct.HeldMicros != 0 || acct.SpentMicros != 0 {
		t.Fatalf("account after the request = %+v, want the hold voided and nothing spent", acct)
	}
	if out := outbox(t, ledger, testOrg); len(out) != 1 || out[0]["kind"] != "void" || out[0]["request_id"] != "req-panic.1" {
		t.Fatalf("outbox = %v, want one void for req-panic.1", out)
	}

	// A call that finished settles once; its finalizer writes nothing more.
	tc2 := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t)[:1], managed: []string{"echo-a"}, ledger: ledger, markupBps: 500})
	rc2 := newRC(t, "req-done")
	if _, err := tc2.Do(context.Background(), rc2, "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true), Body: json.RawMessage(`{}`)}); err != nil {
		t.Fatal(err)
	}
	rc2.RunFinalizers()
	if out := outbox(t, ledger, testOrg); len(out) != 2 || out[1]["kind"] != "spend" {
		t.Fatalf("outbox = %v, want the void and then one spend", out)
	}
}
