package server

// MIXED-HOP-GUARD-1 (648 addendum 15): no attempt hop may move a request
// between Future AGI credits (managed) and the org's own key (byok) unless the
// node turns the mixed billing chain profile on. Each row below is one hop
// source, both directions where the source allows it, streamed and not. A
// refused hop is never sent, held or billed: the request answers with its
// own same-mode error, and no attempt in the hop's billing mode is recorded
// (the hop goes to the request's skipped-hop record, apart from its
// attempts: mixed_hop_guard_r2_control_test.go).
//
// This file uses only helpers and APIs that exist at 2a117d070c, so it runs
// red there (the cross-mode hop is sent and succeeds) and green once the
// guard is in. The flag-ON controls that restore today's mixed behaviour use
// the new node flag and live in mixed_hop_guard_control_test.go.

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const (
	mixedHopManaged = "anthropic/claude-sonnet-5.5" // on the managed openrouter (credits)
	mixedHopCheap   = "qwen/qwen3.7-flash"          // the cheapest managed model
	mixedHopOwn     = "org-model"                   // on the org's own operator key (byok)
	mixedHopOwn2    = "org-model-2"
)

// mixedHopOrg is an org opted in to Future AGI models that also holds its own
// key on provider operator for org-model and org-model-2. Its routing config
// is unset, as an org that never touched the console's switches is.
func mixedHopOrg() *tenant.OrgConfig {
	cfg := managedOptIn()
	cfg.Providers = map[string]*tenant.ProviderConfig{
		"operator": {APIKey: "org-operator-key", APIFormat: "openai", Models: []string{mixedHopOwn, mixedHopOwn2}, Enabled: true},
	}
	return cfg
}

// mixedHopTrace is what the request path showed the hook observer: the
// attempts and billing mode as the request ended, and the hop admissions.
type mixedHopTrace struct {
	mu          sync.Mutex
	attempts    []models.Attempt
	billingMode string
	reserves    int
	hopsJudged  int
}

func traceMixedHop(g *managedTestGateway) *mixedHopTrace {
	tr := &mixedHopTrace{}
	g.srv.handlers.hookObserver = func(hook, _ string, rc *models.RequestContext) {
		tr.mu.Lock()
		defer tr.mu.Unlock()
		switch hook {
		case hookReserveAdd:
			tr.reserves++
		case hookFallbackHopAllowed:
			tr.hopsJudged++
		}
		if rc == nil {
			return
		}
		tr.attempts = rc.AttemptsSnapshot()
		if hook == hookIdempotencyFinish {
			tr.billingMode = rc.Metadata["billing_mode"]
		}
	}
	return tr
}

func (tr *mixedHopTrace) snapshot() ([]models.Attempt, string, int, int) {
	tr.mu.Lock()
	defer tr.mu.Unlock()
	return append([]models.Attempt(nil), tr.attempts...), tr.billingMode, tr.reserves, tr.hopsJudged
}

// mixedHopCase is one cross-mode hop: the request, how its primary fails,
// and the hop that must not run.
type mixedHopCase struct {
	name string
	org  func() *tenant.OrgConfig
	// setup makes the primary fail and installs node-level chains.
	setup func(g *managedTestGateway)
	// body is the request for stream; messages sends it to /v1/messages.
	body     func(stream bool) string
	messages bool
	// from is the primary's billing mode; to is the hop's, on hopProvider
	// (the provider id the attempt records) serving hopModel.
	from, to              models.CredentialType
	hopProvider, hopModel string
}

func mixedHopChat(model string, extra string) func(bool) string {
	return func(stream bool) string {
		return fmt.Sprintf(`{"model":%q,"stream":%v%s,"messages":[{"role":"user","content":"hi"}]}`, model, stream, extra)
	}
}

func mixedHopMessages(model string) func(bool) string {
	return func(stream bool) string {
		return fmt.Sprintf(`{"model":%q,"max_tokens":32,"stream":%v,"messages":[{"role":"user","content":"hello"}]}`, model, stream)
	}
}

func failManaged(model string) func(g *managedTestGateway) {
	return func(g *managedTestGateway) { g.managed.failFor.Store(model, http.StatusServiceUnavailable) }
}

