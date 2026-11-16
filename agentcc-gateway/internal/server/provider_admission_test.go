package server

import (
	"bufio"
	"context"
	"fmt"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func newProbeServer(t *testing.T, cfg *config.Config, tenants *tenant.Store) (*Server, *httptest.Server) {
	t.Helper()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { registry.Close() })
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), tenants, nil)
	srv.ready.Store(true)
	gateway := httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(gateway.Close)
	return srv, gateway
}

func probeConfig(upstream string) *config.Config {
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"probe": {BaseURL: upstream, APIFormat: "openai", Models: []string{"primary"}},
	}
	cfg.Routing.Retry.Enabled = false
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 1}
	cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{
		Enabled: true, FailureThreshold: 1, SuccessThreshold: 2,
		HalfOpenMaxProbes: 1, Cooldown: 10 * time.Millisecond,
	}
	return cfg
}

func probeRequest(t *testing.T, gateway *httptest.Server, model, key string, stream bool) (int, string) {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, gateway.URL+"/v1/chat/completions",
		strings.NewReader(fmt.Sprintf(`{"model":%q,"stream":%t,"messages":[{"role":"user","content":"probe"}]}`, model, stream)))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, string(body)
}

func waitProbeState(t *testing.T, cb *routing.CircuitBreaker, state routing.CircuitState) {
	t.Helper()
	deadline := time.Now().Add(time.Second)
	for cb.State() != state {
		if time.Now().After(deadline) {
			t.Fatalf("breaker state=%v, want %v", cb.State(), state)
		}
		time.Sleep(time.Millisecond)
	}
}

func TestProbeDirectAdmission(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, mode := range []string{"no-router", "single-target", "org-failover"} {
			t.Run(fmt.Sprintf("stream=%t/%s", stream, mode), func(t *testing.T) {
				var calls atomic.Int32
				entered, release := make(chan struct{}, 8), make(chan struct{})
				var once sync.Once
				unblock := func() { once.Do(func() { close(release) }) }
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost {
						return
					}
					if calls.Add(1) == 1 {
						w.WriteHeader(http.StatusServiceUnavailable)
						fmt.Fprint(w, `{"error":{"message":"outage"}}`)
						return
					}
					entered <- struct{}{}
					select {
					case <-release:
					case <-r.Context().Done():
						return
					}
					writeProbeResponse(w, stream, "recovered")
				}))
				t.Cleanup(upstream.Close)
				cfg := probeConfig(upstream.URL)
				var tenants *tenant.Store
				key := ""
				if mode != "no-router" {
					cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"primary": {{Provider: "probe"}}}
				}
				if mode == "org-failover" {
					key = "synthetic-probe-internal"
					cfg.Auth.Enabled = true
					cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic", Key: key, KeyType: "internal", Metadata: map[string]string{"org_id": "probe-org"}}}
					tenants = tenant.NewStore()
					tenants.Set("probe-org", &tenant.OrgConfig{Routing: &tenant.RoutingConfig{FallbackEnabled: true}})
					cfg.Routing.Targets["primary"] = append(cfg.Routing.Targets["primary"], config.RoutingTargetConfig{Provider: "probe"})
				}
				srv, gateway := newProbeServer(t, cfg, tenants)
				if mode == "org-failover" {
					orgCfg := &tenant.OrgConfig{Routing: &tenant.RoutingConfig{FallbackEnabled: true}}
					for i := 0; i < 2; i++ {
						if srv.handlers.effectiveFailover(orgCfg).CircuitBreakers() != srv.handlers.healthMonitor.CircuitBreakerRegistry() {
							t.Fatal("org failover override lost the persistent breaker registry")
						}
					}
				}
				t.Cleanup(unblock)
				if status, _ := probeRequest(t, gateway, "primary", key, stream); status != http.StatusBadGateway {
					t.Fatalf("outage status=%d", status)
				}
				if reg := srv.handlers.healthMonitor.CircuitBreakerRegistry(); reg == nil {
					t.Fatal("no persistent breaker registry")
				}
				cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("probe")
				if cb.State() != routing.StateOpen {
					t.Fatalf("direct request did not open breaker: %v", cb.State())
				}
				time.Sleep(30 * time.Millisecond)
				finished := make(chan struct{})
				go func() { defer close(finished); probeRequest(t, gateway, "primary", key, stream) }()
				select {
				case <-entered:
				case <-time.After(time.Second):
					t.Fatal("recovery probe never reached upstream")
				}
				code := "circuit_open"
				if mode == "org-failover" {
					code = "all_providers_unavailable"
				}
				if status, body := probeRequest(t, gateway, "primary", key, stream); status != 503 || !strings.Contains(body, `"code":"`+code+`"`) {
					t.Errorf("saturated request status=%d body=%s, want %s", status, body, code)
				}
				if calls.Load() != 2 {
					t.Errorf("upstream calls=%d, want outage plus one probe", calls.Load())
				}
				unblock()
				<-finished
			})
		}
	}
}

