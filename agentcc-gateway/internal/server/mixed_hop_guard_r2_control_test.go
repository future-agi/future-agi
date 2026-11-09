package server

// MIXED-HOP-GUARD-1 round 2, the parts that need its new APIs: a refused
// cross-mode hop goes to the request's skipped-hop record (a count and the
// first models.MaxSkippedHops entries), never to its attempts, from every
// place the guard refuses one; it is logged without credential values; and
// the traffic mirror never targets a managed provider, at config load, in
// its lookup or at dispatch, while mixed billing chains on restores
// cross-mode mirroring to provider keys only.

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// mixedHopRecord is what the request showed the hook observer last: its
// attempts and its skipped-hop record.
type mixedHopRecord struct {
	mu       sync.Mutex
	attempts []models.Attempt
	count    int
	skips    []models.SkippedHop
}

func recordMixedHop(g *managedTestGateway) *mixedHopRecord {
	r := &mixedHopRecord{}
	g.srv.handlers.hookObserver = func(_, _ string, rc *models.RequestContext) {
		if rc == nil {
			return
		}
		attempts := rc.AttemptsSnapshot()
		n, skips := rc.SkippedHops()
		r.mu.Lock()
		r.attempts, r.count, r.skips = attempts, n, skips
		r.mu.Unlock()
	}
	return r
}

func (r *mixedHopRecord) snapshot() ([]models.Attempt, int, []models.SkippedHop) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.attempts, r.count, r.skips
}

// captureLogs returns what the gateway logged while fn ran.
func captureLogs(fn func()) string {
	var logs bytes.Buffer
	orig := slog.Default()
	slog.SetDefault(slog.New(slog.NewTextHandler(&logs, nil)))
	defer slog.SetDefault(orig)
	fn()
	return logs.String()
}

// mixedHopSecrets are every credential value the test gateways hold.
var mixedHopSecrets = []string{"org-operator-key", "fagi-openrouter-key", "fagi-operator-key", "org-or-key", managedOrgKey}

func assertNoSecrets(t *testing.T, logs string) {
	t.Helper()
	for _, secret := range mixedHopSecrets {
		if strings.Contains(logs, secret) {
			t.Fatalf("a credential value reached the log:\n%s", logs)
		}
	}
}

// Every cross-mode row of mixed_hop_guard_red_test.go records its refused
// hop in the skipped-hop record, in the hop's own billing mode, and logs it
// value-free; no attempt is recorded for it.
func TestMixedHopGuardRecordsSkippedHops(t *testing.T) {
	for _, c := range mixedHopCases() {
		if c.messages {
			continue // the Messages error path reports nothing to the observer
		}
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", c.name, stream), func(t *testing.T) {
				g := newManagedTestGateway(t, c.org())
				g.grant(t, "org-m", 1_000_000)
				c.setup(g)
				rec := recordMixedHop(g)
				var w int
				logs := captureLogs(func() { w = g.chat(t, c.body(stream), nil).Code })
				if w == http.StatusOK {
					t.Fatalf("status 200; want the %s hop refused", c.to)
				}
				attempts, n, skips := rec.snapshot()
				kind := models.AttemptModelFallback
				if strings.HasPrefix(c.name, "auto") {
					kind = models.AttemptAutoFallback
				}
				want := models.SkippedHop{Kind: kind, Reason: models.ReasonPolicy, Provider: c.hopProvider, Model: c.hopModel, CredentialType: c.to}
				found := false
				for _, s := range skips {
					found = found || s == want
				}
				if n < 1 || n != len(skips) || !found {
					t.Fatalf("skipped hops %d %+v; want %+v among them", n, skips, want)
				}
				for _, a := range attempts {
					if a.CredentialType == c.to || a.Outcome == models.OutcomeSkipped {
						t.Fatalf("attempts %+v hold the refused hop", attempts)
					}
				}
				if !strings.Contains(logs, "hop skipped: it would change the request's billing mode") ||
					!strings.Contains(logs, "credential_type="+string(c.to)) || !strings.Contains(logs, "chain_mode="+string(c.from)) {
					t.Fatalf("the refusal was not logged with its billing modes:\n%s", logs)
				}
				assertNoSecrets(t, logs)
			})
		}
	}
}

