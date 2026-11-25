package guardrails

import (
	"context"
	"errors"
	"math"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Threshold readiness belongs to the selected caller, not the shared leaf:
// legacy org/static checks retain their existing threshold semantics. Preserve
// a resolved stage so an invalid output policy cannot disappear on stored reads.
func selectedThresholdReady(g Guardrail, check *tenant.GuardrailCheck) Guardrail {
	if g == nil || check == nil {
		return g
	}
	// Keep an existing unavailable envelope's stage/identity intact.
	if _, unavailable := g.(unavailableKeyPolicyCheck); unavailable {
		return g
	}
	threshold := check.ConfidenceThreshold
	if math.IsNaN(threshold) || math.IsInf(threshold, 0) || threshold < 0 || threshold > 1 {
		return invalidSelectedThreshold{Guardrail: g}
	}
	return g
}

type invalidSelectedThreshold struct{ Guardrail }

func (invalidSelectedThreshold) Check(context.Context, *CheckInput) *CheckResult {
	return &CheckResult{Err: errors.New("configured guardrail threshold is unavailable")}
}
