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

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/secrets"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	cacheplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cache"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// awsKey is the AWS documentation's example key, which Secret Detection finds.
const awsKey = "AKIAIOSFODNN7EXAMPLE"

// dialectUpstream answers in OpenAI, Responses, Anthropic and GenAI format,
// streamed or not, with reply as the text, and counts the generation calls
// it gets.
type dialectUpstream struct {
	reply string
	mu    sync.Mutex
	calls int
}

func (u *dialectUpstream) served() int { u.mu.Lock(); defer u.mu.Unlock(); return u.calls }

func (u *dialectUpstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	if r.Method != http.MethodPost { // the server lists models at start
		fmt.Fprint(w, `{"data":[],"models":[]}`)
		return
	}
	u.mu.Lock()
	u.calls++
	u.mu.Unlock()
	stream := bytes.Contains(body, []byte(`"stream":true`)) || strings.Contains(r.URL.Path, "streamGenerateContent")
	text, _ := json.Marshal(u.reply)
	sse := func(events ...string) {
		w.Header().Set("Content-Type", "text/event-stream")
		for _, e := range events {
			fmt.Fprint(w, e+"\n\n")
		}
	}
	switch {
	case r.URL.Path == "/v1/chat/completions" && stream:
		sse(`data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.5","choices":[{"index":0,"delta":{"content":`+string(text)+`}}]}`,
			`data: {"id":"c","object":"chat.completion.chunk","model":"gpt-5.5","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}`,
			`data: [DONE]`)
	case r.URL.Path == "/v1/chat/completions":
		fmt.Fprint(w, `{"id":"c","object":"chat.completion","model":"gpt-5.5","choices":[{"index":0,"message":{"role":"assistant","content":`+string(text)+`},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}`)
	case r.URL.Path == "/v1/responses" && stream:
		sse(`event: response.output_text.delta`+"\n"+`data: {"type":"response.output_text.delta","delta":`+string(text)+`}`,
			`event: response.completed`+"\n"+`data: {"type":"response.completed","response":{"id":"r","model":"gpt-5.5","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":`+string(text)+`}]}],"usage":{"input_tokens":5,"output_tokens":3,"total_tokens":8}}}`)
	case r.URL.Path == "/v1/responses":
		fmt.Fprint(w, `{"id":"r","object":"response","model":"gpt-5.5","status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":`+string(text)+`}]}],"usage":{"input_tokens":5,"output_tokens":3,"total_tokens":8}}`)
	case r.URL.Path == "/v1/messages" && stream:
		sse(`event: message_start`+"\n"+`data: {"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-sonnet-5-5","content":[],"usage":{"input_tokens":5,"output_tokens":0}}}`,
			`event: content_block_start`+"\n"+`data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
			`event: content_block_delta`+"\n"+`data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":`+string(text)+`}}`,
			`event: content_block_stop`+"\n"+`data: {"type":"content_block_stop","index":0}`,
			`event: message_delta`+"\n"+`data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}`,
			`event: message_stop`+"\n"+`data: {"type":"message_stop"}`)
	case r.URL.Path == "/v1/messages":
		fmt.Fprint(w, `{"id":"m","type":"message","role":"assistant","model":"claude-sonnet-5-5","content":[{"type":"text","text":`+string(text)+`}],"stop_reason":"end_turn","usage":{"input_tokens":5,"output_tokens":3}}`)
	case strings.HasSuffix(r.URL.Path, ":streamGenerateContent"):
		sse(`data: {"candidates":[{"content":{"role":"model","parts":[{"text":` + string(text) + `}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}`)
	case strings.HasSuffix(r.URL.Path, ":generateContent"):
		fmt.Fprint(w, `{"candidates":[{"content":{"role":"model","parts":[{"text":`+string(text)+`}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3,"totalTokenCount":8}}`)
	default:
		http.Error(w, "unexpected path "+r.URL.Path, http.StatusNotFound)
	}
}

