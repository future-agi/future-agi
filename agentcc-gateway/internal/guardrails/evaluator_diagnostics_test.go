package guardrails_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/plugins/logging"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type diagnosticsContentBlock struct{}

func (*diagnosticsContentBlock) Name() string            { return "first-block" }
func (*diagnosticsContentBlock) Stage() guardrails.Stage { return guardrails.StagePost }
func (*diagnosticsContentBlock) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: false, Score: 1, Message: "PRIVATE_SENTINEL"}
}

func TestPostEvaluatorFailureRemainsDistinctInCallerAndTrace(t *testing.T) {
	for _, mode := range []string{"static", "tenant", "mixed"} {
		for _, tc := range []struct {
			name, body, code string
			status, calls    int
		}{
			{"authentication", "PRIVATE_SENTINEL", "guardrail_evaluation_failed", http.StatusUnauthorized, 1},
			{"parse", `{"PRIVATE_SENTINEL":`, "guardrail_evaluation_failed", http.StatusOK, 1},
			{"content", `{"flagged":true,"categories":{"PRIVATE_SENTINEL":true},"category_scores":{"PRIVATE_SENTINEL":1}}`, "content_blocked", http.StatusOK, 2},
		} {
			t.Run(mode+"/"+tc.name, func(t *testing.T) {
				var logs bytes.Buffer
				logger := slog.Default()
				slog.SetDefault(slog.New(slog.NewJSONHandler(&logs, nil)))
				defer slog.SetDefault(logger)
				var evaluatorCalls atomic.Int32
				evaluator := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					evaluatorCalls.Add(1)
					w.WriteHeader(tc.status)
					w.Write([]byte(tc.body))
				}))
				defer evaluator.Close()
				controls := map[string]interface{}{"provider": "lakera", "endpoint": evaluator.URL, "stage": "post", "on_block": "reflect", "reflect_max_attempts": 1}
				g := external.New("evaluator", controls)
				registry := map[string]guardrails.Guardrail{g.Name(): g}
				cfg := config.GuardrailsConfig{DefaultTimeout: time.Second}
				store := tenant.NewStore()
				if mode == "static" {
					cfg.Rules = []config.GuardrailRuleConfig{{Name: g.Name(), Stage: "post", Action: "block", Config: controls}}
				} else {
					store.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{g.Name(): {Enabled: true, Action: "block", Config: controls}}}})
					if mode == "mixed" {
						first := &diagnosticsContentBlock{}
						registry[first.Name()] = first
						cfg.Rules = []config.GuardrailRuleConfig{{Name: first.Name(), Stage: "post", Action: "block", Config: map[string]interface{}{"on_block": "reflect"}}}
					}
				}
				plugin := guardrails.NewPlugin(guardrails.NewEngine(cfg, registry), registry, nil, nil, store)
				trace := logging.New(config.RequestLoggingConfig{Enabled: true, IncludeBodies: true, Workers: 1}, store)
				defer trace.Close()
				rc := postInputContext(t, "original prompt", "unused")
				providerCalls := 0
				err := pipeline.NewEngine(plugin, trace).Process(context.Background(), rc, func(context.Context, *models.RequestContext) error {
					providerCalls++
					rc.Response = &models.ChatCompletionResponse{Model: rc.Model, Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"PRIVATE_SENTINEL"`)}, FinishReason: "stop"}}}
					return nil
				})
				trace.Close()
				var apiErr *models.APIError
				if !errors.As(err, &apiErr) || apiErr.Code != tc.code || apiErr.Status != http.StatusForbidden {
					t.Fatalf("error=%v, want %s", err, tc.code)
				}
				if providerCalls != tc.calls || int(evaluatorCalls.Load()) != tc.calls || rc.Response != nil {
					t.Fatalf("provider calls=%d evaluator calls=%d response=%+v", providerCalls, evaluatorCalls.Load(), rc.Response)
				}
				if !strings.Contains(logs.String(), `"error_code":"`+tc.code+`"`) {
					t.Fatalf("trace lost failure classification: %s", logs.String())
				}
				response := httptest.NewRecorder()
				models.WriteError(response, apiErr)
				diagnostics, _ := json.Marshal(rc.GuardrailResults)
				if strings.Contains(response.Body.String()+logs.String()+string(diagnostics), "PRIVATE_SENTINEL") {
					t.Fatal("rejected content or evaluator text reached public diagnostics")
				}
			})
		}
	}
}
