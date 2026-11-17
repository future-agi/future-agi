package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const byokBridgeModel = "owned/bridge"

func byokStreamOrg() *tenant.OrgConfig {
	cfg := managedOptIn()
	// Inherit the configured synthetic operator's loopback URL. Never register
	// another URL or relax the org-provider network boundary for this fixture.
	cfg.Providers = map[string]*tenant.ProviderConfig{
		"operator": {APIKey: "synthetic-owned-bridge", APIFormat: "openai", Models: []string{byokBridgeModel}, Enabled: true},
	}
	return cfg
}

func byokEarlyStreamFailure(w http.ResponseWriter, model, cost string, split bool) {
	w.Header().Set("Content-Type", "text/event-stream")
	if split {
		writeManagedChunk(w, model, `"provider":"BYOK endpoint","choices":[{"index":0,"delta":{"role":"assistant"}}]`)
		writeManagedChunk(w, model, `"choices":[],"usage":{"prompt_tokens":8,"completion_tokens":3,"cost":`+cost+`}`)
		fmt.Fprint(w, "data: {\"error\":{\"code\":502,\"message\":\"fixture early failure\"}}\n\n")
		return
	}
	fmt.Fprintf(w, "data: {\"model\":%q,\"provider\":\"BYOK endpoint\",\"error\":{\"code\":502,\"message\":\"fixture early failure\"},\"usage\":{\"prompt_tokens\":8,\"completion_tokens\":3,\"cost\":%s}}\n\n", model, cost)
}

func TestBYOKStreamFallbackChain(t *testing.T) {
	testBYOKStreamFallbackChain(t, "/v1/chat/completions")
}

// Also reused by the pinned CAT-2 integration gate. Canonical Messages and
// chat share configured fallback admission; the retry control below explicitly
// opts the organization in rather than relying on the retired implicit retry.
// The chain bridges a managed request through a byok hop, so it runs on a
// node that allows mixed billing chains (managed.mixed_billing_chains); with
// them off the bridge is skipped (TestMixedHopGuardChainContinuesInMode).
func testBYOKStreamFallbackChain(t *testing.T, surface string) {
	for _, split := range []bool{false, true} {
		t.Run(fmt.Sprintf("split=%t", split), func(t *testing.T) {
			const primary, final = "anthropic/claude-sonnet-5.5", "openai/gpt-6-sol"
			cfg := byokStreamOrg()
			cfg.Routing = &tenant.RoutingConfig{
				FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false},
				ModelFallbacks: map[string][]string{primary: {byokBridgeModel, final}},
			}
			g := newManagedTestGateway(t, cfg)
			allowMixedBillingChains(g)
			g.ledger.SetMicros("org-m", 10_000_000)
			managed := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				if model == primary {
					byokEarlyStreamFailure(w, model, "0.00005", false)
					return
				}
				w.Header().Set("Content-Type", "text/event-stream")
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"final fallback answer"}}]`)
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":3,"cost":0.007}`)
				fmt.Fprint(w, "data: [DONE]\n\n")
			})
			own := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				byokEarlyStreamFailure(w, model, "0.2", split)
			})
			g.managed.streamWith.Store(&managed)
			g.operator.streamWith.Store(&own)
			status, body, _ := sendManaged(t, g, surface, primary, true, false, nil)
			spent, held := settledAccount(t, g)
			recs := g.outbox("org-m")
			if status != 200 || g.managed.calls() != 2 || g.operator.calls() != 1 || !strings.Contains(body, "final fallback answer") || strings.Contains(body, "fixture early failure") {
				t.Errorf("failed BYOK bridge committed output: status=%d managed=%d byok=%d body=%.500s", status, g.managed.calls(), g.operator.calls(), body)
			}
			// 50 upstream micros + 5% rounded = 53, then 7000 + 5% = 7350.
			// The BYOK hop's 200000 upstream micros must never enter this debit.
			if spent != 7403 || held != 0 || len(recs) != 1 {
				t.Fatalf("want one managed settlement excluding BYOK: spent=%d held=%d records=%v", spent, held, recs)
			}
			if recs[0]["v"] != "1" || recs[0]["kind"] != "spend" {
				t.Fatalf("not the single v1 settlement: %v", recs)
			}
			var attempts []billing.AttemptSummary
			if err := json.Unmarshal([]byte(recs[0]["attempts"]), &attempts); err != nil || len(attempts) != 3 {
				t.Fatalf("want all three attempts: %v err=%v", attempts, err)
			}
			// BYOK is metered at upstream cost with no markup; only managed
			// attempts contribute to the request's credit debit above.
			bridge := attempts[1]
			if bridge.Model != byokBridgeModel || bridge.CredentialType != string(models.CredentialBYOK) || bridge.Outcome != string(models.OutcomeError) || bridge.UpstreamMicros != 200000 || bridge.BilledMicros != bridge.UpstreamMicros {
				t.Errorf("BYOK failure evidence lost or marked successful/charged: %+v", bridge)
			}
			if attempts[0].Outcome != string(models.OutcomeError) || attempts[2].Outcome != string(models.OutcomeSuccess) {
				t.Errorf("incorrect fallback attempt outcomes: %+v", attempts)
			}
		})
	}
}

func TestBYOKMessagesStreamRetriesBeforeOutput(t *testing.T) {
	for _, recover := range []bool{false, true} {
		t.Run(fmt.Sprintf("recover=%t", recover), func(t *testing.T) {
			cfg := byokStreamOrg()
			// Messages follows chat's opt-in retry policy after integration.
			// Exercise the real customer route with one configured retry; do
			// not restore implicit retries or weaken the two-dispatch assertion.
			cfg.Routing = &tenant.RoutingConfig{Retry: &tenant.RetryConfig{
				Enabled: true, MaxRetries: 1, InitialDelay: "1ms", MaxDelay: "1ms",
				Multiplier: 1, OnStatusCodes: []int{http.StatusBadGateway},
			}}
			g := newManagedTestGateway(t, cfg)
			var calls atomic.Int32
			own := upstreamStream(func(w http.ResponseWriter, _ *http.Request, model string) {
				if calls.Add(1) == 1 || !recover {
					byokEarlyStreamFailure(w, model, "0.2", true)
					return
				}
				w.Header().Set("Content-Type", "text/event-stream")
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{"content":"retry answer"}}]`)
				writeManagedChunk(w, model, `"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]`)
				fmt.Fprint(w, "data: [DONE]\n\n")
			})
			g.operator.streamWith.Store(&own)
			status, body, _ := sendManaged(t, g, "/v1/messages", byokBridgeModel, true, false, nil)
			wantStatus := http.StatusBadGateway
			if recover {
				wantStatus = http.StatusOK
			}
			if status != wantStatus || calls.Load() != 2 || (recover && !strings.Contains(body, "retry answer")) {
				t.Errorf("BYOK metadata suppressed retry: status=%d want=%d calls=%d body=%.500s", status, wantStatus, calls.Load(), body)
			}
			spent, held := settledAccount(t, g)
			if spent != 0 || held != 0 || len(g.outbox("org-m")) != 0 {
				t.Errorf("BYOK charged managed credit: spent=%d held=%d records=%v", spent, held, g.outbox("org-m"))
			}
		})
	}
}
