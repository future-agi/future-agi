package mcp

import (
	"encoding/json"
	"testing"
)

// Refreshing other tools' run state must not make an unlimited, unscoped
// tool depend on Redis or require a run id it did not previously need.
func TestW5bUnmatchedToolWithoutRunRemainsUnlimited(t *testing.T) {
	cc := w5bCall("org", `{"per_tool":{"mock/echo":{"max_calls_per_run":1}}}`)
	cc.Agent.BudgetScope = ""
	cc.Agent.RunID = ""
	cc.Tool.UpstreamName = "other"
	cc.Tool.ExposedName = "mock__other"
	hold, denial := NewToolLimits(nil).Hold(cc, 0)
	if denial != nil {
		t.Fatalf("unlimited unscoped tool acquired a run requirement: %v", denial)
	}
	hold.Settle(0)
	if cc.Budget.CallsRemaining != nil || len(cc.ResponseMeta) != 0 {
		t.Fatal("unscoped unlimited tool acquired budget metadata")
	}
}

func TestW5bUnmatchedToolHasNoFiniteRemaining(t *testing.T) {
	limits, org := w5bLimits(t)
	cc := w5bCall(org, `{"per_tool":{"mock/echo":{"max_calls_per_run":1}}}`)
	cc.Tool.UpstreamName = "other"
	cc.Tool.ExposedName = "mock__other"
	hold, denial := limits.Hold(cc, 0)
	if denial != nil {
		t.Fatal(denial)
	}
	hold.Settle(0)
	if cc.Budget.CallsRemaining != nil {
		t.Errorf("unlimited tool has finite remaining value %d", *cc.Budget.CallsRemaining)
	}
	var meta map[string]any
	if err := json.Unmarshal(cc.ResponseMeta["io.futureagi/budget"], &meta); err != nil {
		t.Fatal(err)
	}
	if _, exists := meta["calls_remaining"]; exists {
		t.Errorf("unlimited tool must omit calls_remaining, got %v", meta)
	}
	if meta["scope"] != cc.Agent.BudgetScope {
		t.Fatalf("budget metadata lost run scope: %v", meta)
	}
}
