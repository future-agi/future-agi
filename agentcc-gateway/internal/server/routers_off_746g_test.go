package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/catalog"
	creditsplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	"github.com/futureagi/agentcc-gateway/internal/routerbilling"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const routerOffCopy = "Router models (automatic model selection) aren't available with Future AGI credits yet. Choose a specific model."

// Observe the methods the credits plugin actually calls, not just the final
// balance: reserving and then voiding must not count as pre-hold refusal.
type routerOffLedger struct {
	*billing.MemoryLedger
	reserves atomic.Int64
}

func (l *routerOffLedger) Reserve(ctx context.Context, org, hold string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	l.reserves.Add(1)
	return l.MemoryLedger.Reserve(ctx, org, hold, micros, ttl, mode)
}
func (l *routerOffLedger) ReserveInclusive(ctx context.Context, org, req, attempt, model string, micros int64, ttl time.Duration, mode billing.ReserveMode) (billing.ReserveResult, error) {
	l.reserves.Add(1)
	return l.MemoryLedger.ReserveInclusive(ctx, org, req, attempt, model, micros, ttl, mode)
}
func (l *routerOffLedger) ReserveOwned(ctx context.Context, org, req, attempt, model string, micros int64, ttl time.Duration, mode billing.ReserveMode, token bool, rec billing.Settlement) (billing.ReserveResult, error) {
	l.reserves.Add(1)
	return l.MemoryLedger.ReserveOwned(ctx, org, req, attempt, model, micros, ttl, mode, token, rec)
}
func routerOffGateway(t *testing.T, enabled bool) (*managedTestGateway, *routerOffLedger) {
	t.Helper()
	g, owner, _ := cat2OperatorGateway(t, enabled)
	l := &routerOffLedger{MemoryLedger: owner}
	g.ledger = creditsplugin.NewOrgLedger(l)
	g.credits.SetOrgLedger(g.ledger, true)
	g.srv.managed.Ledger = g.ledger
	g.srv.handlers.orgLedger = g.ledger
	return g, l
}
func routerOffRequest(t *testing.T, g *managedTestGateway, surface, body string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodPost, surface, strings.NewReader(body))
	r.Header.Set("Authorization", "Bearer "+managedOrgKey)
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	g.srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

