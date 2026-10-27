package server

import (
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	budgetpkg "github.com/futureagi/agentcc-gateway/internal/budget"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/blocklist"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/validation"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	budgetplugin "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	creditplugin "github.com/futureagi/agentcc-gateway/internal/plugins/credits"
	loggingplugin "github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/providers"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestReflexionNativeStageOverrideInspectsOnlyResponse(t *testing.T) {
	for _, stream := range []bool{false, true} {
		for _, tc := range []struct {
			name, prompt, answer string
			cfg                  map[string]interface{}
			wantStatus, calls    int
		}{
			{"input-validation", "OK, please answer", "INVALID", map[string]interface{}{"require_patterns": []interface{}{"^OK"}}, 403, 2},
			{"keyword-blocklist", "Explain forbidden without repeating it", "clean answer", map[string]interface{}{"words": []interface{}{"forbidden"}}, 200, 1},
		} {
			t.Run(fmt.Sprintf("%s/stream=%t", tc.name, stream), func(t *testing.T) {
				u := &reflectionUpstream{blocked: 99, blockedText: tc.answer}
				upstream := httptest.NewServer(http.HandlerFunc(u.serve))
				defer upstream.Close()
				tc.cfg["stage"], tc.cfg["on_block"] = "post", "reflect"
				store := tenant.NewStore()
				store.Set("native-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{tc.name: {Enabled: true, Action: "block", ConfidenceThreshold: .8, Config: tc.cfg}}}})
				factory := func(name string, cfg map[string]interface{}) guardrails.Guardrail {
					if name == "input-validation" {
						return validation.New(cfg)
					}
					return blocklist.New(cfg)
				}
				observer := &reflectionObserver{initial: map[string]string{"org_id": "native-org"}}
				s := createTestServerWithPlugins(t, upstream.URL, guardrails.NewPlugin(nil, nil, factory, nil, store), observer)
				r := httptest.NewRequest("POST", "/v1/chat/completions", strings.NewReader(fmt.Sprintf(`{"model":"gpt-4o","messages":[{"role":"user","content":%q}],"stream":%t}`, tc.prompt, stream)))
				w := httptest.NewRecorder()
				s.httpServer.Handler.ServeHTTP(w, r)
				if w.Code != tc.wantStatus || len(u.calls()) != tc.calls {
					t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
				}
				if len(observer.snapshot.Request.Messages) != 1 || !strings.Contains(string(observer.snapshot.Request.Messages[0].Content), tc.prompt) {
					t.Fatal("response-only input changed the original request")
				}
			})
		}
	}
}

func reflectionLogCapture(t *testing.T) (*loggingplugin.Plugin, <-chan string) {
	t.Helper()
	records := make(chan string, 1)
	webhook := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		records <- string(body)
		w.WriteHeader(http.StatusNoContent)
	}))
	t.Cleanup(webhook.Close)
	logger := loggingplugin.New(config.RequestLoggingConfig{Enabled: true, BufferSize: 10, Workers: 1}, nil)
	logger.SetFlusher(loggingplugin.NewLogFlusher(webhook.URL, "", time.Hour, 10))
	t.Cleanup(logger.Close)
	return logger, records
}

func TestReflexionHTTPRepairErrorsDoNotExposePrivateMessages(t *testing.T) {
	for _, stream := range []bool{false, true} {
		t.Run(fmt.Sprintf("stream=%t", stream), func(t *testing.T) {
			u := &reflectionUpstream{blocked: 1, repairError: true}
			upstream := httptest.NewServer(http.HandlerFunc(u.serve))
			defer upstream.Close()
			logger, records := reflectionLogCapture(t)
			observer := &reflectionObserver{}
			s := createTestServerWithPlugins(t, upstream.URL, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), observer, logger)
			w := callReflectionServer(s, stream)
			if w.Code != 400 || len(u.calls()) != 2 {
				t.Fatalf("status=%d calls=%d body=%s", w.Code, len(u.calls()), w.Body.String())
			}
			var response models.ErrorResponse
			if err := json.Unmarshal(w.Body.Bytes(), &response); err != nil || response.Error.Type != models.ErrTypeInvalidRequest || response.Error.Code != "provider_400" {
				t.Fatalf("error identity lost: body=%s error=%v", w.Body.String(), err)
			}
			logger.Close()
			var record string
			select {
			case record = <-records:
			default:
				t.Fatal("request log was not delivered")
			}
			if !strings.Contains(record, `"status_code":400`) || !strings.Contains(record, "Provider request failed during response repair") {
				t.Fatalf("unexpected request log: %s", record)
			}
			for _, sink := range []string{w.Body.String(), record, fmt.Sprint(observer.snapshot.Errors)} {
				for _, private := range []string{"rejected secret answer", "Remove the rejected", "The preceding answer failed", "private-error-type", "private-error-code"} {
					if strings.Contains(sink, private) {
						t.Fatalf("private material escaped through an error: %s", sink)
					}
				}
			}
		})
	}
}

