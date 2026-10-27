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
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	rateplugin "github.com/futureagi/agentcc-gateway/internal/plugins/ratelimit"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

func TestReflexionEvaluatorFailuresNeverRegenerate(t *testing.T) {
	for _, kind := range []string{"webhook", "futureagi", "external"} {
		for _, failure := range []string{"http500", "http401", "malformed"} {
			for _, stream := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/stream=%t", kind, failure, stream), func(t *testing.T) {
					var evaluations atomic.Int32
					evaluator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						evaluations.Add(1)
						if failure == "http500" {
							http.Error(w, "private evaluator detail", 500)
							return
						}
						if failure == "http401" {
							http.Error(w, "private evaluator credentials", 401)
							return
						}
						fmt.Fprint(w, `{"private evaluator malformed`)
					}))
					defer evaluator.Close()
					cfg := reflectionPolicy()
					cfg["retry"] = 0
					var check guardrails.Guardrail
					switch kind {
					case "webhook":
						cfg["url"] = evaluator.URL
						check = webhook.New("evaluator", cfg)
					case "futureagi":
						cfg["base_url"] = evaluator.URL
						cfg["api_key"] = "fixture-api-key"
						cfg["secret_key"] = "fixture-secret-key"
						cfg["eval_id"] = "fixture-eval"
						check = futureagi.New("evaluator", cfg)
					case "external":
						cfg["provider"] = "lakera"
						cfg["endpoint"] = evaluator.URL
						cfg["api_key"] = "fixture-api-key"
						check = external.New("evaluator", cfg)
					}
					registry := map[string]guardrails.Guardrail{"evaluator": check}
					ge := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "evaluator", Stage: "post", Action: "block", Config: cfg}}}, registry)
					u := &reflectionUpstream{}
					upstream := httptest.NewServer(http.HandlerFunc(u.serve))
					defer upstream.Close()
					observer := &reflectionObserver{}
					s := createTestServerWithPlugins(t, upstream.URL, guardrails.NewPlugin(ge, registry, nil, nil, nil), reflectionCost(), observer)
					w := callReflectionServer(s, stream)
					if w.Code != 403 || len(u.calls()) != 1 || evaluations.Load() != 1 || observer.count != 1 || observer.usage.TotalTokens != 30 || observer.metadata["cost"] != "0.300000" {
						t.Fatalf("status=%d generations=%d evaluations=%d usage=%+v metadata=%v", w.Code, len(u.calls()), evaluations.Load(), observer.usage, observer.metadata)
					}
					if strings.Contains(w.Body.String(), "private") || strings.Contains(w.Body.String(), "accepted answer") || strings.Contains(w.Body.String(), "data:") || observer.snapshot.Response != nil {
						t.Fatalf("unsafe response: %s", w.Body.String())
					}
				})
			}
		}
	}
}

func TestReflexionAdmissionDoesNotOpenSharedCircuit(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			u := &reflectionUpstream{blocked: 1}
			upstream := httptest.NewServer(http.HandlerFunc(u.serve))
			defer upstream.Close()
			cfg := config.DefaultConfig()
			cfg.Providers["routed"] = config.ProviderConfig{BaseURL: upstream.URL, APIFormat: "openai", Models: []string{"gpt-4o"}}
			cfg.Routing.DefaultStrategy = "round-robin"
			cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "routed", Weight: 1}}}
			cfg.Routing.Failover = config.FailoverConfig{Enabled: true, MaxAttempts: 2}
			registry, err := providers.NewRegistry(cfg)
			if err != nil {
				t.Fatal(err)
			}
			provider, ok := registry.GetProvider("routed")
			if !ok {
				t.Fatal("missing provider")
			}
			breakers := routing.NewCircuitBreakerRegistry(config.CircuitBreakerConfig{Enabled: true, FailureThreshold: 1, Cooldown: time.Hour}, nil)
			h := &Handlers{registry: registry, engine: pipeline.NewEngine(reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), rateplugin.New(config.RateLimitConfig{Enabled: true, GlobalRPM: 1}, nil, nil), reflectionCost())}
			h.failover.Store(routing.NewFailover(cfg.Routing.Failover, registry.Router(), nil, breakers))
			for i := 0; i < 2; i++ {
				rc := models.AcquireRequestContext()
				rc.Model = "gpt-4o"
				rc.Provider = "routed"
				rc.IsStream = stream
				rc.Metadata["auth_key_id"] = fmt.Sprintf("key-%d", i)
				rc.Metadata["org_id"] = fmt.Sprintf("org-%d", i)
				rc.Request = &models.ChatCompletionRequest{Model: "gpt-4o", Stream: stream, Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"original"`)}}, StreamOptions: &models.StreamOptions{IncludeUsage: true}}
				w := httptest.NewRecorder()
				if stream {
					h.handleStream(context.Background(), w, rc, provider, nil)
				} else {
					h.handleNonStream(context.Background(), w, rc, provider, nil)
				}
				want := 429
				if i == 1 {
					want = 200
				}
				if w.Code != want || len(u.calls()) != i+1 || breakers.Get("routed").State() != routing.StateClosed {
					t.Fatalf("request=%d status=%d calls=%d circuit=%v body=%s", i, w.Code, len(u.calls()), breakers.Get("routed").State(), w.Body.String())
				}
				if i == 0 && (rc.AccountedUsage == nil || rc.AccountedUsage.TotalTokens != 30 || rc.Metadata["cost"] != "0.300000") {
					t.Fatalf("denied repair lost or duplicated usage: %+v %v", rc.AccountedUsage, rc.Metadata)
				}
				rc.Release()
			}
		})
	}
}
