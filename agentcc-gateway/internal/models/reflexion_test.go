package models

import "testing"

func TestReleaseClearsReflectionState(t *testing.T) {
	rc := AcquireRequestContext()
	rc.Request = &ChatCompletionRequest{Model: "private-feedback"}
	rc.Response = &ChatCompletionResponse{ID: "rejected-candidate"}
	rc.Attempts = &AttemptLedger{Started: 2, Open: true, Attempts: []ProviderAttempt{{Model: "candidate-model"}}}
	rc.AccountedUsage = &Usage{TotalTokens: 60}
	rc.AttemptHooks = true
	rc.BufferedResponse = true
	rc.GuardrailResults = append(rc.GuardrailResults, GuardrailResult{Message: "private-check-reason"})
	rc.Metadata["reflexion_debug"] = "true"
	rc.Release()
	if rc.Request != nil || rc.Response != nil || rc.Attempts != nil || rc.AccountedUsage != nil || rc.AttemptHooks || rc.BufferedResponse {
		t.Fatal("released context retained candidate state")
	}
	for _, r := range rc.GuardrailResults[:cap(rc.GuardrailResults)] {
		if r.Message != "" {
			t.Fatal("pooled backing array retained a check reason")
		}
	}
	next := AcquireRequestContext()
	defer next.Release()
	if next.Attempts != nil || next.AccountedUsage != nil || next.BufferedResponse || next.Metadata["reflexion_debug"] != "" {
		t.Fatal("next request inherited reflection state")
	}
}
