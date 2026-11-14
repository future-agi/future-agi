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

func completionHealthUpstream(t *testing.T, mode string, fail bool, calls *atomic.Int64) *httptest.Server {
	t.Helper()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"object":"list","data":[{"id":"adaptive-sample","object":"model"}]}`)
			return
		}
		path := "/v1/completions"
		if mode == "translated" {
			path = "/openai/deployments/adaptive-sample/chat/completions"
		} else if mode == "stream" {
			path = "/v1/chat/completions"
		}
		if r.URL.Path != path {
			t.Errorf("upstream path=%s want=%s", r.URL.Path, path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		calls.Add(1)
		time.Sleep(2 * time.Millisecond)
		if mode == "stream" {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprint(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"synthetic response\"}}]}\n\n")
			w.(http.Flusher).Flush()
			if fail {
				fmt.Fprint(w, "data: {\"error\":{\"message\":\"synthetic failure\",\"type\":\"server_error\"}}\n\n")
			} else {
				fmt.Fprint(w, "data: [DONE]\n\n")
			}
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if fail {
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"error":{"message":"synthetic failure","type":"server_error"}}`)
			return
		}
		if mode == "native" {
			fmt.Fprint(w, `{"id":"synthetic","model":"adaptive-sample","choices":[{"text":"synthetic response","index":0,"finish_reason":"stop"}]}`)
		} else {
			fmt.Fprint(w, `{"id":"synthetic","model":"adaptive-sample","choices":[{"message":{"role":"assistant","content":"synthetic response"},"index":0,"finish_reason":"stop"}]}`)
		}
	}))
	t.Cleanup(upstream.Close)
	return upstream
}

func completionHealthConfig(baseURL, mode string) config.ProviderConfig {
	format := "openai"
	if mode == "translated" {
		format = "azure"
	}
	return config.ProviderConfig{BaseURL: baseURL, APIFormat: format, Models: []string{"adaptive-sample"}}
}

func completionHealthHTTP(t *testing.T, gateway *httptest.Server, mode, key string) (int, string) {
	t.Helper()
	body, err := json.Marshal(map[string]any{"model": "adaptive-sample", "prompt": "synthetic probe", "stream": mode == "stream"})
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/completions", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	client := gateway.Client()
	client.Timeout = 5 * time.Second
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, string(data)
}

func TestAdaptiveLegacyCompletionAttemptHealth(t *testing.T) {
	for _, mode := range []string{"native", "translated", "stream"} {
		for _, scenario := range []string{"success", "upstream-error", "local-rate-limit", "output-policy", "cache-hit"} {
			// The cache plugin deliberately neither reads nor writes streams.
			if mode == "stream" && scenario == "cache-hit" {
				continue
			}
			t.Run(mode+"/"+scenario, func(t *testing.T) {
				var calls atomic.Int64
				upstream := completionHealthUpstream(t, mode, scenario == "upstream-error", &calls)
				cfg := config.DefaultConfig()
				cfg.Providers = map[string]config.ProviderConfig{"fast": completionHealthConfig(upstream.URL, mode)}
				cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
				var plugins []pipeline.Plugin
				switch scenario {
				case "local-rate-limit":
					plugins = append(plugins, ratelimit.New(config.RateLimitConfig{Enabled: true, GlobalRPM: 1}, nil, nil))
				case "output-policy":
					plugins = append(plugins, adaptiveOutputPolicy{})
				case "cache-hit":
					plugins = append(plugins, cacheplugin.NewFromStore(cache.NewStore(8), time.Minute))
				}
				srv, gateway := adaptiveServer(t, cfg, plugins...)
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
					status, body := completionHealthHTTP(t, gateway, mode, key)
					want := http.StatusOK
					if scenario == "upstream-error" && mode != "stream" {
						want = http.StatusBadGateway
					} else if scenario == "output-policy" && mode != "stream" {
						want = models.ErrGuardrailBlocked("", "").Status
					} else if scenario == "local-rate-limit" && i == 1 {
						want = http.StatusTooManyRequests
					} else if scenario == "cache-hit" && mode == "native" && i == 1 {
						// Existing native cache response bug: no nativeResp is built on a hit.
						// Even this local response error must not become an upstream sample.
						want = http.StatusInternalServerError
					}
					if status != want {
						t.Fatalf("status=%d want=%d", status, want)
					}
					if want == http.StatusOK && !strings.Contains(body, "synthetic response") {
						t.Fatal("missing upstream/cached content")
					}
					if scenario == "upstream-error" && mode == "stream" && !strings.Contains(body, "synthetic failure") {
						t.Fatal("missing terminal upstream error")
					}
				}
				wantCalls := int64(2)
				if scenario == "local-rate-limit" || scenario == "cache-hit" {
					wantCalls = 1
				}
				wantErrors := int64(0)
				if scenario == "upstream-error" {
					wantErrors = wantCalls
				}
				health := srv.handlers.healthMonitor.GetHealth("fast")
				// Every call feeds least-latency's average; only successful
				// calls feed adaptive's, since failing fast is not speed.
				if calls.Load() != wantCalls || health.RequestCount != wantCalls || health.ErrorCount != wantErrors || health.LatencyEWMAMs < 1 || (health.SuccessLatencyEWMAMs >= 1) != (wantErrors == 0) {
					t.Fatalf("legacy health attribution: calls=%d samples=%d errors=%d latency=%d success_latency=%.0f want_calls=%d want_errors=%d", calls.Load(), health.RequestCount, health.ErrorCount, health.LatencyEWMAMs, health.SuccessLatencyEWMAMs, wantCalls, wantErrors)
				}
			})
		}
	}
}