func reflectionModelOverrideServer(t *testing.T, url, override string, plugins ...pipeline.Plugin) *Server {
	t.Helper()
	if override == "" {
		return createTestServerWithPlugins(t, url, plugins...)
	}
	cfg := config.DefaultConfig()
	cfg.Providers["openai"] = config.ProviderConfig{BaseURL: url, APIFormat: "openai", Models: []string{override}}
	cfg.Routing.Targets = map[string][]config.RoutingTargetConfig{"gpt-4o": {{Provider: "openai", ModelOverride: override}}}
	registry, err := providers.NewRegistry(cfg)
	if err != nil {
		t.Fatal(err)
	}
	s := New(cfg, "", registry, pipeline.NewEngine(plugins...), nil, nil, nil, nil, testModelDBPtr(), nil, nil)
	s.ready.Store(true)
	return s
}

func TestReflexionAttemptBudgetUsesRequestedModelFallback(t *testing.T) {
	for _, override := range []string{"", "deployment-x"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("override=%s/stream=%t", override, stream), func(t *testing.T) {
				u := &reflectionUpstream{blocked: 9, responseModel: "gpt-4o-2024-08-06", usage: &models.Usage{PromptTokens: 1000, CompletionTokens: 500, TotalTokens: 1500}}
				upstream := httptest.NewServer(http.HandlerFunc(u.serve))
				defer upstream.Close()
				tracker := budgetpkg.NewTracker(config.BudgetsConfig{Org: &config.BudgetLevelConfig{Limit: 1, PerModel: map[string]float64{"gpt-4o": .005}}})
				budget := budgetplugin.New(tracker, budgetplugin.NewPricingLookup(nil), true, nil)
				observer := &reflectionObserver{initial: map[string]string{"key_type": "internal"}}
				s := reflectionModelOverrideServer(t, upstream.URL, override, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), budget, reflectionCost(), observer)
				w := callReflectionServer(s, stream)
				spend, modelSpend := tracker.GetSpend("org", "")
				if w.Code != 429 || len(u.calls()) != 1 || math.Abs(spend-.0075) > 1e-9 || math.Abs(modelSpend["gpt-4o"]-.0075) > 1e-9 || modelSpend[u.responseModel] != 0 || modelSpend["deployment-x"] != 0 {
					t.Fatalf("status=%d calls=%d spend=%f models=%v body=%s", w.Code, len(u.calls()), spend, modelSpend, w.Body.String())
				}
				want := override
				if want == "" {
					want = "gpt-4o"
				}
				if u.calls()[0].Model != want || observer.snapshot.Model != "gpt-4o" {
					t.Fatalf("model identities lost: upstream=%s caller=%s", u.calls()[0].Model, observer.snapshot.Model)
				}
				var attempts []models.ProviderAttempt
				if err := json.Unmarshal([]byte(observer.metadata["provider_attempt_usage"]), &attempts); err != nil || len(attempts) != 1 || attempts[0].Model != u.responseModel {
					t.Fatalf("serving model lost from ledger: attempts=%+v error=%v", attempts, err)
				}
			})
		}
	}
}

