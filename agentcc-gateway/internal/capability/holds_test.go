package capability

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// zeroDefaultCard is a card whose default SKU is free while a deeper tier is
// priced at $0.01 a result: a call of the default SKU holds 0 micros.
func zeroDefaultCard() *Card {
	return card("echo", "echo-a", 0, func(c *Card) {
		c.SKUs = []SKU{
			{SKU: "echo.free", Unit: "request", USDPerUnit: 0, FreeOnFailure: true},
			{SKU: "echo.deep", Unit: "result", USDPerUnit: 0.01, Tier: "deep", FreeOnFailure: true},
		}
	})
}

// costFromMetaCard is a card whose only SKU has no list price: the vendor
// reports each call's cost.
func costFromMetaCard() *Card {
	return card("echo", "echo-a", 0, func(c *Card) {
		c.SKUs = []SKU{{SKU: "echo.metered", Unit: "request", CostFromResultMeta: true, FreeOnFailure: true}}
	})
}

// reports is an adapter that answers {} and reports units and a cost.
func reports(units []Unit, costUSD *float64) Adapter {
	return AdapterFunc(func(context.Context, VendorCall) (VendorResponse, error) {
		return VendorResponse{Body: json.RawMessage(`{}`), Units: units, CostUSD: costUSD}, nil
	})
}

