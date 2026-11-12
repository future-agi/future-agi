package server

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// An unused same-ID org override cannot change the representation of
// a global instance. Also exercise an internal key that really selects an org
// instance: ignoring every internal key's org config is not a valid repair.
func TestW2B3SelectedInstanceHandlerReplay(t *testing.T) {
	for _, native := range []bool{true, false} {
		for _, actualOrg := range []bool{false, true} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("native=%t/org=%t/stream=%t", native, actualOrg, stream), func(t *testing.T) {
					srv, seen := w2B3SelectedServer(t, native, actualOrg)
					maxTokens := 512
					req := models.ChatCompletionRequest{Model: "gemini-3.5-flash", MaxTokens: &maxTokens, Stream: stream,
						Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Weather?"`)}}}
					first := w2B3Chat(t, srv, req)
					if first.Code != http.StatusOK {
						t.Fatalf("initial status=%d body=%s", first.Code, first.Body)
					}
					w2B3Seen(t, seen)
					message := w2B3WireAssistant(t, first.Body.String(), stream)
					if len(message.ToolCalls) != 1 {
						t.Fatalf("expected one returned tool call: %+v", message)
					}
					id := message.ToolCalls[0].ID
					if native && !strings.HasSuffix(id, "::sig::synthetic-signature") {
						t.Fatalf("native adapter lost its signature: %q", id)
					}
					if !native && id != "call_weather" {
						t.Fatalf("OpenRouter ID changed: %q", id)
					}
					req.Messages = append(req.Messages, message, models.Message{Role: "tool", ToolCallID: id, Content: json.RawMessage(`"Sunny"`)})
					replay := w2B3Chat(t, srv, req)
					if replay.Code != http.StatusOK || !strings.Contains(replay.Body.String(), "Sunny") {
						t.Fatalf("selected instance replay rejected: status=%d body=%s", replay.Code, replay.Body)
					}
					body := w2B3Seen(t, seen)
					if native {
						if !strings.Contains(body, `"thoughtSignature":"synthetic-signature"`) || !strings.Contains(body, `"functionResponse"`) {
							t.Fatalf("native upstream did not receive restored signature and result: %s", body)
						}
					} else if !strings.Contains(body, `"thought_signature":"synthetic-signature"`) || !strings.Contains(body, `"tool_call_id":"call_weather"`) {
						t.Fatalf("OpenRouter upstream lost continuation: %s", body)
					}
				})
			}
		}
	}
}

func w2B3Seen(t *testing.T, seen <-chan string) string {
	t.Helper()
	select {
	case body := <-seen:
		return body
	default:
		t.Fatal("successful handler response never reached the selected upstream")
		return ""
	}
}

func w2B3SelectedServer(t *testing.T, native, actualOrg bool) (*Server, <-chan string) {
	t.Helper()
	seen := make(chan string, 4)
	wantKey := "synthetic-operator-key"
	if actualOrg {
		wantKey = "synthetic-org-key"
	}
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
			seen <- string(b)
			stream := strings.Contains(r.URL.Path, "streamGenerateContent") || strings.Contains(string(b), `"stream":true`)
			replay := strings.Contains(string(b), `"functionResponse"`) || strings.Contains(string(b), `"role":"tool"`)
			var body string
			if native {
				if r.Host != "native.invalid" || r.Header.Get("x-goog-api-key") != wantKey {
					t.Errorf("wrong native instance: host=%s", r.Host)
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
				if r.Host != "openrouter.ai" || r.URL.Path != "/api/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer "+wantKey {
					t.Errorf("wrong OpenRouter instance: host=%s path=%s", r.Host, r.URL.Path)
				}
				message := `{"role":"assistant","tool_calls":[{"index":0,"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-signature"}}}]}`
				if replay {
					message = `{"role":"assistant","content":"Sunny"}`
				}
				body = w2B3Response(message)
				if stream {
					body = `data: {"id":"gen-selected","choices":[{"index":0,"delta":` + message + `}]}` + "\n\ndata: [DONE]\n\n"
				}
			}
			contentType := "application/json"
			if stream {
				contentType = "text/event-stream"
			}
			response := http.Response{StatusCode: http.StatusOK, ProtoMajor: 1, ProtoMinor: 1, Close: true,
				Header: http.Header{"Content-Type": {contentType}}, ContentLength: int64(len(body)), Body: io.NopCloser(strings.NewReader(body))}
			_ = response.Write(peer)
		}()
		return client, nil
	}
	off := false
	global := config.ProviderConfig{APIFormat: "gemini", BaseURL: "http://native.invalid", APIKey: "synthetic-operator-key",
		Models: []string{"gemini-3.5-flash"}, AutoDiscover: &off, DialContext: dial}
	decoy := &tenant.ProviderConfig{Enabled: true, APIKey: "synthetic-org-key", APIFormat: "openai", BaseURL: "https://openrouter.ai/api", Models: []string{"gpt-4o"}}
	if !native {
		global.APIFormat, global.Type, global.BaseURL = "openai", "openrouter", "http://openrouter.ai/api"
		decoy.APIFormat, decoy.BaseURL = "gemini", "https://generativelanguage.googleapis.com"
	}
	if actualOrg {
		// An inherited org instance is built without DNS/network discovery.
		decoy.APIFormat, decoy.BaseURL, decoy.Models = global.APIFormat, "", []string{"gemini-3.5-flash"}
	}
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic", Key: "sk-synthetic-w2-b3", KeyType: "internal", Metadata: map[string]string{"org_id": "org-w2-b3"}}}
	cfg.Providers = map[string]config.ProviderConfig{"custom": global}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = registry.Close() })
	tenants := tenant.NewStore()
	tenants.Set("org-w2-b3", &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"custom": decoy}})
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	t.Cleanup(srv.handlers.orgProviderCache.EvictAll)
	return srv, seen
}

func w2B3WireAssistant(t *testing.T, body string, stream bool) models.Message {
	t.Helper()
	if !stream {
		var reply models.ChatCompletionResponse
		if err := json.Unmarshal([]byte(body), &reply); err != nil || len(reply.Choices) != 1 {
			t.Fatalf("invalid reply: %s err=%v", body, err)
		}
		return reply.Choices[0].Message
	}
	message := map[string]any{"role": "assistant"}
	call := map[string]any{}
	for _, line := range strings.Split(body, "\n") {
		if !strings.HasPrefix(line, "data: ") || line == "data: [DONE]" {
			continue
		}
		var chunk struct {
			Choices []struct {
				Delta map[string]any `json:"delta"`
			} `json:"choices"`
		}
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &chunk); err != nil {
			t.Fatal(err)
		}
		for _, choice := range chunk.Choices {
			for key, value := range choice.Delta {
				if key != "tool_calls" {
					message[key] = value
					continue
				}
				for _, raw := range value.([]any) {
					for field, v := range raw.(map[string]any) {
						if field != "index" {
							call[field] = v
						}
					}
				}
				message["tool_calls"] = []any{call}
			}
		}
	}
	b, err := json.Marshal(message)
	if err != nil {
		t.Fatal(err)
	}
	var result models.Message
	if err := json.Unmarshal(b, &result); err != nil {
		t.Fatal(err)
	}
	return result
}
