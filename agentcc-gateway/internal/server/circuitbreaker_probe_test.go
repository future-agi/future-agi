package server

import (
	"fmt"
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
)

// Exercise the public HTTP endpoint and real provider adapter, with no external service or key.
func TestChatCompletionHalfOpenProbeBurst(t *testing.T) {
	const burst = 12
	var recovering atomic.Bool
	var upstreamCalls atomic.Int32
	release := make(chan struct{})
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	observed := make(chan string, burst*2)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		if !recovering.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"error":{"message":"synthetic outage"}}`)
			return
		}
		upstreamCalls.Add(1)
		select {
		case <-release:
		default:
			observed <- "provider"
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
		}
		fmt.Fprint(w, `{"id":"synthetic","object":"chat.completion","model":"synthetic-model","choices":[{"index":0,"message":{"role":"assistant","content":"recovered"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
	}))
	defer upstream.Close()

	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"synthetic": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"synthetic-model"}, MaxConcurrent: burst},
		"offline":   {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"synthetic-model"}},
	}
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
		"synthetic-model": {{Provider: "synthetic", Weight: 1}, {Provider: "offline", Weight: 1}},
	}
	cfg.Routing.Retry.Enabled = false
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 1}
	cfg.Routing.CircuitBreaker = config.CircuitBreakerConfig{
		Enabled: true, FailureThreshold: 1, SuccessThreshold: 2, Cooldown: 20 * time.Millisecond,
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	// The HTTP handler enables failover for multi-target models. Model a deployment
	// whose spare is unavailable, leaving only the recovering provider to probe.
	registry.Router().SetHealthy("offline", false)
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	gateway := httptest.NewServer(srv.httpServer.Handler)
	defer gateway.Close()
	defer unblock()
	client := &http.Client{Timeout: 5 * time.Second}
	request := func() (int, string) {
		resp, err := client.Post(gateway.URL+"/v1/chat/completions", "application/json",
			strings.NewReader(`{"model":"synthetic-model","messages":[{"role":"user","content":"probe"}]}`))
		if err != nil {
			return 0, "transport error"
		}
		defer resp.Body.Close()
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return 0, "body read error"
		}
		return resp.StatusCode, string(body)
	}
	// The OpenAI adapter maps upstream 5xx responses to 502.
	if status, _ := request(); status != http.StatusBadGateway {
		t.Fatalf("outage status=%d, want 502", status)
	}
	recovering.Store(true)
	deadline := time.Now().Add(2 * time.Second)
	for {
		if _, err := registry.Router().Select("synthetic-model"); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("provider never re-admitted after cooldown")
		}
		time.Sleep(time.Millisecond)
	}

	statuses := make(chan int, burst)
	for i := 0; i < burst; i++ {
		go func() {
			status, body := request()
			if status != http.StatusOK {
				observed <- "rejected"
			} else if !strings.Contains(body, `"content":"recovered"`) {
				status = 0
			}
			statuses <- status
		}()
	}
observations:
	for i := 0; i < burst; i++ {
		select {
		case <-observed:
		case <-time.After(3 * time.Second):
			t.Logf("burst progress: events=%d upstream=%d completed=%d", i, upstreamCalls.Load(), len(statuses))
			break observations
		}
	}
	admitted := upstreamCalls.Load()
	unblock()
	counts := make(map[int]int)
	for i := 0; i < burst; i++ {
		select {
		case status := <-statuses:
			counts[status]++
		case <-time.After(3 * time.Second):
			t.Fatal("burst request never completed")
		}
	}
	t.Logf("burst=%d upstream_probes=%d http_200=%d http_503=%d other=%d", burst, admitted,
		counts[200], counts[503], burst-counts[200]-counts[503])
	if admitted != 1 || counts[200] != 1 || counts[503] != burst-1 {
		t.Fatalf("recovery burst: upstream=%d statuses=%v, want one probe and eleven 503s", admitted, counts)
	}
	// A second successful probe closes the circuit; ordinary traffic then works again.
	for i := 0; i < 3; i++ {
		if status, body := request(); status != http.StatusOK || !strings.Contains(body, `"content":"recovered"`) {
			t.Fatalf("request after release: status=%d, want recovered response", status)
		}
	}
}
