package guardrails_test

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	g "github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/policy"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// A webhook verdict whose keys differ only in case is still a content
// decision for chat requests: it is scored, not handed to the failure policy.
// Realtime checks refuse the same envelope (see the speech webhook tests).
func caseVariantWebhook(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"Pass":false,"Score":0.9,"Message":"synthetic rejection"}`)
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestOrgWebhookCaseVariantRejectionIsContentBlock(t *testing.T) {
	for _, org := range []bool{false, true} {
		t.Run(fmt.Sprintf("org_fail_open=%v", org), func(t *testing.T) {
			srv := caseVariantWebhook(t)
			raw := fmt.Sprintf(`{"fail_open":%v,"checks":{"hook":{"enabled":true,"action":"block","confidence_threshold":0.5}}}`, org)
			p := acceptancePlugin(t, !org, raw, map[string]g.Guardrail{"hook": webhook.New("hook", map[string]interface{}{"url": srv.URL})})
			assertDecision(t, p, true, 403)
		})
	}
}

func TestEngineWebhookCaseVariantRejectionIsContentBlock(t *testing.T) {
	for _, failOpen := range []bool{false, true} {
		t.Run(fmt.Sprintf("fail_open=%v", failOpen), func(t *testing.T) {
			srv := caseVariantWebhook(t)
			engine := g.NewEngine(config.GuardrailsConfig{
				FailOpen:       failOpen,
				DefaultTimeout: 5 * time.Second,
				Rules:          []config.GuardrailRuleConfig{{Name: "hook", Stage: "pre", Mode: "sync", Action: "block", Threshold: 0.5}},
			}, map[string]g.Guardrail{"hook": webhook.New("hook", map[string]interface{}{"url": srv.URL})})
			input := &g.CheckInput{Request: &models.ChatCompletionRequest{Model: "synthetic-model", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic text"`)}}}}
			res := engine.RunPre(context.Background(), input, nil, policy.RequestPolicyNone)
			if !res.Blocked || res.Unavailable {
				t.Fatalf("blocked=%v unavailable=%v, want a content block", res.Blocked, res.Unavailable)
			}
		})
	}
}
