package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/catalog"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func messagesRoutingCall(srv *Server, key, model string, stream bool, extra string) *httptest.ResponseRecorder {
	body := fmt.Sprintf(`{"model":%q,"messages":[{"role":"user","content":"hello"}],"max_tokens":32,"stream":%v%s}`, model, stream, extra)
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(body))
	req.Header.Set("x-api-key", key)
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	return w
}

// Native wire upstream, not a provider stub: requests traverse the real
// Anthropic adapter, including translation on the routed path.
func messagesRoutingNative(t *testing.T) *r7Upstream {
	t.Helper()
	u := &r7Upstream{}
	u.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Model  string `json:"model"`
			Stream bool   `json:"stream"`
		}
		if r.URL.Path != "/v1/messages" || json.NewDecoder(r.Body).Decode(&body) != nil {
			http.Error(w, "unexpected request", 400)
			return
		}
		u.mu.Lock()
		u.calls = append(u.calls, r7Call{Model: body.Model, Auth: "Bearer " + r.Header.Get("x-api-key")})
		u.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		if body.Model == "org-model" {
			w.WriteHeader(503)
			io.WriteString(w, `{"type":"error","error":{"type":"overloaded_error","message":"synthetic unavailable"}}`)
			return
		}
		if body.Stream {
			w.Header().Set("Content-Type", "text/event-stream")
			fmt.Fprintf(w, "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_r\",\"type\":\"message\",\"role\":\"assistant\",\"model\":%q,\"content\":[],\"usage\":{\"input_tokens\":3,\"output_tokens\":0}}}\n\n", body.Model)
			io.WriteString(w, "event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\nevent: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"ok\"}}\n\nevent: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\nevent: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"},\"usage\":{\"output_tokens\":1}}\n\nevent: message_stop\ndata: {\"type\":\"message_stop\"}\n\n")
			return
		}
		fmt.Fprintf(w, `{"id":"msg_r","type":"message","role":"assistant","model":%q,"content":[{"type":"text","text":"ok"}],"stop_reason":"end_turn","usage":{"input_tokens":3,"output_tokens":1}}`, body.Model)
	}))
	t.Cleanup(u.Close)
	return u
}

