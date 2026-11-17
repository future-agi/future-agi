package server

import (
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A hop down the caller's own models[] (the request's declared plan,
// effectiveFallbackPlan source 1) is recorded as a request_fallback attempt,
// the kind Future AGI's AgentccRequestAttempt row, the Logs label ("Request
// fallback") and the log-entry-v2 schema already name. A hop down the org's or
// the node's configured chain stays a model_fallback, and an auto-routed
// request's runner-up stays an auto_fallback. The kind is a label only: a
// request_fallback hop is admitted, held, priced and billed exactly as the
// same hop down a configured chain is.

// rqfbSol is the managed model every managed hop here moves to.
const rqfbSol = "openai/gpt-6-sol"

// rqfbHopUpstreamMicros and rqfbHopBilledMicros are what a managed hop the
// upstream answers bills (managed_test.go's recordingUpstream reports
// upstreamCost, $0.007, and the gateway's markup is 5%): the amount a models[]
// hop was pinned at while it was still recorded as a model_fallback
// (TestTimedOutPrimaryIsChargedAtItsHoldWhenAFallbackServes, servedBilled).
const (
	rqfbHopUpstreamMicros = 7000
	rqfbHopBilledMicros   = 7350
)

// rqfbRun sends body to a managed test gateway for org whose primary fails as
// setup makes it, checks it is answered, and returns the gateway and the
// request's sent attempts as priced at the spend guard's post step (after
// cost and credits).
func rqfbRun(t *testing.T, org *tenant.OrgConfig, setup func(*managedTestGateway), body string) (*managedTestGateway, []models.Attempt) {
	t.Helper()
	probe := &attemptsAtHoldProbe{}
	g := newManagedTestGatewayWith(t, org, managedGatewayOptions{extraPlugins: []pipeline.Plugin{probe}})
	g.grant(t, "org-m", 1_000_000)
	setup(g)
	w := g.chat(t, body, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status %d body %s; want the hop's answer", w.Code, w.Body)
	}
	// A stream's post step may still be running when its last chunk is
	// written: wait for the one observation, as settledAccount waits.
	for deadline := time.Now().Add(5 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
		probe.mu.Lock()
		n := len(probe.seen)
		probe.mu.Unlock()
		if n > 0 {
			break
		}
	}
	return g, probe.only(t).attempts
}

// rqfbHop checks attempts are the failed primary on primary and one hop of
// kind to model on credential, and returns the hop.
func rqfbHop(t *testing.T, attempts []models.Attempt, primary string, kind models.AttemptKind, model string, credential models.CredentialType) models.Attempt {
	t.Helper()
	if len(attempts) != 2 {
		t.Fatalf("attempts = %+v; want the failed primary and one hop", attempts)
	}
	first, hop := attempts[0], attempts[1]
	if first.Kind != models.AttemptPrimary || first.Model != primary || first.Outcome == models.OutcomeSuccess {
		t.Fatalf("first attempt = %+v; want the failed primary on %s", first, primary)
	}
	if hop.Kind != kind || hop.Model != model || hop.Outcome != models.OutcomeSuccess || hop.CredentialType != credential {
		t.Fatalf("hop = kind %q model %q outcome %q credential %q; want a %s success on %s (%s)",
			hop.Kind, hop.Model, hop.Outcome, hop.CredentialType, kind, model, credential)
	}
	if hop.Reason != models.ReasonStatus5xx {
		t.Errorf("hop reason = %q; want %q", hop.Reason, models.ReasonStatus5xx)
	}
	return hop
}

// rqfbSamePrice fails unless got was priced and billed as want was.
func rqfbSamePrice(t *testing.T, got, want models.Attempt) {
	t.Helper()
	if got.Billable != want.Billable || got.CostSource != want.CostSource ||
		got.UpstreamCostMicros != want.UpstreamCostMicros || got.BilledCostMicros != want.BilledCostMicros ||
		got.LandedCostMicros != want.LandedCostMicros || got.UsageEstimated != want.UsageEstimated {
		t.Fatalf("%s hop priced billable=%v source=%q upstream=%d billed=%d landed=%d estimated=%v; "+
			"want it priced as the %s hop: billable=%v source=%q upstream=%d billed=%d landed=%d estimated=%v",
			got.Kind, got.Billable, got.CostSource, got.UpstreamCostMicros, got.BilledCostMicros, got.LandedCostMicros, got.UsageEstimated,
			want.Kind, want.Billable, want.CostSource, want.UpstreamCostMicros, want.BilledCostMicros, want.LandedCostMicros, want.UsageEstimated)
	}
}

// A managed chat call with models:[failing, ok] whose first upstream answers
// 502 records its second attempt as a request_fallback, billed what the same
// hop billed as a model_fallback: the upstream's charge plus the 5% markup.
// The same hop down the org's configured chain, and down the node's
// config.yaml chain, still records a model_fallback, priced and settled
// alike.
func TestRequestFallbackAttemptKindManaged(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			var caller models.Attempt
			var callerSpent int64
			t.Run("caller models[]", func(t *testing.T) {
				g, attempts := rqfbRun(t, mixedHopOrg(), func(g *managedTestGateway) {
					g.managed.failFor.Store(mixedHopManaged, http.StatusBadGateway)
				}, mixedHopChat(mixedHopManaged, `,"models":["`+rqfbSol+`"]`)(stream))
				caller = rqfbHop(t, attempts, mixedHopManaged, models.AttemptRequestFallback, rqfbSol, models.CredentialManaged)
				// The literal is pinned at C for a non-stream models[] hop only;
				// a streamed hop is held to the configured-chain controls below.
				if !stream && (!caller.Billable || caller.UpstreamCostMicros != rqfbHopUpstreamMicros || caller.BilledCostMicros != rqfbHopBilledMicros) {
					t.Fatalf("request_fallback hop billable=%v upstream=%d billed=%d; want billable at %d upstream, %d billed",
						caller.Billable, caller.UpstreamCostMicros, caller.BilledCostMicros, rqfbHopUpstreamMicros, rqfbHopBilledMicros)
				}
				spent, held := settledAccount(t, g)
				if held != 0 || spent < caller.BilledCostMicros || spent <= 0 {
					t.Fatalf("account spent=%d held=%d; want the hop's %d settled and nothing held", spent, held, caller.BilledCostMicros)
				}
				callerSpent = spent
			})
			if caller.Kind == "" {
				t.Fatal("the caller models[] run did not complete")
			}
			controls := []struct {
				name  string
				org   func() *tenant.OrgConfig
				setup func(*managedTestGateway)
			}{
				{"org model fallbacks",
					withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
						ModelFallbacks: map[string][]string{mixedHopManaged: {rqfbSol}}}),
					failManaged(mixedHopManaged)},
				{"node model fallbacks", mixedHopOrg, func(g *managedTestGateway) {
					failManaged(mixedHopManaged)(g)
					installGlobalChain(g, map[string][]string{mixedHopManaged: {rqfbSol}})
				}},
			}
			for _, c := range controls {
				t.Run(c.name, func(t *testing.T) {
					g, attempts := rqfbRun(t, c.org(), c.setup, mixedHopChat(mixedHopManaged, "")(stream))
					hop := rqfbHop(t, attempts, mixedHopManaged, models.AttemptModelFallback, rqfbSol, models.CredentialManaged)
					rqfbSamePrice(t, caller, hop)
					if spent, held := settledAccount(t, g); held != 0 || spent != callerSpent {
						t.Fatalf("account spent=%d held=%d; want the %d the request_fallback request settled, nothing held", spent, held, callerSpent)
					}
				})
			}
		})
	}
}

