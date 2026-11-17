package server

// MIXED-HOP-GUARD-1 round 2. Three gaps the round-1 guard left, each pinned
// here red-first: this file uses only helpers and APIs that exist at
// 84d4949789 (the round-1 head), where its reds fail.
//
//   - Money: a refused cross-mode candidate was recorded as an attempt, so a
//     chain that refused 15 of them filled the request's 16 attempt slots
//     and the attempt that served it was dropped; the cost plugin then priced
//     a skipped entry and the managed request was voided (billed 0 instead
//     of 7,350 micros).
//   - The traffic mirror sent a copy of a served request to any provider,
//     managed ones included, outside the attempt path, the guard and credit
//     admission.
//   - A chain exhausted after refusing a cross-mode candidate answered with
//     its primary's error, not the error of the last attempt it made.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// mixedHopServingModel is the managed model that serves after the refused
// candidates; the managed mock reports $0.007 for it, billed 7,350 micros
// at the test gateway's 5% markup.
const (
	mixedHopServingModel  = "openai/gpt-6-sol"
	mixedHopServingBilled = 7350
)

// The verifier's exact red: a managed primary fails, the org's chain then
// offers `skips` candidates on the org's own key and finally a managed
// model, which serves. Whatever the number of refused candidates, the
// request bills what the serving attempt cost, nothing is left held, and its
// one settlement lists exactly the two attempts that were sent: the primary
// and the one that served.
//
// providerLock also denies the org's provider by the org's provider lock, so
// its candidates are never sent before the guard either: that row bills
// 7,350 at 2a117d070c too. Without it only the guard refuses them.
func TestMixedHopGuardSkipsNeverDisplaceTheServingAttempt(t *testing.T) {
	for _, providerLock := range []bool{true, false} {
		for _, skips := range []int{0, 1, 14, 15, 16, 40} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("provider_lock=%v/skips=%d/stream=%v", providerLock, skips, stream), func(t *testing.T) {
					org := mixedHopOrg()
					var chain []string
					for i := range skips {
						name := fmt.Sprintf("own-candidate-%d", i)
						org.Providers["operator"].Models = append(org.Providers["operator"].Models, name)
						chain = append(chain, name)
					}
					chain = append(chain, mixedHopServingModel)
					org.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{mixedHopManaged: chain}}
					if providerLock {
						org.Routing.ProviderLock = &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"operator"}}
					}
					g := newManagedTestGateway(t, org)
					g.grant(t, "org-m", 1_000_000)
					failManaged(mixedHopManaged)(g)
					tr := traceMixedHop(g)

					w := g.chat(t, mixedHopChat(mixedHopManaged, "")(stream), nil)
					spent, held := settledAccount(t, g)
					_, _, reserves, _ := tr.snapshot()
					recs := g.outbox("org-m")
					if w.Code != http.StatusOK || g.managed.calls() != 2 || g.operator.calls() != 0 {
						t.Fatalf("status %d body %s, managed calls %d, own calls %d; want the managed fallback to serve after the primary, no own-key call",
							w.Code, w.Body, g.managed.calls(), g.operator.calls())
					}
					if spent != mixedHopServingBilled || held != 0 || reserves != 1 {
						t.Fatalf("spent %d held %d reserves %d; want %d billed for the serving attempt, nothing held, one hop hold",
							spent, held, reserves, mixedHopServingBilled)
					}
					if len(recs) != 1 || recs[0]["kind"] != "spend" || recs[0]["billed_micros"] != fmt.Sprint(mixedHopServingBilled) {
						t.Fatalf("settlements %v; want one spend of %d", recs, mixedHopServingBilled)
					}
					var sent []billing.AttemptSummary
					if err := json.Unmarshal([]byte(recs[0]["attempts"]), &sent); err != nil {
						t.Fatal(err)
					}
					if len(sent) != 2 || sent[0].Outcome == "success" || sent[0].Model != mixedHopManaged ||
						sent[1].Outcome != "success" || sent[1].Model != mixedHopServingModel ||
						sent[1].CredentialType != "managed" || sent[1].BilledMicros != mixedHopServingBilled {
						t.Fatalf("settlement attempts %+v; want exactly the failed primary and the serving %s billed %d",
							sent, mixedHopServingModel, mixedHopServingBilled)
					}
				})
			}
		}
	}
}

