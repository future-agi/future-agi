package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
)

// A routing variant (":floor", ":nitro") is another price of the model it
// decorates. On one Future AGI key it is served only when the catalogue
// lists that exact id, named by that id or behind the managed provider's
// "openrouter/" prefix, and its own price card then prices the hold and the
// settlement however the stream ends. Any other variant is refused as not
// available before any upstream call. A call on the org's own key under the
// Future AGI provider's id is the org's own and is unaffected.

const (
	// decoratedModel is a listed model; listedVariant is a variant of it the
	// catalogue lists at variantPriceFactor times its price, unlistedVariant
	// one it does not list.
	decoratedModel     = "anthropic/claude-sonnet-5.5"
	listedVariant      = "anthropic/claude-sonnet-5.5:nitro"
	unlistedVariant    = "anthropic/claude-sonnet-5.5:floor"
	variantPriceFactor = 10
)

// withListedVariant lists listedVariant in the test gateway's managed
// catalogue, next to testCatalogModels, and rebuilds the model database
// from it as a catalogue refresh does.
func withListedVariant(t *testing.T, g *managedTestGateway) {
	t.Helper()
	all := make(map[string]*catalog.Model, len(testCatalogModels)+1)
	for id, m := range testCatalogModels {
		all[id] = m
	}
	v := *testCatalogModels[decoratedModel]
	v.ID = listedVariant
	v.InputPerToken *= variantPriceFactor
	v.OutputPerToken *= variantPriceFactor
	all[listedVariant] = &v
	g.srv.handlers.catalog.Put(&catalog.Snapshot{ProviderID: "openrouter", Source: "bundled",
		CatalogSource: catalog.SourceOpenRouter, FetchedAt: time.Now(), Models: all})
	g.srv.RefreshModelDB()
	if !g.srv.handlers.catalogHas("openrouter", listedVariant) {
		t.Fatalf("setup: the managed catalogue does not sell %s", listedVariant)
	}
}

// textThenEnd streams managedStreamText and ends the stream without usage.
func textThenEnd(w http.ResponseWriter, _ *http.Request, model string) {
	w.Header().Set("Content-Type", "text/event-stream")
	writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"`+managedStreamText+`"}}]`)
	writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
	fmt.Fprint(w, "data: [DONE]\n\n")
}

// textThenUsage streams managedStreamText and ends the stream with the
// upstream's usage chunk, which reports upstreamCost.
func textThenUsage(w http.ResponseWriter, _ *http.Request, model string) {
	w.Header().Set("Content-Type", "text/event-stream")
	writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"role":"assistant","content":"`+managedStreamText+`"}}]`)
	writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
	writeManagedChunk(w, model, fmt.Sprintf(`"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":500,"total_tokens":1500,"cost":%g}`, upstreamCost))
	fmt.Fprint(w, "data: [DONE]\n\n")
}

// answeringAs is s with every chunk naming model as, the id an upstream may
// answer a variant under; "" leaves s naming the model it was sent.
func answeringAs(as string, s upstreamStream) upstreamStream {
	if as == "" {
		return s
	}
	return func(w http.ResponseWriter, r *http.Request, _ string) { s(w, r, as) }
}

// cutStreamEnds are the ways a Future AGI stream ends before the upstream
// reports usage.
var cutStreamEnds = []struct {
	name     string
	opts     managedGatewayOptions
	upstream upstreamStream
	leave    bool
}{
	{name: "the upstream sends no usage", upstream: textThenEnd},
	{name: "the client leaves after the first text", upstream: textThenWait, leave: true},
	{name: "the upstream fails mid-stream", upstream: textThenFail},
	{name: "a guardrail withholds the answer", opts: managedGatewayOptions{blockAnswers: true}, upstream: textThenEnd},
}

// managedSurfaces are the two APIs a Future AGI key is served on.
var managedSurfaces = []string{"/v1/chat/completions", "/v1/messages"}

// settledRun is what one request left on the org's account.
type settledRun struct {
	status        int
	billing       string
	message       string // error.message of a refusal
	upstreamSent  string
	calls         int
	spent, held   int64
	balance       string
	recs          []map[string]string
	auths         []string
	providerPrefs []json.RawMessage
}

// runManaged sends model on surface through a real HTTP server on a fresh
// gateway with Future AGI models on, listedVariant listed and $1 of credit,
// the upstream answering a stream with upstream (the default chunks when
// nil), and returns what the request left once it is settled.
func runManaged(t *testing.T, surface, model string, stream bool, opts managedGatewayOptions, upstream upstreamStream, leave bool) settledRun {
	t.Helper()
	return runManagedWith(t, surface, model, stream, opts, upstream, leave, nil)
}

