package server

// The guardrail outcome headers in isolation.

import (
	"net/http"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// The outcome headers cost nothing on a request no check triggered on.
func TestGuardrailActionHeadersAllocateNothingWithoutATrigger(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	h := http.Header{}
	if n := testing.AllocsPerRun(100, func() { setGuardrailOutcomeHeaders(h, rc) }); n != 0 {
		t.Errorf("setGuardrailOutcomeHeaders allocates %.0f times with no guardrail result", n)
	}
	rc.GuardrailResults = append(rc.GuardrailResults,
		models.GuardrailResult{Name: "b", Action: "log"}, models.GuardrailResult{Name: "a", Action: "warn"},
		models.GuardrailResult{Name: "b", Action: "log"})
	setGuardrailOutcomeHeaders(h, rc)
	if h.Get("x-agentcc-guardrail-action") != "warn" || h.Get("x-agentcc-guardrails-triggered") != "a,b" {
		t.Errorf("headers %v, want action warn and the names a,b once each", h)
	}
}
