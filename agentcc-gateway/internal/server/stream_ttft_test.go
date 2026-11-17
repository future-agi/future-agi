package server

import (
	"context"
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

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/translation"
)

// ttftContentDelay is how long a fixture upstream waits, after its role-only
// first chunk, before its first chunk of model output. A time to first
// token at or past it was measured to the output, not to the first chunk.
const ttftContentDelay = 50 * time.Millisecond

// ttftObserver is a post-plugin that keeps what the request's single
// post-plugin pass saw of rc.Timings["ttft"]. It copies the value inside
// ProcessResponse: rc is pooled and reused after the request.
type ttftObserver struct {
	mu    sync.Mutex
	calls int
	ttft  time.Duration
	set   bool
}

func (*ttftObserver) Name() string  { return "ttft-observer" }
func (*ttftObserver) Priority() int { return 999 }
func (*ttftObserver) ProcessRequest(context.Context, *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
func (o *ttftObserver) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.calls++
	o.ttft, o.set = rc.Timings[ttftTiming]
	return pipeline.ResultContinue()
}

func (o *ttftObserver) seen() (calls int, ttft time.Duration, set bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.calls, o.ttft, o.set
}

// requireTTFT checks the request's one post-plugin pass saw a time to first
// token of at least atLeast and under below.
func requireTTFT(t *testing.T, o *ttftObserver, atLeast, below time.Duration) {
	t.Helper()
	calls, ttft, set := o.seen()
	if calls != 1 {
		t.Fatalf("post-plugin passes = %d, want exactly one", calls)
	}
	if !set {
		t.Fatal("no ttft recorded for a streamed answer")
	}
	if ttft < atLeast || ttft >= below {
		t.Fatalf("ttft = %v, want in [%v, %v)", ttft, atLeast, below)
	}
}

// ttftDelayedProvider streams a role-only chunk at once, then its first
// chunk of output after delay, then the finish with usage.
type ttftDelayedProvider struct {
	providers.Provider
	delay time.Duration
}

func (p ttftDelayedProvider) StreamChatCompletion(ctx context.Context, _ *models.ChatCompletionRequest) (<-chan models.StreamChunk, <-chan error) {
	chunks, errs := make(chan models.StreamChunk, 4), make(chan error, 1)
	go func() {
		defer close(chunks)
		defer close(errs)
		chunks <- models.StreamChunk{ID: "ttft", Model: "resolved", Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Role: "assistant"}}}}
		select {
		case <-time.After(p.delay):
		case <-ctx.Done():
			return
		}
		text, finish := "hello", "stop"
		chunks <- models.StreamChunk{ID: "ttft", Model: "resolved", Choices: []models.StreamChoice{{Index: 0, Delta: models.Delta{Content: &text}}}}
		chunks <- models.StreamChunk{ID: "ttft", Model: "resolved", Choices: []models.StreamChoice{{Index: 0, FinishReason: &finish}}, Usage: models.MeasuredUsage(3, 1)}
	}()
	return chunks, errs
}

// Legacy completions and GenAI over the canonical providers time the call
// to its first chunk of output, not to its role-only first chunk, and
// record it once, before the request's single post-plugin pass.
func TestCanonicalStreamTTFT(t *testing.T) {
	for _, surface := range []string{"completions", "genai"} {
		t.Run(surface, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.IsStream, rc.Model, rc.Provider = true, "m", "fixture"
			rc.Request = &models.ChatCompletionRequest{Model: "m", Stream: true}
			post := &ttftObserver{}
			h := &Handlers{engine: pipeline.NewEngine(post)}
			w := httptest.NewRecorder()
			p := ttftDelayedProvider{delay: ttftContentDelay}
			if surface == "completions" {
				h.handleCompletionStream(ctx, w, rc, p)
			} else {
				translator, ok := translation.InboundFor("google")
				if !ok {
					t.Fatal("missing GenAI translator")
				}
				h.handleGenAIStreamViaCanonical(ctx, w, rc, p, translator, rc.Request)
			}
			if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "hello") {
				t.Fatalf("stream: status=%d body=%s", w.Code, w.Body.String())
			}
			requireTTFT(t, post, ttftContentDelay, ttftContentDelay+2*time.Second)
		})
	}
}

// ttftUpstream is an OpenAI-shaped upstream. Each call waits callDelay,
// then fails with status when it is non-zero. A stream sends a role-only
// chunk with empty content at once and its first output after
// contentDelay; a non-stream call answers after contentDelay.
type ttftUpstream struct {
	*httptest.Server
	status       int
	callDelay    time.Duration
	contentDelay time.Duration
	calls        atomic.Int32
}

