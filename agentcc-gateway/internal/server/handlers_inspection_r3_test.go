package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	authplugin "github.com/futureagi/agentcc-gateway/internal/plugins/auth"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Only synthetic localhost upstreams are used. An empty reply selects the normal
// dialect/SSE fixture; explicit replies exercise wire shapes translators omit.
func projectionR3Server(t *testing.T, stage, mode, reply string) (*Server, *atomic.Int32) {
	t.Helper()
	calls := &atomic.Int32{}
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			fmt.Fprint(w, `{"data":[],"models":[]}`)
			return
		}
		calls.Add(1)
		if reply != "" {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, reply)
			return
		}
		(&dialectUpstream{reply: "safe reply"}).ServeHTTP(w, r)
	}))
	t.Cleanup(up.Close)
	cfg := config.DefaultConfig()
	cfg.Auth.Enabled = true
	cfg.Auth.Keys = []config.AuthKeyConfig{
		{Name: "selected", Key: "sk-agentcc-r2-a", KeyType: "internal", Metadata: map[string]string{"org_id": "org-r2"}},
		{Name: "unselected", Key: "sk-agentcc-r2-b", KeyType: "internal", Metadata: map[string]string{"org_id": "org-r2"}},
	}
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic", APIFormat: "openai", Models: []string{"gpt-5.5", "gpt-3.5-turbo-instruct"}}
	cfg.Providers["anthropic"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic", APIFormat: "anthropic", Models: []string{"claude-sonnet-5-5"}}
	cfg.Providers["gemini"] = config.ProviderConfig{BaseURL: up.URL, APIKey: "synthetic", APIFormat: "gemini", Models: []string{"gemini-3.8-flash"}}
	cfg.Guardrails = config.GuardrailsConfig{Enabled: true, DefaultTimeout: time.Second}
	check := "pii-detection"
	if stage == "post" {
		check = "r2-post"
	}
	store := tenant.NewStore()
	failOpen := mode == "enforce-open"
	if failOpen {
		mode = "enforce"
	}
	org := routeR2Org(routeR2Policy(mode, "block", check, nil))
	org.FailOpen = failOpen
	store.Set("org-r2", &tenant.OrgConfig{Guardrails: org})
	gr := guardrails.NewEngine(cfg.Guardrails, nil)
	factory := func(name string, c map[string]interface{}) guardrails.Guardrail {
		if name == "r2-post" {
			return runtimePostCheck{}
		}
		return pii.New(c)
	}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	keys := auth.NewKeyStore(cfg.Auth)
	srv := New(cfg, "", registry, pipeline.NewEngine(authplugin.New(keys, true), guardrails.NewPlugin(gr, nil, factory, nil, store)), keys, gr, nil, nil, testModelDBPtr(), store, nil)
	srv.ready.Store(true)
	return srv, calls
}

