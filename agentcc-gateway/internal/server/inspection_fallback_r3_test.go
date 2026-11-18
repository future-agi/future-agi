package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestInspectionR3ChangedModelFallbackAdmission(t *testing.T) {
	for _, targets := range []bool{false, true} {
		for _, denyFallback := range []bool{false, true} {
			for _, stream := range []bool{false, true} {
				for _, key := range []string{"a", "b"} {
					t.Run(fmt.Sprintf("targets=%v/deny=%v/stream=%v/key=%s", targets, denyFallback, stream, key), func(t *testing.T) {
						var primaryCalls, fallbackCalls atomic.Int32
						primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
							if r.Method != "POST" {
								fmt.Fprint(w, `{"data":[]}`)
								return
							}
							primaryCalls.Add(1)
							w.WriteHeader(503)
							fmt.Fprint(w, `{"error":{"message":"temporary unavailable"}}`)
						}))
						defer primary.Close()
						fallback := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
							if r.Method != "POST" {
								fmt.Fprint(w, `{"data":[]}`)
								return
							}
							fallbackCalls.Add(1)
							var req models.ChatCompletionRequest
							if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
								t.Error(err)
								return
							}
							if req.Stream {
								w.Header().Set("Content-Type", "text/event-stream")
								fmt.Fprint(w, "data: {\"id\":\"c\",\"model\":\"fallback-model\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"clean fallback\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n")
								return
							}
							fmt.Fprint(w, `{"id":"c","model":"fallback-model","choices":[{"index":0,"message":{"role":"assistant","content":"clean fallback"},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
						}))
						defer fallback.Close()
						expression := "false"
						if denyFallback {
							expression = `request.model == "fallback-model"`
						}
						policy := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{runtimePolicy("custom-pre", "enforce", "block", true, map[string]interface{}{"expression": expression})}}
						initial, _ := runtimePolicyServer(t, primary.URL, policy, nil)
						initial.asyncWorker.Stop()
						cfg := initial.cfg
						cfg.Providers["spare"] = config.ProviderConfig{BaseURL: fallback.URL, APIFormat: "openai", Models: []string{"fallback-model"}}
						targetModel := "unrelated-model" // install the router without targeting this request
						if targets {
							targetModel = "gpt-5.5"
						}
						cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{targetModel: {{Provider: "openai", ModelOverride: "gpt-5.5"}}}
						cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 1, OnStatusCodes: []int{502, 503}}
						cfg.Routing.ModelFallbacks = map[string][]string{"gpt-5.5": {"fallback-model"}}
						registry, err := providers.NewRegistry(cfg)
						if err != nil {
							t.Fatal(err)
						}
						srv := New(cfg, "", registry, initial.engine, initial.keyStore, nil, nil, nil, testModelDBPtr(), initial.TenantStore, nil)
						defer srv.asyncWorker.Stop()
						srv.ready.Store(true)
						body := fmt.Sprintf(`{"model":"gpt-5.5","stream":%t,"messages":[{"role":"user","content":"hello"}]}`, stream)
						req := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(body))
						req.Header.Set("Authorization", "Bearer sk-agentcc-runtime-"+key)
						w := httptest.NewRecorder()
						srv.httpServer.Handler.ServeHTTP(w, req)
						t.Logf("status=%d primary=%d fallback=%d body=%s", w.Code, primaryCalls.Load(), fallbackCalls.Load(), w.Body.String())
						wantCalls := int32(1)
						if denyFallback && key == "a" {
							wantCalls = 0
							if w.Code != 403 || !strings.Contains(w.Body.String(), "content_blocked") || strings.Contains(w.Body.String(), "clean fallback") {
								t.Fatalf("changed attempted model was not checked before submission")
							}
						} else if w.Code != 200 || !strings.Contains(w.Body.String(), "clean fallback") {
							t.Fatalf("clean model fallback was not returned")
						}
						if primaryCalls.Load() != 1 || fallbackCalls.Load() != wantCalls {
							t.Fatalf("provider calls primary=%d fallback=%d want1/%d", primaryCalls.Load(), fallbackCalls.Load(), wantCalls)
						}
					})
				}
			}
		}
	}
}
