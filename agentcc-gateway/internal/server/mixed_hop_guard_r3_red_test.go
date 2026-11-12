package server

// MIXED-HOP-GUARD-1 round 3, pinned red-first: this file uses only helpers
// and APIs that exist at 4e38b7961b (the round-2 head), where its reds fail.
//
//   - Money: a request records at most 16 attempts (models.MaxAttempts) and
//     dropped every attempt past them, the one that served included. An org
//     model chain of 16 failed managed calls and then a managed model that
//     served billed the answer on the last failure's card (100 micros, not
//     18,375); failures that carried a charge past the record went unbilled.
//   - The traffic mirror copied a customer key's request onto the operator's
//     own credentials for a global provider: a call nobody billed, outside
//     the customer's org providers.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The serving model's card prices 1,000 prompt and 500 completion tokens at
// 17,500 upstream micros, 18,375 billed at the test gateway's 5% markup. A
// failure whose upstream reported 0.0001 USD before it failed is billed 105.
const (
	pastRecordBilled       = 18375
	pastRecordChargeBilled = 105
)

// pastRecordCatalog adds models to the managed catalog at the cheapest
// managed model's card.
func pastRecordCatalog(g *managedTestGateway, names []string) {
	all := make(map[string]*catalog.Model, len(testCatalogModels)+len(names))
	for id, m := range testCatalogModels {
		all[id] = m
	}
	for _, name := range names {
		m := *testCatalogModels[mixedHopCheap]
		m.ID = name
		all[name] = &m
	}
	g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled", CatalogSource: catalog.SourceOpenRouter,
		FetchedAt: time.Now(), Models: all})
	g.srv.RefreshModelDB()
}

// pastRecordAnswers makes the managed upstream answer with usage and no
// reported cost, so every answer is priced on the card of the attempt it is
// billed to. A stream to a model in charged sends its usage, 0.0001 USD,
// and ends before any output: the attempt fails carrying a charge.
func pastRecordAnswers(g *managedTestGateway, charged map[string]bool) {
	response := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"id":"c1","object":"chat.completion","model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500}}`, model)
	})
	stream := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
		w.Header().Set("Content-Type", "text/event-stream")
		if charged[model] {
			writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":0,"total_tokens":10,"cost":0.0001}`)
			fmt.Fprint(w, "data: [DONE]\n\n")
			return
		}
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"hi"}}]`)
		writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500}`)
		writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
		fmt.Fprint(w, "data: [DONE]\n\n")
	})
	g.managed.responseWith.Store(&response)
	g.managed.streamWith.Store(&stream)
}

// callsFor is how many calls u took for model.
func callsFor(u *recordingUpstream, model string) int {
	u.mu.Lock()
	defer u.mu.Unlock()
	n := 0
	for _, m := range u.models {
		if m == model {
			n++
		}
	}
	return n
}

// receiptAttempts is the attempts of the org's one settlement, which must be
// a spend of billed micros. Their ids must read "<request_id>.1" to ".<n>",
// in order: the record log-entry-v2 holds to 16 attempts.
func receiptAttempts(t *testing.T, g *managedTestGateway, billed int64) []billing.AttemptSummary {
	t.Helper()
	recs := g.outbox("org-m")
	if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != fmt.Sprint(billed) {
		t.Fatalf("settlements %v; want one spend of %d", recs, billed)
	}
	var sent []billing.AttemptSummary
	if err := json.Unmarshal([]byte(recs[0]["attempts"]), &sent); err != nil {
		t.Fatal(err)
	}
	if len(sent) == 0 || len(sent) > models.MaxAttempts {
		t.Fatalf("settlement lists %d attempts; want 1 to %d", len(sent), models.MaxAttempts)
	}
	for i, a := range sent {
		if !strings.HasSuffix(a.AttemptID, fmt.Sprintf(".%d", i+1)) {
			t.Fatalf("settlement attempt %d has id %q; want ids .1 to .%d in order", i, a.AttemptID, len(sent))
		}
	}
	return sent
}

