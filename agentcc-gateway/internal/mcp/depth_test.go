package mcp

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestDepthTrackerExtract(t *testing.T) {
	dt := NewDepthTracker(10)

	// No header → 0.
	r := httptest.NewRequest("POST", "/mcp", nil)
	if dt.ExtractDepth(r) != 0 {
		t.Fatal("expected 0 for missing header")
	}

	// Valid header.
	r.Header.Set(HeaderAgentDepth, "5")
	if dt.ExtractDepth(r) != 5 {
		t.Fatal("expected 5")
	}

	// Invalid header → 0.
	r.Header.Set(HeaderAgentDepth, "abc")
	if dt.ExtractDepth(r) != 0 {
		t.Fatal("expected 0 for invalid header")
	}

	// Negative → 0.
	r.Header.Set(HeaderAgentDepth, "-1")
	if dt.ExtractDepth(r) != 0 {
		t.Fatal("expected 0 for negative header")
	}
}

func TestDepthTrackerCheck(t *testing.T) {
	dt := NewDepthTracker(5)

	if err := dt.CheckDepth(0); err != nil {
		t.Fatal("depth 0 should pass")
	}
	if err := dt.CheckDepth(4); err != nil {
		t.Fatal("depth 4 should pass (max=5)")
	}
	if err := dt.CheckDepth(5); err == nil {
		t.Fatal("depth 5 should fail (max=5)")
	}
	if err := dt.CheckDepth(100); err == nil {
		t.Fatal("depth 100 should fail")
	}
}

func TestDepthTrackerIncrementHeader(t *testing.T) {
	dt := NewDepthTracker(10)
	if dt.IncrementHeader(3) != "4" {
		t.Fatal("expected 4")
	}
}

func TestDepthTrackerDefault(t *testing.T) {
	dt := NewDepthTracker(0) // should default to 10
	if dt.MaxDepth() != DefaultMaxAgentDepth {
		t.Fatalf("expected default %d, got %d", DefaultMaxAgentDepth, dt.MaxDepth())
	}
}

func TestDepthTrackerEndToEnd(t *testing.T) {
	dt := NewDepthTracker(3)

	// Simulate a chain of requests.
	r := httptest.NewRequest("POST", "/mcp", nil)

	for i := 0; i < 3; i++ {
		depth := dt.ExtractDepth(r)
		if err := dt.CheckDepth(depth); err != nil {
			t.Fatalf("depth %d should be allowed", depth)
		}
		// Set incremented depth for "next" request.
		r = httptest.NewRequest("POST", "/mcp", nil)
		r.Header.Set(HeaderAgentDepth, dt.IncrementHeader(depth))
	}

	// The 4th should fail.
	depth := dt.ExtractDepth(r)
	if err := dt.CheckDepth(depth); err == nil {
		t.Fatal("depth 3 should exceed max=3")
	}
}

// Ensure HeaderAgentDepth is what we expect for integration.
func TestHeaderConstant(t *testing.T) {
	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set(HeaderAgentDepth, "7")
	if r.Header.Get("X-Agentcc-Agent-Depth") != "7" {
		t.Fatal("header constant mismatch")
	}
	_ = http.StatusOK // use http package
}
