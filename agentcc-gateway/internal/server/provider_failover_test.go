package server

import (
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestAdaptiveOperatorBreakerWithTenantProviders(t *testing.T) {
	for _, strategy := range []string{"adaptive", "round-robin"} {
		for _, stream := range []bool{false, true} {
			for _, entry := range []string{"unrelated-disabled", "unrelated-enabled", "same-disabled", "same-no-credentials", "internal-ignores-override"} {
				t.Run(fmt.Sprintf("%s/stream=%t/%s", strategy, stream, entry), func(t *testing.T) {
					var fastCalls, slowCalls atomic.Int64
					fast := adaptiveUpstream(t, false, time.Millisecond, &fastCalls)
					slow := adaptiveUpstream(t, true, time.Millisecond, &slowCalls)
					cfg := config.DefaultConfig()
					cfg.Providers = map[string]config.ProviderConfig{
						"fast": {BaseURL: fast.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
						"slow": {BaseURL: slow.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
					}
					cfg.Routing = adaptiveRoutingConfig()
					cfg.Routing.DefaultStrategy = strategy
					cfg.Routing.Adaptive.UpdateInterval = time.Hour
					cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
					cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2}
					cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}
					keyType := "byok"
					if entry == "internal-ignores-override" {
						keyType = "internal"
					}
					cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic-breaker", Key: "synthetic-breaker-probe", KeyType: keyType, Metadata: map[string]string{"org_id": "synthetic-org"}}}
					srv, gateway := adaptiveServer(t, cfg)
					id := "unrelated"
					pcfg := &tenant.ProviderConfig{Enabled: false, APIKey: "synthetic-provider-key", Models: []string{"different-model"}}
					switch entry {
					case "unrelated-enabled":
						pcfg.Enabled = true
					case "same-disabled":
						id = "slow"
					case "same-no-credentials":
						id, pcfg.Enabled, pcfg.APIKey = "slow", true, ""
					case "internal-ignores-override":
						id, pcfg.Enabled = "slow", true
					}
					store := tenant.NewStore()
					store.Set("synthetic-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{id: pcfg}})
					orgCache := providers.NewOrgProviderCache(cfg.Providers, false)
					t.Cleanup(orgCache.EvictAll)
					srv.handlers.tenantStore, srv.handlers.orgProviderCache = store, orgCache
					srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
					for i := 0; i < 4; i++ {
						status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-breaker-probe", stream, http.Header{"X-Agentcc-Provider-Lock": {"fast"}})
						if status != http.StatusOK || !strings.Contains(body, "synthetic response") {
							t.Fatalf("operator failover response: status=%d missing_content=%t", status, !strings.Contains(body, "synthetic response"))
						}
					}
					health := srv.handlers.healthMonitor.GetHealth("slow")
					if slowCalls.Load() != 1 || health.ErrorCount != 1 || health.CircuitState != "open" || health.Healthy {
						t.Fatalf("operator breaker bypassed by tenant config: calls=%d errors=%d circuit=%s healthy=%t", slowCalls.Load(), health.ErrorCount, health.CircuitState, health.Healthy)
					}
					if fastCalls.Load() != 4 {
						t.Fatalf("successful operator attempts=%d want=4", fastCalls.Load())
					}
				})
			}
		}
	}
}

func TestAdaptiveMixedOperatorOrgBreakers(t *testing.T) {
	for _, strategy := range []string{"adaptive", "round-robin"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", strategy, stream), func(t *testing.T) {
				var globalFastCalls, globalSlowCalls, orgCalls atomic.Int64
				fast := adaptiveUpstream(t, false, time.Millisecond, &globalFastCalls)
				slow := adaptiveUpstream(t, true, time.Millisecond, &globalSlowCalls)
				org := adaptiveUpstream(t, true, time.Millisecond, &orgCalls)
				cfg := config.DefaultConfig()
				cfg.Providers = map[string]config.ProviderConfig{
					"fast": {BaseURL: fast.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
					"slow": {BaseURL: slow.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
				}
				cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic-mixed", Key: "synthetic-mixed-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "synthetic-org"}}}
				cfg.Routing = adaptiveRoutingConfig()
				cfg.Routing.DefaultStrategy = strategy
				cfg.Routing.Adaptive.UpdateInterval = time.Hour
				cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
				cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2}
				cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}
				srv, gateway := adaptiveServer(t, cfg)
				store := tenant.NewStore()
				store.Set("synthetic-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
					"fast": {Enabled: true, APIKey: "synthetic-provider-key", Models: []string{"different-model"}},
				}})
				orgCache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{
					"fast": {BaseURL: org.URL, APIFormat: "openai", Models: []string{"different-model"}},
				}, false)
				t.Cleanup(orgCache.EvictAll)
				srv.handlers.tenantStore, srv.handlers.orgProviderCache = store, orgCache
				srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
				for i := 0; i < 3; i++ {
					status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-mixed-probe", stream, http.Header{"X-Agentcc-Provider-Lock": {"fast"}})
					if i == 0 && (status != http.StatusBadGateway || !strings.Contains(body, "synthetic upstream unavailable")) {
						t.Fatalf("mixed failover response: status=%d missing_error=%t", status, !strings.Contains(body, "synthetic upstream unavailable"))
					}
					// Both breakers are open now: the org's for its own fast key
					// and the operator's for slow. Nothing is called.
					if i > 0 && status != http.StatusServiceUnavailable {
						t.Fatalf("request %d with every breaker open: status=%d", i, status)
					}
				}
				globalHealth := srv.handlers.healthMonitor.GetHealth("slow")
				if globalSlowCalls.Load() != 1 || globalHealth.ErrorCount != 1 || globalHealth.CircuitState != "open" {
					t.Fatalf("mixed attempt lost operator breaker: calls=%d errors=%d circuit=%s", globalSlowCalls.Load(), globalHealth.ErrorCount, globalHealth.CircuitState)
				}
				// The org's own key failed once, which opens its own breaker
				// (threshold 1), so later requests skip it instead of burning
				// an attempt. The operator's fast stays untouched.
				orgHealth := srv.handlers.healthMonitor.GetHealth("fast")
				if orgCalls.Load() != 1 || globalFastCalls.Load() != 0 || orgHealth.RequestCount != 0 || orgHealth.CircuitState != "closed" {
					t.Fatalf("org attempt contaminated operator state: org_calls=%d global_calls=%d samples=%d circuit=%s", orgCalls.Load(), globalFastCalls.Load(), orgHealth.RequestCount, orgHealth.CircuitState)
				}
			})
		}
	}
}