// runManagedWith is runManaged with headers added to the request.
func runManagedWith(t *testing.T, surface, model string, stream bool, opts managedGatewayOptions, upstream upstreamStream, leave bool, headers map[string]string) settledRun {
	t.Helper()
	return runManagedOn(t, withListedVariant, surface, model, stream, opts, upstream, leave, headers)
}

// runManagedOn is runManagedWith on the managed catalogue lists sets up in
// place of withListedVariant's.
func runManagedOn(t *testing.T, lists func(*testing.T, *managedTestGateway), surface, model string, stream bool, opts managedGatewayOptions, upstream upstreamStream, leave bool, headers map[string]string) settledRun {
	t.Helper()
	g := newManagedTestGatewayWith(t, managedOptIn(), opts)
	lists(t, g)
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	if upstream != nil {
		g.managed.streamWith.Store(&upstream)
	}
	status, body, respHeaders := sendManaged(t, g, surface, model, stream, leave, headers)
	spent, held := settledAccount(t, g)
	r := settledRun{status: status, billing: respHeaders.Get("x-agentcc-billing-mode"), upstreamSent: g.managed.lastModel(),
		calls: g.managed.calls(), spent: spent, held: held,
		balance: fmt.Sprintf("%.6f", g.ledger.BalanceUSD("org-m")), recs: g.outbox("org-m")}
	g.managed.mu.Lock()
	r.auths = append([]string(nil), g.managed.auths...)
	r.providerPrefs = append([]json.RawMessage(nil), g.managed.providerPrefs...)
	g.managed.mu.Unlock()
	if status == http.StatusOK {
		return r
	}
	var e struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	_ = json.Unmarshal([]byte(body), &e)
	r.message = e.Error.Message
	return r
}

// refusedAsNotAvailable checks that r is the 403 refusing named (the model,
// or the provider a request is locked to) as not available, sent before any
// upstream call and leaving the org's credit untouched.
func refusedAsNotAvailable(t *testing.T, what, named string, r settledRun) {
	t.Helper()
	if r.status != http.StatusForbidden || !strings.Contains(r.message, `"`+named+`" is not available`) {
		t.Errorf("%s: status %d, message %q; want 403 naming %s as not available", what, r.status, r.message, named)
	}
	if r.calls != 0 {
		t.Errorf("%s: %d upstream calls (last %q), want none", what, r.calls, r.upstreamSent)
	}
	if r.balance != "1.000000" || r.spent != 0 || r.held != 0 || len(r.recs) != 0 {
		t.Errorf("%s: balance %s, spent %d, held %d, records %v; want the credit untouched", what, r.balance, r.spent, r.held, r.recs)
	}
}

// refusedAsNotFound checks that r is the 404 model_not_found for model, an
// id nothing lists (DX-ERRORS-1: no longer the 403 not available), sent
// before any upstream call and leaving the org's credit untouched.
func refusedAsNotFound(t *testing.T, what, model string, r settledRun) {
	t.Helper()
	if r.status != http.StatusNotFound || !strings.Contains(r.message, `model "`+model+`" was not found`) {
		t.Errorf("%s: status %d, message %q; want 404 naming %s as not found", what, r.status, r.message, model)
	}
	if r.calls != 0 {
		t.Errorf("%s: %d upstream calls (last %q), want none", what, r.calls, r.upstreamSent)
	}
	if r.balance != "1.000000" || r.spent != 0 || r.held != 0 || len(r.recs) != 0 {
		t.Errorf("%s: balance %s, spent %d, held %d, records %v; want the credit untouched", what, r.balance, r.spent, r.held, r.recs)
	}
}

// refusedUnlisted is refusedAsNotFound for the id itself and
// refusedAsNotAvailable behind the managed provider's "openrouter/" prefix,
// which names a provider the gateway has.
func refusedUnlisted(t *testing.T, what, model string, r settledRun) {
	t.Helper()
	if strings.HasPrefix(model, "openrouter/") {
		refusedAsNotAvailable(t, what, model, r)
		return
	}
	refusedAsNotFound(t, what, model, r)
}

// heldMicros is the hold the request's one settlement record released.
func heldMicros(t *testing.T, r settledRun) int64 {
	t.Helper()
	if len(r.recs) != 1 {
		t.Fatalf("settlement records %v, want one", r.recs)
	}
	n, err := strconv.ParseInt(r.recs[0]["hold_micros"], 10, 64)
	if err != nil {
		t.Fatalf("hold_micros %q: %v", r.recs[0]["hold_micros"], err)
	}
	return n
}

