package server

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Synthetic OpenAI-compatible upstreams exercise the real adapter and HTTP path.
func adaptiveUpstream(t *testing.T, fail bool, delay time.Duration, calls *atomic.Int64) *httptest.Server {
	t.Helper()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		calls.Add(1)
		time.Sleep(delay)
		w.Header().Set("Content-Type", "application/json")
		if fail {
			w.WriteHeader(http.StatusServiceUnavailable)
			io.WriteString(w, `{"error":{"message":"synthetic upstream unavailable","type":"server_error"}}`)
			return
		}
		var request struct {
			Stream bool `json:"stream"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		if request.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			io.WriteString(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"synthetic response\"}}]}\n\ndata: [DONE]\n\n")
			return
		}
		io.WriteString(w, `{"id":"synthetic-completion","object":"chat.completion","model":"adaptive-sample","choices":[{"index":0,"message":{"role":"assistant","content":"synthetic response"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(upstream.Close)
	return upstream
}

func adaptiveServer(t *testing.T, cfg *config.Config, plugins ...pipeline.Plugin) (*Server, *httptest.Server) {
	t.Helper()
	cfg.Auth.Enabled = false
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := srv.Shutdown(ctx); err != nil {
			t.Error(err)
		}
	})
	httpServer := httptest.NewServer(srv.httpServer.Handler)
	t.Cleanup(httpServer.Close)
	return srv, httpServer
}

func adaptiveRequest(t *testing.T, server *httptest.Server, model string) int {
	t.Helper()
	body, err := json.Marshal(map[string]any{
		"model":    model,
		"messages": []map[string]string{{"role": "user", "content": "synthetic probe"}},
	})
	if err != nil {
		t.Fatal(err)
	}
	client := server.Client()
	client.Timeout = 5 * time.Second
	resp, err := client.Post(server.URL+"/v1/chat/completions", "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if _, err := io.Copy(io.Discard, resp.Body); err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusBadGateway && resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("gateway returned unexpected status %d", resp.StatusCode)
	}
	return resp.StatusCode
}

func adaptiveRoutingConfig() config.RoutingConfig {
	return config.RoutingConfig{
		DefaultStrategy: "adaptive",
		Targets: map[string][]config.RoutingTargetConfig{
			"adaptive-sample": {{Provider: "fast"}, {Provider: "slow"}},
		},
		Adaptive: config.AdaptiveConfig{
			Enabled: true, LearningRequests: 10, UpdateInterval: 5 * time.Millisecond,
		},
	}
}

func TestAdaptiveGatewayLearnsFromHTTP(t *testing.T) {
	var fastCalls, slowCalls atomic.Int64
	fast := adaptiveUpstream(t, false, time.Millisecond, &fastCalls)
	slow := adaptiveUpstream(t, true, 10*time.Millisecond, &slowCalls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"fast": {BaseURL: fast.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
		"slow": {BaseURL: slow.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = adaptiveRoutingConfig()
	srv, gateway := adaptiveServer(t, cfg)

	// Prime both providers through the gateway, then cross the learning threshold.
	for _, model := range []string{"fast/adaptive-sample", "slow/adaptive-sample"} {
		adaptiveRequest(t, gateway, model)
	}
	for cycle := 0; cycle < 2; cycle++ {
		if cycle > 0 {
			if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
				t.Fatal(err)
			}
		}
		for i := 0; i < 20; i++ {
			adaptiveRequest(t, gateway, "adaptive-sample")
		}
		fastBefore, slowBefore := fastCalls.Load(), slowCalls.Load()
		for i := 0; i < 200; i++ {
			adaptiveRequest(t, gateway, "adaptive-sample")
		}
		fastCount, slowCount := fastCalls.Load()-fastBefore, slowCalls.Load()-slowBefore
		t.Logf("HTTP adaptive sample: reload=%d healthy=%d failing=%d strategy=%s", cycle, fastCount, slowCount, srv.registry.Router().Strategy())
		if fastCount+slowCount != 200 || fastCount < 160 {
			t.Fatalf("adaptive routing did not favor the healthy upstream: healthy=%d failing=%d", fastCount, slowCount)
		}
	}
	if srv.registry.Router().Strategy() != "adaptive" {
		t.Fatal("gateway silently substituted a different strategy")
	}
	if health := srv.handlers.healthMonitor.GetHealth("fast"); health.RequestCount == 0 || health.LatencyEWMAMs == 0 {
		t.Fatal("healthy provider has no live health/latency observations")
	}
	// Failed calls count as errors and feed least-latency's average, but not
	// adaptive's success-only latency: failing fast is not speed.
	if health := srv.handlers.healthMonitor.GetHealth("slow"); health.ErrorCount == 0 || health.LatencyEWMAMs == 0 || health.SuccessLatencyEWMAMs != 0 {
		t.Fatalf("failing provider: errors=%d latency=%dms success_latency=%.0fms, want errors, latency and no success latency", health.ErrorCount, health.LatencyEWMAMs, health.SuccessLatencyEWMAMs)
	}
}

// The failing upstream is the faster one: it answers 503 at once, while the
// healthy one takes 20ms. Round-robin would give each half of the traffic.
func TestAdaptiveFastFailingTargetGetsLessThanRoundRobin(t *testing.T) {
	var failingCalls, healthyCalls atomic.Int64
	failingFast := adaptiveUpstream(t, true, 0, &failingCalls)
	healthySlow := adaptiveUpstream(t, false, 20*time.Millisecond, &healthyCalls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"fast": {BaseURL: failingFast.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
		"slow": {BaseURL: healthySlow.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = adaptiveRoutingConfig()
	_, gateway := adaptiveServer(t, cfg)
	for i := 0; i < 20; i++ {
		adaptiveRequest(t, gateway, "adaptive-sample")
	}
	time.Sleep(20 * time.Millisecond)
	failingBefore, healthyBefore := failingCalls.Load(), healthyCalls.Load()
	const requests = 200
	for i := 0; i < requests; i++ {
		adaptiveRequest(t, gateway, "adaptive-sample")
	}
	failing, healthy := failingCalls.Load()-failingBefore, healthyCalls.Load()-healthyBefore
	t.Logf("fast-failing=%d slow-healthy=%d of %d", failing, healthy, requests)
	// Round-robin gives it 100 and the fix about 11; 60 leaves room for
	// random selection while a reverted fix (about 100) cannot pass by chance.
	const maxFailing = 60
	if failing+healthy != requests || failing > maxFailing {
		t.Fatalf("fast-failing target got %d of %d requests, want at most %d (round-robin gives it %d)", failing, requests, maxFailing, requests/2)
	}
}

func TestAdaptiveReloadPreservesDisabledRoutingLatency(t *testing.T) {
	var calls atomic.Int64
	upstream := adaptiveUpstream(t, false, 10*time.Millisecond, &calls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"fast": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = config.RoutingConfig{}
	srv, gateway := adaptiveServer(t, cfg)
	monitor := srv.handlers.healthMonitor
	if srv.registry.Router() != nil {
		t.Fatal("test must start with routing disabled")
	}
	for cycle := 0; cycle < 2; cycle++ {
		if adaptiveRequest(t, gateway, "adaptive-sample") != http.StatusOK {
			t.Fatal("synthetic completion failed")
		}
		before := monitor.GetHealth("fast")
		if before.LatencyEWMAMs < 1 || before.RequestCount != int64(cycle+1) {
			t.Fatalf("disabled routing lost observations: latency=%d requests=%d", before.LatencyEWMAMs, before.RequestCount)
		}
		cfg.Routing = config.RoutingConfig{DefaultStrategy: "adaptive"}
		if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
			t.Fatal(err)
		}
		after := monitor.GetHealth("fast")
		if srv.handlers.healthMonitor != monitor || after.LatencyEWMAMs != before.LatencyEWMAMs || after.RequestCount != before.RequestCount {
			t.Fatal("enabling adaptive routing discarded observations")
		}
		cfg.Routing = config.RoutingConfig{}
		if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAdaptiveReloadSharesLiveCircuitBreaker(t *testing.T) {
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
	cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2}
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic-reload", Key: "synthetic-reload-probe", KeyType: "internal", Metadata: map[string]string{"org_id": "synthetic-org"}}}
	srv, gateway := adaptiveServer(t, cfg)
	store := tenant.NewStore()
	store.Set("synthetic-org", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{
		"unrelated": {Enabled: false},
		"slow":      {Enabled: false, APIKey: "synthetic-provider-key"},
	}})
	srv.handlers.tenantStore = store
	srv.handlers.keyStore = auth.NewKeyStore(cfg.Auth)
	if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
		t.Fatal(err)
	}
	// Resolution picks fast; failover's next selection tries slow, opens its
	// breaker and succeeds on fast. The monitor must see that same open breaker.
	if status, _ := adaptiveHTTP(t, gateway, "adaptive-sample", "synthetic-reload-probe", false); status != http.StatusOK || slowCalls.Load() == 0 {
		t.Fatal("expected successful failover after a synthetic upstream failure")
	}
	health := srv.handlers.healthMonitor.GetHealth("slow")
	if health.Healthy || health.CircuitState != "open" {
		t.Fatalf("health monitor diverged from live failover breaker: healthy=%v circuit=%s", health.Healthy, health.CircuitState)
	}
	cfg.Routing.CircuitBreaker.Enabled = false
	if err := srv.handlers.ReloadRouting(cfg, srv.registry); err != nil {
		t.Fatal(err)
	}
	if got := srv.handlers.healthMonitor.GetHealth("slow").CircuitState; got != "n/a" {
		t.Fatalf("disabled breaker retained stale state: %s", got)
	}
}

// Streams feed routing latency with time to first chunk, as least-latency used
// before per-attempt sampling, not with how long the whole answer took.
func TestAdaptiveStreamLatencyIsTimeToFirstChunk(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		io.WriteString(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"synthetic \"}}]}\n\n")
		w.(http.Flusher).Flush()
		time.Sleep(150 * time.Millisecond) // a long answer, not a slow provider
		io.WriteString(w, "data: {\"id\":\"synthetic\",\"model\":\"adaptive-sample\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"response\"}}]}\n\ndata: [DONE]\n\n")
	}))
	t.Cleanup(upstream.Close)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"fast": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = adaptiveRoutingConfig()
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"adaptive-sample": {{Provider: "fast"}}}
	srv, gateway := adaptiveServer(t, cfg)
	if status, body := adaptiveHTTP(t, gateway, "adaptive-sample", "", true); status != http.StatusOK || !strings.Contains(body, "response") {
		t.Fatalf("stream failed: status=%d", status)
	}
	health := srv.handlers.healthMonitor.GetHealth("fast")
	if health.RequestCount != 1 || health.LatencyEWMAMs >= 100 {
		t.Fatalf("stream latency sample=%dms requests=%d, want time to first chunk (well under 150ms)", health.LatencyEWMAMs, health.RequestCount)
	}
}

// least-latency, with failover and breakers off, must move off a provider that
// starts failing slowly. Failed calls feed its average as a penalty that never
// lowers it; recording successful calls only left it stuck on the failing one.
func TestLeastLatencyMovesOffAProviderThatStartsFailing(t *testing.T) {
	var flakyCalls, steadyCalls atomic.Int64
	var flakyFailing atomic.Bool
	flaky := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		flakyCalls.Add(1)
		w.Header().Set("Content-Type", "application/json")
		if flakyFailing.Load() {
			time.Sleep(300 * time.Millisecond)
			w.WriteHeader(http.StatusServiceUnavailable)
			io.WriteString(w, `{"error":{"message":"synthetic upstream unavailable","type":"server_error"}}`)
			return
		}
		time.Sleep(5 * time.Millisecond)
		io.WriteString(w, `{"id":"synthetic-completion","object":"chat.completion","model":"adaptive-sample","choices":[{"index":0,"message":{"role":"assistant","content":"synthetic response"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	t.Cleanup(flaky.Close)
	steady := adaptiveUpstream(t, false, 60*time.Millisecond, &steadyCalls)
	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"flaky":  {BaseURL: flaky.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
		"steady": {BaseURL: steady.URL, APIFormat: "openai", Models: []string{"adaptive-sample"}},
	}
	cfg.Routing = config.RoutingConfig{
		DefaultStrategy: "least-latency",
		Targets: map[string][]config.RoutingTargetConfig{
			"adaptive-sample": {{Provider: "flaky"}, {Provider: "steady"}},
		},
	}
	srv, gateway := adaptiveServer(t, cfg)
	if srv.registry.Router() == nil || srv.registry.Router().Strategy() != "least-latency" {
		t.Fatal("least-latency routing is not active")
	}
	// Learn both latencies: flaky about 5ms, steady about 60ms.
	for i := 0; i < 3; i++ {
		adaptiveRequest(t, gateway, "flaky/adaptive-sample")
		adaptiveRequest(t, gateway, "steady/adaptive-sample")
	}
	if status := adaptiveRequest(t, gateway, "adaptive-sample"); status != http.StatusOK || flakyCalls.Load() != 4 {
		t.Fatalf("least-latency did not pick the faster provider while healthy: status=%d flaky_calls=%d", status, flakyCalls.Load())
	}

	flakyFailing.Store(true)
	flakyBefore, steadyBefore := flakyCalls.Load(), steadyCalls.Load()
	const requests = 10
	for i := 0; i < requests; i++ {
		adaptiveRequest(t, gateway, "adaptive-sample")
	}
	flakyGot, steadyGot := flakyCalls.Load()-flakyBefore, steadyCalls.Load()-steadyBefore
	t.Logf("after flaky starts failing: flaky=%d steady=%d of %d", flakyGot, steadyGot, requests)
	if flakyGot+steadyGot != requests || flakyGot > 2 {
		t.Fatalf("least-latency kept the failing provider: flaky=%d steady=%d of %d, want flaky at most 2", flakyGot, steadyGot, requests)
	}
}