// A managed call whose hold comes to 0 micros still settles what it bills:
// the vendor reports a dearer SKU than the one held, or reports its own cost
// with no reserve default configured. The ledger is the system of record,
// so the request's charge and the ledger's spend agree.
func TestZeroHoldManagedCallSettlesWhatItBills(t *testing.T) {
	usd := 0.02
	for name, tt := range map[string]struct {
		card       *Card
		adapter    Adapter
		reserveUSD float64
		want       int64
	}{
		"vendor reports a priced tier": {zeroDefaultCard(), reports([]Unit{{SKU: "echo.deep", Count: 3}}, nil), 0.10, 31_500},
		"vendor reports its cost":      {costFromMetaCard(), reports(nil, &usd), 0, 21_000},
	} {
		t.Run(name, func(t *testing.T) {
			ledger := &recordingLedger{Ledger: testLedger(t)}
			tc := newTestCore(t, coreSetup{capability: "echo", cards: []*Card{tt.card}, managed: []string{"echo-a"}, ledger: ledger,
				markupBps: 500, adapters: map[string]Adapter{"echo-a": tt.adapter}})
			tc.opts.ReserveDefaultUSD = tt.reserveUSD
			grant(t, ledger, testOrg, 100_000)

			rc, res, err := tc.call(t, "req-zero-hold", "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true)})
			if err != nil {
				t.Fatal(err)
			}
			if len(ledger.holds) != 1 || ledger.holds[0].micros != 0 || ledger.holds[0].id != "req-zero-hold.1" {
				t.Fatalf("holds = %+v, want one 0-micro hold for req-zero-hold.1", ledger.holds)
			}
			if got := res.Attempts[0].BilledCostMicros; got != tt.want {
				t.Fatalf("billed %d micros, want %d", got, tt.want)
			}
			if acct := account(t, ledger, testOrg); acct.SpentMicros != tt.want || acct.HeldMicros != 0 {
				t.Fatalf("account = %+v, want %d spent and nothing held", acct, tt.want)
			}
			out := outbox(t, ledger.Ledger, testOrg)
			if len(out) != 1 || out[0]["kind"] != "spend" || out[0]["billed_micros"] != strconv.FormatInt(tt.want, 10) ||
				out[0]["hold_micros"] != "0" || out[0]["request_id"] != "req-zero-hold.1" {
				t.Fatalf("outbox = %v, want one %d-micro spend on a 0-micro hold", out, tt.want)
			}
			Publish(rc)
			if rc.Billing.BilledMicros != tt.want {
				t.Fatalf("rc.Billing.BilledMicros = %d, want %d, the settled amount", rc.Billing.BilledMicros, tt.want)
			}
		})
	}
}

// Every managed candidate fails closed before its vendor is called, whatever
// its hold: with no ledger it is 503 managed_billing_unavailable, and with no
// credit left it is the credits 402.
func TestManagedCallFailsClosedBeforeTheVendor(t *testing.T) {
	usd := 0.02
	for name, c := range map[string]*Card{"priced": echoFixtureCards(t)[0], "zero hold": costFromMetaCard()} {
		t.Run(name+", no ledger", func(t *testing.T) {
			var calls int
			counting := AdapterFunc(func(context.Context, VendorCall) (VendorResponse, error) {
				calls++
				return VendorResponse{Body: json.RawMessage(`{}`), CostUSD: &usd}, nil
			})
			tc := newTestCore(t, coreSetup{capability: "echo", cards: []*Card{c}, managed: []string{"echo-a"}, markupBps: 500,
				adapters: map[string]Adapter{"echo-a": counting}})
			tc.opts.Ledger, tc.opts.ReserveDefaultUSD = nil, 0

			_, _, err := tc.call(t, "req-no-ledger", "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true)})
			apiErr := wantAPIError(t, err, http.StatusServiceUnavailable, codeBillingUnavailable)
			if apiErr.Metadata.(models.PaymentMeta).Reason != models.ReasonRedisUnavailable {
				t.Fatalf("503 metadata = %+v", apiErr.Metadata)
			}
			if calls != 0 {
				t.Fatalf("vendor called %d times, want 0: managed billing fails closed", calls)
			}
		})
	}

	t.Run("zero hold, credit exhausted", func(t *testing.T) {
		ledger := testLedger(t)
		tc := newTestCore(t, coreSetup{capability: "echo", cards: []*Card{zeroDefaultCard()}, managed: []string{"echo-a"},
			ledger: ledger, markupBps: 500})
		grant(t, ledger, testOrg, 0)
		_, _, err := tc.call(t, "req-exhausted", "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true)})
		wantAPIError(t, err, http.StatusPaymentRequired, codeCreditsExhausted)
		if n := tc.vendors.count("echo-a"); n != 0 {
			t.Fatalf("vendor called %d times, want 0", n)
		}
	})

	t.Run("zero hold, no account", func(t *testing.T) {
		tc := newTestCore(t, coreSetup{capability: "echo", cards: []*Card{zeroDefaultCard()}, managed: []string{"echo-a"}, markupBps: 500})
		_, _, err := tc.call(t, "req-no-account", "echo", Request{Org: orgConfig("echo", []string{"echo-a"}, true)})
		apiErr := wantAPIError(t, err, http.StatusServiceUnavailable, codeBillingUnavailable)
		if apiErr.Metadata.(models.PaymentMeta).Reason != models.ReasonAccountStateMissing {
			t.Fatalf("503 metadata = %+v, want account_state_missing", apiErr.Metadata)
		}
		if n := tc.vendors.count("echo-a"); n != 0 {
			t.Fatalf("vendor called %d times, want 0", n)
		}
	})
}

// A caller's fractional units never price a counted unit below one: five
// calls asking for 0.000001 of a $0.001-a-request SKU are held and settled
// at 1,050 micros each, the list price with the 5% markup.
func TestFractionalUnitsNeverPriceBelowOneUnit(t *testing.T) {
	ledger := testLedger(t)
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t)[:1], managed: []string{"echo-a"}, ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 100_000)
	org := orgConfig("echo", []string{"echo-a"}, true)

	for i := 1; i <= 5; i++ {
		req := Request{Org: org, Units: 0.000001}
		if got := tc.WorstCaseMicros(newRC(t, "req-frac-worst"), "echo", req); got != 1050 {
			t.Fatalf("worst case %d micros, want 1050", got)
		}
		_, res, err := tc.call(t, "req-frac-"+strconv.Itoa(i), "echo", req)
		if err != nil {
			t.Fatal(err)
		}
		if got := res.Attempts[0].BilledCostMicros; got != 1050 || res.Usage.Units[0].Count != 1 {
			t.Fatalf("call %d billed %d micros for %v units, want 1050 for 1", i, got, res.Usage.Units)
		}
	}
	if acct := account(t, ledger, testOrg); acct.SpentMicros != 5250 {
		t.Fatalf("spent %d micros, want 5250", acct.SpentMicros)
	}

	for _, tt := range []struct {
		unit      string
		requested float64
		min       float64
		want      float64
	}{
		{"request", 0.000001, 0, 1}, {"request", 2.2, 0, 3}, {"result", 0.5, 0, 1}, {"page", 3, 5, 5},
		{"char_1k", 0.25, 0, 0.25}, {"second", 0, 0, 1}, {"minute", 2.5, 5, 5},
	} {
		if got := callUnits(SKU{Unit: tt.unit, MinUnits: tt.min}, tt.requested); got != tt.want {
			t.Errorf("callUnits(%s, %v, min %v) = %v, want %v", tt.unit, tt.requested, tt.min, got, tt.want)
		}
	}
}

// Attempt indices are allocated under the call record's lock, so they never
// repeat on one request, and a second call on the same request continues
// after the first one's attempts.
func TestAttemptIndicesNeverRepeatOnARequest(t *testing.T) {
	rc := newRC(t, "req-indices")
	call := attachCall(rc, "echo", Request{})
	const n = 64
	seen := make(chan int, n)
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			seen <- call.nextIndex(rc)
		}()
	}
	wg.Wait()
	close(seen)
	got := map[int]bool{}
	for idx := range seen {
		if got[idx] {
			t.Fatalf("attempt index %d handed out twice", idx)
		}
		got[idx] = true
	}
	if len(got) != n || !got[1] || !got[n] {
		t.Fatalf("indices %v, want 1..%d", got, n)
	}

	ledger := testLedger(t)
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t)[:1], managed: []string{"echo-a"}, ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 100_000)
	org := orgConfig("echo", []string{"echo-a"}, true)
	rc2 := newRC(t, "req-two-calls")
	for i := 0; i < 2; i++ {
		if _, err := tc.Do(context.Background(), rc2, "echo", Request{Org: org, Body: json.RawMessage(`{"i":` + strconv.Itoa(i) + `}`)}); err != nil {
			t.Fatal(err)
		}
	}
	out := outbox(t, ledger, testOrg)
	if len(out) != 2 || out[0]["request_id"] != "req-two-calls.1" || out[1]["request_id"] != "req-two-calls.2" ||
		out[0]["kind"] != "spend" || out[1]["kind"] != "spend" {
		t.Fatalf("outbox = %v, want two spends, .1 and .2", out)
	}
}

