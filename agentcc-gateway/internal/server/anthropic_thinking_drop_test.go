package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
)

// thinkingDropObserver records what the pipeline saw for the request so the
// tests can assert on request metadata and on the canonical request's Extra
// without reaching into provider internals.
type thinkingDropObserver struct {
	drops      string
	hasCarrier bool
	sawRequest bool
}

func (p *thinkingDropObserver) Name() string  { return "thinking-drop-observer" }
func (p *thinkingDropObserver) Priority() int { return 10 }
func (p *thinkingDropObserver) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.drops = rc.Metadata["translation_drops"]
	if rc.Request != nil {
		p.sawRequest = true
		_, p.hasCarrier = rc.Request.Extra["anthropic_thinking_config"]
	}
	return pipeline.ResultContinue()
}
func (p *thinkingDropObserver) ProcessResponse(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

const thinkingDropReason = "thinking_unsupported_on_backend"

// newThinkingDropFixture starts a local synthetic upstream speaking the given
// API format and returns a gateway wired to it plus the observer and a pointer
// to the last upstream request body captured on the wire.
func newThinkingDropFixture(t *testing.T, format, model string, stream bool) (*Server, *thinkingDropObserver, *map[string]json.RawMessage) {
	t.Helper()
	wire := &map[string]json.RawMessage{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"data":[]}`))
			return
		}
		captured := map[string]json.RawMessage{}
		if err := json.NewDecoder(r.Body).Decode(&captured); err != nil {
			t.Errorf("decode upstream request: %v", err)
		}
		*wire = captured
		switch format {
		case "openai":
			if stream {
				w.Header().Set("Content-Type", "text/event-stream")
				_, _ = w.Write([]byte("data: {\"id\":\"fx\",\"object\":\"chat.completion.chunk\",\"model\":\"fixture-model\",\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"FIXTURE_OK\"},\"finish_reason\":null}]}\n\n"))
				_, _ = w.Write([]byte("data: {\"id\":\"fx\",\"object\":\"chat.completion.chunk\",\"model\":\"fixture-model\",\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":1,\"completion_tokens\":1,\"total_tokens\":2}}\n\n"))
				_, _ = w.Write([]byte("data: [DONE]\n\n"))
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"fx","object":"chat.completion","model":"fixture-model","choices":[{"index":0,"message":{"role":"assistant","content":"FIXTURE_OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`))
		case "gemini":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"candidates":[{"content":{"role":"model","parts":[{"text":"FIXTURE_OK"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1,"totalTokenCount":2}}`))
		case "bedrock":
			w.Header().Set("Content-Type", "application/json")
			if strings.HasSuffix(r.URL.Path, "/converse") {
				_, _ = w.Write([]byte(`{"output":{"message":{"role":"assistant","content":[{"text":"FIXTURE_OK"}]}},"stopReason":"end_turn","usage":{"inputTokens":1,"outputTokens":1,"totalTokens":2}}`))
				return
			}
			_, _ = w.Write([]byte(`{"id":"fx","type":"message","role":"assistant","model":"fixture-model","content":[{"type":"text","text":"FIXTURE_OK"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`))
		default: // anthropic native
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"id":"fx","type":"message","role":"assistant","model":"fixture-model","content":[{"type":"text","text":"FIXTURE_OK"}],"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1}}`))
		}
	}))
	t.Cleanup(upstream.Close)

	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = false
	cfg.Providers = map[string]config.ProviderConfig{"fixture": {
		BaseURL:            upstream.URL,
		APIKey:             "test-fixture-key",
		APIFormat:          format,
		Models:             []string{model},
		AWSAccessKeyID:     "test-fixture-access-key",
		AWSSecretAccessKey: "test-fixture-secret",
		AWSRegion:          "us-east-1",
	}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	observer := &thinkingDropObserver{}
	srv := New(cfg, "", registry, pipeline.NewEngine(observer), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv, observer, wire
}

func postAnthropicMessages(t *testing.T, srv *Server, body map[string]any) *httptest.ResponseRecorder {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatalf("marshal request: %v", err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/messages", bytes.NewReader(raw))
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("anthropic-version", "2023-06-01")
	response := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(response, request)
	return response
}

func anthropicRequest(model string, thinking json.RawMessage) map[string]any {
	req := map[string]any{
		"model":      model,
		"max_tokens": 64,
		"messages":   []map[string]string{{"role": "user", "content": "hello"}},
	}
	if thinking != nil {
		req["thinking"] = thinking
	}
	return req
}

// TestAnthropicMessagesRecordsThinkingDropOnTranslatedBackends covers the
// translated /v1/messages path: a parsed thinking config that the resolved
// backend cannot honor must be removed from the canonical request before the
// pipeline/provider see it, and recorded exactly once on both existing
// diagnostic surfaces, while the request still succeeds normally.
func TestAnthropicMessagesRecordsThinkingDropOnTranslatedBackends(t *testing.T) {
	enabled := json.RawMessage(`{"type":"enabled","budget_tokens":1024}`)
	cases := []struct {
		name   string
		format string
		model  string
		stream bool
		think  json.RawMessage
	}{
		{"openai nonstream enabled", "openai", "gpt-4o-mini", false, enabled},
		{"openai stream enabled", "openai", "gpt-4o-mini", true, enabled},
		{"openai disabled", "openai", "gpt-4o-mini", false, json.RawMessage(`{"type":"disabled"}`)},
		{"openai empty object", "openai", "gpt-4o-mini", false, json.RawMessage(`{}`)},
		{"gemini enabled", "gemini", "gemini-3.7-flash", false, enabled},
		{"bedrock claude invoke", "bedrock", "anthropic.claude-3-7-sonnet-20250219-v1:0", false, enabled},
		{"bedrock claude converse", "bedrock", "us.anthropic.claude-3-7-sonnet-20250219-v1:0", false, enabled},
		{"bedrock llama converse", "bedrock", "us.meta.llama3-3-70b-instruct-v1:0", false, enabled},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, observer, wire := newThinkingDropFixture(t, tc.format, tc.model, tc.stream)
			body := anthropicRequest(tc.model, tc.think)
			if tc.stream {
				body["stream"] = true
			}
			response := postAnthropicMessages(t, srv, body)

			if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "FIXTURE_OK") {
				t.Fatalf("request must still succeed: status=%d body=%s", response.Code, response.Body.String())
			}
			if got := response.Header().Get("x-agentcc-translation-drops"); got != thinkingDropReason {
				t.Fatalf("x-agentcc-translation-drops = %q, want %q", got, thinkingDropReason)
			}
			if observer.drops != thinkingDropReason {
				t.Fatalf("rc.Metadata[translation_drops] = %q, want %q", observer.drops, thinkingDropReason)
			}
			if !observer.sawRequest {
				t.Fatal("pipeline never saw the canonical request")
			}
			if observer.hasCarrier {
				t.Fatal("anthropic_thinking_config must be removed before the pipeline sees the request")
			}
			if _, leaked := (*wire)["anthropic_thinking_config"]; leaked {
				t.Fatal("anthropic_thinking_config leaked to the upstream wire")
			}
			if _, invented := (*wire)["thinking"]; invented {
				t.Fatal("translated route must not invent an upstream thinking field")
			}
			if strings.Contains(response.Body.String(), `"type":"thinking"`) {
				t.Fatal("translated route must not synthesize thinking blocks")
			}
		})
	}
}

