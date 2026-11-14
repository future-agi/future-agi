package guardrails_test

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
	g "github.com/futureagi/agentcc-gateway/internal/guardrails"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/external"
	fi "github.com/futureagi/agentcc-gateway/internal/guardrails/futureagi"
	"github.com/futureagi/agentcc-gateway/internal/guardrails/webhook"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type executionStage struct {
	g.Guardrail
	stage g.Stage
}

func (s executionStage) Stage() g.Stage { return s.stage }

func TestExecutionEnvelopeHTTPContract(t *testing.T) {
	cases := []struct {
		name, adapter, body, decision string
		status                        int
		ids                           []string
	}{
		{"webhook-http", "webhook", "", "unavailable", 503, nil},
		{"webhook-json", "webhook", `{`, "unavailable", 200, nil},
		{"webhook-empty", "webhook", `{}`, "unavailable", 200, nil},
		{"webhook-null", "webhook", `null`, "unavailable", 200, nil},
		{"webhook-wrong-pass", "webhook", `{"pass":"true"}`, "unavailable", 200, nil},
		{"webhook-null-pass", "webhook", `{"pass":null}`, "unavailable", 200, nil},
		{"webhook-null-score", "webhook", `{"pass":true,"score":null}`, "unavailable", 200, nil},
		{"webhook-wrong-score", "webhook", `{"pass":true,"score":"0"}`, "unavailable", 200, nil},
		{"webhook-negative-score", "webhook", `{"pass":true,"score":-1}`, "unavailable", 200, nil},
		{"webhook-high-score", "webhook", `{"pass":true,"score":1.1}`, "unavailable", 200, nil},
		{"webhook-oversized", "webhook", `{"pass":true}` + strings.Repeat(" ", 1<<20), "unavailable", 200, nil},
		{"webhook-legacy-safe", "webhook", `{"pass":true}`, "safe", 200, nil},
		{"webhook-legacy-deny", "webhook", `{"pass":false}`, "deny", 200, nil},
		{"webhook-scored-deny", "webhook", `{"pass":false,"score":1}`, "scored", 200, nil},
		{"external-http", "lakera", "", "unavailable", 503, nil},
		{"external-malformed", "lakera", `{`, "unavailable", 200, nil},
		{"external-missing-verdict", "lakera", `{}`, "unavailable", 200, nil},
		{"external-safe", "lakera", `{"flagged":false}`, "safe", 200, nil},
		{"external-deny", "lakera", `{"flagged":true,"categories":{"toxicity":true},"category_scores":{"toxicity":1}}`, "scored", 200, nil},
		{"evaluation-http", "futureagi", "", "unavailable", 503, []string{"a"}},
		{"evaluation-empty", "futureagi", `{"result":[]}`, "unavailable", 200, []string{"a"}},
		{"evaluation-missing-output", "futureagi", `{"result":[{"evaluations":[{"evalId":"a"}]}]}`, "unavailable", 200, []string{"a"}},
		{"evaluation-legacy-safe", "futureagi", `{"result":[{"evaluations":[{"output":"Passed"}]}]}`, "safe", 200, []string{"a"}},
		{"evaluation-legacy-deny", "futureagi", `{"result":[{"evaluations":[{"output":"Failed"}]}]}`, "scored", 200, []string{"a"}},
		{"evaluation-multi-missing-id", "futureagi", `{"result":[{"evaluations":[{"output":"Passed"},{"evalId":"b","output":"Passed"}]}]}`, "unavailable", 200, []string{"a", "b"}},
		{"evaluation-duplicate-id", "futureagi", `{"result":[{"evaluations":[{"evalId":"a","output":"Passed"},{"evalId":"a","output":"Passed"}]}]}`, "unavailable", 200, []string{"a", "b"}},
		{"evaluation-unexpected-id", "futureagi", `{"result":[{"evaluations":[{"evalId":"x","output":"Passed"}]}]}`, "unavailable", 200, []string{"a"}},
		{"evaluation-multi-safe", "futureagi", `{"result":[{"evaluations":[{"output":"Passed"}]}]}`, "safe", 200, []string{"a", "b"}},
		{"evaluation-multi-deny", "futureagi", `{"result":[{"evaluations":[{"output":"Failed"}]}]}`, "scored", 200, []string{"a", "b"}},
	}
	for _, tc := range cases {
		for _, mode := range []string{"dynamic", "static"} {
			for _, stage := range []string{"pre", "post"} {
				for _, policy := range []string{"omitted", "false", "true"} {
					for _, threshold := range []float64{0.5, 1} {
						t.Run(fmt.Sprintf("%s/%s/%s/open=%s/threshold=%g", tc.name, mode, stage, policy, threshold), func(t *testing.T) {
							checker := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(tc.status); fmt.Fprint(w, tc.body) }))
							defer checker.Close()
							var check g.Guardrail
							switch tc.adapter {
							case "webhook":
								check = webhook.New("check", map[string]interface{}{"url": checker.URL})
							case "futureagi":
								check = fi.New("check", map[string]interface{}{"base_url": checker.URL, "api_key": "synthetic", "secret_key": "synthetic", "eval_ids": tc.ids})
							default:
								check = external.New("check", map[string]interface{}{"provider": tc.adapter, "api_key": "synthetic", "endpoint": checker.URL})
							}
							if stage == "post" {
								check = executionStage{Guardrail: check, stage: g.StagePost}
							}
							registry := map[string]g.Guardrail{"check": check}
							store := tenant.NewStore()
							cfg := config.GuardrailsConfig{DefaultTimeout: 2 * time.Second}
							if mode == "dynamic" {
								raw := fmt.Sprintf(`{"checks":{"check":{"enabled":true,"stage":%q,"action":"block","confidence_threshold":%g}}}`, stage, threshold)
								var gc tenant.GuardrailConfig
								if err := json.Unmarshal([]byte(raw), &gc); err != nil {
									t.Fatal(err)
								}
								if policy != "omitted" {
									gc.FailOpen = policy == "true"
								}
								store.Set("synthetic-org", &tenant.OrgConfig{Guardrails: &gc})
								cfg.FailOpen = policy != "true" // Deliberately opposite: global cannot enable tenant fail-open.
							} else {
								cfg.FailOpen = policy == "true"
								cfg.Rules = []config.GuardrailRuleConfig{{Name: "check", Stage: stage, Mode: "sync", Action: "block", Threshold: threshold}}
							}
							p := g.NewPlugin(g.NewEngine(cfg, registry), registry, nil, nil, store)
							var dispatches atomic.Int32
							var recorded atomic.Bool
							gateway := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
								rc := models.AcquireRequestContext()
								defer rc.Release()
								rc.Metadata["org_id"] = "synthetic-org"
								rc.Request = &models.ChatCompletionRequest{Model: "synthetic", Messages: []models.Message{{Role: "user", Content: json.RawMessage(`"synthetic"`)}}}
								outcome := p.ProcessRequest(r.Context(), rc)
								if outcome.Action == pipeline.Continue {
									dispatches.Add(1)
									rc.Response = &models.ChatCompletionResponse{Choices: []models.Choice{{Message: models.Message{Role: "assistant", Content: json.RawMessage(`"synthetic-output"`)}}}}
									outcome = p.ProcessResponse(r.Context(), rc)
								}
								for _, result := range rc.GuardrailResults {
									if strings.Contains(result.Message, "guardrail_unavailable") {
										recorded.Store(true)
									}
								}
								if outcome.Error != nil {
									models.WriteError(w, outcome.Error)
									return
								}
								w.Header().Set("Content-Type", "application/json")
								json.NewEncoder(w).Encode(rc.Response)
							}))
							defer gateway.Close()
							req, _ := http.NewRequestWithContext(context.Background(), http.MethodPost, gateway.URL, strings.NewReader(`{}`))
							response, err := http.DefaultClient.Do(req)
							if err != nil {
								t.Fatal(err)
							}
							defer response.Body.Close()
							want := 200
							if tc.decision == "unavailable" && policy != "true" {
								want = 503
							}
							if tc.decision == "deny" || tc.decision == "scored" && threshold < 1 {
								want = 403
							}
							if response.StatusCode != want {
								t.Errorf("status=%d want=%d", response.StatusCode, want)
							}
							var body map[string]interface{}
							if err := json.NewDecoder(response.Body).Decode(&body); err != nil {
								t.Fatal(err)
							}
							if want != 200 {
								if _, exists := body["choices"]; exists {
									t.Error("refusal response included provider choices")
								}
								errorBody, _ := body["error"].(map[string]interface{})
								code := "content_blocked"
								typ := "guardrail_error"
								if want == 503 {
									code = "guardrail_unavailable"
									typ = "guardrail"
									if response.Header.Get("x-should-retry") != "false" {
										t.Error("missing SDK no-retry instruction")
									}
								}
								if errorBody["code"] != code || errorBody["type"] != typ {
									t.Errorf("wrong error envelope: %+v", body)
								}
							}
							if tc.decision == "unavailable" && !recorded.Load() {
								t.Error("unavailable classification not recorded")
							}
							wantDispatch := 1
							if stage == "pre" && want != 200 {
								wantDispatch = 0
							}
							if dispatches.Load() != int32(wantDispatch) {
								t.Errorf("provider dispatches=%d want=%d", dispatches.Load(), wantDispatch)
							}
						})
					}
				}
			}
		}
	}
}
