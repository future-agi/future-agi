package server

import (
	"encoding/json"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestProbeAliasTenantFallbackDuringCooldown(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, chain := range []string{"global", "org"} {
			t.Run(fmt.Sprintf("stream=%t/%s", stream, chain), func(t *testing.T) {
				var primaryCalls, fallbackCalls, wrongCalls atomic.Int32
				primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost {
						return
					}
					primaryCalls.Add(1)
					w.WriteHeader(http.StatusServiceUnavailable)
					fmt.Fprint(w, `{"error":{"message":"outage"}}`)
				}))
				t.Cleanup(primary.Close)
				fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost {
						return
					}
					var request struct {
						Model string `json:"model"`
					}
					if err := json.NewDecoder(r.Body).Decode(&request); err != nil || request.Model != "tenant-fallback" || r.Header.Get("Authorization") != "Bearer synthetic-tenant" {
						wrongCalls.Add(1)
						w.WriteHeader(http.StatusBadRequest)
						return
					}
					fallbackCalls.Add(1)
					writeProbeResponse(w, stream, "tenant-recovered")
				}))
				t.Cleanup(fallback.Close)

				cfg := probeConfig(primary.URL)
				cfg.Routing.CircuitBreaker.Cooldown = time.Hour
				// Neither the alias nor the tenant fallback is in the operator catalog.
				// Two operator providers prevent the registry's only-provider default.
				cfg.Providers["backup"] = config.ProviderConfig{BaseURL: fallback.URL, APIFormat: "openai", Models: []string{"operator-backup"}}
				cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
					"routing-alias": {{Provider: "probe", ModelOverride: "primary"}},
				}
				fallbacks := map[string][]string{"routing-alias": {"tenant-fallback"}}
				orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
					"backup": {Enabled: true, APIKey: "synthetic-tenant", Models: []string{"tenant-fallback"}},
				}}
				if chain == "org" {
					orgCfg.Routing = &tenant.RoutingConfig{ModelFallbacks: fallbacks}
				} else {
					cfg.Routing.ModelFallbacks = fallbacks
				}
				cfg.Auth.Enabled = true
				cfg.Auth.Keys = []config.AuthKeyConfig{
					{Name: "internal", Key: "synthetic-internal", KeyType: "internal", Metadata: map[string]string{"org_id": "probe-org"}},
					{Name: "user", Key: "synthetic-user", KeyType: "user", Metadata: map[string]string{"org_id": "probe-org"}},
				}
				tenants := tenant.NewStore()
				tenants.Set("probe-org", orgCfg)
				srv, gateway := newProbeServer(t, cfg, tenants)

				for request := 1; request <= 2; request++ {
					status, body := probeRequest(t, gateway, "routing-alias", "synthetic-internal", stream)
					if status != http.StatusOK || !strings.Contains(body, "tenant-recovered") || (stream && !strings.Contains(body, "[DONE]")) {
						t.Fatalf("request %d: status=%d tenant_content=%t, want tenant fallback during cooldown", request, status, strings.Contains(body, "tenant-recovered"))
					}
					if primaryCalls.Load() != 1 || fallbackCalls.Load() != int32(request) || wrongCalls.Load() != 0 {
						t.Fatalf("request %d: primary=%d fallback=%d wrong=%d, want 1/%d/0", request, primaryCalls.Load(), fallbackCalls.Load(), wrongCalls.Load(), request)
					}
				}
				if cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("probe"); cb.State() != routing.StateOpen {
					t.Fatalf("primary breaker=%v, want open throughout cooldown", cb.State())
				}

				// Initial fallback resolution must not bypass the operator-credential guard.
				if status, _ := probeRequest(t, gateway, "routing-alias", "synthetic-user", stream); status != http.StatusForbidden {
					t.Errorf("user key resolved an operator-only alias: status=%d", status)
				}

				// A resolved tenant fallback still requires admission at the call boundary.
				_, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("probe-org", "backup", "synthetic-tenant", orgCfg.Providers["backup"])
				if err != nil {
					t.Fatal(err)
				}
				cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().GetScoped("probe-org", "backup")
				generation, allowed := cb.Allow()
				if !allowed {
					t.Fatal("healthy tenant fallback refused admission")
				}
				cb.RecordFailure(generation, models.ErrUpstreamProvider(503, "synthetic upstream failure"))
				status, body := probeRequest(t, gateway, "routing-alias", "synthetic-internal", stream)
				if status != http.StatusServiceUnavailable || !strings.Contains(body, "circuit_open") {
					t.Errorf("open tenant fallback bypassed admission: status=%d circuit_open=%t", status, strings.Contains(body, "circuit_open"))
				}
				if primaryCalls.Load() != 1 || fallbackCalls.Load() != 2 || wrongCalls.Load() != 0 {
					t.Errorf("refused requests reached upstream: primary=%d fallback=%d wrong=%d", primaryCalls.Load(), fallbackCalls.Load(), wrongCalls.Load())
				}
			})
		}
	}
}