// A call that reached a no_perf_disclosure vendor is flagged in the request
// metadata, so the request log can drop the latency that is that vendor's;
// another vendor's call is not.
func TestNoPerfDisclosureFlagged(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{goldenCard(t, "tavily"), goldenCard(t, "exa")}})
	org := withBYOK(orgConfig("search", []string{"tavily", "exa"}, false), "tavily", "exa")

	rc, _, err := tc.call(t, "req-tavily", "search", Request{Org: org, Providers: []string{"tavily"}})
	if err != nil {
		t.Fatal(err)
	}
	if rc.Metadata[MetaNoPerfDisclosure] != "true" {
		t.Fatalf("metadata %v, want %s=true for a no_perf_disclosure vendor", rc.Metadata, MetaNoPerfDisclosure)
	}
	rc, _, err = tc.call(t, "req-exa", "search", Request{Org: org, Providers: []string{"exa"}})
	if err != nil {
		t.Fatal(err)
	}
	if _, set := rc.Metadata[MetaNoPerfDisclosure]; set {
		t.Fatal("a vendor that discloses its performance was flagged")
	}
}

// Metadata retention keeps the request's size, never a hash of its content.
func TestMetadataRequestKeepsNoBodyHash(t *testing.T) {
	rc := newRC(t, "req-meta")
	call := attachCall(rc, "search", Request{Body: json.RawMessage(`{"q":"acme layoffs"}`)})
	var got map[string]any
	if err := json.Unmarshal(call.MetadataRequest(), &got); err != nil {
		t.Fatal(err)
	}
	if _, set := got["body_sha256"]; set || got["body_bytes"] != float64(20) || got["capability"] != "search" {
		t.Fatalf("metadata request = %v, want capability and size only", got)
	}
}
