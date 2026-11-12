package server

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
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

const w2B3Model = "google/gemini-3.5-flash"
const w2B3Details = `[{"type":"reasoning.encrypted","data":"synthetic-signature","format":"google-gemini-v1","index":0,"future_metadata":"preserve"}]`
const w2B3Call = `{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"}}`

// Run the real router, org provider cache and OpenAI adapter, with all HTTP
// exchanges confined to in-memory pipes. No listener, DNS or live model.
func w2B3InheritedServer(t *testing.T, respond func(models.ChatCompletionRequest) string) (*Server, *tenant.OrgConfig, <-chan models.ChatCompletionRequest) {
	t.Helper()
	seen := make(chan models.ChatCompletionRequest, 8)
	dial := func(_ context.Context, _, _ string) (net.Conn, error) {
		client, peer := net.Pipe()
		go func() {
			defer peer.Close()
			r, err := http.ReadRequest(bufio.NewReader(peer))
			if err != nil {
				t.Errorf("reading synthetic upstream request: %v", err)
				return
			}
			var req models.ChatCompletionRequest
			err = json.NewDecoder(r.Body).Decode(&req)
			_ = r.Body.Close()
			if err != nil {
				t.Errorf("decoding synthetic upstream request: %v", err)
				return
			}
			if r.Host != "openrouter.ai" || r.URL.Path != "/api/v1/chat/completions" || r.Header.Get("Authorization") != "Bearer synthetic-org-key" {
				t.Errorf("request did not inherit upstream with org credential: host=%s path=%s", r.Host, r.URL.Path)
			}
			seen <- req
			body := respond(req)
			contentType := "application/json"
			if req.Stream {
				contentType = "text/event-stream"
			}
			resp := http.Response{StatusCode: http.StatusOK, ProtoMajor: 1, ProtoMinor: 1,
				Header: http.Header{"Content-Type": {contentType}}, Close: true,
				ContentLength: int64(len(body)), Body: io.NopCloser(strings.NewReader(body))}
			_ = resp.Write(peer)
		}()
		return client, nil
	}
	off := false
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "synthetic", Key: "sk-synthetic-w2-b3", Owner: "test", KeyType: "byok", Metadata: map[string]string{"org_id": "org-w2-b3"}}}
	cfg.Providers = map[string]config.ProviderConfig{"custom-router": {
		APIFormat: "openai", Type: "openrouter", BaseURL: "http://openrouter.ai/api", Managed: false,
		APIKey: "synthetic-operator-key", Models: []string{w2B3Model}, AutoDiscover: &off, DialContext: dial,
	}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = registry.Close() })
	org := &tenant.OrgConfig{Providers: map[string]*tenant.ProviderConfig{"custom-router": {
		Enabled: true, APIKey: "synthetic-org-key", APIFormat: "openai", Models: []string{w2B3Model},
	}}}
	tenants := tenant.NewStore()
	tenants.Set("org-w2-b3", org)
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true)), keys, nil, nil, nil, testModelDBPtr(), tenants, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	t.Cleanup(srv.handlers.orgProviderCache.EvictAll)
	return srv, org, seen
}

func w2B3Chat(t *testing.T, srv *Server, req models.ChatCompletionRequest) *httptest.ResponseRecorder {
	t.Helper()
	body, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	r := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", strings.NewReader(string(body)))
	r.Header.Set("Authorization", "Bearer sk-synthetic-w2-b3")
	r.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, r)
	return w
}

func w2B3Response(message string) string {
	return `{"id":"gen-w2-b3","object":"chat.completion","model":"` + w2B3Model + `","choices":[{"index":0,"message":` + message + `,"finish_reason":"stop"}],"usage":{"prompt_tokens":8,"completion_tokens":3,"total_tokens":11}}`
}

