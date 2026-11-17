package server

import (
	"context"
	"net/http"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// A decision call runs on the organization's own provider key, which the
// vendor bills. Even on a gateway with Future AGI models on, the
// organization's managed-credit ledger holding managed requests, and a
// managed key, a decision holds nothing on the organization's managed credit,
// settles nothing there, and draws nothing from the key's own balance. A
// chat call for the same organization and key on Future AGI's provider is
// held and settled on that ledger, which shows the ledger is on.
//
// The credits plugin holds a request whose billing_mode is managed
// (plugins/credits/credits.go, isManaged), and a decision never has it:
//   - billing_mode managed is set only in managed_resolution.go, by
//     providerForKey (:45) and resolveManaged (:74), each gated by
//     managedEndpoint (managed_endpoints.go:16-21), which admits only the
//     endpoint types "", "chat" and "anthropic_messages";
//   - serveDecision sets rc.EndpointType "decision" first thing
//     (handlers_decision.go:53), before any resolution;
//   - billing_mode is a gateway-owned metadata name (caller_metadata.go:252),
//     so a caller cannot set it.

// holdSeen is what the request stage left on a request once the credits
// plugin had run: its endpoint, its billing mode and whether the request
// holds credit on its organization's ledger.
type holdSeen struct {
	requestID, endpoint, billingMode string
	held                             bool
}

// holdProbe records holdSeen for each request. Its priority (600) puts its
// request stage after the credits plugin's (510).
type holdProbe struct {
	mu   sync.Mutex
	seen []holdSeen
}

func (p *holdProbe) Name() string  { return "decision-hold-probe" }
func (p *holdProbe) Priority() int { return 600 }
func (p *holdProbe) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.mu.Lock()
	p.seen = append(p.seen, holdSeen{rc.RequestID, rc.EndpointType, rc.Metadata["billing_mode"], rc.Billing.Reserved})
	p.mu.Unlock()
	return pipeline.ResultContinue()
}
func (p *holdProbe) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// last is the latest request's holdSeen; it fails the test when none ran.
func (p *holdProbe) last(t *testing.T) holdSeen {
	t.Helper()
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.seen) == 0 {
		t.Fatal("no request reached the pipeline")
	}
	return p.seen[len(p.seen)-1]
}

// keyAuth is the auth plugin on the gateway's own key store, set once the
// gateway is built. It puts the key's id on the request, so the credits
// plugin finds a managed key's own balance.
type keyAuth struct{ p *authplugin.Plugin }

func (a *keyAuth) Name() string  { return "auth" }
func (a *keyAuth) Priority() int { return (&authplugin.Plugin{}).Priority() }
func (a *keyAuth) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if a.p == nil {
		return pipeline.ResultContinue()
	}
	return a.p.ProcessRequest(ctx, rc)
}
func (a *keyAuth) ProcessResponse(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if a.p == nil {
		return pipeline.ResultContinue()
	}
	return a.p.ProcessResponse(ctx, rc)
}