func failOwn(model string) func(g *managedTestGateway) {
	return func(g *managedTestGateway) { g.operator.failFor.Store(model, http.StatusServiceUnavailable) }
}

func withRouting(r *tenant.RoutingConfig) func() *tenant.OrgConfig {
	return func() *tenant.OrgConfig {
		cfg := mixedHopOrg()
		cfg.Routing = r
		return cfg
	}
}

// mixedHopCases is every reachable cross-mode hop source at INT-3. Provider
// failover and retry cannot change a customer request's billing mode end to
// end (customerFailover runs only over the org's own providers, and the
// gateway's failover never serves a customer key), so they are pinned at the
// attempt start instead (TestMixedHopGuardAttemptStart).
func mixedHopCases() []mixedHopCase {
	return []mixedHopCase{
		{
			// The Opus recipe: auto picks the managed model, its runner-up is
			// the org's own priced model; the primary's 503 moves auto down
			// its runners-up whatever the org's switch says.
			name: "auto runner-up/managed to byok",
			org: func() *tenant.OrgConfig {
				cfg := withRouting(r7omAuto([]string{mixedHopCheap, mixedHopOwn}, "cost", 0))()
				cfg.CostTracking = r7omCustom(true, mixedHopOwn, 1, 2)
				return cfg
			},
			setup: failManaged(mixedHopCheap), body: mixedHopChat("auto", ""),
			from: models.CredentialManaged, to: models.CredentialBYOK, hopProvider: "operator", hopModel: mixedHopOwn,
		},
		{
			name: "auto runner-up/byok to managed",
			org: func() *tenant.OrgConfig {
				cfg := withRouting(r7omAuto([]string{mixedHopOwn, mixedHopManaged}, "cost", 0))()
				cfg.CostTracking = r7omCustom(true, mixedHopOwn, 0.001, 0.002)
				return cfg
			},
			setup: failOwn(mixedHopOwn), body: mixedHopChat("auto", ""),
			from: models.CredentialBYOK, to: models.CredentialManaged, hopProvider: "openrouter", hopModel: mixedHopManaged,
		},
		{
			// The default tenant: routing unset, so requestFallbacksEnabled
			// keeps the caller's models[] on.
			name: "default tenant models[]/managed to byok",
			org:  mixedHopOrg, setup: failManaged(mixedHopManaged),
			body: mixedHopChat(mixedHopManaged, `,"models":["`+mixedHopOwn+`"]`),
			from: models.CredentialManaged, to: models.CredentialBYOK, hopProvider: "operator", hopModel: mixedHopOwn,
		},
		{
			name: "default tenant models[]/byok to managed",
			org:  mixedHopOrg, setup: failOwn(mixedHopOwn),
			body: mixedHopChat(mixedHopOwn, `,"models":["`+mixedHopManaged+`"]`),
			from: models.CredentialBYOK, to: models.CredentialManaged, hopProvider: "openrouter", hopModel: mixedHopManaged,
		},
		{
			name: "org model fallbacks/managed to byok",
			org: withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
				ModelFallbacks: map[string][]string{mixedHopManaged: {mixedHopOwn}}}),
			setup: failManaged(mixedHopManaged), body: mixedHopChat(mixedHopManaged, ""),
			from: models.CredentialManaged, to: models.CredentialBYOK, hopProvider: "operator", hopModel: mixedHopOwn,
		},
		{
			name: "org model fallbacks/byok to managed",
			org: withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
				ModelFallbacks: map[string][]string{mixedHopOwn: {mixedHopManaged}}}),
			setup: failOwn(mixedHopOwn), body: mixedHopChat(mixedHopOwn, ""),
			from: models.CredentialBYOK, to: models.CredentialManaged, hopProvider: "openrouter", hopModel: mixedHopManaged,
		},
		{
			// The gateway's own chain (config.yaml routing.model_fallbacks),
			// tried on the gateway's failover codes, for an org with no routing.
			name: "global model fallbacks/managed to byok",
			org:  mixedHopOrg,
			setup: func(g *managedTestGateway) {
				failManaged(mixedHopManaged)(g)
				installGlobalChain(g, map[string][]string{mixedHopManaged: {mixedHopOwn}})
			},
			body: mixedHopChat(mixedHopManaged, ""),
			from: models.CredentialManaged, to: models.CredentialBYOK, hopProvider: "operator", hopModel: mixedHopOwn,
		},
		{
			name: "global model fallbacks/byok to managed",
			org:  mixedHopOrg,
			setup: func(g *managedTestGateway) {
				failOwn(mixedHopOwn)(g)
				installGlobalChain(g, map[string][]string{mixedHopOwn: {mixedHopManaged}})
			},
			body: mixedHopChat(mixedHopOwn, ""),
			from: models.CredentialBYOK, to: models.CredentialManaged, hopProvider: "openrouter", hopModel: mixedHopManaged,
		},
		{
			// /v1/messages runs a BYOK primary's chain canonically: the org's
			// chain moves it to credits there too.
			name: "messages org chain/byok to managed",
			org: withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
				ModelFallbacks: map[string][]string{mixedHopOwn: {mixedHopManaged}}}),
			setup: failOwn(mixedHopOwn), body: mixedHopMessages(mixedHopOwn), messages: true,
			from: models.CredentialBYOK, to: models.CredentialManaged, hopProvider: "openrouter", hopModel: mixedHopManaged,
		},
		{
			name: "messages org chain/managed to byok",
			org: withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
				ModelFallbacks: map[string][]string{mixedHopManaged: {mixedHopOwn}}}),
			setup: failManaged(mixedHopManaged), body: mixedHopMessages(mixedHopManaged), messages: true,
			from: models.CredentialManaged, to: models.CredentialBYOK, hopProvider: "operator", hopModel: mixedHopOwn,
		},
	}
}