func messagesRoutingGateway(t *testing.T, native bool, routing string) (*Server, *r7Upstream) {
	t.Helper()
	up := messagesRoutingNative(t)
	format := "anthropic"
	if !native {
		up = r7StartUpstream(t, func(model string, _ int) bool { return model == "org-model" })
		format = "openai"
	}
	srv := r7Gateway(t, map[string]r7Op{"alpha": {up, []string{"org-model", "org-model-2"}}}, func(cfg *config.Config) {
		p := cfg.Providers["alpha"]
		p.APIFormat = format
		cfg.Providers["alpha"] = p
	})
	r7Push(t, srv, r7OrgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"`+format+`","models":["org-model","org-model-2"],"enabled":true}},"routing":`+routing+`}`)
	return srv, up
}

func TestMessagesRoutingOrgFallback(t *testing.T) {
	for _, native := range []bool{false, true} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("native=%v/stream=%v", native, stream), func(t *testing.T) {
				srv, up := messagesRoutingGateway(t, native, `{"model_fallbacks":{"org-model":["org-model-2"]}}`)
				w := messagesRoutingCall(srv, r7CustA, "org-model", stream, "")
				if w.Code != 200 || !strings.Contains(w.Body.String(), `"model":"org-model-2"`) {
					t.Fatalf("fallback -> %d %s; calls %v", w.Code, w.Body, up.snapshot())
				}
				calls := up.snapshot()
				if len(calls) != 2 || calls[0].Model != "org-model" || calls[1].Model != "org-model-2" {
					t.Fatalf("want primary then fallback, got %v", calls)
				}
				if stream && !strings.Contains(w.Body.String(), "event: message_stop") {
					t.Fatalf("Messages stream did not finish: %s", w.Body)
				}
				r7AssertOrgCredentialOnly(t, up)
			})
		}
	}
}

func TestMessagesRoutingFallbackOffAndRetry(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, retry := range []bool{false, true} {
			t.Run(fmt.Sprintf("stream=%v/retry=%v", stream, retry), func(t *testing.T) {
				routing := `{"fallback_enabled":false,"model_fallbacks":{"org-model":["org-model-2"]}}`
				want := 1
				if retry {
					routing = `{"model_fallbacks":{"org-model":["org-model-2"]},"retry":{"enabled":true,"max_retries":1,"initial_delay":"1ms","max_delay":"2ms","multiplier":1,"on_status_codes":[503]}}`
					want = 3
				}
				srv, up := messagesRoutingGateway(t, false, routing)
				w := messagesRoutingCall(srv, r7CustA, "org-model", stream, "")
				if (w.Code == 200) != retry || len(up.snapshot()) != want {
					t.Fatalf("retry=%v -> %d %s, calls %v, want %d calls", retry, w.Code, w.Body, up.snapshot(), want)
				}
			})
		}
	}
}

func TestMessagesRoutingAutoSelectionAndFallback(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			up := r7StartUpstream(t, func(model string, _ int) bool { return model == "gpt-4o-mini" })
			srv := r7Gateway(t, map[string]r7Op{"alpha": {up, []string{"gpt-4o-mini", "gpt-4o"}}}, nil)
			r7Push(t, srv, r7OrgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["gpt-4o-mini","gpt-4o"],"enabled":true}},"routing":{"auto":{"preference":"cost","candidates":["gpt-4o-mini","gpt-4o"]}}}`)
			w := messagesRoutingCall(srv, r7CustA, "auto", stream, "")
			calls := up.snapshot()
			if w.Code != 200 || w.Header().Get("x-agentcc-auto-model") != "gpt-4o-mini" || len(calls) != 2 || calls[1].Model != "gpt-4o" {
				t.Fatalf("auto -> %d %s, chosen %q, calls %v", w.Code, w.Body, w.Header().Get("x-agentcc-auto-model"), calls)
			}
			r7AssertOrgCredentialOnly(t, up)
		})
	}
}

func TestMessagesRoutingEligibilityParity(t *testing.T) {
	const model = "openai/gpt-4o-mini"
	for _, policy := range []string{"lock", "data"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/stream=%v", policy, stream), func(t *testing.T) {
				org := optedIn()
				org.Routing = &tenant.RoutingConfig{}
				if policy == "lock" {
					org.Routing.ProviderLock = &tenant.ProviderLockConfig{Enabled: true, DenyProviders: []string{"openrouter"}}
				} else {
					org.Routing.DataPolicy = &tenant.DataPolicyConfig{ZDR: true}
				}
				g := newEligGateway(t, eligOpts{org: org, orModels: map[string]*catalog.Model{model: eligModel(model, 0.1e-6)}})
				chat := g.chat(t, eligChat(model, ""))
				msg := messagesRoutingCall(g.srv, eligKey, model, stream, "")
				var a, b struct {
					Error struct {
						Message string `json:"message"`
					} `json:"error"`
				}
				_ = json.Unmarshal(chat.Body.Bytes(), &a)
				_ = json.Unmarshal(msg.Body.Bytes(), &b)
				if chat.Code != 403 || msg.Code != chat.Code || a.Error.Message != b.Error.Message || len(g.or.received()) != 0 {
					t.Fatalf("chat=%d %s; messages=%d %s; upstream=%v", chat.Code, chat.Body, msg.Code, msg.Body, g.or.received())
				}
			})
		}
	}
}

