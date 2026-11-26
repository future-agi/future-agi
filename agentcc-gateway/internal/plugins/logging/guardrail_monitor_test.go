package logging

import (
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestMonitorOnlyGuardrailAppearsInRequestLog(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.Flags.GuardrailMonitored = true
	rc.GuardrailResults = append(rc.GuardrailResults, models.GuardrailResult{Name: "secret-detection", Action: "log", PolicyID: "policy-1", Mode: "monitor", Outcome: "would_block", WouldAction: "block"})
	rec := buildRecord(rc, config.RequestLoggingConfig{})
	if !rec.GuardrailTriggered || len(rec.GuardrailResults) != 1 || rec.GuardrailResults[0].Outcome != "would_block" {
		t.Fatalf("monitor log: %+v", rec)
	}
}