// installGlobalChain installs a gateway-wide model fallback chain and the
// gateway failover whose codes move a request down it, as a config.yaml
// with routing.model_fallbacks and routing.failover does.
func installGlobalChain(g *managedTestGateway, chains map[string][]string) {
	h := g.srv.handlers
	h.modelFallbacks.Store(routing.NewModelFallbacks(chains))
	h.failover.Store(routing.NewFailover(config.FailoverConfig{Enabled: true, MaxAttempts: 1}, nil, nil, nil))
}

// runMixedHop builds the case's gateway, funds the org and sends the request.
func runMixedHop(t *testing.T, c mixedHopCase, stream bool, prepare func(*managedTestGateway)) (*managedTestGateway, *httptest.ResponseRecorder, *mixedHopTrace) {
	t.Helper()
	g := newManagedTestGateway(t, c.org())
	g.grant(t, "org-m", 1_000_000)
	c.setup(g)
	if prepare != nil {
		prepare(g)
	}
	tr := traceMixedHop(g)
	if c.messages {
		req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(c.body(stream)))
		req.Header.Set("x-api-key", managedOrgKey)
		req.Header.Set("Content-Type", "application/json")
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, req)
		return g, w, tr
	}
	return g, g.chat(t, c.body(stream), nil), tr
}

// hopCalls is how many calls the hop's upstream took.
func (c mixedHopCase) hopCalls(g *managedTestGateway) int {
	if c.to == models.CredentialManaged {
		return g.managed.calls()
	}
	return g.operator.calls()
}

func TestMixedHopGuardRefusesCrossModeHops(t *testing.T) {
	for _, c := range mixedHopCases() {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", c.name, stream), func(t *testing.T) {
				g, w, tr := runMixedHop(t, c, stream, nil)
				attempts, billingMode, reserves, _ := tr.snapshot()

				if w.Code == http.StatusOK {
					t.Fatalf("status 200: the %s hop to %s served the request; want the primary's own error. body %s", c.to, c.hopModel, w.Body)
				}
				if n := c.hopCalls(g); n != 0 {
					t.Fatalf("the %s hop was dispatched %d times; want never", c.to, n)
				}
				if c.to == models.CredentialManaged && reserves != 0 {
					t.Fatalf("ReserveAdd ran %d times for a refused managed hop; want never held", reserves)
				}
				if spent, held := settledAccount(t, g); held != 0 || spent != 0 {
					t.Fatalf("account spent %d held %d; want nothing held or spent once the request ended", spent, held)
				}
				for _, rec := range g.outbox("org-m") {
					if rec["kind"] == "spend" {
						t.Fatalf("outbox %v: a refused hop's request was billed", g.outbox("org-m"))
					}
				}
				if c.messages {
					return // the Messages error path reports no attempts to the observer
				}
				if len(attempts) < 1 || attempts[0].CredentialType != c.from || attempts[0].Outcome == models.OutcomeSuccess {
					t.Fatalf("attempts %+v; want the failed %s primary first", attempts, c.from)
				}
				// A refused hop is not an attempt: none in its mode is
				// recorded, sent or skipped, so it can never take the slot of
				// one that was sent.
				for _, at := range attempts[1:] {
					if at.CredentialType == c.to {
						t.Fatalf("attempt %+v recorded in %s after a %s primary; want none", at, c.to, c.from)
					}
				}
				if billingMode != string(c.from) {
					t.Fatalf("the request ended billed as %q; want its primary's %q", billingMode, c.from)
				}
			})
		}
	}
}