// An org model chain of failed managed calls (the primary and failed-1
// fallbacks, none charged), optionally 40 candidates on the org's own key
// the guard refuses, then a managed model that serves: the request bills the
// serving model's card exactly, nothing is left held, and its settlement
// lists the serving attempt last. With 15 failures every attempt fits the
// record of 16, which is the control row: it bills right at the round-2 head
// too.
func TestMixedHopGuardServingAttemptPastTheRecord(t *testing.T) {
	for _, failed := range []int{15, 16, 20} {
		for _, skips := range []int{0, 40} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("failed=%d/skips=%d/stream=%v", failed, skips, stream), func(t *testing.T) {
					org := mixedHopOrg()
					var chain, failures []string
					for i := 1; i < failed; i++ {
						name := fmt.Sprintf("qwen/failed-%d", i)
						failures, chain = append(failures, name), append(chain, name)
					}
					for i := range skips {
						name := fmt.Sprintf("own-skip-%d", i)
						org.Providers["operator"].Models = append(org.Providers["operator"].Models, name)
						chain = append(chain, name)
					}
					chain = append(chain, mixedHopServingModel)
					org.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{mixedHopManaged: chain}}
					g := newManagedTestGateway(t, org)
					pastRecordCatalog(g, failures)
					g.grant(t, "org-m", 10_000_000)
					g.managed.failFor.Store(mixedHopManaged, http.StatusServiceUnavailable)
					for _, m := range failures {
						g.managed.failFor.Store(m, http.StatusServiceUnavailable)
					}
					pastRecordAnswers(g, nil)

					w := g.chat(t, mixedHopChat(mixedHopManaged, "")(stream), nil)
					if w.Code != http.StatusOK || g.managed.calls() != failed+1 || g.operator.calls() != 0 {
						t.Fatalf("status %d body %s, managed calls %d, own calls %d; want %d failures then the managed model served",
							w.Code, w.Body, g.managed.calls(), g.operator.calls(), failed)
					}
					if spent, held := settledAccount(t, g); spent != pastRecordBilled || held != 0 {
						t.Fatalf("spent %d held %d; want %d, the serving model's card, nothing held", spent, held, pastRecordBilled)
					}
					sent := receiptAttempts(t, g, pastRecordBilled)
					last := sent[len(sent)-1]
					if last.Outcome != "success" || last.Model != mixedHopServingModel || last.CredentialType != "managed" ||
						last.BilledMicros != pastRecordBilled || last.UpstreamMicros != 17500 {
						t.Fatalf("settlement's last attempt %+v; want the serving %s billed %d", last, mixedHopServingModel, pastRecordBilled)
					}
					for _, a := range sent[:len(sent)-1] {
						if a.Outcome == "success" || a.BilledMicros != 0 || a.UpstreamMicros != 0 || a.CredentialType != "managed" {
							t.Fatalf("settlement attempt %+v; want only uncharged managed failures before the serving one", a)
						}
					}
				})
			}
		}
	}
}

// Failures that carry a charge are never dropped from the record. Streams
// only: a stream that reports usage and ends before any output is the
// failure an upstream charges for.
//
//   - Half charged: 20 fallbacks fail, the odd ones charged, then the
//     serving model: every charged failure is billed (105 each), and so is
//     the answer, on its own card; the oldest uncharged failures gave up
//     their slots.
//   - All charged: 20 charged failures, then the serving model. Once the
//     record holds 16 charged failures it has no slot for another attempt,
//     so none is held or sent: the request answers as its primary alone
//     does and bills exactly the 16 it made.
func TestMixedHopGuardChargedFailuresPastTheRecord(t *testing.T) {
	setup := func(t *testing.T, charged func(i int) bool) (*managedTestGateway, *mixedHopTrace, []string) {
		org := mixedHopOrg()
		var chain []string
		answers := map[string]bool{}
		for i := 1; i <= 20; i++ {
			chain = append(chain, fmt.Sprintf("qwen/fails-%d", i))
		}
		org.Routing = &tenant.RoutingConfig{FallbackEnabled: true,
			ModelFallbacks: map[string][]string{mixedHopManaged: append(append([]string(nil), chain...), mixedHopServingModel)}}
		g := newManagedTestGateway(t, org)
		pastRecordCatalog(g, chain)
		g.grant(t, "org-m", 10_000_000)
		g.managed.failFor.Store(mixedHopManaged, http.StatusServiceUnavailable)
		for i, m := range chain {
			if charged(i + 1) {
				answers[m] = true
			} else {
				g.managed.failFor.Store(m, http.StatusServiceUnavailable)
			}
		}
		pastRecordAnswers(g, answers)
		return g, traceMixedHop(g), chain
	}

	t.Run("half charged", func(t *testing.T) {
		g, tr, chain := setup(t, func(i int) bool { return i%2 == 1 })
		w := g.chat(t, mixedHopChat(mixedHopManaged, "")(true), nil)
		const want = pastRecordBilled + 10*pastRecordChargeBilled
		if w.Code != http.StatusOK || g.managed.calls() != 22 || callsFor(g.managed, mixedHopServingModel) != 1 {
			t.Fatalf("status %d body %s, managed calls %d; want 20 fallbacks then the serving model", w.Code, w.Body, g.managed.calls())
		}
		if spent, held := settledAccount(t, g); spent != want || held != 0 {
			t.Fatalf("spent %d held %d; want %d: ten charged failures and the answer", spent, held, want)
		}
		sent := receiptAttempts(t, g, want)
		if len(sent) != models.MaxAttempts {
			t.Fatalf("settlement lists %d attempts; want a full record of %d", len(sent), models.MaxAttempts)
		}
		chargedSeen := map[string]bool{}
		for _, a := range sent[:len(sent)-1] {
			switch {
			case a.BilledMicros == pastRecordChargeBilled && a.Outcome != "success":
				chargedSeen[a.Model] = true
			case a.BilledMicros != 0 || a.Outcome == "success":
				t.Fatalf("settlement attempt %+v; want a charged failure billed %d or an uncharged one", a, pastRecordChargeBilled)
			}
		}
		for i := 1; i <= 20; i += 2 {
			if !chargedSeen[chain[i-1]] {
				t.Fatalf("charged failure %s is not in the settlement %+v", chain[i-1], sent)
			}
		}
		if last := sent[len(sent)-1]; last.Model != mixedHopServingModel || last.BilledMicros != pastRecordBilled {
			t.Fatalf("settlement's last attempt %+v; want the serving model billed %d", last, pastRecordBilled)
		}
		if _, _, reserves, _ := tr.snapshot(); reserves != 21 {
			t.Fatalf("%d hop holds; want one for each of the 21 hops", reserves)
		}
	})

	t.Run("all charged", func(t *testing.T) {
		alone := newManagedTestGateway(t, mixedHopOrg())
		alone.grant(t, "org-m", 10_000_000)
		alone.managed.failFor.Store(mixedHopManaged, http.StatusServiceUnavailable)
		wantStatus := alone.chat(t, mixedHopChat(mixedHopManaged, "")(true), nil).Code

		g, tr, chain := setup(t, func(int) bool { return true })
		w := g.chat(t, mixedHopChat(mixedHopManaged, "")(true), nil)
		const want = models.MaxAttempts * pastRecordChargeBilled
		if w.Code != wantStatus || w.Code == http.StatusOK || g.managed.calls() != 1+models.MaxAttempts ||
			callsFor(g.managed, mixedHopServingModel) != 0 || callsFor(g.managed, chain[models.MaxAttempts]) != 0 {
			t.Fatalf("status %d body %s, managed calls %d (serving %d); want the primary and %d charged fallbacks, then %d as the primary alone answers",
				w.Code, w.Body, g.managed.calls(), callsFor(g.managed, mixedHopServingModel), models.MaxAttempts, wantStatus)
		}
		if spent, held := settledAccount(t, g); spent != want || held != 0 {
			t.Fatalf("spent %d held %d; want %d, the 16 charged failures sent", spent, held, want)
		}
		sent := receiptAttempts(t, g, want)
		if len(sent) != models.MaxAttempts {
			t.Fatalf("settlement lists %d attempts; want %d", len(sent), models.MaxAttempts)
		}
		for i, a := range sent {
			if a.Model != chain[i] || a.BilledMicros != pastRecordChargeBilled || a.Outcome == "success" {
				t.Fatalf("settlement attempt %d is %+v; want charged failure %s billed %d", i+1, a, chain[i], pastRecordChargeBilled)
			}
		}
		if _, _, reserves, _ := tr.snapshot(); reserves != models.MaxAttempts {
			t.Fatalf("%d hop holds; want %d, none for a hop the record had no slot for", reserves, models.MaxAttempts)
		}
	})
}