// An org override instance has its own registry ID (<provider>_org_<org>).
// The response header, request logs, metrics and price lookups must still
// name the routing target, in streaming and non-streaming alike.
func TestAdaptiveOrgFailoverReportsRoutingProvider(t *testing.T) {
	for _, strategy := range []string{"adaptive", "round-robin"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", strategy, stream), func(t *testing.T) {
				var globalFastCalls, globalSlowCalls, orgCalls atomic.Int64
				fast := adaptiveUpstream(t, false, time.Millisecond, &globalFastCalls)
				slow := adaptiveUpstream(t, true, time.Millisecond, &globalSlowCalls)
				org := adaptiveUpstream(t, false, time.Millisecond, &orgCalls)
				cfg := config.DefaultConfig()
				cfg.Providers = map[string]config.ProviderConfig{
					"fast": {BaseURL: fast.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
					"slow": {BaseURL: slow.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
				}
				cfg.Auth.Keys = []config.AuthKeyConfig{
					{Name: "synthetic-org", Key: "synthetic-org-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "synthetic-org"}},
					{Name: "synthetic-global", Key: "synthetic-global-probe", KeyType: "internal"},
				}
				cfg.Routing = adaptiveRoutingConfig()
				cfg.Routing.DefaultStrategy = strategy
				cfg.Routing.Adaptive.UpdateInterval = time.Hour
				cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
				cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2}
				cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}
				srv, gateway := adaptiveServer(t, cfg)
				store := tenant.NewStore()
				store.Set("synthetic-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
					"fast": {Enabled: true, APIKey: "synthetic-provider-key", Models: []string{"different-model"}},
				}})
				orgCache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{
					"fast": {BaseURL: org.URL, APIFormat: "openai", Models: []string{"different-model"}},
				}, false)
				t.Cleanup(orgCache.EvictAll)
				srv.handlers.tenantStore, srv.handlers.orgProviderCache = store, orgCache
				srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
				// One operator sample on fast, so "unchanged" has something to compare.
				if status, _ := adaptiveHTTP(t, gateway, "fast/adaptive-sample", "synthetic-global-probe", false); status != http.StatusOK {
					t.Fatalf("operator priming: status=%d", status)
				}
				before := srv.handlers.healthMonitor.GetHealth("fast")
				if globalFastCalls.Load() != 1 || before.RequestCount != 1 || before.ErrorCount != 0 {
					t.Fatalf("operator priming: calls=%d samples=%d errors=%d", globalFastCalls.Load(), before.RequestCount, before.ErrorCount)
				}
				slowBefore := globalSlowCalls.Load()
				for i := 0; i < 4; i++ {
					status, body, header := adaptiveHTTPResponse(t, gateway, "adaptive-sample", "synthetic-org-probe", stream, http.Header{"X-Agentcc-Provider-Lock": {"fast"}})
					if status != http.StatusOK || !strings.Contains(body, "synthetic response") {
						t.Fatalf("org failover response: status=%d missing_content=%t", status, !strings.Contains(body, "synthetic response"))
					}
					if got := header.Get("x-agentcc-provider"); got != "fast" {
						t.Fatalf("org instance reported as x-agentcc-provider=%q, want routing ID %q", got, "fast")
					}
				}
				after := srv.handlers.healthMonitor.GetHealth("fast")
				if orgCalls.Load() != 4 || globalFastCalls.Load() != 1 || after.RequestCount != before.RequestCount || after.ErrorCount != before.ErrorCount || after.LatencyEWMAMs != before.LatencyEWMAMs || after.CircuitState != before.CircuitState || after.Healthy != before.Healthy {
					t.Fatalf("org successes changed operator fast: org_calls=%d global_calls=%d samples=%d->%d errors=%d->%d latency=%d->%d circuit=%s->%s", orgCalls.Load(), globalFastCalls.Load(), before.RequestCount, after.RequestCount, before.ErrorCount, after.ErrorCount, before.LatencyEWMAMs, after.LatencyEWMAMs, before.CircuitState, after.CircuitState)
				}
				// Operator slow may be tried once; that failure opens its own breaker.
				slowCalls, slowHealth := globalSlowCalls.Load(), srv.handlers.healthMonitor.GetHealth("slow")
				if slowCalls > 1 || slowHealth.ErrorCount != slowCalls || (slowCalls == 1 && slowHealth.CircuitState != "open") {
					t.Fatalf("operator slow breaker: calls=%d (before org requests %d) errors=%d circuit=%s", slowCalls, slowBefore, slowHealth.ErrorCount, slowHealth.CircuitState)
				}
			})
		}
	}
}
