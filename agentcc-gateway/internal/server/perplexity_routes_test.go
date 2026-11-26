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
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	openaiprov "github.com/futureagi/agentcc-gateway/internal/providers/openai"
)

// Perplexity's Agent API models ("perplexity/glm-5.3") answer only
// /v1/responses; on chat completions Perplexity returns 404, which the gateway
// used to pass on as a 502. A chat or messages call for one is now refused
// with a 400 before any upstream call.

const perplexityAgentModel = "perplexity/glm-5.3"

// perplexityUpstream fakes Perplexity: chat completions serve Sonar models and
// answer anything else with a bare 404, as Perplexity does; /v1/responses
// serves every model.
type perplexityUpstream struct {
	mu   sync.Mutex
	hits map[string]int
}

func (u *perplexityUpstream) count(path string) int {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.hits[path]
}

func (u *perplexityUpstream) total() int {
	u.mu.Lock()
	defer u.mu.Unlock()
	n := 0
	for _, c := range u.hits {
		n += c
	}
	return n
}

func (u *perplexityUpstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	raw, _ := io.ReadAll(r.Body)
	u.mu.Lock()
	u.hits[r.URL.Path]++
	u.mu.Unlock()
	var req struct {
		Model string `json:"model"`
	}
	json.Unmarshal(raw, &req)

	switch r.URL.Path {
	case "/v1/chat/completions":
		name := strings.TrimPrefix(req.Model, "perplexity/")
		if !strings.HasPrefix(name, "sonar") {
			w.WriteHeader(http.StatusNotFound)
			io.WriteString(w, "Not Found")
			return
		}
		io.WriteString(w, `{"id":"chatcmpl-p","object":"chat.completion","created":1761580000,"model":"`+req.Model+`","choices":[{"index":0,"message":{"role":"assistant","content":"Hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}`)
	case "/v1/responses":
		io.WriteString(w, `{"id":"resp_p","object":"response","created_at":1761580000,"status":"completed","model":"`+req.Model+`","output":[{"type":"message","id":"msg_p","status":"completed","role":"assistant","content":[{"type":"output_text","text":"Hello","annotations":[]}]}],"usage":{"input_tokens":5,"output_tokens":2,"total_tokens":7}}`)
	default:
		w.WriteHeader(http.StatusNotFound)
	}
}

// newPerplexityServer is a gateway with one provider, "perplexity", of type
// perplexity, pointed at a fake Perplexity.
func newPerplexityServer(t *testing.T, plugins ...pipeline.Plugin) (*Server, *perplexityUpstream) {
	t.Helper()
	upstream := &perplexityUpstream{hits: map[string]int{}}
	ts := httptest.NewServer(upstream)
	t.Cleanup(ts.Close)

	cfg := config.DefaultConfig()
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	provider, err := openaiprov.New("perplexity", config.ProviderConfig{Type: "perplexity", BaseURL: ts.URL, APIKey: "test-key"})
	if err != nil {
		t.Fatalf("creating provider: %v", err)
	}
	registry.RegisterProvider("perplexity", provider, []string{perplexityAgentModel, "sonar-pro", "glm-5.3"})

	srv := New(cfg, "", registry, pipeline.NewEngine(plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	t.Cleanup(func() {
		if srv.asyncWorker != nil {
			srv.asyncWorker.Stop()
		}
	})
	return srv, upstream
}

func postPerplexity(t *testing.T, srv *Server, path, body string, headers map[string]string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewBufferString(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

func perplexityChatBody(model string, stream bool) string {
	return fmt.Sprintf(`{"model":%q,"stream":%t,"messages":[{"role":"user","content":"Hi"}]}`, model, stream)
}

// checkRouteRefusal checks w is the OpenAI-shaped 400 route_not_supported
// for perplexityAgentModel.
func checkRouteRefusal(t *testing.T, w *httptest.ResponseRecorder) {
	t.Helper()
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400. Body: %s", w.Code, w.Body.String())
	}
	var body models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("not an OpenAI error body: %v\n%s", err, w.Body.String())
	}
	if body.Error.Code != "route_not_supported" || body.Error.Type != models.ErrTypeInvalidRequest {
		t.Errorf("error = %+v, want invalid_request_error/route_not_supported", body.Error)
	}
	if !strings.Contains(body.Error.Message, perplexityAgentModel) || !strings.Contains(body.Error.Message, "/v1/responses") {
		t.Errorf("message %q should name the model and /v1/responses", body.Error.Message)
	}
}

func TestChatCompletionsRefusesPerplexityAgentModel(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			srv, upstream := newPerplexityServer(t)
			w := postPerplexity(t, srv, "/v1/chat/completions", perplexityChatBody(perplexityAgentModel, stream), nil)
			checkRouteRefusal(t, w)
			if n := upstream.total(); n != 0 {
				t.Errorf("upstream calls = %d, want 0", n)
			}
		})
	}
}

// An async job is not accepted for a model the route cannot serve.
func TestChatCompletionsAsyncRefusesPerplexityAgentModel(t *testing.T) {
	srv, upstream := newPerplexityServer(t)
	w := postPerplexity(t, srv, "/v1/chat/completions", perplexityChatBody(perplexityAgentModel, false), map[string]string{"x-agentcc-async": "true"})
	checkRouteRefusal(t, w)
	if strings.Contains(w.Body.String(), "job_id") {
		t.Errorf("a job was accepted: %s", w.Body.String())
	}
	time.Sleep(50 * time.Millisecond)
	if n := upstream.total(); n != 0 {
		t.Errorf("upstream calls = %d, want 0", n)
	}
}

