package guardrails

import (
	"errors"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/tenant"
)

// Realtime uses the direct resolver, not HTTP resolveKeyPolicies. Validate the
// actual outer threshold at fresh, cached and static-fallback seams alike.
func TestIntegrationR4DirectResolverReadiness(t *testing.T) {
	for _, factory := range []bool{false, true} {
		g := &config1602Check{runtimeAdmissionCheck: runtimeAdmissionCheck{name: "content-moderation", stage: StagePre}, thresholdErr: errors.New("unreachable")}
		p := keyTestPlugin(nil, map[string]Guardrail{"content-moderation": g})
		if factory {
			p.dynamicFactory = func(name string, cfg map[string]interface{}) Guardrail { return g }
		}
		check := &tenant.GuardrailCheck{Enabled: true, Action: "block", ConfidenceThreshold: .8}
		for i := 0; i < 2; i++ {
			got := p.getKeyPolicyGuardrail("org", "content-moderation", "readiness", check)
			if _, ok := got.(unavailableKeyPolicyCheck); !ok {
				t.Fatalf("factory=%v attempt=%d returned unavailable configuration as %T", factory, i, got)
			}
		}
		if g.thresholdCalls != 2 || g.threshold != .8 {
			t.Fatalf("factory=%v threshold calls=%d value=%g", factory, g.thresholdCalls, g.threshold)
		}
	}
}
