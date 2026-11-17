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

// walkMatchCheckR2 triggers when any choice of the answer reads one of match.
// The checks read a model's refusal and an audio answer's transcript as the
// content of a choice of their own.
type walkMatchCheckR2 struct{ match []string }

func (walkMatchCheckR2) Name() string            { return "text-check" }
func (walkMatchCheckR2) Stage() guardrails.Stage { return guardrails.StagePost }
func (c walkMatchCheckR2) Check(_ context.Context, in *guardrails.CheckInput) *guardrails.CheckResult {
	if in.Response != nil {
		for _, choice := range in.Response.Choices {
			text, _ := guardrails.ContentText(choice.Message.Content)
			for _, m := range c.match {
				if strings.Contains(text, m) {
					return &guardrails.CheckResult{Pass: false, Score: 1, Message: "matched"}
				}
			}
		}
	}
	return &guardrails.CheckResult{Pass: true}
}

const (
	walkTranscriptR2   = `{"role":"assistant","content":null,"audio":{"id":"audio_fixture","data":"SGVsbG8=","transcript":"Hello"}}`
	walkRefusalR2      = `{"role":"assistant","content":null,"refusal":"Cannot help"}`
	walkNoTranscriptR2 = `{"role":"assistant","content":null,"audio":{"id":"audio_fixture","data":"SGVsbG8="}}`
)

// runWalkR2 runs the output checks and the real logging plugin over an answer
// whose second choice is message, and hands assert the request and the
// record the plugin emitted. The logging plugin runs in the pipeline's
// parallel observer phase; its emitter is buffered without workers so the
// exact record can be read without a server or network I/O.
func runWalkR2(t *testing.T, origin, action, override, message string, check guardrails.Guardrail, assert func(*models.RequestContext, TraceRecord)) {
	t.Helper()
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Model, rc.EndpointType = "text", "chat"
	rc.Request = &models.ChatCompletionRequest{Model: "text"}
	rc.Privacy.ContentMode = models.ContentModeFull
	rc.Metadata["org_id"] = "org"
	if override != "" {
		rc.Metadata["key_allow_policy_override"] = "true"
		rc.Metadata["x-guardrail-policy"] = override
	}
	registry := map[string]guardrails.Guardrail{"text-check": check}
	cfg := config.GuardrailsConfig{DefaultTimeout: time.Second}
	tenants := tenant.NewStore()
	if origin == "rules" {
		cfg.Rules = []config.GuardrailRuleConfig{{Name: "text-check", Stage: "post", Mode: "sync", Action: action}}
	} else {
		tenants.Set("org", &tenant.OrgConfig{Guardrails: &tenant.GuardrailConfig{Checks: map[string]*tenant.GuardrailCheck{
			"text-check": {Enabled: true, Action: action},
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
	assert(rc, rec)
}

// A model's refusal or an audio answer's transcript that the output checks
// pass is served and logged as served, in every action including block: the
// checks read it as the answer's text.
func TestWalkSupplementalOutputLoggedAfterRefusalR2(t *testing.T) {
	for _, origin := range []string{"rules", "org"} {
		for _, tc := range []struct{ name, action, override string }{
			{"block", "block", ""},
			{"warn", "warn", ""},
			{"log", "log", ""},
			{"log-only override", "block", "log-only"},
			{"disabled override", "block", "disabled"},
		} {
			// Per-request rule overrides do not override the org's dynamic checks.
			if origin == "org" && tc.override != "" {
				continue
			}
			for _, message := range []string{walkTranscriptR2, walkRefusalR2} {
				t.Run(origin+"/"+tc.name+"/"+message, func(t *testing.T) {
					runWalkR2(t, origin, tc.action, tc.override, message, walkTextCheckR2{}, func(rc *models.RequestContext, rec TraceRecord) {
						if rc.Response == nil || rec.StatusCode != http.StatusOK || rec.ErrorCode != "" || rec.ResponseBody == nil {
							t.Fatalf("a passed answer was not served as is: %+v", rec)
						}
						w := httptest.NewRecorder()
						if err := json.NewEncoder(w).Encode(rc.Response); err != nil {
							t.Fatal(err)
						}
						if !strings.Contains(w.Body.String(), `"audio"`) && !strings.Contains(w.Body.String(), `"refusal"`) {
							t.Fatal("served supplemental output was lost")
						}
					})
				})
			}
		}
	}
}

// A block-mode output check that matches a model's refusal or an audio
// answer's transcript refuses the answer as it refuses matched content: the
// client gets 403 content_blocked, and the request log records the refusal
// and the block with no response body. Audio with no transcript cannot be read
// by the checks, so a block-mode check refuses it with 403 answer_unavailable.
func TestWalkMatchedOrUnreadableSupplementalOutputIsWithheldR2(t *testing.T) {
	for _, origin := range []string{"rules", "org"} {
		for _, tc := range []struct {
			name, message, code string
			check               guardrails.Guardrail
			matched             bool
		}{
			{"matched transcript", walkTranscriptR2, "content_blocked", walkMatchCheckR2{match: []string{"Hello"}}, true},
			{"matched refusal", walkRefusalR2, "content_blocked", walkMatchCheckR2{match: []string{"Cannot help"}}, true},
			{"audio without transcript", walkNoTranscriptR2, "answer_unavailable", walkTextCheckR2{}, false},
		} {
			t.Run(origin+"/"+tc.name, func(t *testing.T) {
				runWalkR2(t, origin, "block", "", tc.message, tc.check, func(rc *models.RequestContext, rec TraceRecord) {
					err := models.WithheldAnswerError(rc)
					if err == nil {
						t.Fatalf("the answer was not withheld; logged status=%d", rec.StatusCode)
					}
					w := httptest.NewRecorder()
					models.WriteError(w, err)
					if w.Code != http.StatusForbidden || !strings.Contains(w.Body.String(), tc.code) {
						t.Errorf("final response is not the refusal: %d %s", w.Code, w.Body.String())
					}
					if rec.StatusCode != http.StatusForbidden || rec.ErrorCode != tc.code || rec.ErrorFromUpstream {
						t.Errorf("log does not describe the gateway's refusal: %+v", rec)
					}
					if rec.ResponseBody != nil || rc.Response != nil {
						t.Errorf("the refused answer reached the log or the response: %+v", rec)
					}
					if tc.matched && (!rec.GuardrailTriggered || len(rec.GuardrailResults) != 1 || rec.GuardrailResults[0].Action != "block") {
						t.Errorf("the block is missing from the log: %+v", rec)
					}
				})
			})
		}
	}
}