func TestAnthropicMessagesRefusesPerplexityAgentModel(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			srv, upstream := newPerplexityServer(t)
			body := fmt.Sprintf(`{"model":%q,"max_tokens":64,"stream":%t,"messages":[{"role":"user","content":"Hi"}]}`, perplexityAgentModel, stream)
			w := postPerplexity(t, srv, "/v1/messages", body, map[string]string{"anthropic-version": "2023-06-01"})
			if w.Code != http.StatusBadRequest {
				t.Fatalf("status = %d, want 400. Body: %s", w.Code, w.Body.String())
			}
			payload := decodeAnthropicError(t, w.Body.String())
			if payload.Type != "error" || payload.Error.Type != models.ErrTypeInvalidRequest {
				t.Errorf("error = %+v, want an Anthropic invalid_request_error", payload)
			}
			if payload.Error.Code != nil && *payload.Error.Code != "route_not_supported" {
				t.Errorf("code = %q, want route_not_supported", *payload.Error.Code)
			}
			if !strings.Contains(payload.Error.Message, perplexityAgentModel) || !strings.Contains(payload.Error.Message, "/v1/responses") {
				t.Errorf("message %q should name the model and /v1/responses", payload.Error.Message)
			}
			if n := upstream.total(); n != 0 {
				t.Errorf("upstream calls = %d, want 0", n)
			}
		})
	}
}

// The same model still works on the route that serves it.
func TestResponsesServesPerplexityAgentModel(t *testing.T) {
	srv, upstream := newPerplexityServer(t)
	body := fmt.Sprintf(`{"model":%q,"input":"Hi"}`, perplexityAgentModel)
	w := postPerplexity(t, srv, "/v1/responses", body, nil)
	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
	}
	if got := upstream.count("/v1/responses"); got != 1 {
		t.Errorf("/v1/responses hits = %d, want 1", got)
	}
}

// Sonar models stay on chat completions, with or without the provider prefix.
func TestChatCompletionsKeepsPerplexitySonarModels(t *testing.T) {
	for _, model := range []string{"sonar-pro", "perplexity/sonar-pro"} {
		t.Run(model, func(t *testing.T) {
			srv, upstream := newPerplexityServer(t)
			w := postPerplexity(t, srv, "/v1/chat/completions", perplexityChatBody(model, false), nil)
			if w.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200. Body: %s", w.Code, w.Body.String())
			}
			if got := upstream.count("/v1/chat/completions"); got != 1 {
				t.Errorf("/v1/chat/completions hits = %d, want 1", got)
			}
		})
	}
}

// A model the gateway cannot place, which Perplexity answers with a 404 on
// chat completions, is the caller's route, not an outage: a 400, not a 502.
func TestChatCompletionsPerplexityUpstream404IsNotAnOutage(t *testing.T) {
	srv, upstream := newPerplexityServer(t)
	w := postPerplexity(t, srv, "/v1/chat/completions", perplexityChatBody("glm-5.3", false), nil)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400. Body: %s", w.Code, w.Body.String())
	}
	var body models.ErrorResponse
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("not an OpenAI error body: %v\n%s", err, w.Body.String())
	}
	if body.Error.Code != "route_not_supported_upstream" || !strings.Contains(body.Error.Message, "/v1/responses") {
		t.Errorf("error = %+v, want route_not_supported_upstream naming /v1/responses", body.Error)
	}
	if got := w.Header().Get("x-agentcc-upstream-status"); got != "404" {
		t.Errorf("x-agentcc-upstream-status = %q, want 404", got)
	}
	if got := upstream.count("/v1/chat/completions"); got != 1 {
		t.Errorf("/v1/chat/completions hits = %d, want 1", got)
	}
}

// Request Logs record the refusal: a 400 with its reason and no cost.
func TestChatCompletionsPerplexityRefusalIsLogged(t *testing.T) {
	type row struct {
		Model        string  `json:"model"`
		StatusCode   int     `json:"status_code"`
		ErrorMessage string  `json:"error_message"`
		Cost         float64 `json:"cost"`
	}
	rows := make(chan row, 4)
	webhook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var batch struct {
			Logs []row `json:"logs"`
		}
		if err := json.NewDecoder(r.Body).Decode(&batch); err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		for _, l := range batch.Logs {
			rows <- l
		}
	}))
	t.Cleanup(webhook.Close)
	logs := logging.New(config.RequestLoggingConfig{Enabled: true}, nil)
	logs.SetFlusher(logging.NewLogFlusher(webhook.URL, "", time.Hour, 10))
	t.Cleanup(logs.Close)

	srv, upstream := newPerplexityServer(t, logs)
	w := postPerplexity(t, srv, "/v1/chat/completions", perplexityChatBody(perplexityAgentModel, false), nil)
	checkRouteRefusal(t, w)
	if n := upstream.total(); n != 0 {
		t.Errorf("upstream calls = %d, want 0", n)
	}
	logs.Close()
	select {
	case got := <-rows:
		if got.StatusCode != http.StatusBadRequest {
			t.Errorf("logged status = %d, want 400", got.StatusCode)
		}
		if !strings.Contains(got.ErrorMessage, "/v1/responses") {
			t.Errorf("logged error = %q, want the refusal's reason", got.ErrorMessage)
		}
		if got.Cost != 0 {
			t.Errorf("logged cost = %v, want 0", got.Cost)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("request log was not delivered")
	}
}
