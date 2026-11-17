package server

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/providers/azure"
	"github.com/futureagi/agentcc-gateway/internal/providers/openai"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

const w2B3IsolationMessages = `[
	{"role":"user","content":"Weather and time?"},
	{"role":"assistant","content":null,"reasoning":"Check both tools.","reasoning_details":[{"type":"reasoning.encrypted","data":"synthetic-signature","format":"google-gemini-v1","future_metadata":"preserve"}],"reasoning_content":"shared summary","tool_calls":[
		{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-weather-signature","future_metadata":"preserve"}}},
		{"id":"call_time","type":"function","function":{"name":"time","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-time-signature"}}}
	]},
	{"role":"tool","tool_call_id":"call_weather","content":"Sunny"},
	{"role":"tool","tool_call_id":"call_time","content":"Noon"},
	{"role":"assistant","content":"Sunny at noon.","reasoning":"Combine the results.","reasoning_details":[{"type":"reasoning.text","text":"Combine the results."}]},
	{"role":"user","content":"Summarize."}
]`

func w2B3IsolationRequest(t *testing.T, model string, stream bool) models.ChatCompletionRequest {
	t.Helper()
	maxTokens := 512
	req := models.ChatCompletionRequest{Model: model, Stream: stream, MaxTokens: &maxTokens,
		StreamOptions: &models.StreamOptions{IncludeUsage: false}}
	if err := json.Unmarshal([]byte(w2B3IsolationMessages), &req.Messages); err != nil {
		t.Fatal(err)
	}
	return req
}

// The selected instance, not its ID or the model spelling, decides whether the
// OpenRouter extensions are legal. Exercise the actual adapters in both modes,
// including Azure, which also serializes the shared OpenAI message types.
func TestW2B3ProviderIsolation(t *testing.T) {
	for _, tc := range []struct {
		name, id, kind, baseURL string
		azure, openRouter       bool
	}{
		{name: "untyped_compat", id: "custom", baseURL: "http://compat.invalid"},
		{name: "openai", id: "custom", kind: "openai", baseURL: "http://api.openai.com"},
		{name: "misleading_id", id: "openrouter", kind: "openai", baseURL: "http://compat.invalid"},
		{name: "misleading_host", id: "custom", kind: "openai", baseURL: "http://openrouter.ai.invalid"},
		{name: "typed_proxy", id: "custom", kind: "openrouter", baseURL: "http://router-proxy.invalid/api", openRouter: true},
		{name: "recognized_host", id: "custom", baseURL: "http://openrouter.ai/api", openRouter: true},
		{name: "azure", id: "openrouter", kind: "azure", baseURL: "http://azure.invalid", azure: true},
	} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%t", tc.name, stream), func(t *testing.T) {
				seen := make(chan w2B3FailoverRequest, 4)
				cfg := w2B3IsolationUpstream(t, seen, tc.id, tc.kind, tc.baseURL, false)
				var p providers.Provider
				var err error
				if tc.azure {
					p, err = azure.New(tc.id, cfg)
				} else {
					p, err = openai.New(tc.id, cfg)
				}
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = p.Close() })
				// Even a Gemini/OpenRouter model spelling must not opt a plain
				// provider into receiving the opaque continuation fields.
				req := w2B3IsolationRequest(t, "google/gemini-3.5-flash", stream)
				before, err := json.Marshal(req)
				if err != nil {
					t.Fatal(err)
				}
				w2B3IsolationDispatch(t, p, &req, stream)
				w2B3AssertIsolation(t, w2B3FailoverSeen(t, seen, tc.id), tc.openRouter)
				after, err := json.Marshal(req)
				if err != nil || string(after) != string(before) {
					t.Fatalf("dispatch mutated canonical request: before=%s after=%s err=%v", before, after, err)
				}

				// Reuse the SAME transcript on a different instance with the same
				// ID. A prior non-OpenRouter attempt must not erase its state.
				router, err := openai.New(tc.id, w2B3IsolationUpstream(t, seen, tc.id, "openrouter", "http://router-proxy.invalid/api", false))
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = router.Close() })
				w2B3IsolationDispatch(t, router, &req, stream)
				w2B3AssertIsolation(t, w2B3FailoverSeen(t, seen, tc.id), true)
			})
		}
	}
}

func w2B3IsolationDispatch(t *testing.T, p providers.Provider, req *models.ChatCompletionRequest, stream bool) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if !stream {
		if _, err := p.ChatCompletion(ctx, req); err != nil {
			t.Fatal(err)
		}
		return
	}
	chunks, errs := p.StreamChatCompletion(ctx, req)
	for range chunks {
	}
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
}

// Inspect raw JSON keys rather than decoding back into the model (which could
// conceal unsupported or empty extension fields). All unrelated message/tool
// fields, IDs and arguments must remain byte-for-byte equivalent as JSON values.
func w2B3AssertIsolation(t *testing.T, body string, openRouter bool) {
	t.Helper()
	var got struct {
		Messages []map[string]any `json:"messages"`
	}
	if err := json.Unmarshal([]byte(body), &got); err != nil {
		t.Fatal(err)
	}
	var want []map[string]any
	if err := json.Unmarshal([]byte(w2B3IsolationMessages), &want); err != nil {
		t.Fatal(err)
	}
	if !openRouter {
		for _, message := range want {
			delete(message, "reasoning")
			delete(message, "reasoning_details")
			if calls, ok := message["tool_calls"].([]any); ok {
				for _, call := range calls {
					delete(call.(map[string]any), "extra_content")
				}
			}
		}
	}
	if !reflect.DeepEqual(got.Messages, want) {
		t.Errorf("upstream continuation isolation (OpenRouter=%t): got %#v, want %#v", openRouter, got.Messages, want)
	}
}

