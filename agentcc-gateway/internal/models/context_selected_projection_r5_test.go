package models

import (
	"encoding/json"
	"testing"
)

func TestRuntimeR5SelectedProjectionPoolReset(t *testing.T) {
	rc := AcquireRequestContext()
	rc.SelectedGuardrailRequest = &ChatCompletionRequest{Model: "original"}
	rc.SelectedGuardrailResponse = &ChatCompletionResponse{Choices: []Choice{{Message: Message{Role: "assistant", Content: json.RawMessage(`"original"`)}}}}
	rc.GuardrailInspectionOnly = true
	rc.Inspection = NewInspectionState(InspectionContract{Operation: "test.output", Output: InspectionSupported})
	rc.Inspection.BindPrincipal("org", "key")
	rc.Inspection.Complete(InspectionOutput, rc.SelectedGuardrailResponse)
	rc.Inspection.Require(InspectionOutput)
	rc.Inspection.Decide(InspectionOutput, rc.SelectedGuardrailResponse)
	retained, output := rc.Inspection, rc.SelectedGuardrailResponse
	rc.Release()
	// No other goroutine is using this context; assert its reset directly so
	// this test does not depend on sync.Pool choosing the same object next.
	if rc.SelectedGuardrailRequest != nil || rc.SelectedGuardrailResponse != nil || rc.GuardrailInspectionOnly || rc.Inspection != nil {
		t.Fatal("release retained selected authority or payload")
	}
	next := AcquireRequestContext()
	defer next.Release()
	if next.SelectedGuardrailRequest != nil || next.SelectedGuardrailResponse != nil || next.Inspection != nil {
		t.Fatal("pooled acquisition inherited selected authority")
	}
	if retained.ReleaseError() != nil || string(output.Choices[0].Message.Content) != `"original"` {
		t.Fatal("reset mutated independently owned payload or retained receipt")
	}
}
