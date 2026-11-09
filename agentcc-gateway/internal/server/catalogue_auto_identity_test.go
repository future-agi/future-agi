package server

import (
	"fmt"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Auto's filtered plan remains authoritative after provider-prefix resolution,
// even if an inherited chain under the upstream ID is in the same price family.
// Otherwise the mixed-plan fence over-refuses before dispatch, or fallback
// lookup silently resurrects a model that auto never selected.
func TestCAT2AutoInheritedChain(t *testing.T) {
	const primary = "google/gemini-3.5-flash"
	// A higher-priced inclusive model keeps the primary stable in cost mode.
	const sameFamily = "google/gemini-4-argon"
	const token = "openai/o1-pro"
	for _, form := range []struct {
		name, candidate, chainKey, request string
	}{
		{"unprefixed", primary, primary, "auto"},
		{"prefixed-requested", "openrouter/" + primary, "openrouter/" + primary, "auto"},
		{"prefixed-upstream", "openrouter/" + primary, primary, "auto"},
		{"auto-alias", "openrouter/" + primary, primary, "omega/auto"},
	} {
		for _, inherited := range []string{token, sameFamily} {
			for _, keepRunner := range []bool{false, true} {
				for _, failPrimary := range []bool{false, true} {
					for _, stream := range []bool{false, true} {
						t.Run(fmt.Sprintf("%s/inherited=%s/keep=%t/fail=%t/stream=%t", form.name, inherited, keepRunner, failPrimary, stream), func(t *testing.T) {
							g, _, _ := cat2OperatorGateway(t, true)
							runner := token
							if keepRunner {
								runner = "openrouter/" + sameFamily
							}
							cfg := managedOptIn()
							cfg.Routing = &tenant.RoutingConfig{
								FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false},
								Auto:           &tenant.AutoRoutingConfig{Preference: "cost", Candidates: []string{form.candidate, runner}},
								ModelFallbacks: map[string][]string{form.chainKey: {inherited}},
							}
							g.tenants.Set("org-m", cfg)
							answer := upstreamStream(func(w http.ResponseWriter, r *http.Request, model string) {
								if failPrimary && g.managed.calls() == 1 {
									w.WriteHeader(http.StatusServiceUnavailable)
									fmt.Fprint(w, `{"error":{"message":"synthetic primary failure"}}`)
									return
								}
								r601Response("0.0000095", stream)(w, r, model)
							})
							g.managed.responseWith.Store(&answer)
							g.managed.streamWith.Store(&answer)
							snaps := make(chan *models.TraceSnapshot, 1)
							g.srv.handlers.SetGenerationRecorder(func(s *models.TraceSnapshot) { snaps <- s })
							body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"synthetic"}]}`, form.request, stream)
							status := sendBillabilityBody(t, g, "/v1/chat/completions", body)
							wantStatus, wantCalls, wantModel := http.StatusOK, 1, primary
							if failPrimary {
								if keepRunner {
									wantCalls, wantModel = 2, sameFamily
								} else {
									wantStatus = http.StatusBadGateway
								}
							}
							if status != wantStatus || g.managed.calls() != wantCalls || g.managed.lastModel() != wantModel {
								t.Fatalf("status=%d calls=%d model=%q want status=%d calls=%d model=%q", status, g.managed.calls(), g.managed.lastModel(), wantStatus, wantCalls, wantModel)
							}
							if status != http.StatusOK {
								return
							}
							select {
							case snap := <-snaps:
								wantFallbacks := ""
								if keepRunner {
									wantFallbacks = runner
								}
								if snap.Metadata["auto_model"] != form.candidate || snap.Metadata["auto_fallbacks"] != wantFallbacks {
									t.Fatalf("auto primary=%q fallbacks=%q want primary=%q fallbacks=%q", snap.Metadata["auto_model"], snap.Metadata["auto_fallbacks"], form.candidate, wantFallbacks)
								}
							case <-time.After(5 * time.Second):
								t.Fatal("no generation record")
							}
						})
					}
				}
			}
		}
	}
}

// A present-but-empty chain is different from a missing chain under every
// requested identity. Missing entries still consult the upstream identity;
// explicit request and configured mixed plans must still be refused.
func TestCAT2FallbackIdentityAdmission(t *testing.T) {
	g, _, _ := cat2OperatorGateway(t, true)
	const upstream = "google/gemini-3.5-flash"
	const token = "openai/o1-pro"
	card, ok := g.srv.handlers.catalog.Load().PriceCard("openrouter", upstream, "")
	if !ok || card.Inclusive == nil {
		t.Fatal("fixture requires an inclusive primary")
	}
	for _, name := range []string{upstream, "openrouter/" + upstream, "gemini-3.5-flash"} {
		for _, plan := range []string{"empty", "missing", "mixed", "request"} {
			t.Run(name+"/"+plan, func(t *testing.T) {
				rc := &models.RequestContext{
					Model: name, Request: &models.ChatCompletionRequest{Model: upstream},
					OrgModelFallbacks: map[string][]string{upstream: {token}},
				}
				switch plan {
				case "empty", "request":
					rc.OrgModelFallbacks[name] = nil
				case "mixed":
					rc.OrgModelFallbacks[name] = []string{token}
				}
				if plan == "request" {
					rc.Routing.Models = []string{token}
				}
				// Routing fixes the request's fallback route just before
				// admission judges it (resolveRoute).
				fixFallbackRoute(rc)
				want := plan != "empty"
				if got := g.srv.handlers.mixedCataloguePlan(rc, managedOptIn(), &card); got != want {
					t.Fatalf("mixedCataloguePlan=%t want=%t", got, want)
				}
			})
		}
	}
}
