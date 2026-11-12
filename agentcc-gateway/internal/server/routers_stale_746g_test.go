package server

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestRoutersOff746GStaleHTTP(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, id := range r602Routers {
			for _, contract := range []string{catalog.RouterInclusiveContract, catalog.InclusiveFamilyContract} {
				t.Run(fmt.Sprintf("%t/%s/%s", enabled, id, contract), func(t *testing.T) {
					g, l := routerOffGateway(t, enabled)
					all, install := installBundledCatalogue(t)
					m := all[id]
					m.DynamicPrice = false
					m.Prices = catalog.Prices{Prompt: 1e-6, Completion: 2e-6}
					m.InclusiveEnabled = true
					m.Inclusive = &models.InclusivePrice{Model: id, Contract: contract, RouterUniverse: `[{"id":"synthetic/model","prompt":"0.000001","completion":"0.000002"}]`, RouterSnapshot: "old-snapshot", SnapshotModels: `["synthetic/model"]`, ContextLimit: 100, OutputLimit: 64, Prompt: "0.000001", Completion: "0.000002"}
					install(t, g)
					// A configured fallback cannot turn a denied primary into a
					// charged fixed-model request, either.
					body := fmt.Sprintf(`{"model":%q,"models":["openai/o1-pro"],"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, id)
					w := routerOffRequest(t, g, managedSurfaces[0], body)
					acct, _ := l.Account(context.Background(), "org-m")
					if w.Code != 403 || !strings.Contains(w.Body.String(), routerOffCopy) || g.managed.calls() != 0 || l.reserves.Load() != 0 || acct.SpentMicros != 0 || acct.HeldMicros != 0 || len(l.Outbox("org-m")) != 0 {
						t.Fatalf("old qualification bypass: %d calls=%d reserves=%d acct=%+v body=%s", w.Code, g.managed.calls(), l.reserves.Load(), acct, w.Body)
					}
				})
			}
		}
	}
}

func TestRoutersOff746GDetail(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, byok := range []bool{false, true} {
			g, _ := routerOffGateway(t, enabled)
			if byok {
				cfg := managedOptIn()
				cfg.Providers = map[string]*tenant.ProviderConfig{"openrouter": {APIKey: "synthetic-user-key", APIFormat: "openai", Models: r602Routers, Enabled: true}}
				g.tenants.Set("org-m", cfg)
			}
			for _, id := range append(append([]string(nil), r602Routers...), "openai/o1-pro") {
				r := httptest.NewRequest(http.MethodGet, "/v1/models/lookup?model="+url.QueryEscape(id), nil)
				r.Header.Set("Authorization", "Bearer "+managedOrgKey)
				w := httptest.NewRecorder()
				// GetModel consumes the decoded model parameter. Invoke the real
				// scoped handler: a placeholder URL path would overwrite it with
				// "lookup" in setPathParams and never test the intended identity.
				g.srv.handlers.GetModel(w, r)
				want := 404
				if byok || id == "openai/o1-pro" {
					want = 200
				}
				if w.Code != want {
					t.Errorf("detail on=%t byok=%t id=%s status=%d want=%d body=%s", enabled, byok, id, w.Code, want, w.Body)
				}
			}
		}
	}
}

func TestRoutersOff746GLockAndAuto(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, id := range r602Routers {
			for _, auto := range []bool{false, true} {
				t.Run(fmt.Sprintf("on=%t/%s/auto=%t", enabled, id, auto), func(t *testing.T) {
					g, l := routerOffGateway(t, enabled)
					named, headers := id, managedLock
					if auto {
						cfg := managedOptIn()
						cfg.Routing = &tenant.RoutingConfig{Auto: &tenant.AutoRoutingConfig{Candidates: []string{id}}}
						g.tenants.Set("org-m", cfg)
						named, headers = "auto", nil
					}
					w := g.chat(t, fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, named), headers)
					acct, _ := l.Account(context.Background(), "org-m")
					if w.Code < 400 || g.managed.calls() != 0 || l.reserves.Load() != 0 || acct.SpentMicros != 0 || acct.HeldMicros != 0 || len(l.Outbox("org-m")) != 0 {
						t.Fatalf("route bypass: status=%d calls=%d reserves=%d acct=%+v", w.Code, g.managed.calls(), l.reserves.Load(), acct)
					}
					if !auto && (w.Code != 403 || !strings.Contains(w.Body.String(), routerOffCopy)) {
						t.Fatalf("lock lost availability contract: %d %s", w.Code, w.Body)
					}
				})
			}
		}
	}
}
