package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestR602RouterOfferInventory(t *testing.T) {
	g, _, all := r601Gateway(t)
	offered := map[string]bool{}
	for _, m := range g.srv.handlers.managedModelObjects(managedOptIn()) {
		offered[m.ID] = true
	}
	for _, id := range r602Routers {
		if offered[id] {
			t.Errorf("deferred router advertised on credits: %s", id)
		}
	}
	ids := r601IDs(all)
	count := 0
	for _, id := range ids {
		if id == "google/gemini-2.5-flash-image" {
			continue
		}
		count++
		if !offered[id] {
			t.Errorf("qualified opt-in listing absent %s", id)
		}
	}
	if count != 28 || offered["openrouter/fusion"] {
		t.Fatalf("count=%d fusion=%t", count, offered["openrouter/fusion"])
	}
	for _, row := range buildCatalogAccess(g.srv.handlers.catalog.Load(), managedOptIn(), 5, g.srv.handlers.catalog.Now(), g.srv.handlers).Data {
		if strings.HasPrefix(row.ID, "google/lyria-3-") && (row.Pricing != nil || row.GenerationPricing == nil) {
			t.Fatalf("access view lacks generation unit: %s", row.ID)
		}
	}
}

func TestR602RouterBYOKEverySurface(t *testing.T) {
	for _, id := range r602Routers {
		for _, surface := range managedSurfaces {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/%t", id, surface, stream), func(t *testing.T) {
					g, l, _ := r601Gateway(t)
					g.tenants.Set("org-m", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"openrouter": {APIKey: "synthetic-user-key", APIFormat: "openai", Models: []string{id}, Enabled: true}}})
					answer := r601Response("0.0000095", stream)
					g.managed.responseWith.Store(&answer)
					g.managed.streamWith.Store(&answer)
					body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, id, stream)
					if code := sendBillabilityBody(t, g, surface, body); code != 200 || len(l.Outbox("org-m")) != 0 {
						t.Fatalf("BYOK status=%d or credit entry", code)
					}
				})
			}
		}
	}
}

func TestR602FusionSpecificRefusal(t *testing.T) {
	for _, surface := range managedSurfaces {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/%t", surface, stream), func(t *testing.T) {
				g, l, _ := r601Gateway(t)
				body := fmt.Sprintf(`{"model":"openrouter/fusion","max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"hi"}]}`, stream)
				req := httptest.NewRequest(http.MethodPost, surface, strings.NewReader(body))
				req.Header.Set("Authorization", "Bearer "+managedOrgKey)
				req.Header.Set("Content-Type", "application/json")
				w := httptest.NewRecorder()
				g.srv.httpServer.Handler.ServeHTTP(w, req)
				if w.Code != 403 || !strings.Contains(w.Body.String(), "child invocations and search/fetch charges") || g.managed.calls() != 0 || len(l.Outbox("org-m")) != 0 {
					t.Fatalf("Fusion refusal changed: %d %s", w.Code, w.Body)
				}
			})
		}
	}
}
