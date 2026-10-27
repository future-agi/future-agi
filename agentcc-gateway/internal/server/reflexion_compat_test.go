package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/leakage"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/pii"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	rateplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestReflexionDisabledFailoverKeepsSingleRPMAdmission(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			var calls atomic.Int32
			primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost || r.URL.Path != "/v1/chat/completions" {
					w.WriteHeader(http.StatusNotFound)
					return
				}
				calls.Add(1)
				http.Error(w, "upstream unavailable", 503)
			}))
			defer primary.Close()
			u := &reflectionUpstream{}
			fallback := httptest.NewServer(http.HandlerFunc(u.serve))
			defer fallback.Close()
			h, p := newStreamingModelFallbackHandler(t, primary.URL, fallback.URL)
			observer := &reflectionObserver{}
			h.engine = pipeline.NewEngine(rateplugin.New(config.RateLimitConfig{Enabled: true, GlobalRPM: 1}, nil, nil), observer)
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model = "primary-model"
			rc.Provider = "primary"
			rc.IsStream = stream
			rc.Request = &models.ChatCompletionRequest{Model: "primary-model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original"`)}}, Stream: stream, StreamOptions: &models.StreamOptions{IncludeUsage: true}}
			w := httptest.NewRecorder()
			if stream {
				h.handleStream(context.Background(), w, rc, p, nil)
			} else {
				h.handleNonStream(context.Background(), w, rc, p, nil)
			}
			if w.Code != 200 || calls.Load() != 1 || len(u.calls()) != 1 || observer.count != 1 {
				t.Fatalf("status=%d primary=%d fallback=%d observers=%d body=%s", w.Code, calls.Load(), len(u.calls()), observer.count, w.Body.String())
			}
			if observer.metadata["provider_attempt_usage"] != "" || observer.metadata["ratelimit_remaining"] != "0" {
				t.Fatalf("ordinary failover acquired reflection lifecycle: %v", observer.metadata)
			}
		})
	}
}

func TestReflexionDynamicProviderModeCannotDisableBuffering(t *testing.T) {
	u := &reflectionUpstream{blocked: 1}
	upstream := httptest.NewServer(http.HandlerFunc(u.serve))
	defer upstream.Close()
	store := tenant.NewStore()
	store.Set("org-provider-mode", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"output": {Enabled: true, Action: "block", Config: map[string]interface{}{"stage": "post", "on_block": "reflect", "mode": "async"}}}}})
	registry := map[string]guardrails.Guardrail{"output": &reflectionCheck{name: "output"}}
	plugin := guardrails.NewPlugin(nil, registry, nil, nil, store)
	observer := &reflectionObserver{initial: map[string]string{"org_id": "org-provider-mode"}}
	s := createTestServerWithPlugins(t, upstream.URL, plugin, observer)
	w := callReflectionServer(s, true)
	if w.Code != 200 || len(u.calls()) != 2 || strings.Contains(w.Body.String(), "rejected") || !strings.Contains(w.Body.String(), "accepted") {
		t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
	}
}

func TestReflexionNativePostGuardrail(t *testing.T) {
	for _, dynamic := range []bool{false, true} {
		for _, reflects := range []bool{false, true} {
			t.Run(fmt.Sprintf("dynamic=%t/reflect=%t", dynamic, reflects), func(t *testing.T) {
				u := &reflectionUpstream{blocked: 1, blockedText: "private /etc/passwd"}
				upstream := httptest.NewServer(http.HandlerFunc(u.serve))
				defer upstream.Close()
				name := "data-leakage-prevention"
				registry := map[string]guardrails.Guardrail{name: leakage.New(nil)}
				cfg := map[string]interface{}{}
				if reflects {
					cfg = reflectionPolicy()
				}
				observer := &reflectionObserver{}
				var plugin *guardrails.GuardrailPlugin
				if dynamic {
					store := tenant.NewStore()
					store.Set("native-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{name: {Enabled: true, Action: "block", Config: cfg}}}})
					observer.initial = map[string]string{"org_id": "native-org"}
					plugin = guardrails.NewPlugin(nil, registry, nil, nil, store)
				} else {
					engine := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: name, Stage: "post", Action: "block", Config: cfg}}}, registry)
					plugin = guardrails.NewPlugin(engine, registry, nil, nil, nil)
				}
				s := createTestServerWithPlugins(t, upstream.URL, plugin, reflectionCost(), observer)
				w := callReflectionServer(s, reflects)
				wantStatus, wantCalls := 403, 1
				if reflects {
					wantStatus, wantCalls = 200, 2
				}
				if w.Code != wantStatus || len(u.calls()) != wantCalls || strings.Contains(w.Body.String(), "/etc/passwd") || observer.usage.TotalTokens != 30*wantCalls {
					t.Fatalf("status=%d calls=%d usage=%+v body=%s", w.Code, len(u.calls()), observer.usage, w.Body.String())
				}
			})
		}
	}
}

func TestReflexionPreservesSynchronousPIIRedaction(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				var req models.ChatCompletionRequest
				json.NewDecoder(r.Body).Decode(&req)
				text := "Contact person@example.com"
				if req.Stream {
					w.Header().Set("Content-Type", "text/event-stream")
					finish := "stop"
					for _, c := range []models.StreamChunk{{ID: "pii", Model: req.Model, Choices: []models.StreamChoice{{Delta: models.Delta{Content: &text}}}}, {ID: "pii", Model: req.Model, Choices: []models.StreamChoice{{FinishReason: &finish}}, Usage: &models.Usage{TotalTokens: 3, PromptTokens: 1, CompletionTokens: 2}}} {
						b, _ := json.Marshal(c)
						fmt.Fprintf(w, "data: %s\n\n", b)
					}
					fmt.Fprint(w, "data: [DONE]\n\n")
					return
				}
				b, _ := json.Marshal(text)
				json.NewEncoder(w).Encode(models.ChatCompletionResponse{Model: req.Model, Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: b}, FinishReason: "stop"}}, Usage: &models.Usage{TotalTokens: 3, PromptTokens: 1, CompletionTokens: 2}})
			}))
			defer upstream.Close()
			registry := map[string]guardrails.Guardrail{"pii-detection": pii.New(map[string]interface{}{"remediation": "redact"}), "repair": &reflectionCheck{name: "repair"}}
			cfg := config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "pii-detection", Stage: "post", Action: "warn"}, {Name: "repair", Stage: "post", Action: "block", Config: reflectionPolicy()}}}
			s := createTestServerWithPlugins(t, upstream.URL, guardrails.NewPlugin(guardrails.NewEngine(cfg, registry), registry, nil, nil, nil))
			w := callReflectionServer(s, stream)
			if w.Code != 200 || strings.Contains(w.Body.String(), "person@example.com") {
				t.Fatalf("PII redaction was discarded: status=%d body=%s", w.Code, w.Body.String())
			}
		})
	}
}