// guardrailServer runs Secret Detection as a blocking rule at stage, against
// providers in each wire format that all point at up.
func guardrailServer(t *testing.T, up *httptest.Server, stage string) *Server {
	t.Helper()
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "k", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "k", APIFormat: "anthropic", Models: []string{"claude-sonnet-5-5"}}
	cfg.Providers["gemini"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "k", APIFormat: "gemini", Models: []string{"gemini-3.8-flash"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, FailOpen: true, DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: "secret-detection", Stage: stage, Mode: "sync", Action: "block", Threshold: 0.5}}}
	registry := map[string]guardrails.Guardrail{"secret-detection": secrets.New(nil)}
	grEngine := guardrails.NewEngine(cfg.Guardrails, registry)
	providerRegistry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	srv := New(cfg, "", providerRegistry, pipeline.NewEngine(guardrails.NewPlugin(grEngine, registry, nil, nil, nil)), nil, grEngine, nil, nil, testModelDBPtr(), nil, nil)
	srv.ready.Store(true)
	return srv
}

type guardedRoute struct {
	name, path, body string // body takes the stream flag, then the prompt
}

var guardedRoutes = []guardedRoute{
	{"chat", "/v1/chat/completions", `{"model":"gpt-5.5","stream":%v,"messages":[{"role":"user","content":%q}]}`},
	{"messages translated", "/v1/messages", `{"model":"gpt-5.5","stream":%v,"max_tokens":16,"messages":[{"role":"user","content":%q}]}`},
	{"messages native", "/v1/messages", `{"model":"claude-sonnet-5-5","stream":%v,"max_tokens":16,"messages":[{"role":"user","content":%q}]}`},
	{"responses", "/v1/responses", `{"model":"gpt-5.5","stream":%v,"input":[{"role":"user","content":[{"type":"input_text","text":%q}]}]}`},
	{"genai native", "/v1beta/models/gemini-3.8-flash:%s", `{"contents":[{"role":"user","parts":[{"text":%[2]q}]}]}`},
}

func (rt guardedRoute) request(stream bool, prompt string) *http.Request {
	path, body := rt.path, fmt.Sprintf(rt.body, stream, prompt)
	if strings.Contains(path, "%s") {
		action := "generateContent"
		if stream {
			action = "streamGenerateContent?alt=sse"
		}
		path = fmt.Sprintf(path, action)
	}
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("anthropic-version", "2023-06-01")
	return req
}

// An input guardrail sees the prompt on every route, the ones that forward
// the client's own body included. Native /v1/messages (Claude Code's path),
// /v1/responses and native GenAI used to reach the upstream unchecked.
func TestInputGuardrailBlocksOnEveryRoute(t *testing.T) {
	for _, rt := range guardedRoutes {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s stream=%v", rt.name, stream), func(t *testing.T) {
				up := &dialectUpstream{reply: "ok"}
				upSrv := httptest.NewServer(up)
				defer upSrv.Close()
				srv := guardrailServer(t, upSrv, "pre")

				w := httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, rt.request(stream, "deploy with "+awsKey))
				if w.Code != http.StatusForbidden || up.served() != 0 {
					t.Fatalf("status %d, upstream calls %d; want 403 and none: %s", w.Code, up.served(), w.Body.String())
				}

				w = httptest.NewRecorder()
				srv.httpServer.Handler.ServeHTTP(w, rt.request(stream, "deploy it"))
				if w.Code != http.StatusOK || up.served() != 1 {
					t.Fatalf("clean prompt: status %d, upstream calls %d; want 200 and one: %s", w.Code, up.served(), w.Body.String())
				}
			})
		}
	}
}

