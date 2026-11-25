package server

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	openaiprov "github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// bridgeUsage is the usage both fake routes report, so a call carried over the
// Responses API can be compared with the same call on chat completions.
const (
	bridgeChatUsage      = `{"prompt_tokens":120,"prompt_tokens_details":{"cached_tokens":0},"completion_tokens":40,"completion_tokens_details":{"reasoning_tokens":16},"total_tokens":160}`
	bridgeResponsesUsage = `{"input_tokens":120,"input_tokens_details":{"cached_tokens":0},"output_tokens":40,"output_tokens_details":{"reasoning_tokens":16},"total_tokens":160}`
)

// openAIUpstream fakes OpenAI for tool calls: chat completions refuse function
// tools on gpt-5.6 unless reasoning_effort is "none", and serve them on other
// models; /v1/responses serves them. Both stream and both report bridge usage.
type openAIUpstream struct {
	mu     sync.Mutex
	hits   map[string]int
	bodies map[string][]byte // the last request body per path
	// failResponses answers /v1/responses with a 503, as an outage would.
	failResponses bool
	// responsesThinks holds a /v1/responses stream this long after
	// response.created, as a model reasoning before its first output.
	responsesThinks time.Duration
	// responsesFailAfterThinking ends that stream with response.failed right
	// after the thinking pause, before any output.
	responsesFailAfterThinking bool
	// failChatModel answers /v1/chat/completions for this model with a 502,
	// so the gateway's failover or model fallback takes over.
	failChatModel string
}

func (u *openAIUpstream) count(path string) int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.hits[path]
}

func (u *openAIUpstream) body(path string) []byte {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.bodies[path]
}

func (u *openAIUpstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	raw, _ := io.ReadAll(r.Body)
	u.mu.Lock()
	u.hits[r.URL.Path]++
	if u.bodies == nil {
		u.bodies = map[string][]byte{}
	}
	u.bodies[r.URL.Path] = raw
	failResponses := u.failResponses
	thinks := u.responsesThinks
	failAfterThinking := u.responsesFailAfterThinking
	failChatModel := u.failChatModel
	u.mu.Unlock()

	var req struct {
		Model  string `json:"model"`
		Stream bool   `json:"stream"`
		Tools  []struct {
			Type string `json:"type"`
		} `json:"tools"`
		ReasoningEffort *string `json:"reasoning_effort"`
	}
	json.Unmarshal(raw, &req)

	switch r.URL.Path {
	case "/v1/chat/completions":
		if failChatModel != "" && req.Model == failChatModel {
			w.WriteHeader(http.StatusBadGateway)
			io.WriteString(w, `{"error":{"message":"Bad gateway.","type":"server_error","param":null,"code":null}}`)
			return
		}
		reasons := req.ReasoningEffort == nil || *req.ReasoningEffort != "none"
		functionTools := false
		for _, tool := range req.Tools {
			functionTools = functionTools || tool.Type == "function"
		}
		if strings.HasPrefix(req.Model, "gpt-5.6") && functionTools && reasons {
			w.WriteHeader(http.StatusBadRequest)
			fmt.Fprintf(w, `{"error":{"message":"Function tools with reasoning_effort are not supported for %s in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.","type":"invalid_request_error","param":"tools","code":null}}`, req.Model)
			return
		}
		if !req.Stream {
			io.WriteString(w, `{"id":"chatcmpl-1","object":"chat.completion","created":1761580000,"model":"`+req.Model+`","choices":[{"index":0,"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call_9","type":"function","function":{"name":"get_weather","arguments":"{\"city\":\"Paris\"}"}}]},"finish_reason":"tool_calls"}],"usage":`+bridgeChatUsage+`}`)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		head := `{"id":"chatcmpl-1","object":"chat.completion.chunk","created":1761580000,"model":"` + req.Model + `",`
		for _, data := range []string{
			head + `"choices":[{"index":0,"delta":{"role":"assistant","content":null,"tool_calls":[{"index":0,"id":"call_9","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}`,
			head + `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"city\":"}}]},"finish_reason":null}]}`,
			head + `"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"Paris\"}"}}]},"finish_reason":null}]}`,
			head + `"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}`,
			head + `"choices":[],"usage":` + bridgeChatUsage + `}`,
			`[DONE]`,
		} {
			fmt.Fprintf(w, "data: %s\n\n", data)
		}
	case "/v1/responses":
		if msg := responsesShapeError(raw); msg != "" {
			w.WriteHeader(http.StatusBadRequest)
			_, _ = fmt.Fprintf(w, `{"error":{"message":%q,"type":"invalid_request_error","param":null,"code":null}}`, msg)
			return
		}
		if failResponses {
			w.WriteHeader(http.StatusServiceUnavailable)
			io.WriteString(w, `{"error":{"message":"The server is overloaded.","type":"server_error","param":null,"code":null}}`)
			return
		}
		final := `{"id":"resp_1","object":"response","created_at":1761580000,"status":"completed","model":"` + req.Model + `","output":[{"id":"rs_1","type":"reasoning","summary":[]},{"id":"fc_1","type":"function_call","status":"completed","call_id":"call_2","name":"get_weather","arguments":"{\"city\":\"Paris\"}"}],"usage":` + bridgeResponsesUsage + `}`
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
			if ev.Type == "response.created" && thinks > 0 {
				w.(http.Flusher).Flush()
				time.Sleep(thinks)
				if failAfterThinking {
					fmt.Fprint(w, "event: response.failed\ndata: {\"type\":\"response.failed\",\"response\":{\"id\":\"resp_1\",\"status\":\"failed\",\"error\":{\"code\":\"server_error\",\"message\":\"synthetic failure\"}}}\n\n")
					return
				}
			}
		}
	default:
		http.NotFound(w, r)
	}
}

