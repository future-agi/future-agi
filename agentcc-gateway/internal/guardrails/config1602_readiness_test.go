package guardrails

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

type config1602Check struct {
	runtimeAdmissionCheck
	configErr, thresholdErr     error
	configCalls, thresholdCalls int
	threshold                   float64
}

func (g *config1602Check) ValidateConfig() error {
	g.configCalls++
	return g.configErr
}

func (g *config1602Check) ValidateSelectedThreshold(threshold float64) error {
	g.thresholdCalls++
	g.threshold = threshold
	return g.thresholdErr
}

func TestConfig1602SelectedValidationDispatch(t *testing.T) {
	for _, name := range []string{"prompt-injection", "content-moderation", "keyword-blocklist", "pii-detection", "secret-detection", "language-detection", "topic-restriction", "input-validation", "deployed-custom"} {
		for _, stage := range []Stage{StagePre, StagePost} {
			t.Run(fmt.Sprintf("%s/%v", name, stage), func(t *testing.T) {
				g := &config1602Check{runtimeAdmissionCheck: runtimeAdmissionCheck{name: name, stage: stage}, configErr: errors.New("private-config-sentinel")}
				got := validateSelectedKeyPolicyCheck(name, g, &tenant.GuardrailCheck{ConfidenceThreshold: .8})
				if name == "deployed-custom" {
					if got != g || g.configCalls != 0 || g.thresholdCalls != 0 {
						t.Fatal("arbitrary custom validation broadened")
					}
					return
				}
				unavailable, ok := got.(unavailableKeyPolicyCheck)
				if !ok || unavailable.name != name || unavailable.stage != stage || !unavailable.stageKnown {
					t.Fatalf("unavailable identity or stage lost: %#v", got)
				}
				result := got.Check(context.Background(), &CheckInput{})
				if result.Err == nil || strings.Contains(result.Err.Error(), "private-config-sentinel") || g.calls != 0 {
					t.Fatal("invalid original config executed or leaked")
				}
				if g.configCalls != 1 || g.thresholdCalls != 0 {
					t.Fatal("threshold validation ran before original config rejection")
				}
			})
		}
	}
}

func TestConfig1602SelectedThresholdDispatch(t *testing.T) {
	for _, threshold := range []float64{0, .3, .8, 1} {
		for _, invalid := range []bool{false, true} {
			t.Run(fmt.Sprintf("threshold=%g/invalid=%v", threshold, invalid), func(t *testing.T) {
				g := &config1602Check{runtimeAdmissionCheck: runtimeAdmissionCheck{name: "content-moderation", stage: StagePre}}
				if invalid {
					g.thresholdErr = errors.New("private-threshold-sentinel")
				}
				got := validateSelectedKeyPolicyCheck(g.name, g, &tenant.GuardrailCheck{ConfidenceThreshold: threshold})
				if g.configCalls != 1 || g.thresholdCalls != 1 || g.threshold != threshold {
					t.Fatal("actual outer threshold was not forwarded as-is")
				}
				if !invalid {
					if got != g {
						t.Fatal("reachable check replaced")
					}
					return
				}
				u, ok := got.(unavailableKeyPolicyCheck)
				if !ok || u.name != g.name || u.stage != g.stage || !u.stageKnown {
					t.Fatalf("threshold rejection lost availability identity: %#v", got)
				}
				if result := got.Check(context.Background(), nil); result.Err == nil || strings.Contains(result.Err.Error(), "private-threshold-sentinel") {
					t.Fatal("threshold rejection did not use sanitized unavailable envelope")
				}
			})
		}
	}
	for _, name := range []string{"deployed-custom", "prompt-injection"} {
		g := &config1602Check{runtimeAdmissionCheck: runtimeAdmissionCheck{name: name, stage: StagePre}, thresholdErr: errors.New("not opted in")}
		if validateSelectedKeyPolicyCheck(name, g, &tenant.GuardrailCheck{}) != g || g.thresholdCalls != 0 {
			t.Fatalf("threshold validation broadened to %s", name)
		}
	}
	if validateSelectedKeyPolicyCheck("content-moderation", nil, &tenant.GuardrailCheck{}) != nil {
		t.Fatal("nil implementation changed")
	}
	g := &config1602Check{runtimeAdmissionCheck: runtimeAdmissionCheck{name: "content-moderation", stage: StagePre}}
	if validateSelectedKeyPolicyCheck(g.name, g, nil) != g || g.thresholdCalls != 0 {
		t.Fatal("nil envelope acquired guessed threshold")
	}
	existing := unavailableKeyPolicyCheck{name: "content-moderation", stage: StagePost, stageKnown: true}
	if validateSelectedKeyPolicyCheck(existing.name, existing, &tenant.GuardrailCheck{}) != existing {
		t.Fatal("existing unavailable identity changed")
	}
	invalidOuter := selectedThresholdReady(g, &tenant.GuardrailCheck{ConfidenceThreshold: math.NaN()})
	if validateSelectedKeyPolicyCheck(g.name, invalidOuter, &tenant.GuardrailCheck{}) != invalidOuter {
		t.Fatal("existing outer-threshold rejection changed")
	}
	for _, threshold := range []float64{0, .3, 1} {
		if shouldTrigger(&CheckResult{Pass: false, Score: threshold}, threshold) && threshold > 0 {
			t.Fatal("positive score equality must not trigger")
		}
	}
	if !shouldTrigger(&CheckResult{Pass: false, Score: .01}, 0) {
		t.Fatal("zero confidence threshold was replaced by a default")
	}
}