func TestProbeStreamTerminalOutcome(t *testing.T) {
	for _, ending := range []string{"success", "error", "cancel"} {
		t.Run(ending, func(t *testing.T) {
			var calls atomic.Int32
			release := make(chan struct{})
			var once sync.Once
			unblock := func() { once.Do(func() { close(release) }) }
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					return
				}
				if calls.Add(1) == 1 {
					w.WriteHeader(503)
					fmt.Fprint(w, `{"error":{"message":"outage"}}`)
					return
				}
				w.Header().Set("Content-Type", "text/event-stream")
				fmt.Fprint(w, "data: {\"model\":\"primary\",\"choices\":[{\"delta\":{\"content\":\"first\"}}]}\n\n")
				w.(http.Flusher).Flush()
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
				if ending == "error" {
					fmt.Fprint(w, "data: {\"error\":{\"message\":\"terminal outage\"}}\n\n")
					return
				}
				fmt.Fprint(w, "data: [DONE]\n\n")
			}))
			t.Cleanup(upstream.Close)
			cfg := probeConfig(upstream.URL)
			// Exercise the multi-target failover path as well as direct streaming above.
			cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"primary": {{Provider: "probe"}, {Provider: "probe"}}}
			srv, gateway := newProbeServer(t, cfg, nil)
			t.Cleanup(unblock)
			probeRequest(t, gateway, "primary", "", true)
			time.Sleep(30 * time.Millisecond)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			req, _ := http.NewRequestWithContext(ctx, http.MethodPost, gateway.URL+"/v1/chat/completions", strings.NewReader(`{"model":"primary","stream":true,"messages":[{"role":"user","content":"probe"}]}`))
			req.Header.Set("Content-Type", "application/json")
			resp, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			scanner := bufio.NewScanner(resp.Body)
			if !scanner.Scan() || !strings.Contains(scanner.Text(), "first") {
				t.Fatal("first chunk not delivered")
			}
			cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("probe")
			if generation, allowed := cb.Allow(); allowed {
				cb.RecordFailure(generation, nil)
				t.Error("stream released its admission token at the first chunk")
			}
			if status, body := probeRequest(t, gateway, "primary", "", true); status != 503 || !strings.Contains(body, "all_providers_unavailable") {
				t.Errorf("stream released probe at first chunk: status=%d", status)
			}
			if ending == "cancel" {
				cancel()
			} else {
				unblock()
			}
			for scanner.Scan() {
			}
			if ending == "error" {
				waitProbeState(t, cb, routing.StateOpen)
			} else {
				// Completion/cancellation releases capacity. Cancellation must not count as recovery.
				deadline := time.Now().Add(time.Second)
				for {
					generation, allowed := cb.Allow()
					if allowed {
						cb.RecordFailure(generation, nil)
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("terminal stream leaked its probe")
					}
					time.Sleep(time.Millisecond)
				}
				unblock()
				probeRequest(t, gateway, "primary", "", true)
				want := routing.StateClosed
				if ending == "cancel" {
					want = routing.StateHalfOpen
				}
				waitProbeState(t, cb, want)
			}
		})
	}
}

func TestProbeTenantFallbackIsolation(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
			t.Cleanup(primary.Close)
			shared := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Authorization") == "Bearer synthetic-org-a" {
					w.WriteHeader(429)
					fmt.Fprint(w, `{"error":{"message":"outage"}}`)
					return
				}
				writeProbeResponse(w, stream, "isolated")
			}))
			t.Cleanup(shared.Close)
			cfg := probeConfig(primary.URL)
			cfg.Routing.CircuitBreaker.Cooldown = time.Hour
			cfg.Providers["shared"] = config.ProviderConfig{BaseURL: shared.URL, APIFormat: "openai", Models: []string{"fallback"}}
			cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"fallback": {{Provider: "shared"}}}
			cfg.Routing.ModelFallbacks = map[string][]string{"primary": {"fallback"}}
			cfg.Auth.Enabled = true
			tenants := tenant.NewStore()
			for _, id := range []string{"a", "b"} {
				cfg.Auth.Keys = append(cfg.Auth.Keys, config.AuthKeyConfig{Name: id, Key: "synthetic-internal-" + id, KeyType: "internal", Metadata: map[string]string{"org_id": id}})
				tenants.Set(id, &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"shared": {Enabled: true, APIKey: "synthetic-org-" + id, Models: []string{"fallback"}}}})
			}
			srv, gateway := newProbeServer(t, cfg, tenants)
			if status, _ := probeRequest(t, gateway, "primary", "synthetic-internal-a", stream); status != 502 {
				t.Fatalf("tenant A status=%d", status)
			}
			if cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("shared"); cb.State() != routing.StateClosed {
				t.Errorf("tenant fallback changed operator breaker: %v", cb.State())
			}
			if _, err := srv.registry.Router().Select("fallback"); err != nil {
				t.Errorf("tenant fallback changed operator health: %v", err)
			}
			if status, body := probeRequest(t, gateway, "primary", "synthetic-internal-b", stream); status != 200 || !strings.Contains(body, "isolated") {
				t.Fatalf("tenant B blocked by tenant A: status=%d, isolated=%t", status, strings.Contains(body, "isolated"))
			}
		})
	}
}

