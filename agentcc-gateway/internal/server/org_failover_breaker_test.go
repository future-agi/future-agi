package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func orgFailoverConfig(legacy bool) *tenant.RoutingConfig {
	if legacy {
		return &tenant.RoutingConfig{FallbackEnabled: true}
	}
	return &tenant.RoutingConfig{Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2}}
}

// Exercise the real org config path: effectiveFailover creates a new failover
// configuration on every HTTP request, but breaker state must survive it.
func TestOrgConfiguredFailoverKeepsBreakers(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, globalEnabled := range []bool{false, true} {
			for _, breakerEnabled := range []bool{false, true} {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("legacy=%t/global=%t/breaker=%t/stream=%t", legacy, globalEnabled, breakerEnabled, stream), func(t *testing.T) {
						var cfg *config.Config
						srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusUnauthorized, func(c *config.Config) {
							c.Routing.Failover.Enabled = globalEnabled
							c.Routing.CircuitBreaker.Enabled = breakerEnabled
							c.Providers["backup"] = c.Providers["primary"]
							cfg = c
						}, "synthetic-org-a-key")
						for _, org := range []string{"org-a", "org-b"} {
							orgCfg := *srv.handlers.tenantStore.Get(org)
							orgCfg.Routing = orgFailoverConfig(legacy)
							orgCfg.Providers["backup"] = &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-" + org + "-backup-key", Models: []string{"different-model"}}
							srv.handlers.tenantStore.Set(org, &orgCfg)
						}
						for _, phase := range []string{"startup", "reload"} {
							t.Run(phase, func(t *testing.T) {
								if phase == "reload" {
									if err := srv.handlers.ReloadRouting(cfg, srv.handlers.registry); err != nil {
										t.Fatal(err)
									}
								}
								before := keyed.count("synthetic-org-a-key")
								statuses := map[int]int{}
								for i := 0; i < 12; i++ {
									status, _ := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", stream, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
									statuses[status]++
								}
								wantCalls := 6
								if breakerEnabled {
									wantCalls = 2
								}
								if got := keyed.count("synthetic-org-a-key") - before; got != wantCalls {
									t.Fatalf("org-configured failover called org-a's rejected key %d times (statuses %v); want %d with breaker enabled=%t", got, statuses, wantCalls, breakerEnabled)
								}
								if statuses[http.StatusUnauthorized] != wantCalls || statuses[http.StatusOK] != 12-wantCalls {
									t.Fatalf("org-a statuses %v; want %d x 401 then remaining requests 200", statuses, wantCalls)
								}
								if keyed.count("synthetic-org-a-backup-key") == 0 {
									t.Fatal("org-a's primary breaker stopped its other provider")
								}
								for _, probe := range []string{"synthetic-org-b-probe", "synthetic-operator-probe"} {
									for i := 0; i < 4; i++ {
										status, body := adaptiveHTTP(t, gateway, "adaptive-sample", probe, stream, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
										if status != http.StatusOK || !strings.Contains(body, "synthetic response") {
											t.Fatalf("unaffected request: status=%d body=%s", status, body)
										}
									}
								}
								if keyed.count("synthetic-org-b-key") == 0 || keyed.count("synthetic-operator-key") == 0 {
									t.Fatal("org-a's breaker stopped another org or operator traffic")
								}
								// Reload resets both registries, but never mixes org outcomes
								// into the operator's health or breaker.
								health := srv.handlers.healthMonitor.GetHealth("primary")
								if (breakerEnabled && health.CircuitState != "closed") || !health.Healthy || health.ErrorCount != 0 {
									t.Fatalf("org-a's failures changed shared health: %+v", health)
								}
							})
						}
					})
				}
			}
		}
	}
}

func TestOrgFailoverSharesGlobalBreakerState(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, tenantFirst := range []bool{false, true} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("legacy=%t/tenant-first=%t/stream=%t", legacy, tenantFirst, stream), func(t *testing.T) {
					srv, gateway, keyed := orgBreakerGatewayWithStatus(t, http.StatusUnauthorized, "synthetic-org-a-key")
					for phase, useTenant := range []bool{tenantFirst, !tenantFirst} {
						orgCfg := *srv.handlers.tenantStore.Get("org-a")
						orgCfg.Routing = nil
						if useTenant {
							orgCfg.Routing = orgFailoverConfig(legacy)
						}
						srv.handlers.tenantStore.Set("org-a", &orgCfg)
						for i := 0; i < 8; i++ {
							status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", stream, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
							if phase > 0 && (status != http.StatusOK || !strings.Contains(body, "synthetic response")) {
								t.Fatalf("switching failover config lost successful fallback: status=%d body=%s", status, body)
							}
						}
						if got := keyed.count("synthetic-org-a-key"); got != 2 {
							t.Fatalf("global and org failover did not share state: tenant-config=%t primary calls=%d, want 2 total", useTenant, got)
						}
					}
				})
			}
		}
	}
}

func TestOrgFailoverAllRefusedOverHTTP(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, legacy := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/legacy=%t", stream, legacy), func(t *testing.T) {
				srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusUnauthorized, func(cfg *config.Config) {
					// Keep two targets so requests use provider failover; both org keys
					// reach the same synthetic failing upstream.
					cfg.Providers["backup"] = cfg.Providers["primary"]
				}, "synthetic-org-a-key")
				orgCfg := *srv.handlers.tenantStore.Get("org-a")
				orgCfg.Providers["backup"] = orgCfg.Providers["primary"]
				orgCfg.Routing = orgFailoverConfig(legacy)
				srv.handlers.tenantStore.Set("org-a", &orgCfg)
				for i := 0; i < 12; i++ {
					status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", stream, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
					want := http.StatusUnauthorized
					if i >= 4 {
						want = http.StatusServiceUnavailable
					}
					if status != want || (i >= 4 && !strings.Contains(body, "all_providers_unavailable")) {
						t.Fatalf("request %d: status=%d body=%s, want %d with clear unavailable error after breaker opens", i, status, body, want)
					}
				}
				if got := keyed.count("synthetic-org-a-key"); got != 4 {
					t.Fatalf("refused requests reached upstream: %d calls, want 4 (2 per provider)", got)
				}
			})
		}
	}
}
