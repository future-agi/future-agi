package server

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// orgFailoverConfig is an org's routing config for its own customer keys. On
// the line a customer (byok) key runs the org's own failover over the org's
// providers only, with the org's circuit breaker, never the operator's, and
// only when the org configured one: breaker turns the org's breaker on. The
// round-robin strategy spreads requests over the org's providers so each one
// is measured; legacy is the model fallback switch alone, which moves a
// request down its chain, not across providers.
func orgFailoverConfig(legacy, breaker bool) *tenant.RoutingConfig {
	r := &tenant.RoutingConfig{Strategy: "round_robin", Failover: &tenant.FailoverConfig{Enabled: true, MaxAttempts: 2}}
	if legacy {
		r = &tenant.RoutingConfig{Strategy: "round_robin", FallbackEnabled: true}
	}
	if breaker {
		r.CircuitBreaker = &tenant.CircuitBreakerConfig{Enabled: true, FailureThreshold: 2, Cooldown: "1h"}
	}
	return r
}

// orgOwnProviders gives org every hop as its own provider listing the model:
// primary with its own key and backup with its own backup key.
func orgOwnProviders(org string) map[string]*tenant.ProviderConfig {
	return map[string]*tenant.ProviderConfig{
		"primary": {Enabled: true, APIKey: "synthetic-" + org + "-key", Models: []string{"adaptive-sample"}},
		"backup":  {Enabled: true, APIKey: "synthetic-" + org + "-backup-key", Models: []string{"adaptive-sample"}},
	}
}