func TestProjectionR3IncompleteInputs(t *testing.T) {
	for _, tc := range []struct{ name, path, body string }{
		{"chat-image", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":[{"type":"text","text":"safe"},{"type":"image_url","image_url":{"url":"data:image/png;base64,eA=="}}]}]}`},
		{"chat-extra", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":"safe"}],"future_context":"hidden"}`},
		{"chat-mixed-unknown", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":[{"type":"text","text":"safe","unknown":"hidden"}]}]}`},
		{"chat-tool", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"assistant","content":"safe","tool_calls":[{"id":"x","type":"function","function":{"name":"f","arguments":"hidden"}}]}]}`},
		{"messages-document", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":16,"messages":[{"role":"user","content":[{"type":"text","text":"safe"},{"type":"document","source":{"type":"text","media_type":"text/plain","data":"hidden"}}]}]}`},
		{"messages-thinking", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":16,"messages":[{"role":"assistant","content":[{"type":"text","text":"safe"},{"type":"thinking","thinking":"hidden","signature":"x"}]}]}`},
		{"responses-reference", "/v1/responses", `{"model":"gpt-5.5","input":"safe","previous_response_id":"prior"}`},
		{"responses-refusal", "/v1/responses", `{"model":"gpt-5.5","input":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"safe"},{"type":"refusal","refusal":"hidden"}]}]}`},
		{"genai-function", "/v1beta/models/gemini-3.8-flash:generateContent", `{"contents":[{"role":"user","parts":[{"text":"safe"},{"functionCall":{"name":"f","args":{"text":"hidden"}}}]}]}`},
	} {
		for _, control := range []string{"enforce", "enforce-open", "monitor", "unselected", "direct"} {
			t.Run(tc.name+"/"+control, func(t *testing.T) {
				mode, key := "enforce", "a"
				if control == "monitor" || control == "enforce-open" {
					mode = control
				}
				if control == "unselected" {
					key = "b"
				}
				srv, calls := projectionR3Server(t, "pre", mode, "")
				var w *httptest.ResponseRecorder
				if control == "direct" {
					w = httptest.NewRecorder()
					r := httptest.NewRequest("POST", tc.path, strings.NewReader(tc.body))
					r.Header.Set("Authorization", "Bearer sk-agentcc-r2-a")
					switch tc.path {
					case "/v1/chat/completions":
						srv.handlers.ChatCompletion(w, r)
					case "/v1/messages":
						srv.handlers.AnthropicMessages(w, r)
					case "/v1/responses":
						srv.handlers.CreateResponse(w, r)
					default:
						r.URL.RawQuery = "model_action=gemini-3.8-flash:generateContent"
						srv.handlers.GenAIHandler(w, r)
					}
				} else {
					w = routeR2Send(srv, tc.path, tc.body, key)
				}
				want, n := 200, int32(1)
				if control == "enforce" || control == "enforce-open" || control == "direct" {
					want, n = 403, 0
				}
				t.Logf("status=%d calls=%d released_bytes=%d", w.Code, calls.Load(), w.Body.Len())
				if w.Code != want || calls.Load() != n {
					t.Fatalf("want status=%d calls=%d got %d/%d: %s", want, n, w.Code, calls.Load(), w.Body.String())
				}
				if want == 403 && !strings.Contains(w.Body.String(), "unsupported") {
					t.Fatalf("not a capability refusal: %s", w.Body.String())
				}
			})
		}
	}
}

func TestProjectionR3IncompleteOutputs(t *testing.T) {
	for _, tc := range []struct{ name, path, body, reply string }{
		{"completion-second", "/v1/completions", `{"model":"gpt-3.5-turbo-instruct","prompt":["safe","also safe"],"n":2}`, `{"id":"r3","model":"gpt-3.5-turbo-instruct","choices":[{"index":0,"text":"safe"},{"index":1,"text":"runtime-sensitive"}]}`},
		{"chat-extra", "/v1/chat/completions", `{"model":"gpt-5.5","messages":[{"role":"user","content":"safe"}]}`, `{"id":"r3","model":"gpt-5.5","choices":[{"index":0,"message":{"role":"assistant","content":"safe"}}],"future_context":"runtime-sensitive"}`},
		{"messages-thinking", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":16,"messages":[{"role":"user","content":"safe"}]}`, `{"id":"r3","type":"message","role":"assistant","model":"claude-sonnet-5-5","content":[{"type":"text","text":"safe"},{"type":"thinking","thinking":"runtime-sensitive","signature":"s"}]}`},
		{"messages-tool", "/v1/messages", `{"model":"claude-sonnet-5-5","max_tokens":16,"messages":[{"role":"user","content":"safe"}]}`, `{"id":"r3","type":"message","role":"assistant","model":"claude-sonnet-5-5","content":[{"type":"text","text":"safe"},{"type":"tool_use","id":"t","name":"f","input":{"text":"runtime-sensitive"}}]}`},
		{"responses-function", "/v1/responses", `{"model":"gpt-5.5","input":"safe"}`, `{"id":"r3","model":"gpt-5.5","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"safe"}]},{"type":"function_call","name":"f","arguments":"runtime-sensitive","call_id":"c"}]}`},
		{"responses-refusal", "/v1/responses", `{"model":"gpt-5.5","input":"safe"}`, `{"id":"r3","model":"gpt-5.5","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"safe"},{"type":"refusal","refusal":"runtime-sensitive"}]}]}`},
		{"genai-function", "/v1beta/models/gemini-3.8-flash:generateContent", `{"contents":[{"role":"user","parts":[{"text":"safe"}]}]}`, `{"candidates":[{"content":{"role":"model","parts":[{"text":"safe"},{"functionCall":{"name":"f","args":{"text":"runtime-sensitive"}}}]}}]}`},
	} {
		for _, control := range []string{"enforce", "enforce-open", "monitor", "unselected", "direct"} {
			t.Run(tc.name+"/"+control, func(t *testing.T) {
				mode, key := "enforce", "a"
				if control == "monitor" || control == "enforce-open" {
					mode = control
				}
				if control == "unselected" {
					key = "b"
				}
				srv, calls := projectionR3Server(t, "post", mode, tc.reply)
				var w *httptest.ResponseRecorder
				if control == "direct" {
					w = httptest.NewRecorder()
					r := httptest.NewRequest("POST", tc.path, strings.NewReader(tc.body))
					r.Header.Set("Authorization", "Bearer sk-agentcc-r2-a")
					switch tc.path {
					case "/v1/chat/completions":
						srv.handlers.ChatCompletion(w, r)
					case "/v1/completions":
						srv.handlers.TextCompletion(w, r)
					case "/v1/messages":
						srv.handlers.AnthropicMessages(w, r)
					case "/v1/responses":
						srv.handlers.CreateResponse(w, r)
					default:
						r.URL.RawQuery = "model_action=gemini-3.8-flash:generateContent"
						srv.handlers.GenAIHandler(w, r)
					}
				} else {
					w = routeR2Send(srv, tc.path, tc.body, key)
				}
				want := 403
				if control == "unselected" || control == "monitor" {
					want = 200
				}
				t.Logf("status=%d calls=%d released_bytes=%d", w.Code, calls.Load(), w.Body.Len())
				if w.Code != want || calls.Load() != 1 {
					t.Fatalf("want %d/1 got %d/%d: %s", want, w.Code, calls.Load(), w.Body.String())
				}
				if want == 403 && strings.Contains(w.Body.String(), "runtime-sensitive") {
					t.Fatal("released unchecked content")
				}
			})
		}
	}
}

func TestProjectionR3SafeTextAndInputOnlyStreams(t *testing.T) {
	routes := append([]guardedRoute(nil), guardedRoutes...)
	routes = append(routes, guardedRoute{"messages alias", "/v1/messages", strings.Replace(guardedRoutes[2].body, "claude-sonnet-5-5", "anthropic/claude-sonnet-5-5", 1)}, guardedRoute{"genai alias", "/v1beta/models/gemini/gemini-3.8-flash:%s", guardedRoutes[4].body})
	for _, rt := range routes {
		for _, stream := range []bool{false, true} {
			for _, direct := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/stream=%v/direct=%v", rt.name, stream, direct), func(t *testing.T) {
					srv, calls := projectionR3Server(t, "pre", "enforce", "")
					req := rt.request(stream, "safe text")
					req.Header.Set("Authorization", "Bearer sk-agentcc-r2-a")
					w := httptest.NewRecorder()
					if !direct {
						srv.httpServer.Handler.ServeHTTP(w, req)
					} else {
						switch {
						case strings.Contains(rt.name, "messages"):
							srv.handlers.AnthropicMessages(w, req)
						case strings.Contains(rt.name, "genai"):
							action := "generateContent"
							if stream {
								action = "streamGenerateContent"
							}
							req.URL.RawQuery = "model_action=gemini-3.8-flash:" + action
							srv.handlers.GenAIHandler(w, req)
						case rt.name == "responses":
							srv.handlers.CreateResponse(w, req)
						default:
							srv.handlers.ChatCompletion(w, req)
						}
					}
					t.Logf("status=%d calls=%d released_bytes=%d", w.Code, calls.Load(), w.Body.Len())
					if w.Code != 200 || calls.Load() != 1 || !strings.Contains(w.Body.String(), "safe reply") {
						t.Fatalf("safe text rejected: %d/%d %s", w.Code, calls.Load(), w.Body.String())
					}
				})
			}
		}
	}
}
