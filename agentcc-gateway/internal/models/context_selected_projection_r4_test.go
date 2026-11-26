package models

import "testing"

func TestSelectedProjectionR4PoolReset(t *testing.T) {
	rc := AcquireRequestContext()
	rc.SelectedGuardrailRequest = &ChatCompletionRequest{Model: "synthetic-selected"}
	rc.Release()

	next := AcquireRequestContext()
	defer next.Release()
	if next.SelectedGuardrailRequest != nil {
		t.Fatal("pooled request retained a previous caller's selected projection")
	}
}