// mixedHopMirror installs a traffic mirror whose one rule copies every
// served request to target as model, and returns the provider ids its
// lookup was asked for. The lookup runs before the copy is sent, so a target
// it was never asked for was never sent anything.
func mixedHopMirror(g *managedTestGateway, target, model string) func() []string {
	var mu sync.Mutex
	var asked []string
	lookup := func(id string) (routing.MirrorProvider, bool) {
		mu.Lock()
		asked = append(asked, id)
		mu.Unlock()
		return g.srv.handlers.registry.GetProvider(id)
	}
	g.srv.handlers.mirror.Store(routing.NewMirror(config.MirrorConfig{Enabled: true,
		Rules: []config.MirrorRule{{SourceModel: "*", TargetProvider: target, TargetModel: model, SampleRate: 1}}}, lookup))
	return func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), asked...)
	}
}

// The traffic mirror's copy is never held or billed, so it never reaches a
// managed provider, whatever the request's billing mode; and a request
// billed to credits is not copied onto a provider key either while mixed
// billing chains are off.
func TestMixedHopGuardMirrorRefused(t *testing.T) {
	cases := []struct {
		name, model   string
		target, tmdl  string
		managedServed int // managed calls the request itself makes
	}{
		{"byok request to a managed target", mixedHopOwn, "openrouter", mixedHopManaged, 0},
		{"managed request to a managed target", mixedHopManaged, "openrouter", mixedHopServingModel, 1},
		{"managed request to a provider key", mixedHopManaged, "operator", "gpt-4o", 1},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			g := newManagedTestGateway(t, mixedHopOrg())
			g.grant(t, "org-m", 1_000_000)
			asked := mixedHopMirror(g, c.target, c.tmdl)
			ownBefore := g.operator.calls()
			w := g.chat(t, mixedHopChat(c.model, "")(false), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status %d body %s; want the request served", w.Code, w.Body)
			}
			if got := asked(); len(got) != 0 {
				t.Fatalf("the mirror looked up %v to send the copy; want the %s target refused before any lookup", got, c.target)
			}
			if n := g.managed.calls(); n != c.managedServed {
				t.Fatalf("managed calls %d; want %d (the request's own)", n, c.managedServed)
			}
			if c.model == mixedHopManaged && g.operator.calls() != ownBefore {
				t.Fatalf("own-key upstream calls %d; want none for a managed request", g.operator.calls())
			}
		})
	}
}

// The operator's own byok request (an internal key's, on the gateway's
// global provider) is still copied to another provider key: the mirror keeps
// working for self-hosted traffic, within the request's billing mode.
//
// Round 3 changed this control. It pinned a customer key's byok request
// being copied onto the operator's provider, which is the unbilled copy on
// the operator's credentials that round 3 refuses
// (TestMixedHopGuardMirrorCustomerKeyRefused); the self-hosted request is the
// legitimate same-mode case. It still passes at the round-1 and round-2
// heads.
func TestMixedHopGuardMirrorSameModeControl(t *testing.T) {
	g := newManagedTestGateway(t, mixedHopOrg())
	key := selfHostedKey(g)
	asked := mixedHopMirror(g, "operator", "gpt-4o")
	w := chatAs(g, key, mixedHopChat("gpt-4o", "")(false))
	if w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	if got := asked(); len(got) != 1 || got[0] != "operator" {
		t.Fatalf("the mirror looked up %v; want the operator target", got)
	}
	for deadline := time.Now().Add(5 * time.Second); g.operator.calls() < 2 && time.Now().Before(deadline); time.Sleep(10 * time.Millisecond) {
	}
	if n := g.operator.calls(); n != 2 || g.operator.lastAuth() != "Bearer fagi-operator-key" || g.operator.lastModel() != "gpt-4o" {
		t.Fatalf("operator calls %d, last auth %q model %q; want the request and its copy on the operator provider",
			n, g.operator.lastAuth(), g.operator.lastModel())
	}
	if g.managed.calls() != 0 {
		t.Fatalf("managed calls %d; want none", g.managed.calls())
	}
}