// Same-mode chains keep today's behaviour: managed to managed and byok to
// byok hops run, are held and billed as before.
func TestMixedHopGuardKeepsSameModeChains(t *testing.T) {
	cases := []struct {
		name   string
		org    func() *tenant.OrgConfig
		setup  func(g *managedTestGateway)
		body   func(bool) string
		to     models.CredentialType
		served string
	}{
		{"models[]/managed to managed", mixedHopOrg, failManaged(mixedHopManaged),
			mixedHopChat(mixedHopManaged, `,"models":["openai/gpt-6-sol"]`), models.CredentialManaged, "openai/gpt-6-sol"},
		{"auto/managed to managed", withRouting(r7omAuto([]string{mixedHopCheap, mixedHopManaged}, "cost", 0)), failManaged(mixedHopCheap),
			mixedHopChat("auto", ""), models.CredentialManaged, mixedHopManaged},
		{"org chain/byok to byok", withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
			ModelFallbacks: map[string][]string{mixedHopOwn: {mixedHopOwn2}}}), failOwn(mixedHopOwn),
			mixedHopChat(mixedHopOwn, ""), models.CredentialBYOK, mixedHopOwn2},
		{"default tenant models[]/byok to byok", mixedHopOrg, failOwn(mixedHopOwn),
			mixedHopChat(mixedHopOwn, `,"models":["`+mixedHopOwn2+`"]`), models.CredentialBYOK, mixedHopOwn2},
	}
	for _, c := range cases {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", c.name, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, c.org())
				g.grant(t, "org-m", 1_000_000)
				c.setup(g)
				w := g.chat(t, c.body(stream), nil)
				if w.Code != http.StatusOK {
					t.Fatalf("status %d body %s; want the same-mode hop to serve", w.Code, w.Body)
				}
				spent, held := settledAccount(t, g)
				if c.to == models.CredentialManaged {
					if g.managed.lastModel() != c.served || spent <= 0 || held != 0 {
						t.Fatalf("managed hop: last managed model %q, spent %d held %d; want %s billed to credits", g.managed.lastModel(), spent, held, c.served)
					}
					return
				}
				if g.operator.lastModel() != c.served || g.operator.lastAuth() != "Bearer org-operator-key" || spent != 0 || held != 0 {
					t.Fatalf("byok hop: last own model %q auth %q, spent %d held %d; want %s on the org's key, nothing billed",
						g.operator.lastModel(), g.operator.lastAuth(), spent, held, c.served)
				}
				if got := w.Header().Get("x-agentcc-billing-mode"); !stream && got != "byok" {
					t.Fatalf("billing mode %q; want byok", got)
				}
			})
		}
	}
}

