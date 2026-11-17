package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	openaiprov "github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// officialOpenAI is a real OpenAI provider pointed at a fake upstream. Only the
// official API host turns the Responses route on, and a test server cannot
// have that host, so this answers ChatNeedsResponses as the official API does.
type officialOpenAI struct{ *openaiprov.Provider }

func (p officialOpenAI) ChatNeedsResponses(req *models.ChatCompletionRequest) bool {
	return len(req.Tools) > 0 && strings.HasPrefix(req.Model, "gpt-5.6")
}

// openAIUpstream fakes OpenAI for tool calls: chat completions refuse function
// tools on gpt-5.6 and serve them on other models; /v1/responses serves them.
type openAIUpstream struct {
	mu   sync.Mutex
	hits map[string]int
}

func (u *openAIUpstream) count(path string) int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.hits[path]
}

func (u *openAIUpstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	u.mu.Lock()
	u.hits[r.URL.Path]++
	u.mu.Unlock()

	var req struct {
		Model  string            `json:"model"`
		Stream bool              `json:"stream"`
		Tools  []json.RawMessage `json:"tools"`
	}
	json.NewDecoder(r.Body).Decode(&req)

	switch r.URL.Path {
	case "/v1/chat/completions":
		if strings.HasPrefix(req.Model, "gpt-5.6") && len(req.Tools) > 0 {
			w.WriteHeader(http.StatusBadRequest)
			fmt.Fprintf(w, `{"error":{"message":"Function tools with reasoning_effort are not supported for %s in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.","type":"invalid_request_error","param":"tools","code":null}}`, req.Model)
			return
		}
		io.WriteString(w, `{"id":"chatcmpl-1","object":"chat.completion","created":1761580000,"model":"`+req.Model+`","choices":[{"index":0,"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call_9","type":"function","function":{"name":"get_weather","arguments":"{\"city\":\"Paris\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":20,"completion_tokens":5,"total_tokens":25}}`)
	case "/v1/responses":
		final := `{"id":"resp_1","object":"response","created_at":1761580000,"status":"completed","model":"` + req.Model + `","output":[{"id":"rs_1","type":"reasoning","summary":[]},{"id":"fc_1","type":"function_call","status":"completed","call_id":"call_2","name":"get_weather","arguments":"{\"city\":\"Paris\"}"}],"usage":{"input_tokens":120,"input_tokens_details":{"cached_tokens":0},"output_tokens":40,"output_tokens_details":{"reasoning_tokens":16},"total_tokens":160}}`
		if !req.Stream {
			io.WriteString(w, final)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		for _, data := range []string{
			`{"type":"response.created","response":{"id":"resp_1","status":"in_progress","model":"` + req.Model + `","output":[]}}`,
			`{"type":"response.output_item.added","output_index":0,"item":{"id":"rs_1","type":"reasoning","summary":[]}}`,
			`{"type":"response.output_item.done","output_index":0,"item":{"id":"rs_1","type":"reasoning","summary":[]}}`,
			`{"type":"response.output_item.added","output_index":1,"item":{"id":"fc_1","type":"function_call","status":"in_progress","arguments":"","call_id":"call_2","name":"get_weather"}}`,
			`{"type":"response.function_call_arguments.delta","item_id":"fc_1","output_index":1,"delta":"{\"city\":"}`,
			`{"type":"response.function_call_arguments.delta","item_id":"fc_1","output_index":1,"delta":"\"Paris\"}"}`,
			`{"type":"response.output_item.done","output_index":1,"item":{"id":"fc_1","type":"function_call","status":"completed","arguments":"{\"city\":\"Paris\"}","call_id":"call_2","name":"get_weather"}}`,
			`{"type":"response.completed","response":` + final + `}`,
		} {
			var ev struct {
				Type string `json:"type"`
			}
			json.Unmarshal([]byte(data), &ev)
			fmt.Fprintf(w, "event: %s\ndata: %s\n\n", ev.Type, data)
		}
	default:
		http.NotFound(w, r)
	}
}

func newResponsesBridgeServer(t *testing.T) (*Server, *openAIUpstream) {
	t.Helper()
	upstream := &openAIUpstream{hits: map[string]int{}}
	ts := httptest.NewServer(upstream)
	t.Cleanup(ts.Close)

	cfg := config.DefaultConfig()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	p, err := openaiprov.New("openai", config.ProviderConfig{BaseURL: ts.URL, APIKey: "test-key"})
	if err != nil {
		t.Fatalf("creating provider: %v", err)
	}
	registry.RegisterProvider("openai", officialOpenAI{p}, []string{"gpt-5.6-terra", "gpt-5.5"})

	srv := New(cfg, "", registry, pipeline.NewEngine(), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv, upstream
}

func postToolMessage(t *testing.T, srv *Server, model string, stream bool) *httptest.ResponseRecorder {
	t.Helper()
	body := fmt.Sprintf(`{"model":%q,"max_tokens":1024,"stream":%t,
		"tools":[{"name":"get_weather","description":"Weather for a city","input_schema":{"type":"object","properties":{"city":{"type":"string"}}}}],
		"messages":[{"role":"user","content":"Weather in Paris?"}]}`, model, stream)
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// gpt-5.6 refuses function tools on chat completions while it reasons, so a
// /v1/messages request with tools used to come back as OpenAI's 400.
func TestAnthropicMessagesCarriesGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)

	w := postToolMessage(t, srv, "gpt-5.6-terra", false)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	var msg struct {
		StopReason string `json:"stop_reason"`
		Content    []struct {
			Type  string          `json:"type"`
			Name  string          `json:"name"`
			Input json.RawMessage `json:"input"`
		} `json:"content"`
		Usage struct {
			InputTokens  int `json:"input_tokens"`
			OutputTokens int `json:"output_tokens"`
		} `json:"usage"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &msg); err != nil {
		t.Fatalf("decoding %s: %v", w.Body.String(), err)
	}
	if msg.StopReason != "tool_use" {
		t.Errorf("stop_reason = %q, want tool_use", msg.StopReason)
	}
	var toolUse bool
	for _, c := range msg.Content {
		if c.Type == "tool_use" && c.Name == "get_weather" && string(c.Input) == `{"city":"Paris"}` {
			toolUse = true
		}
	}
	if !toolUse {
		t.Errorf("content = %s, want a get_weather tool_use", w.Body.String())
	}
	if msg.Usage.InputTokens != 120 || msg.Usage.OutputTokens != 40 {
		t.Errorf("usage = %+v, want 120 in, 40 out", msg.Usage)
	}
	if upstream.count("/v1/chat/completions") != 0 || upstream.count("/v1/responses") != 1 {
		t.Errorf("upstream hits = %v, want one /v1/responses call", upstream.hits)
	}
}

func TestAnthropicMessagesStreamsGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)

	w := postToolMessage(t, srv, "gpt-5.6-terra", true)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	for _, want := range []string{
		`"type":"tool_use"`,
		`"name":"get_weather"`,
		`"stop_reason":"tool_use"`,
		"event: message_stop",
	} {
		if !strings.Contains(body, want) {
			t.Errorf("stream lacks %s:\n%s", want, body)
		}
	}
	var args strings.Builder
	for _, line := range strings.Split(body, "\n") {
		data, ok := strings.CutPrefix(line, "data: ")
		if !ok {
			continue
		}
		var ev struct {
			Delta struct {
				Type        string `json:"type"`
				PartialJSON string `json:"partial_json"`
			} `json:"delta"`
		}
		if json.Unmarshal([]byte(data), &ev) == nil && ev.Delta.Type == "input_json_delta" {
			args.WriteString(ev.Delta.PartialJSON)
		}
	}
	if args.String() != `{"city":"Paris"}` {
		t.Errorf("streamed tool input = %q, want {\"city\":\"Paris\"}", args.String())
	}
	if strings.Contains(body, "event: error") {
		t.Errorf("stream carries an error event:\n%s", body)
	}
	if upstream.count("/v1/chat/completions") != 0 || upstream.count("/v1/responses") != 1 {
		t.Errorf("upstream hits = %v, want one /v1/responses call", upstream.hits)
	}
}

// Models that take tools on chat completions keep that route.
func TestAnthropicMessagesKeepsChatCompletionsForOtherModels(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)

	w := postToolMessage(t, srv, "gpt-5.5", false)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if upstream.count("/v1/chat/completions") != 1 || upstream.count("/v1/responses") != 0 {
		t.Errorf("upstream hits = %v, want one /v1/chat/completions call", upstream.hits)
	}
}

// The GenAI route translates to a chat request the same way, so a Gemini SDK
// caller using gpt-5.6 with tools needs the same route.
func TestGenAIGenerateContentCarriesGPT56ToolsOverResponses(t *testing.T) {
	srv, upstream := newResponsesBridgeServer(t)

	body := `{"contents":[{"role":"user","parts":[{"text":"Weather in Paris?"}]}],
		"tools":[{"functionDeclarations":[{"name":"get_weather","description":"Weather for a city","parameters":{"type":"object","properties":{"city":{"type":"string"}}}}]}]}`
	req := httptest.NewRequest(http.MethodPost, "/v1beta/models/gpt-5.6-terra:generateContent", bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	var resp struct {
		Candidates []struct {
			Content struct {
				Parts []struct {
					FunctionCall *struct {
						Name string          `json:"name"`
						Args json.RawMessage `json:"args"`
					} `json:"functionCall"`
				} `json:"parts"`
			} `json:"content"`
		} `json:"candidates"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil || len(resp.Candidates) == 0 {
		t.Fatalf("decoding %s: %v", w.Body.String(), err)
	}
	var called bool
	for _, part := range resp.Candidates[0].Content.Parts {
		if part.FunctionCall != nil && part.FunctionCall.Name == "get_weather" && string(part.FunctionCall.Args) == `{"city":"Paris"}` {
			called = true
		}
	}
	if !called {
		t.Errorf("response = %s, want a get_weather functionCall", w.Body.String())
	}
	if upstream.count("/v1/chat/completions") != 0 || upstream.count("/v1/responses") != 1 {
		t.Errorf("upstream hits = %v, want one /v1/responses call", upstream.hits)
	}
}