// A chain that refuses more candidates than a request records attempts
// keeps counting them, keeps the first models.MaxSkippedHops in full, logs
// only those, and still records and bills the attempt that served.
func TestMixedHopGuardSkipRecordIsBounded(t *testing.T) {
	const refused = 40
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			org := mixedHopOrg()
			var chain []string
			for i := range refused {
				name := fmt.Sprintf("own-candidate-%d", i)
				org.Providers["operator"].Models = append(org.Providers["operator"].Models, name)
				chain = append(chain, name)
			}
			org.Routing = &tenant.RoutingConfig{FallbackEnabled: true,
				ModelFallbacks: map[string][]string{mixedHopManaged: append(chain, mixedHopServingModel)}}
			g := newManagedTestGateway(t, org)
			g.grant(t, "org-m", 1_000_000)
			failManaged(mixedHopManaged)(g)
			rec := recordMixedHop(g)
			var w int
			logs := captureLogs(func() { w = g.chat(t, mixedHopChat(mixedHopManaged, "")(stream), nil).Code })
			if w != http.StatusOK {
				t.Fatalf("status %d; want the managed fallback to serve", w)
			}
			attempts, n, skips := rec.snapshot()
			if n != refused || len(skips) != models.MaxSkippedHops {
				t.Fatalf("skipped hops: %d counted, %d kept; want %d counted, %d kept", n, len(skips), refused, models.MaxSkippedHops)
			}
			for i, s := range skips {
				if s.Model != chain[i] || s.CredentialType != models.CredentialBYOK || s.Reason != models.ReasonPolicy {
					t.Fatalf("kept[%d] = %+v; want the refusal of %s in byok", i, s, chain[i])
				}
			}
			if len(attempts) != 2 || attempts[1].Outcome != models.OutcomeSuccess || attempts[1].Model != mixedHopServingModel {
				t.Fatalf("attempts %+v; want the primary and the serving attempt", attempts)
			}
			if got := strings.Count(logs, "hop skipped:"); got != models.MaxSkippedHops {
				t.Fatalf("%d refusals logged; want the first %d", got, models.MaxSkippedHops)
			}
			assertNoSecrets(t, logs)
			if spent, held := settledAccount(t, g); spent != mixedHopServingBilled || held != 0 {
				t.Fatalf("spent %d held %d; want %d", spent, held, mixedHopServingBilled)
			}
		})
	}
}

// startAttempt's refusal of a cross-mode attempt, whatever its kind, is a
// skipped hop in the attempt's own billing mode.
func TestMixedHopGuardAttemptStartRecordsSkippedHop(t *testing.T) {
	for _, kind := range []models.AttemptKind{models.AttemptRetry, models.AttemptFailover, models.AttemptModelFallback, models.AttemptAutoFallback} {
		for _, dir := range []struct {
			from, to          string
			provider, model   string
			cred              models.CredentialType
			fromProv, fromMdl string
		}{
			{billingManaged, billingBYOK, "operator", mixedHopOwn, models.CredentialBYOK, "openrouter", mixedHopManaged},
			{billingBYOK, billingManaged, "openrouter", mixedHopManaged, models.CredentialManaged, "operator", mixedHopOwn},
		} {
			t.Run(fmt.Sprintf("%s/%s to %s", kind, dir.from, dir.to), func(t *testing.T) {
				g := newManagedTestGateway(t, mixedHopOrg())
				h := g.srv.handlers
				rc := models.AcquireRequestContext()
				defer rc.Release()
				rc.RequestID = "req-mixed-hop-record"
				rc.Metadata["org_id"] = "org-m"
				rc.Request = &models.ChatCompletionRequest{Model: dir.fromMdl}
				rc.Metadata["billing_mode"] = dir.from
				_, finish, _ := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", dir.fromProv, dir.fromMdl, credentialType(rc))
				finish(nil, nil, &models.APIError{Status: http.StatusServiceUnavailable})

				rc.Metadata["billing_mode"] = dir.to
				_, finish, refusal := h.startAttempt(context.Background(), rc, kind, models.ReasonStatus5xx, dir.provider, dir.model, credentialType(rc))
				if refusal != models.ReasonPolicy || finish != nil {
					t.Fatalf("refusal %q; want policy", refusal)
				}
				want := models.SkippedHop{Kind: kind, Reason: models.ReasonPolicy, Provider: dir.provider, Model: dir.model, CredentialType: dir.cred}
				if n, skips := rc.SkippedHops(); n != 1 || len(skips) != 1 || skips[0] != want {
					t.Fatalf("skipped hops %d %+v; want %+v", n, skips, want)
				}
				if got := rc.AttemptsSnapshot(); len(got) != 1 {
					t.Fatalf("attempts %+v; want only the primary", got)
				}
			})
		}
	}
}