// A chain that refused a cross-mode candidate and served nothing answers
// with the error of the last attempt it made (an upstream 429 here), not its
// primary's (503): non-stream with and without provider failover, and the
// stream's chain.
func TestMixedHopGuardLastSameModeError(t *testing.T) {
	cases := []struct {
		name  string
		org   func() *tenant.OrgConfig
		setup func(g *managedTestGateway)
		body  func(bool) string
	}{
		{
			// No failover for a managed primary: runNonStream's chain.
			name: "managed primary, managed 429, byok refused",
			org:  mixedHopOrg,
			setup: func(g *managedTestGateway) {
				g.managed.failFor.Store(mixedHopManaged, http.StatusServiceUnavailable)
				g.managed.failFor.Store(mixedHopServingModel, http.StatusTooManyRequests)
			},
			body: mixedHopChat(mixedHopManaged, `,"models":["`+mixedHopServingModel+`","`+mixedHopOwn+`"]`),
		},
		{
			// The org's provider failover and retry run the byok primary:
			// runNonStreamWithFailover's chain.
			name: "byok primary with failover, byok 429, managed refused",
			org: withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
				ModelFallbacks: map[string][]string{mixedHopOwn: {mixedHopOwn2, mixedHopManaged}},
				Failover:       &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{502, 503}},
				Retry:          &tenant.RetryConfig{Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "2ms", OnStatusCodes: []int{502, 503}}}),
			setup: func(g *managedTestGateway) {
				g.operator.failFor.Store(mixedHopOwn, http.StatusServiceUnavailable)
				g.operator.failFor.Store(mixedHopOwn2, http.StatusTooManyRequests)
			},
			body: mixedHopChat(mixedHopOwn, ""),
		},
	}
	for _, c := range cases {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", c.name, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, c.org())
				g.grant(t, "org-m", 1_000_000)
				c.setup(g)
				w := g.chat(t, c.body(stream), nil)
				if w.Code != http.StatusTooManyRequests {
					t.Fatalf("status %d body %s (managed calls %d, own calls %d); want the last same-mode attempt's 429",
						w.Code, w.Body, g.managed.calls(), g.operator.calls())
				}
				if spent, held := settledAccount(t, g); spent != 0 || held != 0 {
					t.Fatalf("spent %d held %d; want nothing", spent, held)
				}
			})
		}
	}
}

// A chain that refused nothing keeps today's answer when it is exhausted:
// its primary's error, exactly as the primary alone answers.
func TestMixedHopGuardSameModeExhaustionKeepsPrimaryError(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			alone := newManagedTestGateway(t, mixedHopOrg())
			alone.grant(t, "org-m", 1_000_000)
			alone.managed.failFor.Store(mixedHopManaged, http.StatusServiceUnavailable)
			want := alone.chat(t, mixedHopChat(mixedHopManaged, "")(stream), nil).Code

			g := newManagedTestGateway(t, mixedHopOrg())
			g.grant(t, "org-m", 1_000_000)
			g.managed.failFor.Store(mixedHopManaged, http.StatusServiceUnavailable)
			g.managed.failFor.Store(mixedHopServingModel, http.StatusTooManyRequests)
			w := g.chat(t, mixedHopChat(mixedHopManaged, `,"models":["`+mixedHopServingModel+`"]`)(stream), nil)
			if w.Code != want || w.Code == http.StatusOK || g.managed.calls() != 2 {
				t.Fatalf("status %d (managed calls %d); want %d, the primary's own answer, after the same-mode chain", w.Code, g.managed.calls(), want)
			}
		})
	}
}
