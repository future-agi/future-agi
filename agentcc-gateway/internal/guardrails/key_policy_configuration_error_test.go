package guardrails

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func configurationErrorPolicy(t *testing.T) *tenant.KeyGuardrailPolicy {
	t.Helper()
	var policy tenant.KeyGuardrailPolicy
	// Decode the actual wire shape, including a valid sibling. A malformed
	// legacy check must not disappear or become a valid empty configuration.
	if err := json.Unmarshal([]byte(`{"id":"invalid-policy","key_ids":["key-a"],"checks":{
		"secret":{"enabled":true,"action":"block","configuration_error":"invalid_config"},
		"sibling":{"enabled":true,"action":"warn","config":{"retained":"value"}}
	}}`), &policy); err != nil {
		t.Fatal(err)
	}
	return &policy
}

func TestKeyPolicyConfigurationErrorUsesUnavailableEnvelope(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor"} {
		for _, failOpen := range []bool{false, true} {
			for _, action := range []string{"block", "warn", "log"} {
				for _, pipelineMode := range []string{"parallel", "sequential"} {
					t.Run(fmt.Sprintf("%s/open=%v/%s/%s", mode, failOpen, action, pipelineMode), func(t *testing.T) {
						policy := configurationErrorPolicy(t)
						policy.Mode = mode
						policy.Checks["secret"].Action = action
						policy.Checks["secret"].ConfidenceThreshold = 1
						invalid := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
						sibling := &runtimeAdmissionCheck{name: "sibling", stage: StagePre}
						p := keyTestPlugin(&tenant.GuardrailConfig{FailOpen: failOpen, PipelineMode: pipelineMode, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, map[string]Guardrail{"secret": invalid, "sibling": sibling})
						p.dynamicFactory = func(name string, cfg map[string]interface{}) Guardrail {
							if name == "secret" {
								t.Error("attempted to construct a check marked invalid")
							}
							return nil // exercise static fallback without discarding the marker
						}
						rc := keyTestContext("key-a")
						defer rc.Release()
						res := p.ProcessRequest(context.Background(), rc)
						blocked := mode == "enforce" && action == "block" && !failOpen
						if blocked {
							if res.Error == nil || res.Error.Code != "guardrail_unavailable" || res.Error.Status != 503 {
								t.Errorf("expected unavailable refusal, got %+v", res)
							}
						} else if res.Action != pipeline.Continue {
							t.Errorf("nonblocking control refused: %+v", res)
						}
						if invalid.calls != 0 {
							t.Error("invalid configuration executed as a passing check")
						}
						if !blocked && sibling.calls != 1 {
							t.Errorf("valid sibling lost: calls=%d", sibling.calls)
						}
						found := false
						for _, result := range rc.GuardrailResults {
							if result.Name == "secret" {
								found = true
								if !strings.Contains(result.Message, "unavailable") {
									t.Errorf("invalid check did not record execution failure: %+v", result)
								}
							}
						}
						if !found {
							t.Error("missing invalid check attribution")
						}
					})
				}
			}
		}
	}
}

func TestKeyPolicyConfigurationErrorScopeAndCorrection(t *testing.T) {
	for _, control := range []string{"disabled", "unselected", "legacy-org", "corrected"} {
		t.Run(control, func(t *testing.T) {
			policy := configurationErrorPolicy(t)
			delete(policy.Checks, "sibling")
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}
			key := "key-a"
			wantCalls := 0
			switch control {
			case "disabled":
				policy.Checks["secret"].Enabled = false
			case "unselected":
				key = "key-b"
			case "legacy-org":
				cfg.Checks = policy.Checks
				cfg.KeyPolicies = nil
				wantCalls = 1
			case "corrected":
				// A corrected push removes the marker, retaining the same check.
				policy.Checks["secret"] = &tenant.GuardrailCheck{Enabled: true, Action: "block"}
				wantCalls = 1
			}
			g := &runtimeAdmissionCheck{name: "secret", stage: StagePre}
			p := keyTestPlugin(cfg, map[string]Guardrail{"secret": g})
			rc := keyTestContext(key)
			defer rc.Release()
			if res := p.ProcessRequest(context.Background(), rc); res.Action != pipeline.Continue {
				t.Fatalf("control refused: %+v", res)
			}
			if g.calls != wantCalls {
				t.Errorf("calls=%d want=%d", g.calls, wantCalls)
			}
		})
	}
}

type configurationValidatorCheck struct {
	runtimeAdmissionCheck
	err         error
	validations int
}

func (g *configurationValidatorCheck) ValidateConfig() error {
	g.validations++
	return g.err
}

func TestKeyPolicyBuiltinValidationOptIn(t *testing.T) {
	for _, name := range []string{"keyword-blocklist", "pii-detection", "content-moderation", "secret-detection", "language-detection", "topic-restriction", "input-validation", "deployed-custom"} {
		for _, invalid := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/invalid=%v", name, invalid), func(t *testing.T) {
				g := &configurationValidatorCheck{runtimeAdmissionCheck: runtimeAdmissionCheck{name: name, stage: StagePre}}
				if invalid {
					g.err = errors.New("synthetic-private-config")
				}
				got := validateKeyPolicyCheck(name, g)
				if name == "deployed-custom" {
					if got != g || g.validations != 0 {
						t.Fatal("custom implementation behavior changed")
					}
					return
				}
				if g.validations != 1 {
					t.Fatalf("leaf readiness not consumed: validations=%d", g.validations)
				}
				if !invalid {
					if got != g {
						t.Fatal("valid builtin replaced")
					}
					return
				}
				result := got.Check(context.Background(), &CheckInput{})
				if result.Err == nil || strings.Contains(result.Err.Error(), "synthetic-private-config") {
					t.Fatalf("expected sanitized unavailable error: %+v", result)
				}
			})
		}
	}
}
