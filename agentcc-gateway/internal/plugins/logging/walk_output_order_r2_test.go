package logging

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type walkTextCheckR2 struct{}

func (walkTextCheckR2) Name() string            { return "text-check" }
func (walkTextCheckR2) Stage() guardrails.Stage { return guardrails.StagePost }
func (walkTextCheckR2) Check(context.Context, *guardrails.CheckInput) *guardrails.CheckResult {
	return &guardrails.CheckResult{Pass: true}
}

// The real logging plugin runs in the pipeline's parallel observer phase. Its
// emitter is buffered without workers so the exact emitted record can be read
// without starting a server or performing network I/O.
func TestWalkSupplementalOutputLoggedAfterRefusalR2(t *testing.T) {
	for _, origin := range []string{"rules", "org"} {
		for _, tc := range []struct {
			name, action, override string
			blocked                bool
		}{
			{"block", "block", "", true},
			{"warn", "warn", "", false},
			{"log", "log", "", false},
			{"log-only override", "block", "log-only", false},
			{"disabled override", "block", "disabled", false},
		} {
			// Per-request rule overrides do not override the org's dynamic checks.
			if origin == "org" && tc.override != "" {
				continue
			}
			for _, message := range []string{
				`{"role":"assistant","content":null,"audio":{"id":"audio_fixture","data":"SGVsbG8=","transcript":"Hello"}}`,
				`{"role":"assistant","content":null,"refusal":"Cannot help"}`,
			} {
				t.Run(origin+"/"+tc.name+"/"+message, func(t *testing.T) {
					rc := models.AcquireRequestContext()
					defer rc.Release()
					rc.Model, rc.EndpointType = "text", "chat"
					rc.Request = &models.ChatCompletionRequest{Model: "text"}
					rc.Privacy.ContentMode = models.ContentModeFull
					rc.Metadata["org_id"] = "org"
					if tc.override != "" {
						rc.Metadata["key_allow_policy_override"] = "true"
						rc.Metadata["x-guardrail-policy"] = tc.override
					}
					registry := map[string]guardrails.Guardrail{"text-check": walkTextCheckR2{}}
					cfg := config.GuardrailsConfig{DefaultTimeout: time.Second}
					tenants := tenant.NewStore()
					if origin == "rules" {
						cfg.Rules = []config.GuardrailRuleConfig{{Name: "text-check", Stage: "post", Mode: "sync", Action: tc.action}}
					} else {
						tenants.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
							"text-check": {Enabled: true, Action: tc.action},
						}}})
					}
					checks := guardrails.NewPlugin(guardrails.NewEngine(cfg, registry), registry, nil, nil, tenants)
					if result := checks.ProcessRequest(context.Background(), rc); result.Error != nil {
						t.Fatal(result.Error)
					}
					if err := json.Unmarshal([]byte(`{"choices":[{"index":0,"message":{"role":"assistant","content":"plain"}},{"index":1,"message":`+message+`}]}`), &rc.Response); err != nil {
						t.Fatal(err)
					}
					log := &Plugin{cfg: config.RequestLoggingConfig{Enabled: true, IncludeBodies: true}, emitter: &TraceEmitter{ch: make(chan TraceRecord, 1)}}
					pipeline.NewEngine(log, checks).RunPostPlugins(context.Background(), rc)
					var rec TraceRecord
					select {
					case rec = <-log.emitter.ch:
					default:
						t.Fatal("request was not logged")
					}
					w := httptest.NewRecorder()
					if tc.blocked {
						err := models.WithheldAnswerError(rc)
						if err == nil {
							t.Errorf("post-plugins did not withhold supplemental output; logged status=%d", rec.StatusCode)
						} else {
							models.WriteError(w, err)
							if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), "answer_unavailable") {
								t.Errorf("final response is not the refusal: %d %s", w.Code, w.Body.String())
							}
						}
						if rec.StatusCode != http.StatusForbidden || rec.ErrorCode != "answer_unavailable" || rec.ErrorFromUpstream {
							t.Errorf("log does not describe final gateway refusal: %+v", rec)
						}
						if rec.ResponseBody != nil || rc.Response != nil || !rec.GuardrailTriggered || len(rec.GuardrailResults) != 1 || rec.GuardrailResults[0].Action != "block" {
							t.Errorf("refused answer or block missing from log state: %+v", rec)
						}
					} else {
						if rc.Response == nil || rec.StatusCode != http.StatusOK || rec.ErrorCode != "" || rec.ResponseBody == nil {
							t.Fatalf("nonblocking output was changed: %+v", rec)
						}
						if err := json.NewEncoder(w).Encode(rc.Response); err != nil {
							t.Fatal(err)
						}
						if !strings.Contains(w.Body.String(), `"audio"`) && !strings.Contains(w.Body.String(), `"refusal"`) {
							t.Fatal("allowed supplemental output was lost")
						}
					}
				})
			}
		}
	}
}
