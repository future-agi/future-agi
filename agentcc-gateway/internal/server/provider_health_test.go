package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type adaptiveOutputPolicy struct{}

func (adaptiveOutputPolicy) Name() string  { return "synthetic-output-policy" }
func (adaptiveOutputPolicy) Priority() int { return 50 }
func (adaptiveOutputPolicy) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (adaptiveOutputPolicy) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultError(models.ErrGuardrailBlocked("synthetic-output-policy", "synthetic refusal"))
}

func adaptiveHTTP(t *testing.T, gateway *httptest.Server, model, authorization string, stream bool, headers ...http.Header) (int, string) {
	t.Helper()
	status, body, _ := adaptiveHTTPResponse(t, gateway, model, authorization, stream, headers...)
	return status, body
}

// adaptiveHTTPResponse is adaptiveHTTP for tests that also check response headers.
func adaptiveHTTPResponse(t *testing.T, gateway *httptest.Server, model, authorization string, stream bool, headers ...http.Header) (int, string, http.Header) {
	t.Helper()
	body, err := json.Marshal(map[string]any{
		"model": model, "stream": stream,
		"messages": []map[string]string{{"role": "user", "content": "synthetic probe"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if len(headers) > 0 {
		req.Header = headers[0].Clone()
	}
	req.Header.Set("Content-Type", "application/json")
	if authorization != "" {
		req.Header.Set("Authorization", "Bearer "+authorization)
	}
	client := gateway.Client()
	client.Timeout = 5 * time.Second
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	t.Logf("synthetic request: status=%d cache=%s", resp.StatusCode, resp.Header.Get("x-agentcc-cache-status"))
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, string(data), resp.Header
}

func TestAdaptiveHealthExcludesPipelineOutcomes(t *testing.T) {
	for _, scenario := range []string{"local-rate-limit", "cache-hit", "output-policy"} {
		t.Run(scenario, func(t *testing.T) {
			var calls atomic.Int64
			upstream := adaptiveUpstream(t, false, time.Millisecond, &calls)
			cfg := config.DefaultConfig()
			cfg.Providers = map[string]config.ProviderConfig{
				"fast": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
			}
			cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
			var plugin pipeline.Plugin
			switch scenario {
			case "local-rate-limit":
				plugin = ratelimit.New(config.RateLimitConfig{Enabled: true, GlobalRPM: 1}, nil, nil)
			case "cache-hit":
				plugin = cacheplugin.NewFromStore(cache.NewStore(8), time.Minute)
			case "output-policy":
				plugin = adaptiveOutputPolicy{}
			}
			srv, gateway := adaptiveServer(t, cfg, plugin)
			key := ""
			if scenario == "cache-hit" {
				key = "synthetic-cache-probe"
				store := tenant.NewStore()
				store.Set("synthetic-cache-org", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
				srv.handlers.tenantStore = store
				srv.handlers.keyStore = auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
					{Name: "synthetic-cache", Key: key, KeyType: "internal", Metadata: map[string]string{"org_id": "synthetic-cache-org"}},
				}})
			}
			for i := 0; i < 2; i++ {
				status, body := adaptiveHTTP(t, gateway, "adaptive-sample", key, false)
				want := http.StatusOK
				if scenario == "local-rate-limit" && i == 1 {
					want = http.StatusTooManyRequests
				}
				if scenario == "output-policy" {
					want = models.ErrGuardrailBlocked("", "").Status
				}
				if status != want {
					t.Fatalf("status=%d want=%d", status, want)
				}
				if want == http.StatusOK && !strings.Contains(body, "synthetic response") {
					t.Fatal("missing upstream/cached content")
				}
			}
			wantCalls := int64(1)
			if scenario == "output-policy" {
				wantCalls = 2
			}
			health := srv.handlers.healthMonitor.GetHealth("fast")
			if calls.Load() != wantCalls || health.RequestCount != wantCalls || health.ErrorCount != 0 || health.SuccessRate != 1 {
				t.Fatalf("pipeline corrupted upstream health: calls=%d samples=%d errors=%d success=%v", calls.Load(), health.RequestCount, health.ErrorCount, health.SuccessRate)
			}
		})
	}
}

func TestAdaptiveGlobalHealthExcludesOrgInstances(t *testing.T) {
	for _, path := range []string{"org-first", "override", "failover-override"} {
		t.Run(path, func(t *testing.T) {
			var globalCalls, orgCalls atomic.Int64
			global := adaptiveUpstream(t, false, time.Millisecond, &globalCalls)
			org := adaptiveUpstream(t, true, 10*time.Millisecond, &orgCalls)
			cfg := config.DefaultConfig()
			cfg.Providers = map[string]config.ProviderConfig{
				"fast": {BaseURL: global.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
			}
			cfg.Auth.Keys = []config.AuthKeyConfig{
				{Name: "synthetic-org", Key: "synthetic-org-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "synthetic-org"}},
				{Name: "synthetic-global", Key: "synthetic-global-probe", KeyType: "internal"},
			}
			cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
			if path != "org-first" {
				cfg.Routing.ProviderLock = config.ProviderLockConfig{Enabled: true}
			}
			if path == "failover-override" {
				cfg.Providers["backup"] = cfg.Providers["fast"]
				cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"adaptive-sample": {{Provider: "fast"}, {Provider: "backup"}}}
				cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 1}
				cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}
			}
			srv, gateway := adaptiveServer(t, cfg)
			orgModels := []string{"different-model"}
			if path == "org-first" {
				orgModels = []string{"adaptive-sample"}
			}
			store := tenant.NewStore()
			orgProviders := map[string]*tenant.ProviderConfig{
				"fast": {Enabled: true, APIKey: "synthetic-provider-key", Models: orgModels},
			}
			orgBases := map[string]config.ProviderConfig{
				"fast": {BaseURL: org.URL, APIFormat: "openai", Models: orgModels},
			}
			if path == "failover-override" {
				orgProviders["backup"] = orgProviders["fast"]
				orgBases["backup"] = orgBases["fast"]
			}
			store.Set("synthetic-org", &tenant.OrgConfig{Providers: orgProviders})
			// Operator-supplied synthetic base URLs avoid bypassing tenant URL validation.
			orgCache := providers.NewOrgProviderCache(orgBases, false)
			t.Cleanup(orgCache.EvictAll)
			srv.handlers.tenantStore = store
			srv.handlers.orgProviderCache = orgCache
			srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
			if status, _ := adaptiveHTTP(t, gateway, "fast/adaptive-sample", "synthetic-global-probe", false); status != http.StatusOK {
				t.Fatal("global priming failed")
			}
			before := srv.handlers.healthMonitor.GetHealth("fast")
			headers := http.Header{}
			if path != "org-first" {
				headers.Set("x-agentcc-provider-lock", "fast")
			}
			wantOrgCalls := int64(3)
			for i := 0; i < 3; i++ {
				status, _ := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-org-probe", false, headers)
				want := http.StatusBadGateway
				if path == "failover-override" {
					// Each failure opens the org's own breaker (threshold 1) for
					// that target, fast then backup; then nothing is left to try.
					wantOrgCalls = 2
					if i == 2 {
						want = http.StatusServiceUnavailable
					}
				}
				if status != want {
					t.Fatalf("org request %d status=%d want=%d org_calls=%d", i, status, want, orgCalls.Load())
				}
			}
			after := srv.handlers.healthMonitor.GetHealth("fast")
			if got := len(srv.handlers.healthMonitor.GetAllHealth()); got != 1 {
				t.Fatalf("global monitor retained org samples: providers=%d want=1", got)
			}
			if orgCalls.Load() != wantOrgCalls || globalCalls.Load() != 1 {
				t.Fatalf("wrong instances called: global=%d org=%d", globalCalls.Load(), orgCalls.Load())
			}
			if after.RequestCount != before.RequestCount || after.ErrorCount != 0 || after.LatencyEWMAMs != before.LatencyEWMAMs || !after.Healthy || after.CircuitState == "open" {
				t.Fatalf("org instance polluted global health: samples=%d errors=%d latency=%d before_latency=%d circuit=%s", after.RequestCount, after.ErrorCount, after.LatencyEWMAMs, before.LatencyEWMAMs, after.CircuitState)
			}
			if status, _ := adaptiveHTTP(t, gateway, "fast/adaptive-sample", "synthetic-global-probe", false); status != http.StatusOK {
				t.Fatal("org failure blocked unrelated global traffic")
			}
		})
	}
}

func TestAdaptiveHealthRecordsEachFailoverAttempt(t *testing.T) {
	var fastCalls, slowCalls atomic.Int64
	fast := adaptiveUpstream(t, false, time.Millisecond, &fastCalls)
	slow := adaptiveUpstream(t, true, time.Millisecond, &slowCalls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"fast": {BaseURL: fast.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
		"slow": {BaseURL: slow.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = adaptiveRoutingConfig()
	cfg.Routing.Adaptive.UpdateInterval = time.Hour
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2}
	srv, gateway := adaptiveServer(t, cfg)
	if adaptiveRequest(t, gateway, "adaptive-sample") != http.StatusOK {
		t.Fatal("failover failed")
	}
	for _, id := range []string{"fast", "slow"} {
		health := srv.handlers.healthMonitor.GetHealth(id)
		wantErrors := int64(0)
		if id == "slow" {
			wantErrors = 1
		}
		if health.RequestCount != 1 || health.ErrorCount != wantErrors {
			t.Fatalf("wrong attempt attribution for %s: samples=%d errors=%d", id, health.RequestCount, health.ErrorCount)
		}
	}
}

func TestAdaptiveStreamHealthUsesUpstreamCompletion(t *testing.T) {
	for _, fail := range []bool{false, true} {
		t.Run(fmt.Sprintf("upstream-failure=%t", fail), func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/v1/chat/completions" {
					w.WriteHeader(http.StatusNotFound)
					return
				}
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprint(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"synthetic response\"}}]}\n\n")
				w.(http.Flusher).Flush()
				if fail {
					fmt.Fprint(w, "data: {\"error\":{\"message\":\"synthetic failure\",\"type\":\"server_error\",\"code\":\"synthetic\"}}\n\n")
				} else {
					fmt.Fprint(w, "data: [DONE]\n\n")
				}
			}))
			t.Cleanup(upstream.Close)
			cfg := config.DefaultConfig()
			cfg.Providers = map[string]config.ProviderConfig{"fast": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}}}
			cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
			srv, gateway := adaptiveServer(t, cfg, adaptiveOutputPolicy{})
			status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "", true)
			if status != http.StatusOK || !strings.Contains(body, "synthetic response") {
				t.Fatalf("stream not received: status=%d", status)
			}
			health := srv.handlers.healthMonitor.GetHealth("fast")
			wantErrors := int64(0)
			if fail {
				wantErrors = 1
			}
			if health.RequestCount != 1 || health.ErrorCount != wantErrors {
				t.Fatalf("stream outcome attribution: samples=%d errors=%d want_errors=%d", health.RequestCount, health.ErrorCount, wantErrors)
			}
		})
	}
}
