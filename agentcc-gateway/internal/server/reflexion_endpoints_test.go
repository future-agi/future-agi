package server

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestReflexionUnsupportedGenerationRoutesFailBeforeProvider(t *testing.T) {
	for _, route := range []struct{ path, body string }{
		{"/v1/messages", `{"model":"gpt-4o","max_tokens":16,"messages":[{"role":"user","content":"hello"}]}`},
		{"/v1/messages", `{"model":"gpt-4o","max_tokens":16,"messages":[{"role":"user","content":"hello"}],"stream":true}`},
		{"/v1beta/models/gpt-4o:generateContent", `{"contents":[{"role":"user","parts":[{"text":"hello"}]}]}`},
		{"/v1beta/models/gpt-4o:streamGenerateContent", `{"contents":[{"role":"user","parts":[{"text":"hello"}]}]}`},
		{"/v1/responses", `{"model":"gpt-4o","input":"hello"}`},
		{"/v1/responses", `{"model":"gpt-4o","input":"hello","stream":true}`},
		{"/v1/completions", `{"model":"gpt-4o","prompt":"hello"}`},
		{"/v1/completions", `{"model":"gpt-4o","prompt":"hello","stream":true}`},
	} {
		t.Run(route.path+route.body, func(t *testing.T) {
			var calls atomic.Int32
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == http.MethodPost {
					calls.Add(1)
				}
				http.Error(w, "must not be called", 500)
			}))
			defer upstream.Close()
			s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"))
			r := httptest.NewRequest(http.MethodPost, route.path, strings.NewReader(route.body))
			w := httptest.NewRecorder()
			s.httpServer.Handler.ServeHTTP(w, r)
			if w.Code != 400 || calls.Load() != 0 || !strings.Contains(w.Body.String(), "Response repair is supported only on /v1/chat/completions") || strings.Contains(w.Body.String(), "data:") {
				t.Fatalf("status=%d calls=%d body=%s", w.Code, calls.Load(), w.Body.String())
			}
		})
	}
}

func TestReflexionDoesNotBlockEmbeddingsHealthOrSupportedChat(t *testing.T) {
	var calls atomic.Int32
	u := &reflectionUpstream{}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(404)
			return
		}
		calls.Add(1)
		if r.URL.Path == "/v1/embeddings" {
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"object":"list","data":[{"object":"embedding","index":0,"embedding":[0.1]}],"model":"gpt-4o","usage":{"prompt_tokens":1,"total_tokens":1}}`)
			return
		}
		u.serve(w, r)
	}))
	defer upstream.Close()
	s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"))
	for _, route := range []struct{ method, path, body string }{{"GET", "/healthz", ""}, {"POST", "/v1/embeddings", `{"model":"gpt-4o","input":"hello"}`}, {"POST", "/v1/chat/completions", `{"model":"gpt-4o","messages":[{"role":"user","content":"hello"}]}`}} {
		r := httptest.NewRequest(route.method, route.path, strings.NewReader(route.body))
		w := httptest.NewRecorder()
		s.httpServer.Handler.ServeHTTP(w, r)
		if w.Code != 200 {
			t.Fatalf("route=%s status=%d body=%s", route.path, w.Code, w.Body.String())
		}
	}
	if calls.Load() != 2 {
		t.Fatalf("provider calls=%d", calls.Load())
	}
}

func TestReflexionEndpointGuardHonorsPolicyOverrides(t *testing.T) {
	for _, dynamic := range []bool{false, true} {
		for _, override := range []string{"key-disabled", "key-log", "request-disabled", "request-log"} {
			t.Run(fmt.Sprintf("dynamic=%t/%s", dynamic, override), func(t *testing.T) {
				var calls atomic.Int32
				upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if r.Method != http.MethodPost {
						w.WriteHeader(404)
						return
					}
					calls.Add(1)
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprint(w, `{"id":"ok","object":"text_completion","model":"gpt-4o","choices":[{"index":0,"text":"accepted","finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}`)
				}))
				defer upstream.Close()
				registry := map[string]guardrails.Guardrail{"output": &reflectionCheck{name: "output", always: true}}
				policies := policy.NewStore()
				metadata := map[string]string{"auth_key_id": "override-key", "key_allow_policy_override": "true", "org_id": "override-org"}
				if strings.HasPrefix(override, "key-") {
					ov := config.KeyGuardrailOverride{Name: "output", Disabled: override == "key-disabled"}
					if override == "key-log" {
						ov.Action = "log"
					}
					policies.Register("override-key", &config.KeyGuardrailPolicyConfig{Overrides: []config.KeyGuardrailOverride{ov}})
				}
				var plugin *guardrails.GuardrailPlugin
				if dynamic {
					store := tenant.NewStore()
					cfg := reflectionPolicy()
					cfg["stage"] = "post"
					store.Set("override-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"output": {Enabled: true, Action: "block", Config: cfg}}}})
					plugin = guardrails.NewPlugin(nil, registry, nil, policies, store)
				} else {
					e := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "output", Stage: "post", Action: "block", Config: reflectionPolicy()}}}, registry)
					plugin = guardrails.NewPlugin(e, registry, nil, policies, nil)
				}
				s := createTestServerWithPlugins(t, upstream.URL, &reflectionObserver{initial: metadata}, plugin)
				r := httptest.NewRequest("POST", "/v1/completions", strings.NewReader(`{"model":"gpt-4o","prompt":"hello"}`))
				if strings.HasPrefix(override, "request-") {
					value := "disabled"
					if override == "request-log" {
						value = "log-only"
					}
					r.Header.Set("X-Guardrail-Policy", value)
				}
				w := httptest.NewRecorder()
				s.httpServer.Handler.ServeHTTP(w, r)
				if w.Code != 200 || calls.Load() != 1 {
					t.Fatalf("status=%d calls=%d body=%s", w.Code, calls.Load(), w.Body.String())
				}
			})
		}
	}
}

func TestReflexionUnknownChecksFailBeforeProvider(t *testing.T) {
	for _, dynamic := range []bool{false, true} {
		t.Run(fmt.Sprint(dynamic), func(t *testing.T) {
			u := &reflectionUpstream{}
			upstream := httptest.NewServer(http.HandlerFunc(u.serve))
			defer upstream.Close()
			var plugin *guardrails.GuardrailPlugin
			observer := &reflectionObserver{initial: map[string]string{"org_id": "unknown-org"}}
			if dynamic {
				store := tenant.NewStore()
				cfg := reflectionPolicy()
				cfg["stage"] = "post"
				store.Set("unknown-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"missing": {Enabled: true, Action: "block", Config: cfg}}}})
				plugin = guardrails.NewPlugin(nil, nil, func(string, map[string]interface{}) guardrails.Guardrail { return nil }, nil, store)
			} else {
				e := guardrails.NewEngine(config.GuardrailsConfig{Rules: []config.GuardrailRuleConfig{{Name: "missing", Stage: "post", Action: "block", Config: reflectionPolicy()}}}, nil)
				plugin = guardrails.NewPlugin(e, nil, nil, nil, nil)
			}
			s := createTestServerWithPlugins(t, upstream.URL, []pipeline.Plugin{observer, plugin}...)
			w := callReflectionServer(s, true)
			if w.Code != 400 || len(u.calls()) != 0 || !strings.Contains(w.Body.String(), "invalid_guardrail_config") {
				t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
			}
		})
	}
}