// responsesShapeError checks a /v1/responses request the way the Responses API
// does for the parts the bridge writes, so a request-shape mistake fails the
// test as it would fail in production. It returns "" for a request it accepts.
func responsesShapeError(raw []byte) string {
	var req struct {
		Model string `json:"model"`
		Input []struct {
			Type    string `json:"type"`
			Role    string `json:"role"`
			Content []struct {
				Type     string `json:"type"`
				FileData string `json:"file_data"`
				FileID   string `json:"file_id"`
			} `json:"content"`
			CallID string `json:"call_id"`
		} `json:"input"`
		Tools []struct {
			Type       string          `json:"type"`
			Name       string          `json:"name"`
			Function   json.RawMessage `json:"function"`
			Parameters json.RawMessage `json:"parameters"`
		} `json:"tools"`
		ToolChoice json.RawMessage `json:"tool_choice"`
		Text       *struct {
			Format *struct {
				Type   string          `json:"type"`
				Name   *string         `json:"name"`
				Schema json.RawMessage `json:"schema"`
			} `json:"format"`
		} `json:"text"`
		Messages json.RawMessage `json:"messages"`
	}
	if err := json.Unmarshal(raw, &req); err != nil {
		return "invalid JSON: " + err.Error()
	}
	if req.Model == "" {
		return "Missing required parameter: 'model'."
	}
	if req.Messages != nil {
		return "Unknown parameter: 'messages'."
	}
	for i, item := range req.Input {
		switch item.Type {
		case "message":
			allowed := map[string]bool{"input_text": true, "input_image": true, "input_file": true}
			if item.Role == "assistant" {
				allowed = map[string]bool{"output_text": true, "refusal": true}
			}
			for j, c := range item.Content {
				if !allowed[c.Type] {
					return fmt.Sprintf("Invalid value: '%s'. Supported values for input[%d].content[%d].type", c.Type, i, j)
				}
				if c.Type == "input_file" && c.FileData == "" && c.FileID == "" {
					return fmt.Sprintf("input[%d].content[%d]: input_file needs file_data or file_id", i, j)
				}
			}
		case "function_call", "function_call_output":
			if item.CallID == "" {
				return fmt.Sprintf("Missing required parameter: 'input[%d].call_id'.", i)
			}
		default:
			return fmt.Sprintf("Invalid value: '%s' for input[%d].type", item.Type, i)
		}
	}
	for i, tool := range req.Tools {
		if tool.Type != "function" {
			return fmt.Sprintf("Invalid value: '%s' for tools[%d].type", tool.Type, i)
		}
		if tool.Function != nil || tool.Name == "" || tool.Parameters == nil {
			return fmt.Sprintf("Missing required parameter: 'tools[%d].name'.", i)
		}
	}
	if len(req.ToolChoice) > 0 && req.ToolChoice[0] == '{' {
		var choice struct {
			Type     string          `json:"type"`
			Name     string          `json:"name"`
			Function json.RawMessage `json:"function"`
			Mode     string          `json:"mode"`
			Tools    []struct {
				Type string `json:"type"`
				Name string `json:"name"`
			} `json:"tools"`
		}
		_ = json.Unmarshal(req.ToolChoice, &choice)
		switch {
		case choice.Type == "allowed_tools":
			if choice.Mode == "" || len(choice.Tools) == 0 {
				return "Invalid value for 'tool_choice.tools'."
			}
			for _, t := range choice.Tools {
				if t.Type != "function" || t.Name == "" {
					return "Invalid value for 'tool_choice.tools'."
				}
			}
		case choice.Type != "function" || choice.Name == "" || choice.Function != nil:
			return "Invalid value for 'tool_choice'."
		}
	}
	if req.Text != nil && req.Text.Format != nil && req.Text.Format.Type == "json_schema" {
		if req.Text.Format.Name == nil || *req.Text.Format.Name == "" {
			return "Invalid 'text.format.name': empty string. Expected a string with minimum length 1."
		}
	}
	return ""
}