// managedLock locks a chat request to the Future AGI provider.
var managedLock = map[string]string{"x-agentcc-provider-lock": "openrouter"}

// TestManagedVariantIsServedOnlyAsItsOwnListing: on chat and on
// /v1/messages, streamed and not, a variant the catalogue does not list is
// refused as not available with no upstream call and the credit untouched,
// whether named by its id or on a provider-locked request (404
// model_not_found), or behind the "openrouter/" prefix (403 not available);
// a variant the catalogue
// lists is served as that exact id each way.
func TestManagedVariantIsServedOnlyAsItsOwnListing(t *testing.T) {
	for _, stream := range []bool{true, false} {
		what := fmt.Sprintf("chat locked to openrouter, stream=%t, %s", stream, unlistedVariant)
		refusedAsNotFound(t, what, unlistedVariant,
			runManagedWith(t, "/v1/chat/completions", unlistedVariant, stream, managedGatewayOptions{}, nil, false, managedLock))
		r := runManagedWith(t, "/v1/chat/completions", listedVariant, stream, managedGatewayOptions{}, nil, false, managedLock)
		if r.status != http.StatusOK || r.billing != "managed" || r.calls != 1 || r.upstreamSent != listedVariant {
			t.Errorf("chat locked to openrouter, stream=%t, %s: status %d, billing %q, %d upstream calls of %q; want 200 managed, one call of %s",
				stream, listedVariant, r.status, r.billing, r.calls, r.upstreamSent, listedVariant)
		}
	}
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{true, false} {
			for _, model := range []string{unlistedVariant, "openrouter/" + unlistedVariant} {
				what := fmt.Sprintf("%s stream=%t %s", surface, stream, model)
				refusedUnlisted(t, what, model, runManaged(t, surface, model, stream, managedGatewayOptions{}, nil, false))
			}
			for _, model := range []string{listedVariant, "openrouter/" + listedVariant} {
				r := runManaged(t, surface, model, stream, managedGatewayOptions{}, nil, false)
				if r.status != http.StatusOK || r.billing != "managed" || r.calls != 1 || r.upstreamSent != listedVariant {
					t.Errorf("%s stream=%t %s: status %d, billing %q, %d upstream calls of %q; want 200 managed, one call of %s",
						surface, stream, model, r.status, r.billing, r.calls, r.upstreamSent, listedVariant)
				}
			}
		}
	}
}

// TestManagedListedVariantIsBilledOnItsOwnCard: a listed variant, by its id
// or behind the "openrouter/" prefix, on chat and on /v1/messages. A stream
// that ends before the upstream reports usage (no usage, the client leaves,
// the upstream fails, a guardrail withholds the answer) is billed once on an
// estimate, at the variant's own price: its hold and its spend are
// variantPriceFactor times the model it decorates', even when the upstream
// answers naming that model, and nothing stays held. A stream that reports
// usage is billed once from it, at the upstream's charge plus 5%.
func TestManagedListedVariantIsBilledOnItsOwnCard(t *testing.T) {
	names := []string{listedVariant, "openrouter/" + listedVariant}
	answers := []struct{ name, as string }{
		{"the upstream echoes the variant", ""},
		{"the upstream names the model it decorates", decoratedModel},
	}
	for _, end := range cutStreamEnds {
		t.Run(end.name, func(t *testing.T) {
			for _, surface := range managedSurfaces {
				base := runManaged(t, surface, decoratedModel, true, end.opts, end.upstream, end.leave)
				if base.status != http.StatusOK || base.spent <= 0 {
					t.Fatalf("%s %s: status %d, spent %d; want the model it decorates billed", surface, decoratedModel, base.status, base.spent)
				}
				baseHold := heldMicros(t, base)
				for _, model := range names {
					for _, ans := range answers {
						what := fmt.Sprintf("%s %s, %s", surface, model, ans.name)
						r := runManaged(t, surface, model, true, end.opts, answeringAs(ans.as, end.upstream), end.leave)
						if r.status != http.StatusOK || r.billing != "managed" || r.calls != 1 || r.upstreamSent != listedVariant {
							t.Errorf("%s: status %d, billing %q, %d upstream calls of %q; want 200 managed, one call of %s",
								what, r.status, r.billing, r.calls, r.upstreamSent, listedVariant)
							continue
						}
						if r.held != 0 {
							t.Errorf("%s: %d micros still held after the stream ended", what, r.held)
						}
						if r.spent <= 0 {
							t.Errorf("%s: a Future AGI stream the upstream charged for was billed nothing", what)
						}
						if len(r.recs) != 1 || r.recs[0]["kind"] != "spend" || r.recs[0]["usage_estimated"] != "1" {
							t.Errorf("%s: settlement records %v, want one spend on an estimate", what, r.recs)
							continue
						}
						if hold := heldMicros(t, r); hold < (variantPriceFactor-1)*baseHold {
							t.Errorf("%s: held %d micros where the model it decorates holds %d; want the variant's own card (x%d)",
								what, hold, baseHold, variantPriceFactor)
						}
						if r.spent < (variantPriceFactor-1)*base.spent {
							t.Errorf("%s: spent %d micros where the model it decorates spends %d; want the variant's own card (x%d)",
								what, r.spent, base.spent, variantPriceFactor)
						}
					}
				}
			}
		})
	}

	billed := fmt.Sprintf("%.6f", 1-upstreamCost*1.05)
	for _, surface := range managedSurfaces {
		for _, model := range names {
			for _, ans := range answers {
				what := fmt.Sprintf("usage reported: %s %s, %s", surface, model, ans.name)
				r := runManaged(t, surface, model, true, managedGatewayOptions{}, answeringAs(ans.as, textThenUsage), false)
				if r.status != http.StatusOK || r.calls != 1 || r.upstreamSent != listedVariant || r.held != 0 {
					t.Errorf("%s: status %d, %d upstream calls of %q, held %d; want 200, one call of %s, nothing held",
						what, r.status, r.calls, r.upstreamSent, r.held, listedVariant)
				}
				if r.balance != billed {
					t.Errorf("%s: balance %s, want %s: billed once at the upstream's charge plus 5%%", what, r.balance, billed)
				}
				if len(r.recs) != 1 || r.recs[0]["kind"] != "spend" || r.recs[0]["usage_estimated"] == "1" {
					t.Errorf("%s: settlement records %v, want one spend from the reported usage", what, r.recs)
				}
			}
		}
	}
}