// A refused hop does not end the chain: it goes on to the next candidate in
// the request's own billing mode, which is held and billed as before.
func TestMixedHopGuardChainContinuesInMode(t *testing.T) {
	cases := []struct {
		name, primary string
		chain         []string
		fail          func(g *managedTestGateway)
		to            models.CredentialType
		served        string
	}{
		{"managed, byok skipped, managed serves", mixedHopManaged, []string{mixedHopOwn, "openai/gpt-6-sol"},
			failManaged(mixedHopManaged), models.CredentialManaged, "openai/gpt-6-sol"},
		{"byok, managed skipped, byok serves", mixedHopOwn, []string{mixedHopManaged, mixedHopOwn2},
			failOwn(mixedHopOwn), models.CredentialBYOK, mixedHopOwn2},
	}
	for _, c := range cases {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", c.name, stream), func(t *testing.T) {
				org := withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
					ModelFallbacks: map[string][]string{c.primary: c.chain}})()
				g := newManagedTestGateway(t, org)
				g.grant(t, "org-m", 1_000_000)
				c.fail(g)
				w := g.chat(t, mixedHopChat(c.primary, "")(stream), nil)
				if w.Code != http.StatusOK {
					t.Fatalf("status %d body %s; want the same-mode candidate after the skipped hop to serve", w.Code, w.Body)
				}
				spent, held := settledAccount(t, g)
				if c.to == models.CredentialManaged {
					if g.operator.calls() != 0 || g.managed.lastModel() != c.served || spent <= 0 || held != 0 {
						t.Fatalf("own calls %d, last managed %q, spent %d held %d; want the byok hop skipped and %s billed",
							g.operator.calls(), g.managed.lastModel(), spent, held, c.served)
					}
					return
				}
				if g.managed.calls() != 0 || g.operator.lastModel() != c.served || spent != 0 || held != 0 {
					t.Fatalf("managed calls %d, last own %q, spent %d held %d; want the managed hop skipped and %s on the org's key",
						g.managed.calls(), g.operator.lastModel(), spent, held, c.served)
				}
			})
		}
	}
}

// Messages fallbacks[] never builds a gateway chain at INT-3: every canonical
// /v1/messages route refuses it before any attempt or hold, and native
// pass-through forwards it to the vendor unchanged. Pinned here as a control
// (green before and after the guard) so a future change that starts honouring
// it meets the guard's rows above.
func TestMixedHopGuardMessagesFallbacksRefused(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, primary := range []string{mixedHopManaged, mixedHopOwn} {
			t.Run(fmt.Sprintf("%s/stream=%v", primary, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, mixedHopOrg())
				g.grant(t, "org-m", 1_000_000)
				g.managed.failFor.Store(mixedHopManaged, http.StatusServiceUnavailable)
				g.operator.failFor.Store(mixedHopOwn, http.StatusServiceUnavailable)
				other := mixedHopOwn
				if primary == mixedHopOwn {
					other = mixedHopManaged
				}
				w := messagesRoutingCall(g.srv, managedOrgKey, primary, stream, `,"fallbacks":[{"model":"`+other+`"}]`)
				if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "fallbacks") {
					t.Fatalf("status %d body %s; want 400 naming fallbacks", w.Code, w.Body)
				}
				if g.managed.calls() != 0 || g.operator.calls() != 0 {
					t.Fatalf("managed %d, own %d calls; want none before the refusal", g.managed.calls(), g.operator.calls())
				}
				if a := g.account(t, "org-m"); a.HeldMicros != 0 || a.SpentMicros != 0 {
					t.Fatalf("account %+v; want nothing held or spent", a)
				}
			})
		}
	}
}