// selfHostedKey adds an internal key, the operator's own, to g and returns
// it. Its requests may use the gateway's global providers.
func selfHostedKey(g *managedTestGateway) string {
	key, raw := g.srv.handlers.keyStore.Create("ops", "ops", nil, nil, nil)
	key.KeyType = "internal"
	return raw
}

// chatAs sends body to /v1/chat/completions with key.
func chatAs(g *managedTestGateway, key, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// A customer key's request is never mirrored: the copy would go out on the
// operator's credentials for a global provider, unbilled. Whatever the
// request's billing mode and whatever managed.mixed_billing_chains says, the
// mirror never looks the target up, so nothing is sent on the operator's
// key.
func TestMixedHopGuardMirrorCustomerKeyRefused(t *testing.T) {
	for _, c := range []struct {
		name    string
		model   string
		flagOn  bool
		managed int // managed calls the request itself makes
	}{
		{"byok request", mixedHopOwn, false, 0},
		{"byok request, mixed chains on", mixedHopOwn, true, 0},
		{"managed request, mixed chains on", mixedHopManaged, true, 1},
	} {
		t.Run(c.name, func(t *testing.T) {
			g := newManagedTestGateway(t, mixedHopOrg())
			if c.flagOn {
				allowMixedBillingChains(g)
			}
			g.grant(t, "org-m", 1_000_000)
			asked := mixedHopMirror(g, "operator", "gpt-4o")
			ownBefore := 0
			if c.model == mixedHopOwn {
				ownBefore = 1 // the request itself, on the org's own key
			}
			w := g.chat(t, mixedHopChat(c.model, "")(false), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d body %s; want the request served", w.Code, w.Body)
			}
			if got := asked(); len(got) != 0 {
				t.Fatalf("the mirror looked up %v to copy a customer key's request; want no copy", got)
			}
			if n := g.operator.calls(); n != ownBefore || (n > 0 && g.operator.lastAuth() == "Bearer fagi-operator-key") {
				t.Fatalf("operator upstream calls %d, last auth %q; want only the request's own call, never the operator's key",
					n, g.operator.lastAuth())
			}
			if n := g.managed.calls(); n != c.managed {
				t.Fatalf("managed calls %d; want %d (the request's own)", n, c.managed)
			}
		})
	}
}
