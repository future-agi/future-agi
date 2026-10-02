package server

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
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

// wireCapture holds the last request body the synthetic upstream received.
// The upstream handler runs on the test server's goroutine, so access is
// mutex-guarded rather than relying on the HTTP round-trip for ordering.
type wireCapture struct {
	mu   sync.Mutex
	body map[string]json.RawMessage
}

func (c *wireCapture) set(b map[string]json.RawMessage) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.body = b
}

func (c *wireCapture) has(key string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.body[key]
	return ok
}

func (c *wireCapture) get(key string) (json.RawMessage, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	v, ok := c.body[key]
	return v, ok
}

// thinkingDropFixtureOpts tunes the synthetic upstream.
type thinkingDropFixtureOpts struct {
	stream         bool
	upstreamStatus int // 0 means 200
	plugins        []pipeline.Plugin
}

// newThinkingDropFixture starts a local synthetic upstream speaking the given
// API format and returns a gateway wired to it plus the observer and the
// upstream wire capture.
func newThinkingDropFixture(t *testing.T, format, model string, stream bool) (*Server, *thinkingDropObserver, *wireCapture) {
	return newThinkingDropFixtureWith(t, format, model, thinkingDropFixtureOpts{stream: stream})
}

func newThinkingDropFixtureWith(t *testing.T, format, model string, opts thinkingDropFixtureOpts) (*Server, *thinkingDropObserver, *wireCapture) {
	t.Helper()
	stream := opts.stream
	wire := &wireCapture{}
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
		wire.set(captured)
		if opts.upstreamStatus != 0 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(opts.upstreamStatus)
			_, _ = w.Write([]byte(`{"error":{"message":"synthetic upstream failure","type":"server_error"}}`))
			return
		}
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
	plugins := append([]pipeline.Plugin{observer}, opts.plugins...)
	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
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
			if wire.has("anthropic_thinking_config") {
				t.Fatal("anthropic_thinking_config leaked to the upstream wire")
			}
			if wire.has("thinking") {
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
			if wire.has("anthropic_thinking_config") {
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
	raw, ok := wire.get("thinking")
	if !ok {
		t.Fatal("native route must forward the raw thinking config upstream")
	}
	if !bytes.Contains(raw, []byte(`"enabled"`)) || !bytes.Contains(raw, []byte(`1024`)) {
		t.Fatalf("native thinking config altered: %s", raw)
	}
	if wire.has("anthropic_thinking_config") {
		t.Fatal("native route must not carry the internal canonical field")
	}
}

// shortCircuitPlugin answers every request itself, like a cache hit, so the
// provider is never called.
type shortCircuitPlugin struct{ calls int }

func (p *shortCircuitPlugin) Name() string  { return "thinking-drop-short-circuit" }
func (p *shortCircuitPlugin) Priority() int { return 20 }
func (p *shortCircuitPlugin) ProcessRequest(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	p.calls++
	return pipeline.ResultShortCircuit(&models.ChatCompletionResponse{
		ID:     "short-circuit",
		Object: "chat.completion",
		Model:  "fixture-model",
		Choices: []models.Choice{{
			Index:        0,
			Message:      models.Message{Role: "assistant", Content: json.RawMessage(`"SHORT_CIRCUIT_OK"`)},
			FinishReason: "stop",
		}},
	})
}
func (p *shortCircuitPlugin) ProcessResponse(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}

// TestAnthropicMessagesThinkingDropSurvivesPipelineShortCircuit verifies the
// reason is determined before the pipeline runs: a plugin that answers a
// streaming request itself still yields the header and metadata, the existing
// JSON-for-short-circuit behavior is preserved, and the upstream is never hit.
func TestAnthropicMessagesThinkingDropSurvivesPipelineShortCircuit(t *testing.T) {
	sc := &shortCircuitPlugin{}
	srv, observer, wire := newThinkingDropFixtureWith(t, "openai", "gpt-4o-mini", thinkingDropFixtureOpts{stream: true, plugins: []pipeline.Plugin{sc}})
	body := anthropicRequest("gpt-4o-mini", json.RawMessage(`{"type":"enabled","budget_tokens":1024}`))
	body["stream"] = true
	response := postAnthropicMessages(t, srv, body)

	if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "SHORT_CIRCUIT_OK") {
		t.Fatalf("short-circuit must still answer: status=%d body=%s", response.Code, response.Body.String())
	}
	if sc.calls != 1 {
		t.Fatalf("short-circuit plugin calls = %d, want 1", sc.calls)
	}
	if got := response.Header().Get("x-agentcc-translation-drops"); got != thinkingDropReason {
		t.Fatalf("header = %q, want %q", got, thinkingDropReason)
	}
	if observer.drops != thinkingDropReason {
		t.Fatalf("metadata = %q, want %q", observer.drops, thinkingDropReason)
	}
	if observer.hasCarrier {
		t.Fatal("carrier must be gone before the short-circuiting plugin runs")
	}
	if ct := response.Header().Get("Content-Type"); !strings.HasPrefix(ct, "application/json") {
		t.Fatalf("short-circuit on a stream request must keep the existing JSON answer, got Content-Type %q", ct)
	}
	if wire.has("model") {
		t.Fatal("upstream must not be called on a pipeline short circuit")
	}
}

