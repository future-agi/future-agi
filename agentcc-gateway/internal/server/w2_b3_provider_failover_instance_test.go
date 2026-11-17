package server

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestW2B3ProviderFailoverSelectedInstanceReplayNonStream(t *testing.T) {
	w2B3ProviderFailoverReplay(t, false)
}

func TestW2B3ProviderFailoverSelectedInstanceReplayStream(t *testing.T) {
	w2B3ProviderFailoverReplay(t, true)
}

// The org lists the upstream model, but NOT the requested global alias. Its
// same-ID override must not determine how the selected global fallback replays
// a tool call, in either direction (native Gemini or OpenRouter).
func w2B3ProviderFailoverReplay(t *testing.T, stream bool) {
	t.Helper()
	for _, format := range []string{"native", "openrouter"} {
		t.Run(format, func(t *testing.T) {
			native := format == "native"
			srv, seen := w2B3ProviderFailoverServer(t, native)
			maxTokens := 512
			req := models.ChatCompletionRequest{Model: "support-chat", Stream: stream, MaxTokens: &maxTokens,
				Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Weather?"`)}}}
			first := w2B3Chat(t, srv, req)
			if first.Code != http.StatusOK {
				t.Fatalf("initial response: status=%d body=%s", first.Code, first.Body)
			}
			w2B3FailoverSeen(t, seen, "global-a")
			message := w2B3WireAssistant(t, first.Body.String(), stream)
			if len(message.ToolCalls) != 1 {
				t.Fatalf("expected one signed tool call: %+v", message)
			}
			id := message.ToolCalls[0].ID
			if native && !strings.HasSuffix(id, "::sig::synthetic-signature") {
				t.Fatalf("native tool call lost its signature: %q", id)
			}
			if !native && id != "call_weather" {
				t.Fatalf("OpenRouter tool ID changed: %q", id)
			}
			req.Messages = append(req.Messages, message, models.Message{Role: "tool", ToolCallID: id, Content: json.RawMessage(`"Sunny"`)})
			replay := w2B3Chat(t, srv, req)
			// The first provider fails ONLY when replayed. Reaching it proves
			// primary admission accepted the history; reaching global-b proves
			// this request actually exercised provider-failover admission.
			w2B3FailoverSeen(t, seen, "global-a")
			if replay.Code != http.StatusOK || !strings.Contains(replay.Body.String(), "Sunny") {
				t.Fatalf("global failover rejected signed replay: status=%d body=%s", replay.Code, replay.Body)
			}
			body := w2B3FailoverSeen(t, seen, "global-b")
			if native {
				if !strings.Contains(body, `"thoughtSignature":"synthetic-signature"`) || !strings.Contains(body, `"functionResponse"`) {
					t.Fatalf("native fallback lost signature or tool result: %s", body)
				}
			} else if !strings.Contains(body, `"thought_signature":"synthetic-signature"`) || !strings.Contains(body, `"tool_call_id":"call_weather"`) {
				t.Fatalf("OpenRouter fallback lost continuation: %s", body)
			}
			select {
			case extra := <-seen:
				t.Fatalf("unexpected extra provider attempt: %s", extra.provider)
			default:
			}
		})
	}
}

type w2B3FailoverRequest struct {
	provider string
	body     string
}

func w2B3FailoverSeen(t *testing.T, seen <-chan w2B3FailoverRequest, provider string) string {
	t.Helper()
	select {
	case got := <-seen:
		if got.provider != provider {
			t.Fatalf("provider attempt=%s, want %s", got.provider, provider)
		}
		return got.body
	default:
		t.Fatalf("request never reached %s", provider)
		return ""
	}
}

func w2B3ProviderFailoverServer(t *testing.T, native bool) (*Server, <-chan w2B3FailoverRequest) {
	t.Helper()
	seen := make(chan w2B3FailoverRequest, 8)
	provider := func(id string) config.ProviderConfig {
		dial := func(_ context.Context, _, _ string) (net.Conn, error) {
			client, peer := net.Pipe()
			go func() {
				defer peer.Close()
				r, err := http.ReadRequest(bufio.NewReader(peer))
				if err != nil {
					t.Errorf("read upstream: %v", err)
					return
				}
				b, err := io.ReadAll(r.Body)
				_ = r.Body.Close()
				if err != nil {
					t.Errorf("read body: %v", err)
					return
				}
				seen <- w2B3FailoverRequest{provider: id, body: string(b)}
				stream := strings.Contains(r.URL.Path, "streamGenerateContent") || strings.Contains(string(b), `"stream":true`)
				replay := strings.Contains(string(b), `"functionResponse"`) || strings.Contains(string(b), `"role":"tool"`)
				var body string
				if native {
					if r.Host != "native.invalid" || r.Header.Get("x-goog-api-key") != "synthetic-operator-key" || !strings.Contains(r.URL.Path, "/models/gemini-3.5-flash:") {
						t.Errorf("wrong native instance/model: host=%s path=%s", r.Host, r.URL.Path)
					}
					part := `{"functionCall":{"name":"weather","args":{}},"thoughtSignature":"synthetic-signature"}`
					if replay {
						part = `{"text":"Sunny"}`
					}
					body = `{"candidates":[{"content":{"role":"model","parts":[` + part + `]},"finishReason":"STOP"}]}`
					if stream {
						body = "data: " + body + "\n\n"
					}
				} else {
					if r.Host != "openrouter.ai" || r.URL.Path != "/api/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer synthetic-operator-key" || !strings.Contains(string(b), `"model":"gemini-3.5-flash"`) {
						t.Errorf("wrong OpenRouter instance/model: host=%s path=%s", r.Host, r.URL.Path)
					}
					message := `{"role":"assistant","tool_calls":[{"index":0,"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-signature"}}}]}`
					if replay {
						message = `{"role":"assistant","content":"Sunny"}`
					}
					body = `{"id":"gen-failover","model":"gemini-3.5-flash","choices":[{"index":0,"message":` + message + `,"finish_reason":"stop"}]}`
					if stream {
						body = `data: {"id":"gen-failover","choices":[{"index":0,"delta":` + message + `}]}` + "\n\ndata: [DONE]\n\n"
					}
				}
				status, contentType := http.StatusOK, "application/json"
				if stream {
					contentType = "text/event-stream"
				}
				if replay && id == "global-a" {
					status, contentType = http.StatusBadGateway, "application/json"
					body = `{"error":{"code":502,"message":"synthetic primary failure","status":"UNAVAILABLE"}}`
				}
				response := http.Response{StatusCode: status, ProtoMajor: 1, ProtoMinor: 1, Close: true,
					Header: http.Header{"Content-Type": {contentType}}, ContentLength: int64(len(body)), Body: io.NopCloser(strings.NewReader(body))}
				_ = response.Write(peer)
			}()
			return client, nil
		}
		off := false
		pc := config.ProviderConfig{APIFormat: "gemini", BaseURL: "http://native.invalid", APIKey: "synthetic-operator-key",
			Models: []string{"gemini-3.5-flash"}, AutoDiscover: &off, DialContext: dial}
		if !native {
			pc.APIFormat, pc.Type, pc.BaseURL = "openai", "openrouter", "http://openrouter.ai/api"
		}
		return pc
	}
	decoy := &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-org-key", APIFormat: "openai",
		BaseURL: "https://openrouter.ai/api", Models: []string{"gemini-3.5-flash"}}
	if !native {
		decoy.APIFormat, decoy.BaseURL = "gemini", "https://generativelanguage.googleapis.com"
	}
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic", Key: "sk-synthetic-w2-b3", KeyType: "internal", Metadata: map[string]string{"org_id": "org-w2-b3"}}}
	cfg.Providers = map[string]config.ProviderConfig{"global-a": provider("global-a"), "global-b": provider("global-b")}
	cfg.Routing.DefaultStrategy = "least-latency"
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"support-chat": {
		{Provider: "global-a", Weight: 1, ModelOverride: "gemini-3.5-flash"},
		{Provider: "global-b", Weight: 1, ModelOverride: "gemini-3.5-flash"},
	}}
	cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusBadGateway}}
	cfg.Routing.Retry.Enabled = false
	cfg.Routing.CircuitBreaker.Enabled = false
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = registry.Close() })
	// Both initial routing and failover must choose global-a first, even after
	// the first successful round trip updates its measured latency.
	registry.Router().RecordLatency("global-a", time.Millisecond)
	registry.Router().RecordLatency("global-b", time.Hour)
	tenants := tenant.NewStore()
	tenants.Set("org-w2-b3", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"global-b": decoy}})
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	t.Cleanup(srv.handlers.orgProviderCache.EvictAll)
	return srv, seen
}
