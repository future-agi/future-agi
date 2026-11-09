package server

// MIXED-HOP-GUARD-1 controls that need the new node flag
// (managed.mixed_billing_chains): turned on, every cross-mode row of
// mixed_hop_guard_red_test.go behaves as it did before the guard; the flag
// is wired from the config; and a refused model-fallback hop leaves the
// request's routing exactly as it was before the hop was resolved.

import (
	"context"
	"fmt"
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// allowMixedBillingChains turns the mixed billing chain profile on for g, as
// managed.mixed_billing_chains: true does at startup.
func allowMixedBillingChains(g *managedTestGateway) { g.srv.handlers.mixedBillingChains = true }

func TestMixedHopGuardFlagOnRestoresMixedChains(t *testing.T) {
	for _, c := range mixedHopCases() {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", c.name, stream), func(t *testing.T) {
				g, w, tr := runMixedHop(t, c, stream, allowMixedBillingChains)
				if w.Code != http.StatusOK {
					t.Fatalf("status %d body %s; with mixed chains on the %s hop to %s serves", w.Code, w.Body, c.to, c.hopModel)
				}
				if n := c.hopCalls(g); n != 1 {
					t.Fatalf("the %s hop was dispatched %d times; want once", c.to, n)
				}
				spent, held := settledAccount(t, g)
				if c.to == models.CredentialManaged {
					if spent <= 0 || held != 0 {
						t.Fatalf("spent %d held %d; want the managed hop billed to credits and its hold settled", spent, held)
					}
				} else {
					if spent != 0 || held != 0 {
						t.Fatalf("spent %d held %d; want nothing billed to credits for the byok hop", spent, held)
					}
					if got := g.operator.lastAuth(); got != "Bearer org-operator-key" {
						t.Fatalf("the byok hop was sent with %q; want the org's own key", got)
					}
				}
				if stream || c.messages {
					return
				}
				attempts, _, _, _ := tr.snapshot()
				if len(attempts) < 2 {
					t.Fatalf("attempts %+v; want the primary and its hop", attempts)
				}
				last := attempts[len(attempts)-1]
				if last.Outcome != models.OutcomeSuccess || last.CredentialType != c.to || last.Provider != c.hopProvider {
					t.Fatalf("attempts %+v; want the %s hop on %s to serve", attempts, c.to, c.hopProvider)
				}
			})
		}
	}
}

// The attempt start lets a cross-mode attempt through with the flag on.
func TestMixedHopGuardAttemptStartFlagOn(t *testing.T) {
	g := newManagedTestGateway(t, mixedHopOrg())
	allowMixedBillingChains(g)
	h := g.srv.handlers
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "req-mixed-hop-on"
	rc.Metadata["org_id"] = "org-m"
	rc.Request = &models.ChatCompletionRequest{Model: mixedHopManaged}
	rc.Metadata["billing_mode"] = billingManaged
	_, finish, refusal := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", "openrouter", mixedHopManaged, credentialType(rc))
	if refusal != "" {
		t.Fatalf("primary refused (%q)", refusal)
	}
	finish(nil, nil, &models.APIError{Status: http.StatusServiceUnavailable})
	rc.Metadata["billing_mode"] = billingBYOK
	_, finish, refusal = h.startAttempt(context.Background(), rc, models.AttemptModelFallback, models.ReasonStatus5xx, "operator", mixedHopOwn, credentialType(rc))
	if refusal != "" || finish == nil {
		t.Fatalf("with mixed chains on a byok hop after a managed primary was refused (%q)", refusal)
	}
	finish(nil, nil, nil)
}

