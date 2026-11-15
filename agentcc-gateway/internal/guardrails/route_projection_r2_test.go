package guardrails

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// The leaf validator lives in blocklist (which imports this package). This
// contract test proves the selected-policy hook without that import cycle.
type validatedRouteR2Check struct {
	name    string
	invalid bool
	calls   int
}

func (g *validatedRouteR2Check) Name() string { return g.name }
func (*validatedRouteR2Check) Stage() Stage   { return StagePre }
func (g *validatedRouteR2Check) ValidateConfig() error {
	if g.invalid {
		return errors.New("private-configuration-must-not-leak")
	}
	return nil
}
func (g *validatedRouteR2Check) Check(context.Context, *CheckInput) *CheckResult {
	g.calls++
	return &CheckResult{Pass: true}
}

func TestR2SelectedBuiltinValidationHook(t *testing.T) {
	for _, source := range []string{"dynamic", "registry"} {
		for _, mode := range []string{"enforce", "monitor"} {
			for _, failOpen := range []bool{false, true} {
				for _, invalid := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s/open=%v/invalid=%v", source, mode, failOpen, invalid), func(t *testing.T) {
						policy := keyTestPolicy("one", mode, "block", false)
						policy.Checks["keyword-blocklist"] = policy.Checks["secret"]
						delete(policy.Checks, "secret")
						g := &validatedRouteR2Check{name: "keyword-blocklist", invalid: invalid}
						p := keyTestPlugin(&tenant.GuardrailConfig{FailOpen: failOpen, KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}, map[string]Guardrail{"keyword-blocklist": g})
						created := 0
						if source == "dynamic" {
							p.dynamicFactory = func(string, map[string]interface{}) Guardrail { created++; return g }
						}
						for i := 0; i < 2; i++ { // second invocation exercises the dynamic cache
							rc := keyTestContext("key-a")
							res := p.ProcessRequest(context.Background(), rc)
							blocked := invalid && mode == "enforce" && !failOpen
							if blocked {
								if res.Error == nil || res.Error.Status != 503 || res.Error.Code != "guardrail_unavailable" {
									t.Fatalf("result=%+v", res)
								}
							} else if res.Action != pipeline.Continue {
								t.Fatalf("nonblocking result=%+v", res)
							}
							if invalid {
								if len(rc.GuardrailResults) != 1 || rc.GuardrailResults[0].PolicyID != "one" {
									t.Fatalf("missing attribution: %+v", rc.GuardrailResults)
								}
								if strings.Contains(fmt.Sprint(rc.GuardrailResults), "private-configuration") {
									t.Fatal("private config in attribution")
								}
							} else if len(rc.GuardrailResults) != 0 {
								t.Fatalf("valid config triggered: %+v", rc.GuardrailResults)
							}
							rc.Release()
						}
						wantCalls := 2
						if invalid {
							wantCalls = 0
						}
						if g.calls != wantCalls {
							t.Fatalf("check calls=%d want=%d", g.calls, wantCalls)
						}
						if source == "dynamic" && created != 1 {
							t.Fatalf("factory calls=%d want=1", created)
						}
					})
				}
			}
		}
	}
}

func TestR2ValidationHookLeavesCustomNamesAndOrgChecksAlone(t *testing.T) {
	for _, scope := range []string{"custom-key", "organization", "unselected", "disabled"} {
		t.Run(scope, func(t *testing.T) {
			name := "keyword-blocklist"
			if scope == "custom-key" {
				name = "custom-check"
			}
			policy := keyTestPolicy("one", "enforce", "block", false)
			policy.Checks[name] = policy.Checks["secret"]
			delete(policy.Checks, "secret")
			g := &validatedRouteR2Check{name: name, invalid: true}
			cfg := &tenant.GuardrailConfig{KeyPolicies: []*tenant.KeyGuardrailPolicy{policy}}
			if scope == "organization" {
				cfg.Checks = policy.Checks
				cfg.KeyPolicies = nil
			}
			if scope == "disabled" {
				policy.Checks[name].Enabled = false
			}
			p := keyTestPlugin(cfg, map[string]Guardrail{name: g})
			rc := keyTestContext("key-a")
			defer rc.Release()
			if scope == "unselected" {
				rc.Metadata["auth_key_id"] = "key-b"
				completeKeyTestContext(rc)
			}
			if got := p.ProcessRequest(context.Background(), rc); got.Action != pipeline.Continue {
				t.Fatalf("legacy control changed: %+v", got)
			}
			if len(rc.GuardrailResults) != 0 {
				t.Fatalf("legacy attribution changed: %+v", rc.GuardrailResults)
			}
			want := 0
			if scope == "custom-key" || scope == "organization" {
				want = 1
			}
			if g.calls != want {
				t.Fatalf("calls=%d want=%d", g.calls, want)
			}
		})
	}
}