// Provider failover and retry reach the attempt start with no fallback
// resolution of their own. Every attempt, whatever its kind, is held to the
// request's first billing mode there: a cross-mode attempt is refused before
// its retry hold, is not recorded as an attempt (it goes to the skipped-hop
// record: mixed_hop_guard_r2_control_test.go), and the request's billing
// mode is put back. The failover hop's admission refuses it too, before its
// eligibility verdict and hold.
func TestMixedHopGuardAttemptStart(t *testing.T) {
	type side struct {
		billing, provider, model string
		cred                     models.CredentialType
	}
	managed := side{billingManaged, "openrouter", mixedHopManaged, models.CredentialManaged}
	// The org's own key on the managed provider's id: the instance a retry
	// can switch to under the same provider id.
	ownOnManagedID := side{billingBYOK, "openrouter", mixedHopManaged, models.CredentialBYOK}
	own := side{billingBYOK, "operator", mixedHopOwn, models.CredentialBYOK}
	for _, kind := range []models.AttemptKind{models.AttemptRetry, models.AttemptFailover, models.AttemptModelFallback, models.AttemptAutoFallback} {
		for _, dir := range []struct {
			name     string
			from, to side
		}{
			{"managed to byok", managed, ownOnManagedID},
			{"byok to managed", ownOnManagedID, managed},
			{"managed to own provider", managed, own},
			{"own provider to managed", own, managed},
		} {
			t.Run(fmt.Sprintf("%s/%s", kind, dir.name), func(t *testing.T) {
				org := mixedHopOrg()
				org.Providers["openrouter"] = &tenant.ProviderConfig{APIKey: "org-or-key", APIFormat: "openai", Models: []string{mixedHopManaged}, Enabled: true}
				g := newManagedTestGateway(t, org)
				g.grant(t, "org-m", 1_000_000)
				h := g.srv.handlers
				tr := traceMixedHop(g)
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.RequestID = "req-mixed-hop-start"
				rc.Metadata["org_id"] = "org-m"
				rc.Request = &models.ChatCompletionRequest{Model: dir.from.model}
				rc.Metadata["billing_mode"] = dir.from.billing

				_, finish, refusal := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", dir.from.provider, dir.from.model, credentialType(rc))
				if refusal != "" || finish == nil {
					t.Fatalf("the primary was refused (%q)", refusal)
				}
				// The primary reported usage, so a managed retry needs a hold of
				// its own: the refusal must come before it.
				finish(nil, &models.Usage{PromptTokens: 10, CompletionTokens: 5, TotalTokens: 15}, &models.APIError{Status: http.StatusServiceUnavailable})

				rc.Metadata["billing_mode"] = dir.to.billing
				rc.Request.Model = dir.to.model
				_, finish, refusal = h.startAttempt(context.Background(), rc, kind, models.ReasonStatus5xx, dir.to.provider, dir.to.model, credentialType(rc))
				if refusal != models.ReasonPolicy || finish != nil {
					if finish != nil {
						finish(nil, nil, nil)
					}
					t.Fatalf("a %s attempt in %s after a %s primary was let through (refusal %q); want it skipped (policy)", kind, dir.to.cred, dir.from.cred, refusal)
				}
				if got := rc.AttemptsSnapshot(); len(got) != 1 {
					t.Fatalf("attempts %+v; want only the primary: the refused %s attempt is not an attempt", got, kind)
				}
				if rc.Metadata["billing_mode"] != dir.from.billing {
					t.Fatalf("billing mode %q after the refusal; want the request's %q back", rc.Metadata["billing_mode"], dir.from.billing)
				}
				if _, _, reserves, _ := tr.snapshot(); reserves != 0 {
					t.Fatalf("ReserveAdd ran %d times for a refused attempt", reserves)
				}

				// A failover hop's admission refuses it before the verdict and hold.
				rc.Metadata["billing_mode"] = dir.to.billing
				if reason := h.admitHop(rc, org, dir.to.provider, dir.to.model); reason != models.ReasonPolicy {
					t.Fatalf("admitHop let a %s hop after a %s primary through (%q); want policy", dir.to.cred, dir.from.cred, reason)
				}
				if _, _, reserves, judged := tr.snapshot(); reserves != 0 || judged != 0 {
					t.Fatalf("a refused failover hop was judged %d times and held %d times; want neither", judged, reserves)
				}
			})
		}
	}
}

// The same attempt start still lets every same-mode attempt through.
func TestMixedHopGuardAttemptStartSameMode(t *testing.T) {
	for _, billing := range []string{billingManaged, billingBYOK} {
		for _, kind := range []models.AttemptKind{models.AttemptRetry, models.AttemptFailover, models.AttemptModelFallback} {
			t.Run(billing+"/"+string(kind), func(t *testing.T) {
				g := newManagedTestGateway(t, mixedHopOrg())
				h := g.srv.handlers
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.RequestID = "req-mixed-hop-same"
				rc.Metadata["org_id"] = "org-m"
				rc.Request = &models.ChatCompletionRequest{Model: mixedHopManaged}
				rc.Metadata["billing_mode"] = billing
				for _, k := range []models.AttemptKind{models.AttemptPrimary, kind} {
					_, finish, refusal := h.startAttempt(context.Background(), rc, k, "", "openrouter", mixedHopManaged, credentialType(rc))
					if refusal != "" || finish == nil {
						t.Fatalf("a same-mode %s attempt was refused (%q)", k, refusal)
					}
					finish(nil, nil, &models.APIError{Status: http.StatusServiceUnavailable})
				}
			})
		}
	}
}