func TestRoutersOff746GHTTP(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, id := range r602Routers {
			if !routerbilling.FamilyMember(id) {
				t.Fatalf("not authoritative: %s", id)
			}
			for _, surface := range managedSurfaces {
				for _, stream := range []bool{false, true} {
					for _, prefix := range []string{"", "openrouter/"} {
						t.Run(fmt.Sprintf("on=%t/%s/%s/stream=%t/%s", enabled, id, surface, stream, prefix), func(t *testing.T) {
							g, l := routerOffGateway(t, enabled)
							body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"synthetic"}]}`, prefix+id, stream)
							w := routerOffRequest(t, g, surface, body)
							acct, _ := l.Account(context.Background(), "org-m")
							var refusal struct {
								Error struct{ Type, Code, Message string }
							}
							decodeErr := json.Unmarshal(w.Body.Bytes(), &refusal)
							typed := refusal.Error.Code == "managed_router_unbounded"
							if surface == "/v1/messages" {
								// Messages preserves its existing Anthropic error envelope,
								// whose discriminator is type, not an OpenAI code field.
								typed = refusal.Error.Type == "permission_error" && refusal.Error.Code == ""
							}
							if decodeErr != nil || !typed || refusal.Error.Message != routerOffCopy || w.Code != 403 || g.managed.calls() != 0 || l.reserves.Load() != 0 || acct.HeldMicros != 0 || acct.SpentMicros != 0 || len(l.Outbox("org-m")) != 0 {
								t.Fatalf("status=%d calls=%d reserves=%d account=%+v body=%s", w.Code, g.managed.calls(), l.reserves.Load(), acct, w.Body)
							}
						})
					}
				}
			}
		}
	}
}

func TestRoutersOff746GViews(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		g, _ := routerOffGateway(t, enabled)
		cat := g.srv.handlers.catalog.Load()
		for _, row := range buildCatalogAccess(cat, managedOptIn(), 5, g.srv.handlers.catalog.Now(), g.srv.handlers).Data {
			if routerbilling.FamilyMember(row.ID) && (row.Managed || row.ManagedAvailableOnOptIn) {
				t.Errorf("on=%t access offers %s", enabled, row.ID)
			}
		}
		r := httptest.NewRequest(http.MethodGet, "/v1/models", nil)
		r.Header.Set("Authorization", "Bearer "+managedOrgKey)
		w := httptest.NewRecorder()
		g.srv.httpServer.Handler.ServeHTTP(w, r)
		var list struct {
			Data []struct {
				ID string `json:"id"`
			} `json:"data"`
		}
		if w.Code != 200 || json.Unmarshal(w.Body.Bytes(), &list) != nil {
			t.Fatal("invalid model list")
		}
		for _, row := range list.Data {
			if routerbilling.FamilyMember(row.ID) {
				t.Errorf("on=%t models offers %s", enabled, row.ID)
			}
		}
		for _, raw := range cat2AdminRead(t, g, "/-/catalog")["models"].([]any) {
			row := raw.(map[string]any)
			if !routerbilling.FamilyMember(row["id"].(string)) {
				continue
			}
			if row["state"] != catalog.StateBlocked {
				t.Errorf("on=%t admin offers %s", enabled, row["id"])
			}
			for _, field := range []string{"inclusive_price", "prices", "input_per_token", "billed_input_per_mtok"} {
				if _, ok := row[field]; ok {
					t.Errorf("blocked router exposes %s", field)
				}
			}
		}
		for _, id := range r602Routers {
			if _, ok := cat.Get("openrouter", id); !ok {
				t.Errorf("BYOK identity removed: %s", id)
			}
			doc := cat2AdminRead(t, g, "/-/catalog/endpoints?model="+id)["data"].(map[string]any)
			if len(doc["endpoints"].([]any)) != 0 {
				t.Errorf("router endpoint prices exposed: %s", id)
			}
		}
		t.Logf("CAT2=%t credits model list=%d", enabled, len(list.Data))
	}
}

func TestRoutersOff746GFallback(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		for _, id := range r602Routers {
			for _, configured := range []bool{false, true} {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("on=%t/%s/configured=%t/stream=%t", enabled, id, configured, stream), func(t *testing.T) {
						g, l := routerOffGateway(t, enabled)
						primary := "openai/o1-pro"
						extra := fmt.Sprintf(`,"models":[%q]`, id)
						if configured {
							cfg := managedOptIn()
							cfg.Routing = &tenant.RoutingConfig{FallbackEnabled: true, Retry: &tenant.RetryConfig{Enabled: false}, ModelFallbacks: map[string][]string{primary: {id}}}
							g.tenants.Set("org-m", cfg)
							extra = ""
						}
						g.managed.failFor.Store(primary, 500)
						w := routerOffRequest(t, g, managedSurfaces[0], fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]%s}`, primary, stream, extra))
						acct, _ := l.Account(context.Background(), "org-m")
						if w.Code < 400 || g.managed.calls() > 1 || (g.managed.calls() == 1 && g.managed.lastModel() != primary) || l.reserves.Load() > 1 || acct.SpentMicros != 0 || acct.HeldMicros != 0 {
							t.Fatalf("router fallback ran: status=%d calls=%d model=%s reserves=%d acct=%+v", w.Code, g.managed.calls(), g.managed.lastModel(), l.reserves.Load(), acct)
						}
					})
				}
			}
		}
	}
}

func TestRoutersOff746GControls(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		g, l := routerOffGateway(t, enabled)
		w := routerOffRequest(t, g, managedSurfaces[0], `{"model":"openai/o1-pro","max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`)
		acct, _ := l.Account(context.Background(), "org-m")
		if w.Code != 200 || g.managed.calls() != 1 || l.reserves.Load() == 0 || acct.SpentMicros != 7350 || acct.HeldMicros != 0 {
			t.Fatalf("fixed credits control failed: %d calls=%d reserves=%d acct=%+v", w.Code, g.managed.calls(), l.reserves.Load(), acct)
		}
		for _, id := range r602Routers {
			g, l := routerOffGateway(t, enabled)
			cfg := managedOptIn()
			cfg.Providers = map[string]*tenant.ProviderConfig{"openrouter": {APIKey: "synthetic-user-key", APIFormat: "openai", Models: []string{id}, Enabled: true}}
			g.tenants.Set("org-m", cfg)
			w := routerOffRequest(t, g, managedSurfaces[0], fmt.Sprintf(`{"model":%q,"max_tokens":64,"messages":[{"role":"user","content":"hi"}]}`, id))
			acct, _ := l.Account(context.Background(), "org-m")
			if w.Code != 200 || w.Header().Get("x-agentcc-billing-mode") != "byok" || g.managed.calls() != 1 || l.reserves.Load() != 0 || acct.SpentMicros != 0 || acct.HeldMicros != 0 || len(l.Outbox("org-m")) != 0 || strings.Contains(w.Body.String(), "0.00735") {
				t.Fatalf("BYOK control failed: %d calls=%d acct=%+v body=%s", w.Code, g.managed.calls(), acct, w.Body)
			}
			if len(g.managed.auths) != 1 || g.managed.auths[0] != "Bearer synthetic-user-key" {
				t.Fatal("BYOK used wrong credential")
			}
		}
	}
}
