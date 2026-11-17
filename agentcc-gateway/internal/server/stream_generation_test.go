package server

import (
	"encoding/json"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestRouterMessagesTerminalEvidenceHasFrozenOwner(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "messages"
	rc.Model = "wire-alias"
	first := rc.AddAttempt(models.Attempt{Model: "openrouter/auto"})
	owner := latestStreamAttempt(rc)
	rc.AddAttempt(models.Attempt{Model: "another-attempt"})
	var usage models.Usage
	if err := json.Unmarshal([]byte(`{"cost":0.00000949999999999999999}`), &usage); err != nil {
		t.Fatal(err)
	}
	var output streamedOutput
	output.observe(models.StreamChunk{ID: "gen-messages", Model: "test/served", UpstreamProvider: "Synthetic"})
	ch := make(chan streamUsage, 1)
	ch <- streamUsage{usage: &usage, model: "test/served", streamed: output}
	close(ch)
	applyCanonicalStreamUsageForAttempt(rc, ch, owner)
	attempts := rc.AttemptsSnapshot()
	if owner != first.AttemptID || attempts[0].Generation.ID != "gen-messages" || attempts[0].Generation.ServedModel != "test/served" || attempts[1].Generation.ID != "" || attempts[0].Usage.CostDecimal != usage.CostDecimal || rc.Response.ID != "gen-messages" {
		t.Fatalf("terminal evidence not bound to its admitted owner: %+v", attempts)
	}
	*usage.Cost = 1
	if *rc.AttemptsSnapshot()[0].Usage.Cost == 1 {
		t.Fatal("stored provider cost was mutable")
	}
}

func TestRouterStreamErrorEvidenceWinsChannelRace(t *testing.T) {
	rc := models.AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "error"
	a := rc.AddAttempt(models.Attempt{Model: "openrouter/auto"})
	var usage models.Usage
	if err := json.Unmarshal([]byte(`{"cost":0.0000095}`), &usage); err != nil {
		t.Fatal(err)
	}
	applyStreamFailureEvidence(rc, a.AttemptID, &models.ProviderBillingEvidence{
		Generation: models.GenerationEvidence{ID: "gen-error", ServedModel: "test/served"}, Usage: &usage,
	})
	got := rc.AttemptsSnapshot()[0]
	if got.Generation.ID != "gen-error" || got.Usage.CostDecimal != "0.0000095" || rc.Response.Usage.CostDecimal != "0.0000095" {
		t.Fatalf("error evidence lost before the tee reported usage: %+v", got)
	}
}