// A BYOK hop down the caller's own models[] is priced on its own model, as a
// configured model fallback hop is: never at the org's own price for the
// model asked for (the cost plugin's movesDownModelChain), even though its
// host answers naming the id it was sent, which makes the hop's model the
// request's resolved one. Before request_fallback moved down the model chain
// there, such a hop took the primary's org price (org_custom, $100 a million
// tokens here).
func TestRequestFallbackAttemptKindBYOKPricing(t *testing.T) {
	priced := func(org *tenant.OrgConfig) *tenant.OrgConfig {
		org.CostTracking = r7omCustom(true, mixedHopOwn, 100, 100)
		return org
	}
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			_, attempts := rqfbRun(t, priced(mixedHopOrg()), func(g *managedTestGateway) {
				g.operator.failFor.Store(mixedHopOwn, http.StatusBadGateway)
			}, mixedHopChat(mixedHopOwn, `,"models":["`+mixedHopOwn2+`"]`)(stream))
			caller := rqfbHop(t, attempts, mixedHopOwn, models.AttemptRequestFallback, mixedHopOwn2, models.CredentialBYOK)
			if caller.CostSource == models.CostSourceOrgCustom {
				t.Fatalf("request_fallback hop on %s priced at the org's price for %s (%d micros, %s); want its own price",
					mixedHopOwn2, mixedHopOwn, caller.BilledCostMicros, caller.CostSource)
			}

			_, attempts = rqfbRun(t, priced(withRouting(&tenant.RoutingConfig{FallbackEnabled: true,
				ModelFallbacks: map[string][]string{mixedHopOwn: {mixedHopOwn2}}})()), failOwn(mixedHopOwn),
				mixedHopChat(mixedHopOwn, "")(stream))
			control := rqfbHop(t, attempts, mixedHopOwn, models.AttemptModelFallback, mixedHopOwn2, models.CredentialBYOK)
			rqfbSamePrice(t, caller, control)
		})
	}
}

