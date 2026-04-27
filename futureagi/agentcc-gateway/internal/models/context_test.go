package models

import (
	"context"
	"testing"
	"time"
)

func TestRequestContextPool(t *testing.T) {
	// Acquire and release multiple times to test pool reuse.
	for i := 0; i < 100; i++ {
		rc := AcquireRequestContext()
		if rc == nil {
			t.Fatal("AcquireRequestContext returned nil")
		}
		if rc.StartTime.IsZero() {
			t.Error("StartTime should be set on acquire")
		}

		rc.RequestID = "test-id"
		rc.Model = "gpt-4o"
		rc.Metadata["key"] = "value"
		rc.Timings["test"] = time.Millisecond
		rc.AddError(ErrInternal("test"))

		rc.Release()
	}
}

func TestRequestContextReleaseClearsFields(t *testing.T) {
	rc := AcquireRequestContext()
	rc.RequestID = "test"
	rc.Model = "gpt-4o"
	rc.Metadata["key"] = "value"
	rc.Timings["test"] = time.Millisecond
	rc.AddError(ErrInternal("test"))
	rc.Flags.CacheHit = true

	rc.Release()

	// Get it back from the pool.
	rc2 := AcquireRequestContext()
	defer rc2.Release()

	if rc2.RequestID != "" {
		t.Errorf("RequestID should be empty, got %q", rc2.RequestID)
	}
	if rc2.Model != "" {
		t.Errorf("Model should be empty, got %q", rc2.Model)
	}
	if len(rc2.Metadata) != 0 {
		t.Errorf("Metadata should be empty, got %v", rc2.Metadata)
	}
	if len(rc2.Timings) != 0 {
		t.Errorf("Timings should be empty, got %v", rc2.Timings)
	}
	if len(rc2.Errors) != 0 {
		t.Errorf("Errors should be empty, got %v", rc2.Errors)
	}
	if rc2.Flags.CacheHit {
		t.Error("Flags should be zeroed")
	}
}

func TestContextHelpers(t *testing.T) {
	ctx := context.Background()

	// RequestID
	ctx = WithRequestID(ctx, "req-123")
	if id := GetRequestID(ctx); id != "req-123" {
		t.Errorf("GetRequestID = %q, want %q", id, "req-123")
	}

	// RequestContext
	rc := AcquireRequestContext()
	defer rc.Release()
	rc.RequestID = "test"

	ctx = WithRequestContext(ctx, rc)
	got := GetRequestContext(ctx)
	if got == nil || got.RequestID != "test" {
		t.Error("GetRequestContext should return the stored context")
	}

	// Missing values return defaults.
	if id := GetRequestID(context.Background()); id != "" {
		t.Errorf("GetRequestID on empty ctx = %q, want empty", id)
	}
	if rc := GetRequestContext(context.Background()); rc != nil {
		t.Error("GetRequestContext on empty ctx should return nil")
	}
}

func TestRequestContextElapsed(t *testing.T) {
	rc := AcquireRequestContext()
	defer rc.Release()

	time.Sleep(5 * time.Millisecond)
	elapsed := rc.Elapsed()

	if elapsed < 5*time.Millisecond {
		t.Errorf("Elapsed = %v, want >= 5ms", elapsed)
	}
}