func TestDecisionHoldsNothingOnTheOrgLedger(t *testing.T) {
	const decisionKey = "sk-agentcc-managed-decision"
	vendor := newDecisionUpstream(t)
	orgCfg := managedOptIn()
	orgCfg.Providers = map[string]*tenant.ProviderConfig{
		"typesafe": {APIKey: orgTypeSafeKey, APIFormat: "systemone", Models: []string{"jev-latest", "jev-1.13.0"}, Enabled: true},
	}
	authStage, probe := &keyAuth{}, &holdProbe{}
	g := newManagedTestGatewayWith(t, orgCfg, managedGatewayOptions{
		extraPlugins: []pipeline.Plugin{authStage, probe},
		configure: func(cfg *config.Config) {
			off := false
			cfg.Providers["typesafe"] = config.ProviderConfig{APIFormat: "systemone", BaseURL: vendor.URL, APIKey: fagiHeldKey,
				Models: []string{"jev-latest", "jev-1.13.0"}, AutoDiscover: &off, Local: &off}
			cfg.Auth.Keys = append(cfg.Auth.Keys, config.AuthKeyConfig{Name: "managed", Key: decisionKey, Owner: "user",
				KeyType: "managed", CreditBalance: 5, Metadata: map[string]string{"org_id": "org-m"}})
		},
	})
	authStage.p = authplugin.New(g.srv.handlers.keyStore, true)
	g.ledger.SetMicros("org-m", auth.USDToMicros(1))
	if !g.srv.handlers.registry.IsManaged("openrouter") || g.credits.OrgLedger() == nil {
		t.Fatal("setup: the gateway has no managed provider or no org ledger; the test proves nothing")
	}
	var key *auth.APIKey
	for _, k := range g.srv.handlers.keyStore.List() {
		if k.KeyType == "managed" {
			key = k
		}
	}
	if key == nil {
		t.Fatal("setup: no managed key")
	}
	balance := key.BalanceUSD()
	headers := map[string]string{"Authorization": "Bearer " + decisionKey}

	var decisions []string
	for i, path := range []string{"/v1/decisions", "/v1/systemone"} {
		w := dxRequest(g.srv, http.MethodPost, path, decisionSysBody, headers)
		if w.Code != http.StatusOK {
			t.Fatalf("%s: HTTP %d %s", path, w.Code, w.Body.String())
		}
		if n := vendor.callsWith("Bearer " + orgTypeSafeKey); n != i+1 || vendor.calls() != i+1 {
			t.Fatalf("%s: vendor calls %v, want %d, all with the organization's own key", path, vendor.authorizations(), i+1)
		}
		seen := probe.last(t)
		decisions = append(decisions, seen.requestID)
		if seen.endpoint != "decision" || seen.billingMode == billingManaged || seen.held {
			t.Errorf("%s: endpoint %q billing_mode %q held %v; a decision is never billed to the org's managed credit", path, seen.endpoint, seen.billingMode, seen.held)
		}
		if a := g.account(t, "org-m"); a.HeldMicros != 0 || a.SpentMicros != 0 {
			t.Errorf("%s: the org's managed credit holds %d and spent %d micros on a decision", path, a.HeldMicros, a.SpentMicros)
		}
		if recs := g.outbox("org-m"); len(recs) != 0 {
			t.Errorf("%s: the org's ledger wrote %d settlement records for a decision: %v", path, len(recs), recs)
		}
		if got := key.BalanceUSD(); got != balance {
			t.Errorf("%s: the managed key's balance went from %f to %f on a decision", path, balance, got)
		}
	}

	// The control: a chat call for the same organization and key, on Future
	// AGI's provider, is held and then settled on the organization's ledger.
	w := dxRequest(g.srv, http.MethodPost, "/v1/chat/completions", chatBody("anthropic/claude-sonnet-5.5"), headers)
	if w.Code != http.StatusOK {
		t.Fatalf("control chat: HTTP %d %s", w.Code, w.Body.String())
	}
	seen := probe.last(t)
	if seen.billingMode != billingManaged || !seen.held {
		t.Fatalf("control chat: billing_mode %q held %v; the ledger is not holding managed requests, so the test proves nothing", seen.billingMode, seen.held)
	}
	spent, held := settledAccount(t, g)
	recs := g.outbox("org-m")
	if held != 0 || spent <= 0 || len(recs) != 1 || recs[0]["request_id"] != seen.requestID {
		t.Fatalf("control chat: spent %d held %d, records %v; want one settlement for request %s", spent, held, recs, seen.requestID)
	}
	for _, r := range recs {
		for _, id := range decisions {
			if id != "" && r["request_id"] == id {
				t.Errorf("a settlement record names decision request %s: %v", id, r)
			}
		}
	}
	if got := key.BalanceUSD(); got != balance {
		t.Errorf("control chat: settled to the org, and the managed key's balance also went from %f to %f", balance, got)
	}
}