// TestAnthropicMessagesThinkingDropComposesWithExistingDrops verifies the new
// reason is appended after existing translator drops in the single
// comma-joined emission, with no duplicates and no added whitespace.
func TestAnthropicMessagesThinkingDropComposesWithExistingDrops(t *testing.T) {
	srv, observer, _ := newThinkingDropFixture(t, "openai", "gpt-4o-mini", false)
	body := anthropicRequest("gpt-4o-mini", json.RawMessage(`{"type":"enabled","budget_tokens":1024}`))
	body["top_k"] = 40
	response := postAnthropicMessages(t, srv, body)
	if response.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	const want = "top_k_unsupported," + thinkingDropReason
	if got := response.Header().Get("x-agentcc-translation-drops"); got != want {
		t.Fatalf("header = %q, want %q", got, want)
	}
	if observer.drops != want {
		t.Fatalf("metadata = %q, want %q", observer.drops, want)
	}
}

// TestAnthropicMessagesNoThinkingDropWithoutConfig verifies omitted and null
// thinking produce no new reason and no empty diagnostic header.
func TestAnthropicMessagesNoThinkingDropWithoutConfig(t *testing.T) {
	cases := []struct {
		name  string
		think json.RawMessage
	}{
		{"omitted", nil},
		{"null", json.RawMessage(`null`)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			srv, observer, wire := newThinkingDropFixture(t, "openai", "gpt-4o-mini", false)
			response := postAnthropicMessages(t, srv, anthropicRequest("gpt-4o-mini", tc.think))
			if response.Code != http.StatusOK {
				t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
			}
			if _, present := response.Header()["X-Agentcc-Translation-Drops"]; present {
				t.Fatalf("no diagnostic header expected, got %q", response.Header().Get("x-agentcc-translation-drops"))
			}
			if observer.drops != "" {
				t.Fatalf("metadata = %q, want empty", observer.drops)
			}
			if observer.hasCarrier {
				t.Fatal("no carrier expected without a thinking config")
			}
			if _, leaked := (*wire)["anthropic_thinking_config"]; leaked {
				t.Fatal("unexpected carrier on upstream wire")
			}
		})
	}
}

// TestAnthropicMessagesNativeBackendKeepsThinking verifies the native Anthropic
// fast path is untouched: raw thinking is forwarded and no drop is recorded.
func TestAnthropicMessagesNativeBackendKeepsThinking(t *testing.T) {
	srv, observer, wire := newThinkingDropFixture(t, "anthropic", "claude-3-7-sonnet-20250219", false)
	response := postAnthropicMessages(t, srv, anthropicRequest("claude-3-7-sonnet-20250219", json.RawMessage(`{"type":"enabled","budget_tokens":1024}`)))
	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "FIXTURE_OK") {
		t.Fatalf("status=%d body=%s", response.Code, response.Body.String())
	}
	if _, present := response.Header()["X-Agentcc-Translation-Drops"]; present {
		t.Fatalf("native route must not record a drop, got %q", response.Header().Get("x-agentcc-translation-drops"))
	}
	if observer.drops != "" {
		t.Fatalf("native metadata = %q, want empty", observer.drops)
	}
	raw, ok := (*wire)["thinking"]
	if !ok {
		t.Fatal("native route must forward the raw thinking config upstream")
	}
	if !bytes.Contains(raw, []byte(`"enabled"`)) || !bytes.Contains(raw, []byte(`1024`)) {
		t.Fatalf("native thinking config altered: %s", raw)
	}
	if _, leaked := (*wire)["anthropic_thinking_config"]; leaked {
		t.Fatal("native route must not carry the internal canonical field")
	}
}