// An output guardrail's block refuses a non-streamed response on every route.
// Chat used to answer 500 "no response from provider"; the other routes sent
// the blocked text.
func TestOutputGuardrailRefusesANonStreamedResponseOnEveryRoute(t *testing.T) {
	for _, rt := range guardedRoutes {
		t.Run(rt.name, func(t *testing.T) {
			up := &dialectUpstream{reply: "your key is " + awsKey}
			upSrv := httptest.NewServer(up)
			defer upSrv.Close()
			srv := guardrailServer(t, upSrv, "post")

			w := httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, rt.request(false, "what is the key?"))
			if w.Code != http.StatusForbidden || strings.Contains(w.Body.String(), awsKey) {
				t.Fatalf("status %d; want 403 without the blocked text: %s", w.Code, w.Body.String())
			}

			up.reply = "no key here"
			w = httptest.NewRecorder()
			srv.httpServer.Handler.ServeHTTP(w, rt.request(false, "what is the key?"))
			if w.Code != http.StatusOK {
				t.Fatalf("clean reply: status %d; want 200: %s", w.Code, w.Body.String())
			}
		})
	}
}

// A response an output guardrail blocked is not cached. The cache stored it
// before the guardrail ran, so the same request was then served the blocked
// response from the cache, without calling the upstream again.
func TestBlockedResponseIsNotCached(t *testing.T) {
	up := &dialectUpstream{reply: "your key is " + awsKey}
	upSrv := httptest.NewServer(up)
	defer upSrv.Close()
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	// The cache is opt-in per org.
	cfg.Auth.Keys = []config.AuthKeyConfig{{Name: "cached", Key: "sk-agentcc-cached", Owner: "user", KeyType: "internal", Metadata: map[string]string{"org_id": "org-cache"}}}
	tenantStore := tenant.NewStore()
	tenantStore.Set("org-cache", &tenant.OrgConfig{Cache: &tenant.CacheConfig{Enabled: true}})
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: upSrv.URL, APIKey: "k", APIFormat: "openai", Models: []string{"gpt-5.5"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, FailOpen: true, DefaultTimeout: 5 * time.Second,
		Rules: []config.GuardrailRuleConfig{{Name: "secret-detection", Stage: "post", Mode: "sync", Action: "block", Threshold: 0.5}}}
	registry := map[string]guardrails.Guardrail{"secret-detection": secrets.New(nil)}
	grEngine := guardrails.NewEngine(cfg.Guardrails, registry)
	providerRegistry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatalf("creating registry: %v", err)
	}
	engine := pipeline.NewEngine(
		cacheplugin.New(cache.NewMemoryBackend(cache.NewStore(100)), time.Minute),
		guardrails.NewPlugin(grEngine, registry, nil, nil, nil),
	)
	srv := New(cfg, "", providerRegistry, engine, nil, grEngine, nil, nil, testModelDBPtr(), tenantStore, nil)
	srv.ready.Store(true)
	send := func(prompt, reply string) *httptest.ResponseRecorder {
		up.reply = reply
		req := guardedRoutes[0].request(false, prompt)
		req.Header.Set("Authorization", "Bearer sk-agentcc-cached")
		w := httptest.NewRecorder()
		srv.httpServer.Handler.ServeHTTP(w, req)
		return w
	}

	// Control: the cache serves a clean answer the second time.
	send("say hello", "hello")
	if w := send("say hello", "hello"); w.Header().Get("x-agentcc-cache") == "" || up.served() != 1 {
		t.Fatalf("control: cache header %q, upstream calls %d; want a cache hit and one call", w.Header().Get("x-agentcc-cache"), up.served())
	}
	before := up.served()

	for i := 1; i <= 2; i++ {
		w := send("what is the key?", "your key is "+awsKey)
		if w.Code != http.StatusForbidden || strings.Contains(w.Body.String(), awsKey) {
			t.Fatalf("request %d: status %d; want 403 without the blocked text: %s", i, w.Code, w.Body.String())
		}
	}
	if up.served()-before != 2 {
		t.Fatalf("upstream calls = %d; want 2, one per request", up.served()-before)
	}
}
