package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Exercise the real org config path: effectiveFailover creates a new failover
// configuration on every HTTP request, but breaker state must survive it.
func TestOrgConfiguredFailoverKeepsBreakers(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, globalEnabled := range []bool{false, true} {
			for _, reload := range []bool{false, true} {
				t.Run(fmt.Sprintf("legacy=%t/global=%t/reload=%t", legacy, globalEnabled, reload), func(t *testing.T) {
					var cfg *config.Config
					srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusUnauthorized, func(c *config.Config) {
						c.Routing.Failover.Enabled = globalEnabled
						cfg = c
					}, "synthetic-org-a-key")
					if reload {
						if err := srv.handlers.ReloadRouting(cfg, srv.handlers.registry); err != nil {
							t.Fatal(err)
						}
					}
					for _, org := range []string{"org-a", "org-b"} {
						orgCfg := *srv.handlers.tenantStore.Get(org)
						orgCfg.Routing = &tenant.RoutingConfig{FallbackEnabled: legacy}
						if !legacy {
							orgCfg.Routing.Failover = &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2}
						}
						srv.handlers.tenantStore.Set(org, &orgCfg)
					}
					statuses := map[int]int{}
					for i := 0; i < 12; i++ {
						status, _ := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", false, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
						statuses[status]++
					}
					if got := keyed.count("synthetic-org-a-key"); got != 2 {
						t.Fatalf("org-configured failover called org-a's rejected key %d times (statuses %v); want 2 before its persistent breaker opens", got, statuses)
					}
					if statuses[http.StatusUnauthorized] != 2 || statuses[http.StatusOK] != 10 {
						t.Fatalf("org-a statuses %v; want 2 x 401 then 10 x 200", statuses)
					}
					sendOrgRequests(t, gateway, "synthetic-org-b-probe", 12)
					if keyed.count("synthetic-org-b-key") == 0 {
						t.Fatal("org-a's breaker stopped org-b's key")
					}
					sendOrgRequests(t, gateway, "synthetic-operator-probe", 4)
					if keyed.count("synthetic-operator-key") == 0 {
						t.Fatal("org-a's breaker stopped operator traffic")
					}
					health := srv.handlers.healthMonitor.GetHealth("primary")
					if health.CircuitState != "closed" || !health.Healthy || health.ErrorCount != 0 {
						t.Fatalf("org-a's failures changed shared health: %+v", health)
					}
				})
			}
		}
	}
}

func TestOrgFailoverAllRefusedOverHTTP(t *testing.T) {
	srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusUnauthorized, func(cfg *config.Config) {
		cfg.Routing.Targets["adaptive-sample"] = []config.RoutingTargetConfig{{Provider: "primary"}}
	}, "synthetic-org-a-key")
	orgCfg := *srv.handlers.tenantStore.Get("org-a")
	orgCfg.Routing = &tenant.RoutingConfig{Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2}}
	srv.handlers.tenantStore.Set("org-a", &orgCfg)
	for i := 0; i < 12; i++ {
		status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", false, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
		want := http.StatusUnauthorized
		if i >= 2 {
			want = http.StatusServiceUnavailable
		}
		if status != want || (i >= 2 && !strings.Contains(body, "all_providers_unavailable")) {
			t.Fatalf("request %d: status=%d body=%s, want %d with clear unavailable error after breaker opens", i, status, body, want)
		}
	}
	if got := keyed.count("synthetic-org-a-key"); got != 2 {
		t.Fatalf("refused requests reached upstream: %d calls, want 2", got)
	}
}