// A provider-failover hop that would change billing mode is refused by
// failoverHops before admitHop's verdict and hold, as a skipped hop: its
// run's error stays the previous attempt's, behind the hop-skipped sentinel.
func TestMixedHopGuardFailoverHopIsASkippedHop(t *testing.T) {
	for _, routed := range []bool{false, true} {
		t.Run(fmt.Sprintf("routed=%v", routed), func(t *testing.T) {
			org := mixedHopOrg()
			g := newManagedTestGateway(t, org)
			g.grant(t, "org-m", 1_000_000)
			h := g.srv.handlers
			tr := traceMixedHop(g)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req-mixed-hop-failover"
			rc.Metadata["org_id"] = "org-m"
			rc.Request = &models.ChatCompletionRequest{Model: mixedHopManaged}
			rc.Metadata["billing_mode"] = billingManaged
			prev := &models.APIError{Status: http.StatusServiceUnavailable}
			hops := failoverHops{prevProvider: "openrouter", lastErr: prev}
			wantKind := models.AttemptFailover
			if routed {
				// The org failover's first attempt sent elsewhere than routed.
				hops = failoverHops{routed: "openrouter", lastErr: prev}
				wantKind = models.AttemptPrimary
				setChainMode(rc, models.CredentialManaged)
			} else {
				_, finish, _ := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", "openrouter", mixedHopManaged, credentialType(rc))
				finish(nil, nil, prev)
			}
			attemptsBefore := rc.AttemptCount()
			rc.Metadata["billing_mode"] = billingBYOK

			kind, admitted := hops.next(h, rc, org, "operator", mixedHopOwn)
			if admitted || kind != wantKind {
				t.Fatalf("next = %s, %v; want the %s hop refused", kind, admitted, wantKind)
			}
			reason, last, ok := routing.SkippedHop(hops.lastErr)
			if !ok || reason != models.ReasonPolicy || !errors.Is(last, prev) {
				t.Fatalf("lastErr %v; want the hop-skipped sentinel (policy) over the previous error", hops.lastErr)
			}
			want := models.SkippedHop{Kind: wantKind, Reason: models.ReasonPolicy, Provider: "operator", Model: mixedHopOwn, CredentialType: models.CredentialBYOK}
			if n, skips := rc.SkippedHops(); n != 1 || len(skips) != 1 || skips[0] != want {
				t.Fatalf("skipped hops %d %+v; want %+v", n, skips, want)
			}
			if rc.AttemptCount() != attemptsBefore {
				t.Fatalf("attempts %+v; the refused hop was recorded as one", rc.AttemptsSnapshot())
			}
			if _, _, reserves, judged := tr.snapshot(); reserves != 0 || judged != 0 {
				t.Fatalf("the refused hop was judged %d and held %d times; want neither", judged, reserves)
			}
		})
	}
}

// With mixed billing chains on, a managed target stays refused, whatever the
// flag, and so does a customer key's request to a provider key.
//
// Round 3 changed the first half. It pinned a customer's managed request
// being copied onto the operator's provider with the flag on; every managed
// request is a customer key's (only a key limited to its org's providers is
// billed to credits), and round 3 refuses mirroring any customer key's
// request, which would go out unbilled on the operator's credentials.
func TestMixedHopGuardMirrorFlagOn(t *testing.T) {
	g := newManagedTestGateway(t, mixedHopOrg())
	allowMixedBillingChains(g)
	g.grant(t, "org-m", 1_000_000)
	asked := mixedHopMirror(g, "operator", "gpt-4o")
	if w := g.chat(t, mixedHopChat(mixedHopManaged, "")(false), nil); w.Code != http.StatusOK {
		t.Fatalf("status %d body %s", w.Code, w.Body)
	}
	if got := asked(); len(got) != 0 || g.operator.calls() != 0 {
		t.Fatalf("lookups %v, operator calls %d; want a customer's managed request never copied, flag on or not", got, g.operator.calls())
	}

	for _, model := range []string{mixedHopOwn, mixedHopManaged} {
		g := newManagedTestGateway(t, mixedHopOrg())
		allowMixedBillingChains(g)
		g.grant(t, "org-m", 1_000_000)
		asked := mixedHopMirror(g, "openrouter", mixedHopServingModel)
		managedBefore := 0
		if model == mixedHopManaged {
			managedBefore = 1
		}
		var w int
		logs := captureLogs(func() { w = g.chat(t, mixedHopChat(model, "")(false), nil).Code })
		if w != http.StatusOK {
			t.Fatalf("%s: status %d", model, w)
		}
		if got := asked(); len(got) != 0 || g.managed.calls() != managedBefore {
			t.Fatalf("%s: lookups %v, managed calls %d; want the managed target refused with mixed chains on", model, got, g.managed.calls())
		}
		if !strings.Contains(logs, "traffic mirror refused") || !strings.Contains(logs, "reason="+mirrorRefusedManagedTarget) {
			t.Fatalf("%s: the refusal was not logged:\n%s", model, logs)
		}
		assertNoSecrets(t, logs)
	}
}

// The mirror's lookup never returns a managed provider, whoever asks.
func TestMirrorLookupRefusesManagedProviders(t *testing.T) {
	g := newManagedTestGateway(t, mixedHopOrg())
	lookup := mirrorLookup(g.srv.handlers.registry)
	if p, ok := lookup("openrouter"); ok || p != nil {
		t.Fatal("the mirror lookup returned the managed openrouter provider")
	}
	if p, ok := lookup("operator"); !ok || p == nil {
		t.Fatal("the mirror lookup refused the operator provider")
	}
}