// Exercise the real org config path: an org's customer key runs the org's own
// failover over the org's own providers, and breaker state must survive the
// requests and a routing reload. The org's providers fail with a 5xx, which
// the org's breaker counts; a key rejected by its provider is answered, not
// routed around (TestOrgFailoverRejectedOwnKeyIsAnswered).
func TestOrgConfiguredFailoverKeepsBreakers(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, globalEnabled := range []bool{false, true} {
			for _, breakerEnabled := range []bool{false, true} {
				for _, stream := range []bool{false, true} {
					t.Run(fmt.Sprintf("legacy=%t/global=%t/breaker=%t/stream=%t", legacy, globalEnabled, breakerEnabled, stream), func(t *testing.T) {
						var cfg *config.Config
						srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusServiceUnavailable, func(c *config.Config) {
							c.Routing.Failover.Enabled = globalEnabled
							c.Routing.CircuitBreaker.Enabled = breakerEnabled
							c.Providers["backup"] = c.Providers["primary"]
							cfg = c
						}, "synthetic-org-a-key")
						for _, org := range []string{"org-a", "org-b"} {
							orgCfg := *srv.handlers.tenantStore.Get(org)
							orgCfg.Routing = orgFailoverConfig(legacy, breakerEnabled)
							orgCfg.Providers = orgOwnProviders(org)
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
								// No provider lock: a lock pins a customer key to that one
								// provider, so it would never reach the org's backup.
								for i := 0; i < 12; i++ {
									status, _ := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", stream)
									statuses[status]++
								}
								wantCalls := 6
								if breakerEnabled {
									wantCalls = 2
									if phase == "reload" {
										// The org's breaker is the org's own: a gateway routing
										// reload does not reset it, so the circuit opened at
										// startup still refuses org-a's failing key.
										wantCalls = 0
									}
								}
								if got := keyed.count("synthetic-org-a-key") - before; got != wantCalls {
									t.Fatalf("org-configured failover called org-a's failing key %d times (statuses %v); want %d with breaker enabled=%t", got, statuses, wantCalls, breakerEnabled)
								}
								// With provider failover a failing primary attempt moves on to
								// the org's backup in the same request; with the model fallback
								// switch alone it does not, and the request gets the provider's
								// error.
								wantFailed := 0
								if legacy {
									wantFailed = wantCalls
								}
								if statuses[http.StatusBadGateway] != wantFailed || statuses[http.StatusOK] != 12-wantFailed {
									t.Fatalf("org-a statuses %v; want %d x 502 and the remaining requests 200", statuses, wantFailed)
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
								if keyed.count("synthetic-org-a-key")+keyed.count("synthetic-org-a-backup-key") == 0 {
									t.Fatal("org-a's customer key never reached its own providers")
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

// An internal key with the org: on the line a customer (byok) key never fails
// over to the operator's providers, so the shared operator breaker is driven
// by a key that may reach them, with the org's routing config switched on and
// off underneath it. The operator's primary fails with a 5xx, which the
// operator's breaker counts.
func TestOrgFailoverSharesGlobalBreakerState(t *testing.T) {
	for _, legacy := range []bool{false, true} {
		for _, tenantFirst := range []bool{false, true} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("legacy=%t/tenant-first=%t/stream=%t", legacy, tenantFirst, stream), func(t *testing.T) {
					srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusServiceUnavailable, func(c *config.Config) {
						c.Auth.Keys = append(c.Auth.Keys, config.AuthKeyConfig{Name: "org-a-internal", Key: "synthetic-org-a-internal-probe", KeyType: "internal", Metadata: map[string]string{"org_id": "org-a"}})
					}, "synthetic-operator-key")
					for phase, useTenant := range []bool{tenantFirst, !tenantFirst} {
						orgCfg := *srv.handlers.tenantStore.Get("org-a")
						orgCfg.Routing = nil
						if useTenant {
							orgCfg.Routing = orgFailoverConfig(legacy, false)
						}
						srv.handlers.tenantStore.Set("org-a", &orgCfg)
						for i := 0; i < 8; i++ {
							status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-internal-probe", stream, http.Header{"X-Agentcc-Provider-Lock": {"primary"}})
							if phase > 0 && (status != http.StatusOK || !strings.Contains(body, "synthetic response")) {
								t.Fatalf("switching failover config lost successful fallback: status=%d body=%s", status, body)
							}
						}
						if got := keyed.count("synthetic-operator-key"); got != 2 {
							t.Fatalf("global and org failover did not share state: tenant-config=%t primary calls=%d, want 2 total", useTenant, got)
						}
					}
				})
			}
		}
	}
}

// A customer key whose org's own providers all fail gets the provider's
// error, then, with both of the org's breakers open, a clear unavailable
// error, and the operator's providers are never called.
func TestOrgFailoverAllRefusedOverHTTP(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, legacy := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/legacy=%t", stream, legacy), func(t *testing.T) {
				srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusServiceUnavailable, func(cfg *config.Config) {
					// The operator's backup reaches the same failing upstream with the
					// operator's key, so any operator call would be counted.
					cfg.Providers["backup"] = cfg.Providers["primary"]
				}, "synthetic-org-a-key")
				orgCfg := *srv.handlers.tenantStore.Get("org-a")
				// Every hop is the org's own provider, both on its failing key.
				orgCfg.Providers = map[string]*tenant.ProviderConfig{
					"primary": {Enabled: true, APIKey: "synthetic-org-a-key", Models: []string{"adaptive-sample"}},
					"backup":  {Enabled: true, APIKey: "synthetic-org-a-key", Models: []string{"adaptive-sample"}},
				}
				orgCfg.Routing = orgFailoverConfig(legacy, true)
				srv.handlers.tenantStore.Set("org-a", &orgCfg)
				// With provider failover each request tries both providers, so both
				// breakers (threshold 2) are open after two requests; with the model
				// fallback switch alone each request tries one, so after four.
				refusedFrom := 2
				if legacy {
					refusedFrom = 4
				}
				for i := 0; i < 12; i++ {
					status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", stream)
					want := http.StatusBadGateway
					if i >= refusedFrom {
						want = http.StatusServiceUnavailable
					}
					if status != want || (i >= refusedFrom && !strings.Contains(body, "all_providers_unavailable")) {
						t.Fatalf("request %d: status=%d body=%s, want %d with clear unavailable error after the breakers open", i, status, body, want)
					}
				}
				if got := keyed.count("synthetic-org-a-key"); got != 4 {
					t.Fatalf("refused requests reached upstream: %d calls, want 4 (2 per provider)", got)
				}
				if got := keyed.count("synthetic-operator-key"); got != 0 {
					t.Fatalf("a customer key reached the operator's providers: %d calls, want 0", got)
				}
			})
		}
	}
}

// A provider that rejects a customer's own key (401) is answered with that
// provider's error on every request: it is not routed around, and neither the
// org's breaker nor the operator's providers come into it.
func TestOrgFailoverRejectedOwnKeyIsAnswered(t *testing.T) {
	srv, gateway, keyed := orgBreakerGatewayWithConfig(t, http.StatusUnauthorized, func(cfg *config.Config) {
		cfg.Providers["backup"] = cfg.Providers["primary"]
	}, "synthetic-org-a-key", "synthetic-org-a-backup-key")
	orgCfg := *srv.handlers.tenantStore.Get("org-a")
	orgCfg.Providers = orgOwnProviders("org-a")
	orgCfg.Routing = orgFailoverConfig(false, true)
	srv.handlers.tenantStore.Set("org-a", &orgCfg)
	for i := 0; i < 6; i++ {
		status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-a-probe", false)
		if status != http.StatusBadGateway || !strings.Contains(body, "rejected the API key") {
			t.Fatalf("request %d: status=%d body=%s, want 502 with the provider's rejected-key error", i, status, body)
		}
	}
	if got := keyed.count("synthetic-operator-key"); got != 0 {
		t.Fatalf("a rejected customer key reached the operator's providers: %d calls, want 0", got)
	}
}