// An auto-routed request's runner-up stays an auto_fallback.
func TestRequestFallbackAttemptKindAutoUnchanged(t *testing.T) {
	_, attempts := rqfbRun(t, managedOptIn(), func(g *managedTestGateway) {
		g.managed.failFor.Store("qwen/qwen3.7-flash", http.StatusServiceUnavailable)
	}, chatBody("auto"))
	if len(attempts) != 2 {
		t.Fatalf("attempts = %+v; want the auto pick and its runner-up", attempts)
	}
	if attempts[0].Kind != models.AttemptPrimary || attempts[1].Kind != models.AttemptAutoFallback {
		t.Fatalf("attempt kinds %q, %q; want primary then auto_fallback", attempts[0].Kind, attempts[1].Kind)
	}
}

// fallbackKind names a hop for the plan its request's fixed route resolves
// (effectiveFallbackPlan): the caller's models[] wins over an auto-routed
// request's runners-up, as it does in the resolver. Every dispatch entry
// fixes the route before any attempt, so the route is fixed here first.
func TestRequestFallbackKindFollowsThePlanSource(t *testing.T) {
	for _, c := range []struct {
		name   string
		models []string
		auto   bool
		want   models.AttemptKind
	}{
		{"configured chain", nil, false, models.AttemptModelFallback},
		{"auto runners-up", nil, true, models.AttemptAutoFallback},
		{"caller models[]", []string{"primary", "next"}, false, models.AttemptRequestFallback},
		{"caller models[] on an auto-routed request", []string{"primary", "next"}, true, models.AttemptRequestFallback},
	} {
		t.Run(c.name, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = "primary"
			rc.Request = &models.ChatCompletionRequest{Model: "primary"}
			rc.Routing.Models = c.models
			if c.auto {
				rc.Metadata["auto_model"] = "primary"
			}
			fixFallbackRoute(rc)
			if got := fallbackKind(rc); got != c.want {
				t.Fatalf("kind %q; want %q", got, c.want)
			}
		})
	}
}