// bridgeModels are the models the bridge test server registers.
var bridgeModels = []string{"gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.5"}

// bridgeServerOptions vary the bridge test server.
type bridgeServerOptions struct {
	// plain registers the provider as it is, an OpenAI-format upstream that
	// is not the official API, so the Responses route stays off.
	plain     bool
	configure func(*config.Config)
	plugins   []pipeline.Plugin
	// alsoAs registers more providers on the same upstream under these ids,
	// so failover has another provider to move to.
	alsoAs []string
}

// bridgeProviderConfig points an OpenAI provider at the fake upstream. Only the
// official API host turns the Responses route on, so unless plain is set the
// provider is configured for https://api.openai.com, as in production, and its
// connections are dialled to the fake instead: the gateway runs its own code
// for the official host, and nothing leaves the test.
func bridgeProviderConfig(ts *httptest.Server, plain bool) config.ProviderConfig {
	if plain {
		return config.ProviderConfig{BaseURL: ts.URL, APIKey: "test-key"}
	}
	return config.ProviderConfig{
		BaseURL: "http://" + officialOpenAIHost,
		APIKey:  "test-key",
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, network, ts.Listener.Addr().String())
		},
	}
}

// officialOpenAIHost is the official API host the bridge provider is
// configured for.
const officialOpenAIHost = "api.openai.com"

func newResponsesBridgeServer(t *testing.T) (*Server, *openAIUpstream) {
	t.Helper()
	return newResponsesBridgeServerWith(t, bridgeServerOptions{})
}

func newResponsesBridgeServerWith(t *testing.T, opts bridgeServerOptions) (*Server, *openAIUpstream) {
	t.Helper()
	upstream := &openAIUpstream{hits: map[string]int{}}
	ts := httptest.NewServer(upstream)
	t.Cleanup(ts.Close)

	cfg := config.DefaultConfig()
	if opts.configure != nil {
		opts.configure(cfg)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	provider, err := openaiprov.New("openai", bridgeProviderConfig(ts, opts.plain))
	if err != nil {
		t.Fatalf("creating provider: %v", err)
	}
	registry.RegisterProvider("openai", provider, bridgeModels)
	for _, id := range opts.alsoAs {
		other, err := openaiprov.New(id, bridgeProviderConfig(ts, opts.plain))
		if err != nil {
			t.Fatalf("creating provider %s: %v", id, err)
		}
		registry.RegisterProvider(id, other, nil)
	}

	srv := New(cfg, "", registry, pipeline.NewEngine(opts.plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	t.Cleanup(func() {
		if srv.asyncWorker != nil {
			srv.asyncWorker.Stop()
		}
	})
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
