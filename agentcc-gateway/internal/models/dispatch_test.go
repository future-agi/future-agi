package models

import (
	"context"
	"testing"
)

type otherKey struct{}

func TestRequestScopeCarriesTheIDAndTheRecord(t *testing.T) {
	parent, cancel := context.WithCancel(context.WithValue(context.Background(), otherKey{}, "kept"))
	ctx, record := WithRequestScope(parent, "req-1")

	if got := GetRequestID(ctx); got != "req-1" {
		t.Fatalf("GetRequestID = %q, want req-1", got)
	}
	if got := ctx.Value(otherKey{}); got != "kept" {
		t.Fatalf("a parent value was lost: %v", got)
	}
	if record.Dispatched() {
		t.Fatal("a new record says dispatched")
	}

	// A context derived from the scope, with its own request id, still
	// marks the request's record.
	derived, stop := context.WithCancel(WithRequestID(ctx, "attempt-1"))
	defer stop()
	if got := GetRequestID(derived); got != "attempt-1" {
		t.Fatalf("GetRequestID on a derived context = %q", got)
	}
	MarkDispatched(derived)
	if !record.Dispatched() {
		t.Fatal("MarkDispatched on a derived context did not mark the record")
	}

	cancel()
	<-derived.Done()
	if ctx.Err() == nil {
		t.Fatal("the scope does not carry its parent's cancellation")
	}
}

func TestMarkDispatchedWithoutARecordDoesNothing(t *testing.T) {
	MarkDispatched(context.Background())
	var none *DispatchRecord
	if none.Dispatched() {
		t.Fatal("a missing record says dispatched")
	}
}