// A non-managed configured OpenRouter upstream is inherited even
// though the enabled org BYOK provider leaves BaseURL empty.
func TestW2B3InheritedOpenRouterHandlerReplay(t *testing.T) {
	for _, representation := range []string{"extra_content", "reasoning_details"} {
		t.Run(representation, func(t *testing.T) {
			message := `{"role":"assistant","tool_calls":[` + w2B3Call + `],"reasoning_details":` + w2B3Details + `}`
			if representation == "extra_content" {
				message = `{"role":"assistant","tool_calls":[{"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"},"extra_content":{"google":{"thought_signature":"synthetic-signature"}}}]}`
			}
			srv, _, seen := w2B3InheritedServer(t, func(req models.ChatCompletionRequest) string {
				if len(req.Messages) == 1 {
					return w2B3Response(message)
				}
				return w2B3Response(`{"role":"assistant","content":"Sunny"}`)
			})
			maxTokens := 512
			req := models.ChatCompletionRequest{Model: w2B3Model, MaxTokens: &maxTokens, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Weather?"`)}}}
			first := w2B3Chat(t, srv, req)
			if first.Code != http.StatusOK {
				t.Fatalf("initial completion: %d %s", first.Code, first.Body)
			}
			<-seen
			var reply models.ChatCompletionResponse
			if err := json.Unmarshal(first.Body.Bytes(), &reply); err != nil || len(reply.Choices) != 1 {
				t.Fatalf("initial reply: %s, err=%v", first.Body, err)
			}
			req.Messages = append(req.Messages, reply.Choices[0].Message, models.Message{Role: "tool", ToolCallID: "call_weather", Content: json.RawMessage(`"Sunny"`)})
			second := w2B3Chat(t, srv, req)
			if second.Code != http.StatusOK || !strings.Contains(second.Body.String(), "Sunny") {
				t.Fatalf("replay rejected: %d %s", second.Code, second.Body)
			}
			upstream := <-seen
			if len(upstream.Messages) != 3 || upstream.Messages[2].ToolCallID != "call_weather" {
				t.Fatalf("replay did not reach inherited upstream intact: %+v", upstream.Messages)
			}
			got, _ := json.Marshal(upstream.Messages[1])
			want, _ := json.Marshal(reply.Choices[0].Message)
			if string(got) != string(want) {
				t.Fatalf("continuation changed: got %s want %s", got, want)
			}
		})
	}
}

// Exercise the real chat stream startup, with encrypted state
// arriving after the optional metadata budget, before any tool output.
func TestW2B3StreamHandlerPreservesLateSignature(t *testing.T) {
	srv, _, seen := w2B3InheritedServer(t, func(req models.ChatCompletionRequest) string {
		if !req.Stream {
			return w2B3Response(`{"role":"assistant","content":"Sunny"}`)
		}
		var body strings.Builder
		write := func(delta string) {
			fmt.Fprintf(&body, "data: {\"id\":\"gen-w2-b3\",\"model\":\"%s\",\"choices\":[{\"index\":0,\"delta\":%s}]}\n\n", w2B3Model, delta)
		}
		for i := 0; i < 3*maxStreamPrefixChunks; i++ {
			write(`{"role":"assistant","reasoning":"step"}`)
		}
		write(`{"reasoning_details":` + w2B3Details + `}`)
		write(`{"tool_calls":[{"index":0,"id":"call_weather","type":"function","function":{"name":"weather","arguments":"{}"}}]}`)
		body.WriteString("data: [DONE]\n\n")
		return body.String()
	})
	maxTokens := 512
	req := models.ChatCompletionRequest{Model: w2B3Model, MaxTokens: &maxTokens, Stream: true, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"Weather?"`)}}}
	w := w2B3Chat(t, srv, req)
	if w.Code != http.StatusOK {
		t.Fatalf("small required prefix should survive independently: %d %s", w.Code, w.Body)
	}
	<-seen
	message := map[string]any{"role": "assistant"}
	var calls []models.ToolCall
	var details json.RawMessage
	metadataCount, signaturePosition, toolPosition, position := 0, -1, -1, 0
	for _, line := range strings.Split(w.Body.String(), "\n") {
		if !strings.HasPrefix(line, "data: ") || strings.TrimPrefix(line, "data: ") == "[DONE]" {
			continue
		}
		var chunk struct {
			Choices []struct {
				Delta struct {
					Reasoning        *string                `json:"reasoning"`
					ReasoningDetails json.RawMessage        `json:"reasoning_details"`
					ToolCalls        []models.ToolCallDelta `json:"tool_calls"`
				} `json:"delta"`
			} `json:"choices"`
		}
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &chunk); err != nil {
			t.Fatal(err)
		}
		for _, choice := range chunk.Choices {
			d := choice.Delta
			if d.Reasoning != nil {
				metadataCount++
			}
			if len(d.ReasoningDetails) > 0 {
				signaturePosition = position
				details = d.ReasoningDetails
			}
			for _, tc := range d.ToolCalls {
				if tc.Function == nil {
					t.Fatal("expected tool function output")
				}
				toolPosition = position
				calls = append(calls, models.ToolCall{ID: tc.ID, Type: tc.Type, Function: *tc.Function})
			}
		}
		position++
	}
	if signaturePosition < 0 || toolPosition <= signaturePosition || string(details) != w2B3Details {
		t.Fatalf("successful unreplayable stream: signature position=%d tool position=%d details=%s", signaturePosition, toolPosition, details)
	}
	if metadataCount != maxStreamPrefixChunks {
		t.Fatalf("optional metadata bound changed: got %d want %d", metadataCount, maxStreamPrefixChunks)
	}
	message["tool_calls"], message["reasoning_details"] = calls, details
	encoded, err := json.Marshal(message)
	if err != nil {
		t.Fatal(err)
	}
	var replayMessage models.Message
	if err := json.Unmarshal(encoded, &replayMessage); err != nil {
		t.Fatal(err)
	}
	req.Stream = false
	req.Messages = append(req.Messages, replayMessage, models.Message{Role: "tool", ToolCallID: "call_weather", Content: json.RawMessage(`"Sunny"`)})
	replay := w2B3Chat(t, srv, req)
	if replay.Code != http.StatusOK {
		t.Fatalf("stream replay: %d %s", replay.Code, replay.Body)
	}
	<-seen
}

func TestW2B3StreamStartupRequiredSignatureBudget(t *testing.T) {
	for _, managed := range []bool{false, true} {
		for _, budget := range []string{"optional-count", "optional-bytes", "required-count", "required-bytes"} {
			t.Run(fmt.Sprintf("managed=%t/%s", managed, budget), func(t *testing.T) {
				signature := w2B3SignatureChunk(t, w2B3Details)
				var before []models.StreamChunk
				wantError := strings.HasPrefix(budget, "required-")
				switch budget {
				case "optional-count":
					for i := 0; i < 3*maxStreamPrefixChunks; i++ {
						before = append(before, models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant"}}}})
					}
				case "optional-bytes":
					before = append(before, models.StreamChunk{SystemFingerprint: strings.Repeat("x", maxStreamPrefixBytes-200)})
				case "required-count":
					for i := 0; i < maxStreamPrefixChunks; i++ {
						before = append(before, signature)
					}
				case "required-bytes":
					signature = w2B3SignatureChunk(t, `[{"type":"reasoning.encrypted","format":"google-gemini-v1","data":"`+strings.Repeat("x", maxStreamPrefixBytes)+`"}]`)
				}
				before = append(before, signature)
				chunks, errs := make(chan models.StreamChunk, len(before)+1), make(chan error)
				for _, chunk := range before {
					chunks <- chunk
				}
				chunks <- models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{ID: "call_weather", Function: &models.FunctionCall{Name: "weather", Arguments: "{}"}}}}}}}
				close(chunks)
				close(errs)
				first, prefixes, err := waitForManagedStreamStart(context.Background(), chunks, errs, managed)
				if wantError {
					var apiErr *models.APIError
					if !errors.As(err, &apiErr) || apiErr.Status != http.StatusBadGateway || !strings.Contains(apiErr.Message, "stream limit") {
						t.Fatalf("oversized required state silently accepted: first=%+v err=%v", first, err)
					}
					return
				}
				preserved := false
				for _, prefix := range prefixes {
					encoded, marshalErr := json.Marshal(prefix)
					if marshalErr != nil {
						t.Fatal(marshalErr)
					}
					preserved = preserved || strings.Contains(string(encoded), `"reasoning_details":`+w2B3Details)
				}
				if err != nil || first == nil || !preserved {
					t.Fatalf("required signature crowded out by optional metadata: prefixes=%d err=%v", len(prefixes), err)
				}
			})
		}
	}
}

// Decode wire fixtures rather than naming new production fields, so RED
// compiles on the base and fails for dropped continuation state, not missing symbols.
func w2B3SignatureChunk(t *testing.T, details string) models.StreamChunk {
	t.Helper()
	var chunk models.StreamChunk
	if err := json.Unmarshal([]byte(`{"choices":[{"index":0,"delta":{"reasoning_details":`+details+`}}]}`), &chunk); err != nil {
		t.Fatal(err)
	}
	return chunk
}