// Real gateway failover: OpenRouter -> plain OpenAI-compatible -> optional
// OpenRouter. The second hop must receive no extensions, while a third hop
// must still receive every signature from the canonical transcript.
func TestW2B3ContinuationIsolationFailover(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, returnToRouter := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%t/return_to_router=%t", stream, returnToRouter), func(t *testing.T) {
				seen := make(chan w2B3FailoverRequest, 8)
				cfg := config.DefaultConfig()
				cfg.Auth.Enabled = true
				cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic", Key: "sk-synthetic-w2-b3", KeyType: "internal", Metadata: map[string]string{"org_id": "org-w2-b3"}}}
				cfg.Providers = map[string]config.ProviderConfig{
					"router-a": w2B3IsolationUpstream(t, seen, "router-a", "openrouter", "http://router-a.invalid/api", true),
					"plain-b":  w2B3IsolationUpstream(t, seen, "plain-b", "openai", "http://plain-b.invalid", returnToRouter),
				}
				targets := []config.RoutingTargetConfig{
					{Provider: "router-a", Weight: 1, ModelOverride: "gpt-4o"},
					{Provider: "plain-b", Weight: 1, ModelOverride: "gpt-4o"},
				}
				if returnToRouter {
					cfg.Providers["router-c"] = w2B3IsolationUpstream(t, seen, "router-c", "openrouter", "http://router-c.invalid/api", false)
					targets = append(targets, config.RoutingTargetConfig{Provider: "router-c", Weight: 1, ModelOverride: "gpt-4o"})
				}
				cfg.Routing.DefaultStrategy = "least-latency"
				cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"support-chat": targets}
				cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: len(targets), OnStatusCodes: []int{http.StatusBadGateway}}
				cfg.Routing.Retry.Enabled = false
				cfg.Routing.CircuitBreaker.Enabled = false
				registry, err := providers.NewRegistry(cfg)
				if err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = registry.Close() })
				registry.Router().RecordLatency("router-a", time.Millisecond)
				registry.Router().RecordLatency("plain-b", time.Hour)
				if returnToRouter {
					registry.Router().RecordLatency("router-c", 2*time.Hour)
				}
				keys := auth.NewKeyStore(cfg.Auth)
				srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenant.NewStore(), nil)
				srv.ready.Store(true)
				t.Cleanup(srv.handlers.orgProviderCache.EvictAll)
				response := w2B3Chat(t, srv, w2B3IsolationRequest(t, "support-chat", stream))
				if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "Sunny") {
					t.Fatalf("failover response: status=%d body=%s", response.Code, response.Body)
				}
				w2B3AssertIsolation(t, w2B3FailoverSeen(t, seen, "router-a"), true)
				w2B3AssertIsolation(t, w2B3FailoverSeen(t, seen, "plain-b"), false)
				if returnToRouter {
					w2B3AssertIsolation(t, w2B3FailoverSeen(t, seen, "router-c"), true)
				}
				select {
				case extra := <-seen:
					t.Fatalf("unexpected extra attempt: %s", extra.provider)
				default:
				}
			})
		}
	}
}

// Every upstream exchange stays in a net.Pipe, including URLs whose hostnames
// exercise instance detection. No DNS, sockets, credentials or live provider.
func w2B3IsolationUpstream(t *testing.T, seen chan<- w2B3FailoverRequest, id, kind, baseURL string, fail bool) config.ProviderConfig {
	t.Helper()
	dial := func(_ context.Context, _, _ string) (net.Conn, error) {
		client, peer := net.Pipe()
		go func() {
			defer peer.Close()
			r, err := http.ReadRequest(bufio.NewReader(peer))
			if err != nil {
				t.Errorf("read synthetic request: %v", err)
				return
			}
			body, err := io.ReadAll(r.Body)
			_ = r.Body.Close()
			if err != nil {
				t.Errorf("read synthetic body: %v", err)
				return
			}
			seen <- w2B3FailoverRequest{provider: id, body: string(body)}
			var request struct {
				Stream bool `json:"stream"`
			}
			if err := json.Unmarshal(body, &request); err != nil {
				t.Errorf("decode synthetic request: %v", err)
				return
			}
			status, contentType := http.StatusOK, "application/json"
			response := `{"id":"gen-isolation","model":"gpt-4o","choices":[{"index":0,"message":{"role":"assistant","content":"Sunny"},"finish_reason":"stop"}]}`
			if request.Stream {
				contentType = "text/event-stream"
				response = "data: " + `{"id":"gen-isolation","model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":"Sunny"},"finish_reason":"stop"}]}` + "\n\ndata: [DONE]\n\n"
			}
			if fail {
				status, contentType = http.StatusBadGateway, "application/json"
				response = `{"error":{"message":"synthetic failure","type":"server_error"}}`
			}
			out := http.Response{StatusCode: status, ProtoMajor: 1, ProtoMinor: 1, Close: true,
				Header: http.Header{"Content-Type": {contentType}}, ContentLength: int64(len(response)), Body: io.NopCloser(strings.NewReader(response))}
			_ = out.Write(peer)
		}()
		return client, nil
	}
	off := false
	return config.ProviderConfig{APIFormat: "openai", Type: kind, BaseURL: baseURL,
		APIKey: "synthetic-isolation-key", Models: []string{"gpt-4o"}, AutoDiscover: &off,
		DefaultTimeout: 5 * time.Second, DialContext: dial}
}
