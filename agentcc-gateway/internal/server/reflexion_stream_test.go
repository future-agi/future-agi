package server

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/modeldb"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	costplugin "github.com/futureagi/agentcc-gateway/internal/plugins/cost"
)

type gatedReflectionCheck struct {
	reflectionCheck
	once    sync.Once
	entered chan struct{}
	release chan struct{}
}

func (g *gatedReflectionCheck) Check(ctx context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	g.once.Do(func() { close(g.entered) })
	select {
	case <-g.release:
	case <-ctx.Done():
		return &guardrails.CheckResult{Pass: false, OperationalError: true}
	}
	return g.reflectionCheck.Check(ctx, in)
}

type countedResponseWriter struct {
	*httptest.ResponseRecorder
	writes atomic.Int32
}

func (w *countedResponseWriter) WriteHeader(status int) {
	w.writes.Add(1)
	w.ResponseRecorder.WriteHeader(status)
}
func (w *countedResponseWriter) Write(b []byte) (int, error) {
	w.writes.Add(1)
	return w.ResponseRecorder.Write(b)
}
func (w *countedResponseWriter) Flush() { w.writes.Add(1); w.ResponseRecorder.Flush() }

func TestReflexionStreamWritesNothingUntilValidated(t *testing.T) {
	u := &reflectionUpstream{blocked: 1}
	upstream := httptest.NewServer(http.HandlerFunc(u.serve))
	defer upstream.Close()
	g := &gatedReflectionCheck{reflectionCheck: reflectionCheck{name: "gate"}, entered: make(chan struct{}), release: make(chan struct{})}
	registry := map[string]guardrails.Guardrail{"gate": g}
	engine := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: 5 * time.Second, Rules: []config.GuardrailRuleConfig{{Name: "gate", Stage: "post", Action: "block", Config: reflectionPolicy()}}}, registry)
	s := createTestServerWithPlugins(t, upstream.URL, guardrails.NewPlugin(engine, registry, nil, nil, nil))
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(`{"model":"gpt-4o","messages":[{"role":"user","content":"original"}],"stream":true}`)).WithContext(ctx)
	w := &countedResponseWriter{ResponseRecorder: httptest.NewRecorder()}
	done := make(chan struct{})
	go func() { defer close(done); s.httpServer.Handler.ServeHTTP(w, r) }()
	select {
	case <-g.entered:
	case <-ctx.Done():
		t.Fatal("guardrail did not run")
	}
	if writes := w.writes.Load(); writes != 0 {
		t.Errorf("response committed %d times before validation", writes)
	}
	close(g.release)
	select {
	case <-done:
	case <-ctx.Done():
		t.Fatal("request did not finish")
	}
	if w.Code != 200 || strings.Contains(w.Body.String(), "rejected") || !strings.Contains(w.Body.String(), "accepted") {
		t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
	}
}

func TestReflexionFailoverUsageRetainsServingModel(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			var primaryCalls atomic.Int32
			blocked := &reflectionUpstream{blocked: 1}
			primary := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if primaryCalls.Add(1) == 1 {
					blocked.serve(w, r)
				} else {
					http.Error(w, "upstream unavailable", 503)
				}
			}))
			defer primary.Close()
			accepted := &reflectionUpstream{}
			fallback := httptest.NewServer(http.HandlerFunc(accepted.serve))
			defer fallback.Close()
			h, p := newStreamingModelFallbackHandler(t, primary.URL, fallback.URL)
			db := modeldb.New(map[string]*modeldb.ModelInfo{
				"primary-model":  {ID: "primary-model", Pricing: modeldb.PricingInfo{InputPerToken: .01, OutputPerToken: .01}},
				"fallback-model": {ID: "fallback-model", Pricing: modeldb.PricingInfo{InputPerToken: .02, OutputPerToken: .02}},
			})
			observer := &reflectionObserver{}
			h.engine = pipeline.NewEngine(reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), costplugin.New(true, func() *modeldb.ModelDB { return db }, nil, nil), observer)
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
			if w.Code != 200 || primaryCalls.Load() != 2 || len(accepted.calls()) != 1 {
				t.Fatalf("status=%d primary=%d fallback=%d body=%s", w.Code, primaryCalls.Load(), len(accepted.calls()), w.Body.String())
			}
			if observer.count != 1 || observer.usage.TotalTokens != 60 || observer.metadata["cost"] != "0.900000" {
				t.Fatalf("usage=%+v metadata=%v observers=%d", observer.usage, observer.metadata, observer.count)
			}
			var attempts []models.ProviderAttempt
			if err := json.Unmarshal([]byte(observer.metadata["provider_attempt_usage"]), &attempts); err != nil {
				t.Fatal(err)
			}
			if len(attempts) != 3 || attempts[0].Model != "primary-model" || attempts[0].Cost != "0.300000" || !attempts[1].UsageMissing || attempts[2].Model != "fallback-model" || attempts[2].Cost != "0.600000" {
				t.Fatalf("attempts=%+v", attempts)
			}
			if w.Header().Get("x-agentcc-model-used") != "fallback-model" || w.Header().Get("x-agentcc-usage-incomplete") != "true" {
				t.Fatalf("headers=%v", w.Header())
			}
		})
	}
}
