package guardrails

import (
	"context"
	"fmt"
	"math"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

func TestSelectedThresholdReadiness(t *testing.T) {
	for _, threshold := range []float64{-0.1, 1.1, math.NaN(), math.Inf(1), math.Inf(-1), 0, 0.5, 1} {
		for _, stage := range []Stage{StagePre, StagePost} {
			t.Run(fmt.Sprintf("%v/%v", threshold, stage), func(t *testing.T) {
				g := &runtimeAdmissionCheck{name: "custom", stage: stage}
				p := keyTestPlugin(nil, map[string]Guardrail{"custom": g})
				check := &tenant.GuardrailCheck{Enabled: true, ConfidenceThreshold: threshold}
				got := p.getKeyPolicyGuardrail("org-a", "custom", "threshold", check)
				invalid := math.IsNaN(threshold) || math.IsInf(threshold, 0) || threshold < 0 || threshold > 1
				if got.Stage() != stage {
					t.Fatalf("stage lost: %v", got.Stage())
				}
				result := got.Check(context.Background(), &CheckInput{})
				if (result.Err != nil) != invalid {
					t.Fatalf("threshold=%v error=%v", threshold, result.Err)
				}
				if invalid && g.calls != 0 {
					t.Fatal("invalid threshold executed implementation")
				}
			})
		}
	}
}

func TestSelectedThresholdUnavailableEnvelope(t *testing.T) {
	for _, mode := range []string{"enforce", "monitor"} {
		for _, open := range []bool{false, true} {
			for _, action := range []string{"block", "warn", "log"} {
				t.Run(fmt.Sprintf("%s/%v/%s", mode, open, action), func(t *testing.T) {
					g := &runtimeAdmissionCheck{name: "custom", stage: StagePre}
					pol := &tenant.KeyGuardrailPolicy{ID: "threshold", Mode: mode, KeyIDs: []string{"key-a"}, Checks: map[string]*tenant.GuardrailCheck{"custom": {Enabled: true, Action: action, ConfidenceThreshold: 1.1}}}
					p := keyTestPlugin(&tenant.GuardrailConfig{FailOpen: open, KeyPolicies: []*tenant.KeyGuardrailPolicy{pol}}, map[string]Guardrail{"custom": g})
					rc := keyTestContext("key-a")
					defer rc.Release()
					res := p.ProcessRequest(context.Background(), rc)
					if mode == "enforce" && action == "block" && !open {
						if res.Error == nil || res.Error.Code != "guardrail_unavailable" || res.Error.Status != 503 {
							t.Fatalf("expected unavailable: %+v", res)
						}
					} else if res.Action != pipeline.Continue {
						t.Fatalf("nonblocking control refused: %+v", res)
					}
					if g.calls != 0 || len(rc.GuardrailResults) != 1 {
						t.Fatalf("calls=%d results=%+v", g.calls, rc.GuardrailResults)
					}
				})
			}
		}
	}
}