// TestAnthropicMessagesThinkingDropSurvivesUpstreamError verifies an upstream
// failure after the boundary keeps its existing error semantics and still
// carries the already-determined reason once.
func TestAnthropicMessagesThinkingDropSurvivesUpstreamError(t *testing.T) {
	srv, observer, wire := newThinkingDropFixtureWith(t, "openai", "gpt-4o-mini", thinkingDropFixtureOpts{upstreamStatus: http.StatusBadGateway})
	response := postAnthropicMessages(t, srv, anthropicRequest("gpt-4o-mini", json.RawMessage(`{"type":"enabled","budget_tokens":1024}`)))

	if response.Code == http.StatusOK {
		t.Fatalf("upstream 502 must not become a successful answer: body=%s", response.Body.String())
	}
	if response.Code != http.StatusBadGateway {
		t.Fatalf("status = %d, want %d (existing upstream error mapping)", response.Code, http.StatusBadGateway)
	}
	if !strings.Contains(response.Body.String(), `"type":"error"`) {
		t.Fatalf("expected Anthropic-format error body, got %s", response.Body.String())
	}
	if got := response.Header().Get("x-agentcc-translation-drops"); got != thinkingDropReason {
		t.Fatalf("header = %q, want %q", got, thinkingDropReason)
	}
	if observer.drops != thinkingDropReason {
		t.Fatalf("metadata = %q, want %q", observer.drops, thinkingDropReason)
	}
	if wire.has("anthropic_thinking_config") {
		t.Fatal("carrier leaked to upstream on the error path")
	}
}

// TestAppendUniqueDrop pins the dedup branch: an already-present reason is not
// appended again and the existing order is preserved.
func TestAppendUniqueDrop(t *testing.T) {
	cases := []struct {
		name string
		in   []string
		want []string
	}{
		{"appends when absent", []string{"top_k_unsupported"}, []string{"top_k_unsupported", thinkingDropReason}},
		{"nil input", nil, []string{thinkingDropReason}},
		{"skips when present", []string{"a", thinkingDropReason}, []string{"a", thinkingDropReason}},
		{"skips when present and later", []string{thinkingDropReason, "z"}, []string{thinkingDropReason, "z"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := appendUniqueDrop(tc.in, thinkingDropReason)
			if strings.Join(got, ",") != strings.Join(tc.want, ",") {
				t.Fatalf("appendUniqueDrop(%v) = %v, want %v", tc.in, got, tc.want)
			}
		})
	}
}
