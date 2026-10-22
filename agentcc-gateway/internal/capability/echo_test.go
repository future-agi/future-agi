package capability

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// With $0.01 granted, a managed echo priced $0.001
// settles 1,050 micros per call (5% tool markup), and the tenth call is
// refused with the credits 402: 550 micros remain, under the 1,050 hold.
func TestEchoManagedSettles1050AndRefusesTenth(t *testing.T) {
	ledger := testLedger(t)
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t), managed: []string{"echo-a"}, ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 10_000)
	org := orgConfig("echo", []string{"echo-a"}, true)

	for i := 1; i <= 9; i++ {
		_, res, err := tc.call(t, "req-echo-"+strconv.Itoa(i), "echo", Request{Org: org})
		if err != nil {
			t.Fatalf("call %d: %v", i, err)
		}
		if got := res.Attempts[len(res.Attempts)-1].BilledCostMicros; got != 1050 {
			t.Fatalf("call %d billed %d micros, want 1050", i, got)
		}
		if errs := schematest.ValidateDef(t, "capability-usage-v1.schema.json", "Usage", res.Usage); len(errs) > 0 {
			t.Fatalf("call %d usage fails capability-usage-v1: %v", i, errs)
		}
	}
	_, _, err := tc.call(t, "req-echo-10", "echo", Request{Org: org})
	apiErr := wantAPIError(t, err, http.StatusPaymentRequired, "managed_credits_insufficient")
	meta := apiErr.Metadata.(models.PaymentMeta)
	if meta.LimitSource != models.LimitSourceCredits || *meta.RequiredUSD != 0.00105 || *meta.AvailableUSD != 0.00055 || meta.Scope != models.ScopeOrg {
		t.Fatalf("402 metadata = %+v", meta)
	}
	out := outbox(t, ledger, testOrg)
	if len(out) != 9 {
		t.Fatalf("outbox has %d records, want 9", len(out))
	}
	for _, rec := range out {
		if rec["kind"] != "spend" || rec["billed_micros"] != "1050" || rec["provider"] != "echo-a" {
			t.Fatalf("record = %v", rec)
		}
	}
	if acct := account(t, ledger, testOrg); acct.SpentMicros != 9450 || acct.HeldMicros != 0 {
		t.Fatalf("account = %+v, want 9450 spent, nothing held", acct)
	}
	if n := tc.vendors.count("echo-a"); n != 9 {
		t.Fatalf("vendor called %d times, want 9: the refused call never reaches it", n)
	}
}

// The same call on the org's own echo key is free and
// writes nothing to the org's outbox.
func TestEchoByokFreeNoOutbox(t *testing.T) {
	ledger := testLedger(t)
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t), managed: []string{"echo-a"}, ledger: ledger, markupBps: 500})
	grant(t, ledger, testOrg, 10_000)
	org := withBYOK(orgConfig("echo", []string{"echo-a"}, true), "echo-a")

	rc, res, err := tc.call(t, "req-byok", "echo", Request{Org: org})
	if err != nil {
		t.Fatal(err)
	}
	if res.Usage.BillingMode != BillingBYOK || res.Usage.ChargedUSD != 0 || res.Usage.UpstreamCostUSD != 0.001 {
		t.Fatalf("usage = %+v, want byok, $0 charged, a $0.001 upstream estimate", res.Usage)
	}
	if out := outbox(t, ledger, testOrg); len(out) != 0 {
		t.Fatalf("outbox = %v, want nothing", out)
	}
	if acct := account(t, ledger, testOrg); acct.SpentMicros != 0 || acct.HeldMicros != 0 {
		t.Fatalf("account = %+v, want untouched", acct)
	}
	Publish(rc)
	a := rc.AttemptsSnapshot()
	if len(a) != 1 || a[0].CredentialType != models.CredentialBYOK || a[0].BilledCostMicros != 0 || a[0].UpstreamCostMicros != 1000 {
		t.Fatalf("attempts = %+v", a)
	}
	if rc.Billing.Mode != BillingBYOK || rc.Billing.BilledMicros != 0 {
		t.Fatalf("rc.Billing = %+v", rc.Billing)
	}
}

// A 503 from vendor 1 falls back to vendor 2, with
// two attempts recorded.
func TestEchoVendorFallbackTwoAttempts(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "echo", cards: echoFixtureCards(t)})
	tc.vendors.fail("echo-a", http.StatusServiceUnavailable)
	org := withBYOK(orgConfig("echo", []string{"echo-a", "echo-b"}, false), "echo-a", "echo-b")

	rc, res, err := tc.call(t, "req-fallback", "echo", Request{Org: org, Body: json.RawMessage(`{"say":"hi"}`)})
	if err != nil {
		t.Fatal(err)
	}
	if res.Provider != "echo-b" || string(res.Body) != `{"say":"hi"}` {
		t.Fatalf("served by %s with %s", res.Provider, res.Body)
	}
	Publish(rc)
	a := rc.AttemptsSnapshot()
	if len(a) != 2 || a[0].AttemptID != "req-fallback.1" || a[0].Provider != "echo-a" || a[0].StatusCode != 503 ||
		a[0].Outcome != models.OutcomeError || a[1].AttemptID != "req-fallback.2" || a[1].Provider != "echo-b" ||
		a[1].Kind != models.AttemptFailover || a[1].Reason != models.ReasonStatus5xx || a[1].Outcome != models.OutcomeSuccess {
		t.Fatalf("attempts = %+v", a)
	}
	if tc.vendors.count("echo-a") != 1 || tc.vendors.count("echo-b") != 1 {
		t.Fatal("each vendor should be called once")
	}
}