// TestManagedVariantOnTheOrgsOwnKeyIsByok: with the org's own key under the
// Future AGI provider's id, a variant the catalogue does not list, behind the
// "openrouter/" prefix, goes out on the org's key as the org's own call, on
// chat and on /v1/messages: billed byok, the credit untouched, no spend.
func TestManagedVariantOnTheOrgsOwnKeyIsByok(t *testing.T) {
	servedOnTheOrgsOwnKey(t, "openrouter/"+unlistedVariant, unlistedVariant)
}

// servedOnTheOrgsOwnKey checks that model, sent upstream as sent, is served
// on the org's own key under the Future AGI provider's id, on chat and on
// /v1/messages, streamed and not: 200 byok, one call on the org's key, the
// credit untouched and no spend record.
func servedOnTheOrgsOwnKey(t *testing.T, model, sent string) {
	t.Helper()
	servedOnTheOrgsOwnKeyOn(t, nil, model, sent)
}

// servedOnTheOrgsOwnKeyOn is servedOnTheOrgsOwnKey on the managed catalogue
// lists sets up (nil: the test catalogue as it is).
func servedOnTheOrgsOwnKeyOn(t *testing.T, lists func(*testing.T, *managedTestGateway), model, sent string) {
	t.Helper()
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{true, false} {
			g := newManagedTestGateway(t, orgKeyUnderFutureAGIID())
			if lists != nil {
				lists(t, g)
			}
			g.ledger.SetMicros("org-m", auth.USDToMicros(1))
			status, body, headers := sendManaged(t, g, surface, model, stream, false, nil)
			what := fmt.Sprintf("%s stream=%t %s", surface, stream, model)
			if status != http.StatusOK || headers.Get("x-agentcc-billing-mode") != "byok" {
				t.Errorf("%s: status %d, billing %q, body %s; want 200 byok", what, status, headers.Get("x-agentcc-billing-mode"), body)
			}
			g.managed.mu.Lock()
			auths := append([]string(nil), g.managed.auths...)
			g.managed.mu.Unlock()
			if len(auths) != 1 || auths[0] != "Bearer org-or-key" || g.managed.lastModel() != sent {
				t.Errorf("%s: upstream auths %v, model %q; want one call of %s on the org's own key", what, auths, g.managed.lastModel(), sent)
			}
			spent, held := settledAccount(t, g)
			if bal := g.ledger.BalanceUSD("org-m"); fmt.Sprintf("%.6f", bal) != "1.000000" || spent != 0 || held != 0 {
				t.Errorf("%s: balance %.6f, spent %d, held %d; want the credit untouched", what, bal, spent, held)
			}
			for _, rec := range g.outbox("org-m") {
				if rec["kind"] == "spend" {
					t.Errorf("%s: a spend record %v for a call on the org's own key", what, rec)
				}
			}
		}
	}
}