// A refused model-fallback hop is undone: the routing state its resolution
// wrote (billing mode, managed model, provider, upstream model) is back as
// it was, nothing judged or held, and the hop recorded in the skipped-hop
// record in its own mode, never as an attempt. With the flag on the same hop
// is admitted.
func TestMixedHopGuardFallbackHopIsUndone(t *testing.T) {
	for _, on := range []bool{false, true} {
		t.Run(fmt.Sprintf("mixed=%v", on), func(t *testing.T) {
			org := mixedHopOrg()
			g := newManagedTestGateway(t, org)
			g.grant(t, "org-m", 1_000_000)
			if on {
				allowMixedBillingChains(g)
			}
			h := g.srv.handlers
			tr := traceMixedHop(g)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.RequestID = "req-mixed-hop-undo"
			rc.Metadata["org_id"] = "org-m"
			rc.Request = &models.ChatCompletionRequest{Model: mixedHopManaged}
			rc.Provider = "openrouter"
			rc.Model = mixedHopManaged
			rc.Metadata["billing_mode"] = billingManaged
			rc.Metadata["managed_model"] = "upstream-id"
			_, finish, _ := h.startAttempt(context.Background(), rc, models.AttemptPrimary, "", "openrouter", mixedHopManaged, credentialType(rc))
			finish(nil, nil, &models.APIError{Status: http.StatusServiceUnavailable})

			p, providerID, model, ok := h.admitModelFallback(context.Background(), "org-m", org, rc, mixedHopOwn)
			if on {
				if !ok || p == nil || providerID != "operator" || model != mixedHopOwn || rc.Metadata["billing_mode"] != billingBYOK {
					t.Fatalf("mixed on: ok=%v provider=%q model=%q billing=%q; want the byok hop admitted", ok, providerID, model, rc.Metadata["billing_mode"])
				}
				return
			}
			if ok || p != nil {
				t.Fatalf("a byok hop after a managed primary was admitted (%q on %q)", model, providerID)
			}
			if rc.Provider != "openrouter" || rc.Request.Model != mixedHopManaged ||
				rc.Metadata["billing_mode"] != billingManaged || rc.Metadata["managed_model"] != "upstream-id" {
				t.Fatalf("routing after the refusal: provider %q model %q billing %q managed_model %q; want it as before the hop",
					rc.Provider, rc.Request.Model, rc.Metadata["billing_mode"], rc.Metadata["managed_model"])
			}
			if _, _, reserves, judged := tr.snapshot(); reserves != 0 || judged != 0 {
				t.Fatalf("the refused hop was judged %d and held %d times; want neither", judged, reserves)
			}
			if got := rc.AttemptsSnapshot(); len(got) != 1 {
				t.Fatalf("attempts %+v; want only the primary: the refused hop is not an attempt", got)
			}
			want := models.SkippedHop{Kind: models.AttemptModelFallback, Reason: models.ReasonPolicy,
				Provider: "operator", Model: mixedHopOwn, CredentialType: models.CredentialBYOK}
			if n, skips := rc.SkippedHops(); n != 1 || len(skips) != 1 || skips[0] != want {
				t.Fatalf("skipped hops %d %+v; want the byok hop %+v", n, skips, want)
			}
		})
	}
}

// managed.mixed_billing_chains reaches the request path, and is off unless
// set.
func TestMixedHopGuardFlagWiring(t *testing.T) {
	for _, on := range []bool{false, true} {
		off := false
		cfg := config.DefaultConfig()
		cfg.Providers["operator"] = config.ProviderConfig{BaseURL: "http://127.0.0.1:9", APIKey: "synthetic-unused", APIFormat: "openai",
			Models: []string{"gpt-4o"}, AutoDiscover: &off}
		if cfg.Managed.MixedBillingChains {
			t.Fatal("the default config allows mixed billing chains")
		}
		cfg.Managed.MixedBillingChains = on
		registry, err := providers.NewRegistry(cfg)
		if err != nil {
			t.Fatal(err)
		}
		srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
		if srv.handlers.mixedBillingChains != on {
			t.Fatalf("managed.mixed_billing_chains=%v reached the handlers as %v", on, srv.handlers.mixedBillingChains)
		}
	}
}