func TestProbeOrgFallbackReselectsSaturatedTarget(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
			t.Cleanup(primary.Close)
			var blockedCalls atomic.Int32
			blocked := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == http.MethodPost {
					blockedCalls.Add(1)
				}
				writeProbeResponse(w, stream, "wrong")
			}))
			t.Cleanup(blocked.Close)
			healthy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeProbeResponse(w, stream, "org-alternative") }))
			t.Cleanup(healthy.Close)
			cfg := probeConfig(primary.URL)
			cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"primary": {{Provider: "probe"}}}
			cfg.Providers["a-blocked"] = config.ProviderConfig{BaseURL: blocked.URL, APIFormat: "openai", Models: []string{"operator-blocked"}}
			cfg.Providers["b-healthy"] = config.ProviderConfig{BaseURL: healthy.URL, APIFormat: "openai", Models: []string{"operator-healthy"}}
			cfg.Routing.ModelFallbacks = map[string][]string{"primary": {"org-fallback"}}
			cfg.Auth.Enabled = true
			cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic", Key: "synthetic-internal", KeyType: "internal", Metadata: map[string]string{"org_id": "probe-org"}}}
			orgCfg := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
				"a-blocked": {Enabled: true, APIKey: "synthetic-org", Models: []string{"org-fallback"}},
				"b-healthy": {Enabled: true, APIKey: "synthetic-org", Models: []string{"org-fallback"}},
			}}
			tenants := tenant.NewStore()
			tenants.Set("probe-org", orgCfg)
			srv, gateway := newProbeServer(t, cfg, tenants)
			_, err := srv.OrgProviderCache.GetOrCreateWithTenantConfig("probe-org", "a-blocked", "synthetic-org", orgCfg.Providers["a-blocked"])
			if err != nil {
				t.Fatal(err)
			}
			cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().GetScoped("probe-org", "a-blocked")
			generation, _ := cb.Allow()
			cb.RecordFailure(generation, models.ErrUpstreamProvider(503, "synthetic upstream failure"))
			time.Sleep(30 * time.Millisecond)
			generation, allowed := cb.Allow()
			if !allowed {
				t.Fatal("could not hold the org probe")
			}
			defer cb.RecordFailure(generation, nil)
			status, body := probeRequest(t, gateway, "primary", "synthetic-internal", stream)
			if status != 200 || !strings.Contains(body, "org-alternative") || blockedCalls.Load() != 0 {
				t.Fatalf("org fallback selection: status=%d alternative=%t blocked_calls=%d", status, strings.Contains(body, "org-alternative"), blockedCalls.Load())
			}
		})
	}
}

func TestProbeFallbackReselectsSaturatedTarget(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			var saturatedCalls atomic.Int32
			primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(503) }))
			t.Cleanup(primary.Close)
			blocked := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					return
				}
				saturatedCalls.Add(1)
				writeProbeResponse(w, stream, "wrong")
			}))
			t.Cleanup(blocked.Close)
			healthy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { writeProbeResponse(w, stream, "alternative") }))
			t.Cleanup(healthy.Close)
			cfg := probeConfig(primary.URL)
			cfg.Providers["blocked"] = config.ProviderConfig{BaseURL: blocked.URL, APIFormat: "openai", Models: []string{"fallback"}}
			cfg.Providers["healthy"] = config.ProviderConfig{BaseURL: healthy.URL, APIFormat: "openai", Models: []string{"fallback"}}
			cfg.Routing.DefaultStrategy = "least-latency"
			cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
				"primary":  {{Provider: "probe"}, {Provider: "probe"}},
				"fallback": {{Provider: "blocked", Priority: 1}, {Provider: "healthy", Priority: 2}},
			}
			cfg.Routing.ModelFallbacks = map[string][]string{"primary": {"fallback"}}
			srv, gateway := newProbeServer(t, cfg, nil)
			cb := srv.handlers.healthMonitor.CircuitBreakerRegistry().Get("blocked")
			generation, _ := cb.Allow()
			cb.RecordFailure(generation, models.ErrUpstreamProvider(503, "synthetic upstream failure"))
			time.Sleep(30 * time.Millisecond)
			generation, allowed := cb.Allow()
			if !allowed {
				t.Fatal("could not hold half-open probe")
			}
			defer cb.RecordFailure(generation, nil)
			for i := 0; i < 2; i++ { // second call exercises already-open primary -> 503 -> model fallback
				status, body := probeRequest(t, gateway, "primary", "", stream)
				if status != 200 || !strings.Contains(body, "alternative") {
					t.Errorf("fallback target was skipped: status=%d alternative=%t", status, strings.Contains(body, "alternative"))
				}
			}
			if saturatedCalls.Load() != 0 {
				t.Errorf("saturated upstream called %d times", saturatedCalls.Load())
			}
		})
	}
}