func startTTFTUpstream(t *testing.T, status int, callDelay, contentDelay time.Duration) *ttftUpstream {
	t.Helper()
	u := &ttftUpstream{status: status, callDelay: callDelay, contentDelay: contentDelay}
	wait := func(ctx context.Context, d time.Duration) bool {
		select {
		case <-time.After(d):
			return true
		case <-ctx.Done():
			return false
		}
	}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
			w.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(w, `{"object":"list","data":[]}`)
			return
		}
		u.calls.Add(1)
		var req models.ChatCompletionRequest
		_ = json.NewDecoder(r.Body).Decode(&req)
		if !wait(r.Context(), u.callDelay) {
			return
		}
		if u.status != 0 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(u.status)
			_, _ = io.WriteString(w, `{"error":{"message":"upstream down","type":"server_error"}}`)
			return
		}
		if !req.Stream {
			if !wait(r.Context(), u.contentDelay) {
				return
			}
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"id":"t1","object":"chat.completion","created":1,"model":%q,"choices":[{"index":0,"message":{"role":"assistant","content":"hello"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}`, req.Model)
			return
		}
		w.Header().Set("Content-Type", "text/event-stream")
		flusher, _ := w.(http.Flusher)
		flush := func() {
			if flusher != nil {
				flusher.Flush()
			}
		}
		fmt.Fprintf(w, "data: {\"id\":\"t1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"role\":\"assistant\",\"content\":\"\"},\"finish_reason\":null}]}\n\n", req.Model)
		flush()
		if !wait(r.Context(), u.contentDelay) {
			return
		}
		fmt.Fprintf(w, "data: {\"id\":\"t1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hello\"},\"finish_reason\":null}]}\n\n", req.Model)
		flush()
		fmt.Fprintf(w, "data: {\"id\":\"t1\",\"object\":\"chat.completion.chunk\",\"created\":1,\"model\":%q,\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}],\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":1,\"total_tokens\":4}}\n\n", req.Model)
		fmt.Fprint(w, "data: [DONE]\n\n")
		flush()
	}))
	t.Cleanup(u.Close)
	return u
}

const ttftKey = "sk-ttft-internal"

// newTTFTGateway is a real server over first, serving gpt-4o, and, when
// second is set, second after it with provider failover on a 502 (least
// latency picks first while neither has a latency yet).
func newTTFTGateway(t *testing.T, first, second *ttftUpstream) (*Server, *ttftObserver) {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "ttft", Key: ttftKey, Owner: "t", KeyType: "internal"}}
	cfg.Providers["first"] = config.ProviderConfig{BaseURL: first.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
	if second != nil {
		cfg.Providers["second"] = config.ProviderConfig{BaseURL: second.URL, APIKey: "dummy", APIFormat: "openai", Models: []string{"gpt-4o"}}
		cfg.Routing.DefaultStrategy = "least-latency"
		cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "first", Weight: 1}, {Provider: "second", Weight: 1}}}
		cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2, OnStatusCodes: []int{http.StatusBadGateway}}
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	keyStore := auth.NewKeyStore(cfg.Auth)
	post := &ttftObserver{}
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keyStore, true), post), keyStore, nil, nil, nil, testModelDBPtr(), nil, nil)
	releaseTestServer(t, srv)
	srv.ready.Store(true)
	return srv, post
}

func ttftPost(srv *Server, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if path == "/v1/messages" {
		req.Header.Set("x-api-key", ttftKey)
	} else {
		req.Header.Set("Authorization", "Bearer "+ttftKey)
	}
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// Every streamed surface over a real server records the time to its first
// chunk of output (the upstream's role-only first chunk comes at once), once.
func TestStreamTTFTRecorded(t *testing.T) {
	for _, tc := range []struct{ name, path, body string }{
		{"chat", "/v1/chat/completions", `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`},
		{"completions", "/v1/completions", `{"model":"gpt-4o","stream":true,"prompt":"hi"}`},
		{"messages", "/v1/messages", `{"model":"gpt-4o","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			up := startTTFTUpstream(t, 0, 0, ttftContentDelay)
			srv, post := newTTFTGateway(t, up, nil)
			w := ttftPost(srv, tc.path, tc.body)
			if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "hello") {
				t.Fatalf("stream: status=%d body=%s", w.Code, w.Body.String())
			}
			requireTTFT(t, post, ttftContentDelay, ttftContentDelay+2*time.Second)
		})
	}
}