// A no_perf_disclosure vendor's latency reaches no attempt, usage or
// refusal; another vendor's does (so the check can fail).
func TestNoPerfDisclosureNotInResponse(t *testing.T) {
	tc := newTestCore(t, coreSetup{capability: "search", cards: []*Card{goldenCard(t, "tavily"), goldenCard(t, "exa")},
		now: steppingClock(50 * time.Millisecond)})
	org := withBYOK(orgConfig("search", []string{"tavily", "exa"}, false), "tavily", "exa")

	_, res, err := tc.call(t, "req-tavily", "search", Request{Org: org, Providers: []string{"tavily"}})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(res)
	if res.Attempts[0].LatencyMs != 0 || strings.Contains(string(raw), "latency") || strings.Contains(string(raw), "ttfb") {
		t.Fatalf("a no_perf_disclosure vendor's result carries a latency figure: %s", raw)
	}
	_, res, err = tc.call(t, "req-exa", "search", Request{Org: org, Providers: []string{"exa"}})
	if err != nil || res.Attempts[0].LatencyMs <= 0 {
		t.Fatalf("exa attempt = %+v (%v), want its latency recorded", res.Attempts, err)
	}
	tc.vendors.fail("tavily", http.StatusServiceUnavailable)
	_, _, err = tc.call(t, "req-tavily-down", "search", Request{Org: org, Providers: []string{"tavily"}})
	refusal, _ := json.Marshal(wantAPIError(t, err, http.StatusServiceUnavailable, CodeCapabilityProviderUnavailable).Metadata)
	if strings.Contains(string(refusal), "latency") {
		t.Fatalf("refusal carries a latency figure: %s", refusal)
	}
}

// Every fixture card the core's tests and the scenario run on is a valid
// tool-price-card-v1 card, and Card carries every golden field unchanged.
func TestCardSourceFixturesMatchGolden(t *testing.T) {
	validate := func(name string, c *Card) {
		raw, _ := json.Marshal(c)
		var instance map[string]any
		_ = json.Unmarshal(raw, &instance)
		if errs := schematest.ValidateDef(t, "tool-price-card-v1.schema.json", "Card", instance); len(errs) > 0 {
			t.Errorf("%s fails tool-price-card-v1: %v", name, errs)
		}
	}
	for _, c := range echoFixtureCards(t) {
		validate("fixture "+c.Vendor, c)
	}
	validate("test card", card("echo", "echo-x", 0.001))

	// Card knows every field the golden uses (an unknown one would be lost
	// between the catalog and the core), and re-encodes as a valid card.
	golden := schematest.ReadExample(t, "tool-price-card-v1.json")
	for _, raw := range golden["cards"].([]any) {
		b, _ := json.Marshal(raw)
		dec := json.NewDecoder(bytes.NewReader(b))
		dec.DisallowUnknownFields()
		var c Card
		if err := dec.Decode(&c); err != nil {
			t.Fatalf("golden card %s: %v", raw.(map[string]any)["vendor"], err)
		}
		validate("golden "+c.Vendor, &c)
	}
}

// The echo adapter prices what the vendor reports in its usage object, as
// the capability mock's echo fakes report it: units at the card's rate, and
// a cost when the SKU takes the cost from the result.
func TestEchoReadsVendorReportedUsage(t *testing.T) {
	reports := card("echo", "echo-b", 0, func(c *Card) { c.SKUs[0].CostFromResultMeta = true })
	vendor := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/echo-a/") {
			io.WriteString(w, `{"vendor":"echo-a","echo":{},"usage":{"units":3}}`)
			return
		}
		io.WriteString(w, `{"vendor":"echo-b","echo":{},"usage":{"units":1,"cost_usd":0.002}}`)
	}))
	t.Cleanup(vendor.Close)
	tc := newTestCore(t, coreSetup{capability: "echo", cards: []*Card{echoFixtureCards(t)[0], reports}, managed: []string{"echo-a", "echo-b"},
		markupBps: 500, adapters: map[string]Adapter{"echo-a": EchoAdapter{Client: vendor.Client()}, "echo-b": EchoAdapter{Client: vendor.Client()}}})
	tc.Core.creds = ConfigCredentials{Operator: map[string]config.ProviderConfig{
		"echo-a": {BaseURL: vendor.URL + "/echo-a", Managed: true, APIKey: "k"},
		"echo-b": {BaseURL: vendor.URL + "/echo-b", Managed: true, APIKey: "k"}}}
	grant(t, tc.ledger, testOrg, 1_000_000)
	org := orgConfig("echo", []string{"echo-a"}, true)

	_, res, err := tc.call(t, "req-units", "echo", Request{Org: org})
	if err != nil {
		t.Fatal(err)
	}
	if res.Attempts[0].UpstreamCostMicros != 3000 || res.Attempts[0].BilledCostMicros != 3150 || res.Usage.Units[0].Count != 3 ||
		res.Usage.CostSource != CostRateCard || res.Usage.Estimated {
		t.Fatalf("reported units: attempt %+v, usage %+v; want 3 units at $0.001", res.Attempts[0], res.Usage)
	}
	org.Tools.Capabilities["echo"].Providers = []string{"echo-b"}
	_, res, err = tc.call(t, "req-cost", "echo", Request{Org: org})
	if err != nil {
		t.Fatal(err)
	}
	if res.Attempts[0].UpstreamCostMicros != 2000 || res.Attempts[0].BilledCostMicros != 2100 || res.Usage.CostSource != CostVendorReported ||
		res.Attempts[0].CostSource != models.CostSourceProviderReported {
		t.Fatalf("reported cost: attempt %+v, usage %+v; want $0.002 from the vendor", res.Attempts[0], res.Usage)
	}
}