// adaptiveEmptyShortCircuit stops the pipeline without a response, so the
// provider is never called.
type adaptiveEmptyShortCircuit struct{}

func (adaptiveEmptyShortCircuit) Name() string  { return "synthetic-empty-short-circuit" }
func (adaptiveEmptyShortCircuit) Priority() int { return 50 }
func (adaptiveEmptyShortCircuit) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultShortCircuit(nil)
}
func (adaptiveEmptyShortCircuit) ProcessResponse(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

func TestAdaptiveLegacyCompletionStreamShortCircuitNotSampled(t *testing.T) {
	var calls atomic.Int64
	upstream := completionHealthUpstream(t, "stream", false, &calls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{"fast": completionHealthConfig(upstream.URL, "stream")}
	cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
	srv, gateway := adaptiveServer(t, cfg, adaptiveEmptyShortCircuit{})
	// The existing local response for a short-circuit with no response is a 500.
	if status, _ := completionHealthHTTP(t, gateway, "stream", ""); status != http.StatusInternalServerError {
		t.Fatalf("status=%d want=%d", status, http.StatusInternalServerError)
	}
	health := srv.handlers.healthMonitor.GetHealth("fast")
	if calls.Load() != 0 || health.RequestCount != 0 || health.ErrorCount != 0 || health.LatencyEWMAMs != 0 {
		t.Fatalf("provider sampled without being called: calls=%d samples=%d errors=%d latency_ms=%d", calls.Load(), health.RequestCount, health.ErrorCount, health.LatencyEWMAMs)
	}
}

func TestAdaptiveLegacyCompletionExcludesOrgInstances(t *testing.T) {
	for _, mode := range []string{"native", "translated", "stream"} {
		t.Run(mode, func(t *testing.T) {
			var globalCalls, orgCalls atomic.Int64
			global := completionHealthUpstream(t, mode, false, &globalCalls)
			org := completionHealthUpstream(t, mode, true, &orgCalls)
			cfg := config.DefaultConfig()
			cfg.Providers = map[string]config.ProviderConfig{"fast": completionHealthConfig(global.URL, mode)}
			cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
			srv, gateway := adaptiveServer(t, cfg)
			store := tenant.NewStore()
			store.Set("synthetic-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"fast": {Enabled: true, APIKey: "synthetic-provider-key", Models: []string{"adaptive-sample"}},
			}})
			orgCache := providers.NewOrgProviderCache(map[string]config.ProviderConfig{"fast": completionHealthConfig(org.URL, mode)}, false)
			t.Cleanup(orgCache.EvictAll)
			srv.handlers.tenantStore = store
			srv.handlers.orgProviderCache = orgCache
			srv.handlers.keyStore = auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{
				{Name: "synthetic-org", Key: "synthetic-org-probe", KeyType: "byok", Metadata: map[string]string{"org_id": "synthetic-org"}},
				{Name: "synthetic-global", Key: "synthetic-global-probe", KeyType: "internal"},
			}})
			if status, _ := completionHealthHTTP(t, gateway, mode, "synthetic-global-probe"); status != http.StatusOK {
				t.Fatal("global priming failed")
			}
			before := srv.handlers.healthMonitor.GetHealth("fast")
			status, body := completionHealthHTTP(t, gateway, mode, "synthetic-org-probe")
			want := http.StatusBadGateway
			if mode == "stream" {
				want = http.StatusOK
			}
			if status != want || !strings.Contains(body, "synthetic failure") {
				t.Fatalf("org failure not relayed: status=%d", status)
			}
			after := srv.handlers.healthMonitor.GetHealth("fast")
			if globalCalls.Load() != 1 || orgCalls.Load() != 1 || after.RequestCount != before.RequestCount || after.ErrorCount != 0 || after.LatencyEWMAMs != before.LatencyEWMAMs {
				t.Fatalf("legacy org instance polluted global health: global_calls=%d org_calls=%d samples=%d errors=%d latency=%d before_latency=%d", globalCalls.Load(), orgCalls.Load(), after.RequestCount, after.ErrorCount, after.LatencyEWMAMs, before.LatencyEWMAMs)
			}
		})
	}
}