func TestConfig1602UnavailableModes(t *testing.T) {
	for _, name := range []string{"prompt-injection", "content-moderation"} {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, failOpen := range []bool{false, true} {
				for _, action := range []string{"block", "warn", "log"} {
					for _, pipelineMode := range []string{"parallel", "sequential"} {
						t.Run(fmt.Sprintf("%s/%s/open=%v/%s/%s", name, mode, failOpen, action, pipelineMode), func(t *testing.T) {
							g := &config1602Check{runtimeAdmissionCheck: runtimeAdmissionCheck{name: name, stage: StagePre}}
							if name == "prompt-injection" {
								g.configErr = errors.New("invalid original selector")
							} else {
								g.thresholdErr = errors.New("unreachable threshold")
							}
							sibling := &runtimeAdmissionCheck{name: "sibling", stage: StagePre}
							policy := &tenant.KeyGuardrailPolicy{ID: "config1602", Mode: mode, KeyIDs: []string{"key-a"}, Checks: map[string]*tenant.GuardrailCheck{
								name:      {Enabled: true, Action: action, ConfidenceThreshold: .8},
								"sibling": {Enabled: true, Action: "warn"},
							}}
							p := keyTestPlugin(&tenant.GuardrailConfig{FailOpen: failOpen, PipelineMode: pipelineMode, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, map[string]Guardrail{name: g, "sibling": sibling})
							rc := keyTestContext("key-a")
							defer rc.Release()
							rc.Request.Messages[0].Content = json.RawMessage(`"Please describe the weather."`)
							completeKeyTestContext(rc)
							result := p.ProcessRequest(context.Background(), rc)
							blocked := mode == "enforce" && action == "block" && !failOpen
							if blocked {
								if result.Error == nil || result.Error.Code != "guardrail_unavailable" || result.Error.Status != 503 {
									t.Fatalf("missing unavailable refusal: %+v", result)
								}
							} else if result.Action != pipeline.Continue {
								t.Fatalf("nonblocking control refused: %+v", result)
							}
							if g.calls != 0 {
								t.Fatal("unready check executed")
							}
							if !blocked && sibling.calls != 1 {
								t.Fatal("valid sibling lost")
							}
							found := false
							for _, outcome := range rc.GuardrailResults {
								if outcome.Name == name {
									found = true
									if !strings.Contains(outcome.Message, "unavailable") {
										t.Fatal("unavailable check recorded as success")
									}
								}
							}
							if !found {
								t.Fatal("missing unavailable attribution")
							}
						})
					}
				}
			}
		}
	}
}

func TestConfig1602SelectedScopeAndCorrection(t *testing.T) {
	for _, control := range []string{"disabled", "unselected", "legacy-org", "custom", "corrected"} {
		t.Run(control, func(t *testing.T) {
			name := "prompt-injection"
			if control == "custom" {
				name = "custom"
			}
			g := &config1602Check{runtimeAdmissionCheck: runtimeAdmissionCheck{name: name, stage: StagePre}, configErr: errors.New("invalid selected config")}
			check := &tenant.GuardrailCheck{Enabled: true, Action: "block", ConfidenceThreshold: .8}
			policy := &tenant.KeyGuardrailPolicy{ID: "config1602", Mode: "enforce", KeyIDs: []string{"key-a"}, Checks: map[string]*tenant.GuardrailCheck{name: check}}
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}
			key, wantCalls := "key-a", 0
			switch control {
			case "disabled":
				check.Enabled = false
			case "unselected":
				key = "key-b"
			case "legacy-org":
				cfg.Checks = policy.Checks
				cfg.KeyPolicies = nil
				wantCalls = 1
			case "custom":
				wantCalls = 1
			case "corrected":
				g.configErr = nil
				wantCalls = 1
			}
			p := keyTestPlugin(cfg, map[string]Guardrail{name: g})
			rc := keyTestContext(key)
			defer rc.Release()
			rc.Request.Messages[0].Content = json.RawMessage(`"Please describe the weather."`)
			completeKeyTestContext(rc)
			if result := p.ProcessRequest(context.Background(), rc); result.Action != pipeline.Continue {
				t.Fatalf("control refused: %+v", result)
			}
			if g.calls != wantCalls {
				t.Fatalf("calls=%d want=%d", g.calls, wantCalls)
			}
			if control != "corrected" && (g.configCalls != 0 || g.thresholdCalls != 0) {
				t.Fatal("selected readiness broadened outside active selected builtin")
			}
		})
	}
}