// The time to first token is the serving attempt's, from its own dispatch:
// an attempt that failed first, slowly, is not part of it.
func TestStreamTTFTIsTheServingAttempts(t *testing.T) {
	const failingCall = 600 * time.Millisecond
	for _, tc := range []struct{ name, path, body string }{
		{"chat", "/v1/chat/completions", `{"model":"gpt-4o","stream":true,"messages":[{"role":"user","content":"hi"}]}`},
		{"messages", "/v1/messages", `{"model":"gpt-4o","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"hi"}]}`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			failing := startTTFTUpstream(t, http.StatusBadGateway, failingCall, 0)
			serving := startTTFTUpstream(t, 0, 0, ttftContentDelay)
			srv, post := newTTFTGateway(t, failing, serving)
			w := ttftPost(srv, tc.path, tc.body)
			if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "hello") {
				t.Fatalf("stream: status=%d body=%s", w.Code, w.Body.String())
			}
			if failing.calls.Load() != 1 || serving.calls.Load() != 1 {
				t.Fatalf("upstream calls: failing=%d serving=%d, want one each (the failover this test needs did not happen)", failing.calls.Load(), serving.calls.Load())
			}
			requireTTFT(t, post, ttftContentDelay, failingCall)
		})
	}
}

// A non-stream call has no first token: no ttft, so no span attribute.
func TestNonStreamChatHasNoTTFT(t *testing.T) {
	up := startTTFTUpstream(t, 0, 0, ttftContentDelay)
	srv, post := newTTFTGateway(t, up, nil)
	w := ttftPost(srv, "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hi"}]}`)
	if w.Code != http.StatusOK || !strings.Contains(w.Body.String(), "hello") {
		t.Fatalf("chat: status=%d body=%s", w.Code, w.Body.String())
	}
	calls, ttft, set := post.seen()
	if calls != 1 {
		t.Fatalf("post-plugin passes = %d, want exactly one", calls)
	}
	if set {
		t.Fatalf("non-stream call recorded ttft = %v, want none", ttft)
	}
}

func TestChunkCarriesOutput(t *testing.T) {
	text, empty, reasoning, stop, failed := "x", "", "thinking", "stop", "error"
	for _, tc := range []struct {
		name  string
		chunk models.StreamChunk
		want  bool
	}{
		{"role only", models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant"}}}}, false},
		{"empty content", models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Role: "assistant", Content: &empty}}}}, false},
		{"usage only", models.StreamChunk{Usage: models.MeasuredUsage(3, 1)}, false},
		{"empty images", models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Images: json.RawMessage(`[]`)}}}}, false},
		{"error finish", models.StreamChunk{Choices: []models.StreamChoice{{FinishReason: &failed}}}, false},
		{"text", models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}, true},
		{"reasoning", models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{ReasoningContent: &reasoning}}}}, true},
		{"tool call", models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{ToolCalls: []models.ToolCallDelta{{Function: &models.FunctionCallDelta{Name: "lookup"}}}}}}}, true},
		{"image", models.StreamChunk{Choices: []models.StreamChoice{{Delta: models.Delta{Images: json.RawMessage(`[{"type":"image_url"}]`)}}}}, true},
		{"finish", models.StreamChunk{Choices: []models.StreamChoice{{FinishReason: &stop}}}, true},
	} {
		if got := chunkCarriesOutput(tc.chunk); got != tc.want {
			t.Errorf("%s: chunkCarriesOutput = %t, want %t", tc.name, got, tc.want)
		}
	}
}

func TestTTFTClockRecordsOnceAfterBegin(t *testing.T) {
	rc := &models.RequestContext{Timings: map[string]time.Duration{}}
	var clock ttftClock
	clock.recordAt(rc, time.Now())
	if _, ok := rc.Timings[ttftTiming]; ok {
		t.Fatal("a clock that did not begin recorded a ttft")
	}
	clock.begin()
	clock.recordAt(rc, clock.started.Add(30*time.Millisecond))
	if got := rc.Timings[ttftTiming]; got != 30*time.Millisecond {
		t.Fatalf("ttft = %v, want 30ms", got)
	}
	clock.recordAt(rc, clock.started.Add(time.Second))
	if got := rc.Timings[ttftTiming]; got != 30*time.Millisecond {
		t.Fatalf("second record changed ttft to %v; it is recorded once", got)
	}
}

// The relay's watch reads server-sent events split anywhere across reads,
// records at the first event of output only, and once.
func TestFirstTokenWatch(t *testing.T) {
	for _, tc := range []struct {
		name          string
		isToken       func([]byte) bool
		before, token string
	}{
		{
			name:    "anthropic",
			isToken: anthropicFirstToken,
			before: "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg\",\"content\":[],\"usage\":{\"input_tokens\":3,\"output_tokens\":0}}}\n\n" +
				"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n" +
				"event: ping\ndata: {\"type\":\"ping\"}\n\n",
			token: "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"Hi\"}}\n\n",
		},
		{
			name:    "genai",
			isToken: genaiFirstToken,
			before:  "data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"\"}]}}]}\r\n\r\n",
			token:   "data: {\"candidates\":[{\"content\":{\"role\":\"model\",\"parts\":[{\"text\":\"Hi\"}]}}]}\r\n\r\n",
		},
		{
			name:    "responses",
			isToken: responsesFirstToken,
			before: "event: response.created\ndata: {\"type\":\"response.created\",\"response\":{\"id\":\"resp\",\"status\":\"in_progress\"}}\n\n" +
				"event: response.output_item.added\ndata: {\"type\":\"response.output_item.added\",\"item\":{\"type\":\"message\"}}\n\n",
			token: "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"Hi\"}\n\n",
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rc := &models.RequestContext{Timings: map[string]time.Duration{}}
			w := newFirstTokenWatch(rc, tc.isToken)
			w.Write([]byte(tc.token))
			if _, ok := rc.Timings[ttftTiming]; ok {
				t.Fatal("recorded before the upstream call began")
			}
			w.begin()
			// The call began 120ms ago.
			w.started = w.started.Add(-120 * time.Millisecond)
			writeInPieces(w, tc.before, 7)
			if _, ok := rc.Timings[ttftTiming]; ok {
				t.Fatalf("recorded on events before any output: %v", rc.Timings[ttftTiming])
			}
			writeInPieces(w, tc.token, 5)
			got, ok := rc.Timings[ttftTiming]
			if !ok || got < 120*time.Millisecond || got > 120*time.Millisecond+2*time.Second {
				t.Fatalf("ttft = %v (%t), want about 120ms", got, ok)
			}
			rc.Timings[ttftTiming] = time.Nanosecond
			w.Write([]byte(tc.token))
			if got := rc.Timings[ttftTiming]; got != time.Nanosecond {
				t.Fatalf("a later event recorded again: %v", got)
			}
		})
	}
}

func writeInPieces(w *firstTokenWatch, s string, size int) {
	for len(s) > 0 {
		n := min(size, len(s))
		w.Write([]byte(s[:n]))
		s = s[n:]
	}
}

func TestFirstTokenDetectors(t *testing.T) {
	for _, tc := range []struct {
		name    string
		isToken func([]byte) bool
		data    string
		want    bool
	}{
		{"anthropic ping", anthropicFirstToken, `{"type":"ping"}`, false},
		{"anthropic message_delta without stop", anthropicFirstToken, `{"type":"message_delta","delta":{},"usage":{"output_tokens":0}}`, false},
		{"anthropic thinking delta", anthropicFirstToken, `{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"hm"}}`, true},
		{"anthropic tool input delta", anthropicFirstToken, `{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{"}}`, true},
		{"anthropic empty answer stop", anthropicFirstToken, `{"type":"message_delta","delta":{"stop_reason":"end_turn"}}`, true},
		{"genai usage only", genaiFirstToken, `{"usageMetadata":{"promptTokenCount":3}}`, false},
		{"genai signature only", genaiFirstToken, `{"candidates":[{"content":{"parts":[{"thoughtSignature":"c2ln"}]}}]}`, false},
		{"genai thought", genaiFirstToken, `{"candidates":[{"content":{"parts":[{"text":"hm","thought":true}]}}]}`, true},
		{"genai function call", genaiFirstToken, `{"candidates":[{"content":{"parts":[{"functionCall":{"name":"f","args":{}}}]}}]}`, true},
		{"genai finish", genaiFirstToken, `{"candidates":[{"finishReason":"STOP"}]}`, true},
		{"responses empty delta", responsesFirstToken, `{"type":"response.output_text.delta","delta":""}`, false},
		{"responses failed", responsesFirstToken, `{"type":"response.failed","response":{"status":"failed"}}`, false},
		{"responses arguments delta", responsesFirstToken, `{"type":"response.function_call_arguments.delta","delta":"{"}`, true},
		{"responses completed", responsesFirstToken, `{"type":"response.completed","response":{"status":"completed"}}`, true},
		{"not json", responsesFirstToken, `[DONE]`, false},
	} {
		if got := tc.isToken([]byte(tc.data)); got != tc.want {
			t.Errorf("%s: %t, want %t", tc.name, got, tc.want)
		}
	}
}