func TestReflexionAttemptCostUsesRequestedModelFallback(t *testing.T) {
	for _, override := range []string{"", "deployment-x"} {
		for _, stream := range []bool{false, true} {
			t.Run(fmt.Sprintf("override=%s/stream=%t", override, stream), func(t *testing.T) {
				u := &reflectionUpstream{blocked: 1, responseModel: "unknown-serving-model"}
				upstream := httptest.NewServer(http.HandlerFunc(u.serve))
				defer upstream.Close()
				keys := auth.NewKeyStore(config.AuthConfig{Keys: []config.AuthKeyConfig{{Name: "managed", Key: "test-repair-cost-key", KeyType: "managed", CreditBalance: 1}}})
				key := keys.List()[0]
				observer := &reflectionObserver{initial: map[string]string{"auth_key_id": key.ID, "key_type": "managed"}}
				s := reflectionModelOverrideServer(t, upstream.URL, override, reflectionPlugin(&reflectionCheck{name: "output"}, reflectionPolicy(), "block", "post"), reflectionCost(), creditplugin.New(true, keys), observer)
				w := callReflectionServer(s, stream)
				if w.Code != 200 || len(u.calls()) != 2 || observer.metadata["cost"] != "0.600000" || math.Abs(key.BalanceUSD()-.4) > 1e-9 {
					t.Fatalf("status=%d calls=%d metadata=%v balance=%f", w.Code, len(u.calls()), observer.metadata, key.BalanceUSD())
				}
				var attempts []models.ProviderAttempt
				if err := json.Unmarshal([]byte(observer.metadata["provider_attempt_usage"]), &attempts); err != nil || len(attempts) != 2 || attempts[0].Model != u.responseModel || attempts[1].Model != u.responseModel {
					t.Fatalf("serving model lost from ledger: attempts=%+v error=%v", attempts, err)
				}
			})
		}
	}
}

func TestPostRejectScrubsTenantWarningsFromRequestLog(t *testing.T) {
	u := &reflectionUpstream{blocked: 9}
	upstream := httptest.NewServer(http.HandlerFunc(u.serve))
	defer upstream.Close()
	registry := map[string]guardrails.Guardrail{"reject": &reflectionCheck{name: "reject", stage: guardrails.StagePost}, "warn": &reflectionCheck{name: "warn", stage: guardrails.StagePost}}
	engine := guardrails.NewEngine(config.GuardrailsConfig{DefaultTimeout: time.Second, Rules: []config.GuardrailRuleConfig{{Name: "reject", Stage: "post", Action: "block"}}}, registry)
	store := tenant.NewStore()
	store.Set("warning-org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{"warn": {Enabled: true, Action: "warn", ConfidenceThreshold: .5, Config: map[string]interface{}{"stage": "post"}}}}})
	observer := &reflectionObserver{initial: map[string]string{"org_id": "warning-org", "guardrail_warnings": "safe pre-stage warning"}}
	logger, records := reflectionLogCapture(t)
	s := createTestServerWithPlugins(t, upstream.URL, guardrails.NewPlugin(engine, registry, nil, nil, store), observer, logger)
	w := callReflectionServer(s, false)
	if w.Code != 403 || len(u.calls()) != 1 || observer.snapshot.Response != nil || observer.metadata["guardrail_warnings"] != "safe pre-stage warning" {
		t.Fatalf("status=%d calls=%d metadata=%v", w.Code, len(u.calls()), observer.metadata)
	}
	if len(observer.snapshot.GuardrailResults) != 2 {
		t.Fatalf("diagnostics lost: %+v", observer.snapshot.GuardrailResults)
	}
	for _, result := range observer.snapshot.GuardrailResults {
		if result.Message != "response check failed" || result.Score != 1 || (result.Action != "block" && result.Action != "warn") {
			t.Fatalf("unsafe or incomplete diagnostic: %+v", result)
		}
	}
	logger.Close()
	select {
	case record := <-records:
		if strings.Contains(record, "rejected secret answer") || !strings.Contains(record, "safe pre-stage warning") || !strings.Contains(record, `"status_code":403`) {
			t.Fatalf("unsafe request log: %s", record)
		}
	default:
		t.Fatal("request log was not delivered")
	}
}
