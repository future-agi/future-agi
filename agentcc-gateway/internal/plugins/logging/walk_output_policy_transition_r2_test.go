package logging

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The final-writer backstop uses the pre-request blocker snapshot. A policy
// relaxed while the provider runs must not postpone that refusal until after
// logging. The post stage must preserve the same fail-closed decision.
func TestWalkSupplementalOutputPolicyTransitionR2(t *testing.T) {
	for _, origin := range []string{"rules", "org"} {
		t.Run(origin, func(t *testing.T) {
			rc := models.AcquireRequestContext()
			defer rc.Release()
			rc.Model, rc.EndpointType = "text", "chat"
			rc.Request = &models.ChatCompletionRequest{Model: "text"}
			rc.Metadata["org_id"] = "org"
			registry := map[string]guardrails.Guardrail{"text-check": walkTextCheckR2{}}
			cfg := config.GuardrailsConfig{DefaultTimeout: time.Second}
			tenants := tenant.NewStore()
			orgConfig := func(action string) *tenant.OrgConfig {
				return &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
					"text-check": {Enabled: true, Action: action},
				}}}
			}
			if origin == "rules" {
				cfg.Rules = []config.GuardrailRuleConfig{{Name: "text-check", Stage: "post", Mode: "sync", Action: "block"}}
			} else {
				tenants.Set("org", orgConfig("block"))
			}
			checks := guardrails.NewPlugin(guardrails.NewEngine(cfg, registry), registry, nil, nil, tenants)
			if result := checks.ProcessRequest(context.Background(), rc); result.Error != nil {
				t.Fatal(result.Error)
			}
			if rc.Flags.AnswerBlockers == 0 {
				t.Fatal("fixture has no pre-request blocker snapshot")
			}
			// RulePolicy reads the current org policy in the response stage too.
			tenants.Set("org", orgConfig("log"))
			if err := json.Unmarshal([]byte(`{"choices":[{"message":{"role":"assistant","content":null,"audio":{"id":"audio_fixture"}}}]}`), &rc.Response); err != nil {
				t.Fatal(err)
			}
			log := &Plugin{cfg: config.RequestLoggingConfig{Enabled: true}, emitter: &TraceEmitter{ch: make(chan TraceRecord, 1)}}
			pipeline.NewEngine(log, checks).RunPostPlugins(context.Background(), rc)
			rec := <-log.emitter.ch
			if rec.StatusCode != http.StatusForbidden || rec.ErrorCode != "answer_unavailable" || models.WithheldAnswerError(rc) == nil {
				t.Fatalf("snapshot refusal was deferred until after logging: status=%d code=%q response=%+v", rec.StatusCode, rec.ErrorCode, rc.Response)
			}
		})
	}
}