func TestMessagesRoutingHopEligibilityAndFunding(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, deny := range []string{"policy", "funding"} {
			t.Run(fmt.Sprintf("%s/stream=%v", deny, stream), func(t *testing.T) {
				org := optedIn()
				org.Routing = &tenant.RoutingConfig{FallbackEnabled: true, ModelFallbacks: map[string][]string{"qwen/qwen3.7-flash": {"openai/gpt-6-sol"}}}
				if deny == "policy" {
					org.ManagedModels.DenyModels = []string{"openai/gpt-6-sol"}
				}
				g := newEligGateway(t, eligOpts{org: org, orFails: 1, orModels: map[string]*catalog.Model{
					"qwen/qwen3.7-flash": eligModel("qwen/qwen3.7-flash", 0.03e-6), "openai/gpt-6-sol": eligModel("openai/gpt-6-sol", 5e-6),
				}})
				billing := &refusingBilling{}
				if deny == "funding" {
					g.srv.handlers.SetBilling(billing)
				}
				w := messagesRoutingCall(g.srv, eligKey, "qwen/qwen3.7-flash", stream, "")
				if w.Code < 400 || len(g.or.received()) != 1 {
					t.Fatalf("denied hop dispatched: %d %s, calls %v", w.Code, w.Body, g.or.received())
				}
				if deny == "funding" && billing.asked.Load() != 1 {
					t.Fatalf("funding gate asked %d times, want 1", billing.asked.Load())
				}
			})
		}
	}
}

func TestMessagesRoutingProviderFailover(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%v", stream), func(t *testing.T) {
			alpha := r7StartUpstream(t, func(string, int) bool { return true })
			beta := r7StartUpstream(t, nil)
			srv := r7Gateway(t, map[string]r7Op{"alpha": {alpha, []string{"org-model"}}, "beta": {beta, []string{"org-model"}}}, nil)
			r7Push(t, srv, r7OrgA, `{"providers":{"alpha":{"api_key":"org-alpha-key","api_format":"openai","models":["org-model"],"enabled":true},"beta":{"api_key":"org-beta-key","api_format":"openai","models":["org-model"],"enabled":true}},"routing":{"failover":{"enabled":true,"max_attempts":2,"on_status_codes":[503]}}}`)
			w := messagesRoutingCall(srv, r7CustA, "org-model", stream, "")
			if w.Code != 200 || alpha.count() != 1 || beta.count() != 1 {
				t.Fatalf("provider failover -> %d %s; alpha=%v beta=%v", w.Code, w.Body, alpha.snapshot(), beta.snapshot())
			}
			r7AssertOrgCredentialOnly(t, alpha, beta)
		})
	}
}

func TestMessagesRoutingNativeHeadersRefused(t *testing.T) {
	srv, up := messagesRoutingGateway(t, true, `{"model_fallbacks":{"org-model":["org-model-2"]}}`)
	req := httptest.NewRequest(http.MethodPost, "/v1/messages", strings.NewReader(`{"model":"org-model","max_tokens":32,"messages":[{"role":"user","content":"hello"}]}`))
	req.Header.Set("x-api-key", r7CustA)
	req.Header.Set("anthropic-beta", "synthetic-native-feature")
	w := httptest.NewRecorder()
	srv.httpServer.Handler.ServeHTTP(w, req)
	if w.Code != 400 || len(up.snapshot()) != 0 {
		t.Fatalf("beta header silently dropped: %d %s, calls %v", w.Code, w.Body, up.snapshot())
	}
}

func TestMessagesRoutingNativeOnlyRefusedNotDropped(t *testing.T) {
	srv, up := messagesRoutingGateway(t, true, `{"model_fallbacks":{"org-model":["org-model-2"]}}`)
	w := messagesRoutingCall(srv, r7CustA, "org-model", false, `,"thinking":{"type":"enabled","budget_tokens":1024}`)
	if w.Code != 400 || !strings.Contains(w.Body.String(), "native-only") || len(up.snapshot()) != 0 {
		t.Fatalf("native-only request silently changed: %d %s, calls %v", w.Code, w.Body, up.snapshot())
	}
}
