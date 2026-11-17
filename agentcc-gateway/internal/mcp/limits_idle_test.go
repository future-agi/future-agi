package mcp

import (
	"context"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// The verifier's sequence: echo has a per-run ceiling of one, but other
// calls keep that run active beyond echo's own last-call TTL. Both MCP and
// REST activity must preserve every ceiling and settled call's replay record.
func TestW5bActiveRunKeepsPerToolCeiling(t *testing.T) {
	for _, activity := range []string{"mcp", "rest", "unmatched"} {
		t.Run(activity, func(t *testing.T) {
			limits, org := w5bLimits(t)
			r := w5bRedis(t)
			cfg := `{"max_tool_calls_per_run":50,"per_tool":{"mock/echo":{"max_calls_per_run":1}}}`
			if activity == "unmatched" {
				cfg = `{"per_tool":{"mock/echo":{"max_calls_per_run":1}}}`
			}
			call := func(other bool) *CallContext {
				cc := w5bScopeTTL(t, w5bCall(org, cfg), 2)
				if other {
					cc.Tool.UpstreamName = "other"
					cc.Tool.ExposedName = "mock__other"
				}
				return cc
			}
			first := call(false)
			hold, denial := limits.Hold(first, 0)
			if denial != nil {
				t.Fatal(denial)
			}
			hold.Settle(0)
			for i := 0; i < 6; i++ {
				time.Sleep(500 * time.Millisecond)
				if activity == "rest" {
					v6Take(t, org, first.Agent.BudgetScope, 2*time.Second)
				} else {
					h, d := limits.Hold(call(true), 0)
					if d != nil {
						t.Fatal(d)
					}
					h.Settle(0)
				}
			}
			key := mc6Key(org, first.Agent.BudgetScope, "calls")
			if got := w5bCounter(t, r, key); got != 7 {
				t.Errorf("active run counter = %d, want 7", got)
			}
			if h, d := limits.Hold(call(false), 0); d == nil {
				h.Void()
				t.Error("per-tool cap reset while same run was continuously active")
			} else if d.Code != "tool_budget_exhausted" {
				t.Errorf("wrong ceiling refusal: %v", d)
			}
			replay := call(false)
			replay.ToolCallID = first.ToolCallID
			if h, d := limits.Hold(replay, 0); d == nil {
				h.Void()
				t.Error("settled tool call id replayed while same run was continuously active")
			} else if d.Code != "policy_unavailable" {
				t.Errorf("replay history lost: %v", d)
			}

			// A genuinely idle run expires all counts and replay state together.
			time.Sleep(2200 * time.Millisecond)
			if keys := r.Keys(context.Background(), "mtb:{"+org+"}:*").Val(); len(keys) != 0 {
				t.Errorf("idle run retained keys: %v", keys)
			}
			h, d := limits.Hold(replay, 0)
			if d != nil {
				t.Fatalf("idle run did not start fresh: %v", d)
			}
			h.Settle(0)
		})
	}
}

// A pending call's void after activity on another tool must return its
// per-tool slot as well as the shared run slot, exactly once.
func TestW5bActiveRunKeepsPendingHold(t *testing.T) {
	limits, org := w5bLimits(t)
	r := w5bRedis(t)
	cfg := `{"max_tool_calls_per_run":50,"per_tool":{"mock/echo":{"max_calls_per_run":1}}}`
	call := func() *CallContext { return w5bScopeTTL(t, w5bCall(org, cfg), 2) }
	pending := call()
	hold, denial := limits.Hold(pending, 0)
	if denial != nil {
		t.Fatal(denial)
	}
	for i := 0; i < 6; i++ {
		time.Sleep(500 * time.Millisecond)
		v6Take(t, org, pending.Agent.BudgetScope, 2*time.Second)
	}
	hold.Void()
	hold.Void()
	if got := w5bCounter(t, r, mc6Key(org, pending.Agent.BudgetScope, "calls")); got != 6 {
		t.Fatalf("void lost or double-returned run slot: %d", got)
	}
	next, denial := limits.Hold(call(), 0)
	if denial != nil {
		t.Fatalf("void did not return per-tool slot: %v", denial)
	}
	next.Settle(0)
}

// The search ceiling is per-run too. MCP must refresh its lifetime without
// incrementing it, and a REST refusal must keep all state on one deadline.
func TestW5bActiveRunKeepsRESTSearchCeiling(t *testing.T) {
	limits, org := w5bLimits(t)
	client := w5bRedisClient(t)
	r := client.Redis()
	rest := redisstate.NewToolCallBudget(client, "")
	ctx := context.Background()
	cfg := `{"max_tool_calls_per_run":50}`
	call := func() *CallContext { return w5bScopeTTL(t, w5bCall(org, cfg), 2) }
	scope := call().Agent.BudgetScope
	first, err := rest.Take(ctx, org, scope, 1, redisstate.ToolCounterSearches, 1, 1, 2*time.Second)
	if err != nil || !first.Taken {
		t.Fatalf("first search: %+v %v", first, err)
	}
	for i := 0; i < 6; i++ {
		time.Sleep(500 * time.Millisecond)
		hold, denial := limits.Hold(call(), 0)
		if denial != nil {
			t.Fatal(denial)
		}
		hold.Settle(0)
	}
	refused, err := rest.Take(ctx, org, scope, 1, redisstate.ToolCounterSearches, 1, 1, 2*time.Second)
	if err != nil || refused.Taken || refused.Calls != 7 || refused.Count != 1 {
		t.Fatalf("search ceiling lost during MCP activity: %+v %v", refused, err)
	}
	run := mc6Key(org, scope, "calls")
	deadline, err := r.Do(ctx, "PEXPIRETIME", run).Int64()
	if err != nil || deadline <= 0 {
		t.Fatalf("run expiry: %d %v", deadline, err)
	}
	for _, key := range []string{mc6Key(org, scope, "searches"), run + ":state"} {
		if got, err := r.Do(ctx, "PEXPIRETIME", key).Int64(); err != nil || got != deadline {
			t.Fatalf("split run expiry for %s: %d, want %d: %v", key, got, deadline, err)
		}
	}
}
