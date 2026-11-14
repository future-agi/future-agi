package server

import (
	"encoding/json"
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
	for _, stream := range []bool{false, true} {
		for _, fallback := range []string{"none", "same-provider", "healthy-provider"} {
			t.Run(fmt.Sprintf("stream=%t/%s", stream, fallback), func(t *testing.T) {
				testHalfOpenProbeBurst(t, stream, fallback)
			})
		}
	}
}

func testHalfOpenProbeBurst(t *testing.T, stream bool, fallback string) {
	t.Helper()
	const burst = 12
	var recovering atomic.Bool
	var upstreamCalls, sameProviderFallbackCalls, healthyCalls atomic.Int32
	release := make(chan struct{})
	var releaseOnce sync.Once
	unblock := func() { releaseOnce.Do(func() { close(release) }) }
	observed := make(chan struct{}, burst*2)
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !recovering.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			fmt.Fprint(w, `{"error":{"message":"synthetic outage"}}`)
			return
		}
		var req struct {
			Model string `json:"model"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		upstreamCalls.Add(1)
		if req.Model == "fallback-model" {
			sameProviderFallbackCalls.Add(1)
		}
		select {
		case <-release:
		default:
			observed <- struct{}{}
			select {
			case <-release:
			case <-r.Context().Done():
				return
			}
		}
		writeProbeResponse(w, stream, "recovered")
	}))
	defer upstream.Close()
	healthy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if recovering.Load() {
			healthyCalls.Add(1)
		}
		writeProbeResponse(w, stream, "healthy")
	}))
	defer healthy.Close()

	cfg := config.DefaultConfig()
	cfg.Providers = map[string]config.ProviderConfig{
		"synthetic": {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"synthetic-model", "fallback-model"}, MaxConcurrent: burst},
		"offline":   {BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"synthetic-model"}},
		"healthy":   {BaseURL: healthy.URL, APIFormat: "openai", Models: []string{"healthy-model"}},
	}
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{
		"synthetic-model": {{Provider: "synthetic", Weight: 1}, {Provider: "offline", Weight: 1}},
	}
	if fallback != "none" {
		cfg.Routing.ModelFallbacks = map[string][]string{"synthetic-model": {"fallback-model"}}
		if fallback == "healthy-provider" {
			cfg.Routing.ModelFallbacks["synthetic-model"] = append(cfg.Routing.ModelFallbacks["synthetic-model"], "healthy-model")
		}
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
	// Failover is enabled for multi-target models. The spare is unavailable.
	registry.Router().SetHealthy("offline", false)
	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	gateway := httptest.NewServer(srv.httpServer.Handler)
	defer gateway.Close()
	defer unblock()
	client := &http.Client{Timeout: 5 * time.Second}
	type response struct {
		status int
		body   string
	}
	request := func() response {
		resp, err := client.Post(gateway.URL+"/v1/chat/completions", "application/json",
			strings.NewReader(fmt.Sprintf(`{"model":"synthetic-model","stream":%t,"messages":[{"role":"user","content":"probe"}]}`, stream)))
		if err != nil {
			return response{}
		}
		defer resp.Body.Close()
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			return response{}
		}
		return response{resp.StatusCode, string(body)}
	}
	// The OpenAI adapter maps upstream 5xx to 502, unless the healthy fallback serves it.
	outageStatus := http.StatusBadGateway
	if fallback == "healthy-provider" {
		outageStatus = http.StatusOK
	}
	if resp := request(); resp.status != outageStatus {
		t.Fatalf("outage status=%d, want %d", resp.status, outageStatus)
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

	responses := make(chan response, burst)
	for i := 0; i < burst; i++ {
		go func() {
			resp := request()
			if resp.status != http.StatusOK || strings.Contains(resp.body, `"content":"healthy"`) {
				observed <- struct{}{}
			}
			responses <- resp
		}()
	}
	for i := 0; i < burst; i++ {
		select {
		case <-observed:
		case <-time.After(3 * time.Second):
			t.Fatalf("burst did not settle: events=%d upstream=%d completed=%d", i, upstreamCalls.Load(), len(responses))
		}
	}
	admitted := upstreamCalls.Load()
	unblock()
	counts := make(map[int]int)
	for i := 0; i < burst; i++ {
		select {
		case resp := <-responses:
			counts[resp.status]++
			if resp.status == http.StatusOK {
				if !strings.Contains(resp.body, `"content":"recovered"`) && !strings.Contains(resp.body, `"content":"healthy"`) {
					t.Error("successful response did not contain the upstream content")
				}
				if stream && !strings.Contains(resp.body, "[DONE]") {
					t.Error("successful stream did not complete")
				}
			} else if !strings.Contains(resp.body, `"code":"all_providers_unavailable"`) {
				t.Error("rejected response did not contain all_providers_unavailable")
			}
		case <-time.After(3 * time.Second):
			t.Fatal("burst request never completed")
		}
	}
	wantOK, wantRejected, wantHealthy := 1, burst-1, int32(0)
	if fallback == "healthy-provider" {
		wantOK, wantRejected, wantHealthy = burst, 0, burst-1
	}
	t.Logf("burst=%d upstream_probes=%d same_provider_fallbacks=%d healthy_calls=%d http_200=%d http_503=%d", burst, admitted,
		sameProviderFallbackCalls.Load(), healthyCalls.Load(), counts[200], counts[503])
	if admitted != 1 || sameProviderFallbackCalls.Load() != 0 || healthyCalls.Load() != wantHealthy || counts[200] != wantOK || counts[503] != wantRejected {
		t.Fatalf("recovery burst: upstream=%d same_provider_fallbacks=%d healthy=%d statuses=%v, want one probe, no same-provider fallbacks, %d healthy calls", admitted, sameProviderFallbackCalls.Load(), healthyCalls.Load(), counts, wantHealthy)
	}
	// A second successful probe closes the circuit; ordinary traffic works again.
	for i := 0; i < 3; i++ {
		if resp := request(); resp.status != http.StatusOK || !strings.Contains(resp.body, `"content":"recovered"`) {
			t.Fatalf("request after release: status=%d, want recovered response", resp.status)
		}
	}
}

func writeProbeResponse(w http.ResponseWriter, stream bool, content string) {
	if stream {
		w.Header().Set("Content-Type", "text/event-stream")
		fmt.Fprintf(w, "data: {\"id\":\"synthetic\",\"object\":\"chat.completion.chunk\",\"model\":\"synthetic-model\",\"choices\":[{\"index\":0,\"delta\":{\"content\":%q},\"finish_reason\":null}]}\n\ndata: [DONE]\n\n", content)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"id":"synthetic","object":"chat.completion","model":"synthetic-model","choices":[{"index":0,"message":{"role":"assistant","content":%q},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`, content)
}
