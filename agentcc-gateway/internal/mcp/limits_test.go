package mcp

import (
	"context"
	"encoding/json"
	"os"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/redisstate"
	"github.com/oklog/ulid/v2"
)

func w5bLimits(t *testing.T) (*ToolLimits, string) {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Fatal("TEST_REDIS_ADDR required for shared limits")
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: 13})
	if err != nil {
		t.Fatal(err)
	}
	org := "w5b-" + ulid.Make().String()
	t.Cleanup(func() {
		for _, prefix := range []string{"mtb:", "mrl:"} {
			keys, _ := client.Redis().Keys(context.Background(), prefix+"{"+org+"}:*").Result()
			if len(keys) > 0 {
				client.Redis().Del(context.Background(), keys...)
			}
		}
		client.Close()
	})
	return NewToolLimits(redisstate.NewToolCallBudgetStore(client)), org
}
func w5bCall(org, budget string) *CallContext {
	cc := w5Call(org)
	cc.ToolCallID = "tc_" + ulid.Make().String()
	cc.Agent.BudgetScope = "shared-run"
	_ = json.Unmarshal([]byte(`{"mcp":{"virtual_servers":{"review":{"id":"vs-review","budgets":`+budget+`}}}}`), cc.OrgConfig)
	return cc
}
func TestW5bLimitsDefaultOff(t *testing.T) {
	l := NewToolLimits(nil)
	cc := w5bCall("org", `{}`)
	if _, d := l.Admit(cc); d != nil {
		t.Fatal(d)
	}
	if _, d := l.Hold(cc, 0); d != nil {
		t.Fatal(d)
	}
}
func TestW5bSharedRateAndIsolation(t *testing.T) {
	a, org := w5bLimits(t)
	b := NewToolLimits(a.store)
	cfg := `{"per_tool":{"mock/*":{"rpm":1}}}`
	if _, d := a.Admit(w5bCall(org, cfg)); d != nil {
		t.Fatal(d)
	}
	denied := w5bCall(org, cfg)
	if _, d := b.Admit(denied); d == nil || d.Code != "tool_rate_limited" {
		t.Fatal("replica did not share rate", d)
	}
	for _, dimension := range []string{"org", "principal", "virtual", "tool"} {
		cc := w5bCall(org, cfg)
		switch dimension {
		case "org":
			cc.Principal.OrgID += "-other"
		case "principal":
			cc.Principal.KeyID = "other"
		case "virtual":
			cc.VirtualServer = "vs-other"
			cc.OrgConfig.MCP.VirtualServers["review"].ID = "vs-other"
		case "tool":
			cc.Tool.UpstreamName = "other"
		}
		if _, d := b.Admit(cc); d != nil {
			t.Fatalf("%s leaked: %v", dimension, d)
		}
	}
}
func TestW5bKeyRateAcrossTools(t *testing.T) {
	l, org := w5bLimits(t)
	cfg := `{"rpm_per_key":1}`
	if _, d := l.Admit(w5bCall(org, cfg)); d != nil {
		t.Fatal(d)
	}
	cc := w5bCall(org, cfg)
	cc.Tool.UpstreamName = "other"
	if _, d := l.Admit(cc); d == nil {
		t.Fatal("key cap reset by tool")
	}
	cc.Principal.KeyID = "other-key"
	if _, d := l.Admit(cc); d != nil {
		t.Fatal(d)
	}
}
func TestW5bConcurrentCountNeverOvershoots(t *testing.T) {
	a, org := w5bLimits(t)
	b := NewToolLimits(a.store)
	var wg sync.WaitGroup
	var admitted atomic.Int32
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			l := a
			if i%2 == 0 {
				l = b
			}
			cc := w5bCall(org, `{"max_tool_calls_per_run":5}`)
			h, d := l.Hold(cc, 0)
			if d == nil {
				admitted.Add(1)
				h.Settle(0)
				h.Void()
			} else if d.Code != "tool_budget_exhausted" {
				t.Error(d)
			}
		}(i)
	}
	wg.Wait()
	if admitted.Load() != 5 {
		t.Fatalf("admitted %d, want 5", admitted.Load())
	}
}
func TestW5bCountVoidAndPerToolCaps(t *testing.T) {
	l, org := w5bLimits(t)
	cfg := `{"max_tool_calls_per_run":9,"per_tool":{"mock/*":{"max_calls_per_run":1}}}`
	cc := w5bCall(org, cfg)
	h, d := l.Hold(cc, 0)
	if d != nil {
		t.Fatal(d)
	}
	h.Void()
	h.Void()
	h, d = l.Hold(w5bCall(org, cfg), 0)
	if d != nil {
		t.Fatal(d)
	}
	h.Settle(0)
	h.Void()
	refused := w5bCall(org, cfg)
	if _, d = l.Hold(refused, 0); d == nil || d.Code != "tool_budget_exhausted" {
		t.Fatal(d)
	}
	if refused.Executed || refused.Dispatched {
		t.Fatal("denial executed")
	}
}
func TestW5bCallsShareCanonicalCounterAndChildScope(t *testing.T) {
	l, org := w5bLimits(t)
	cc := w5bCall(org, `{"max_tool_calls_per_run":2}`)
	cc.Agent.RunID = "child-one"
	h, d := l.Hold(cc, 0)
	if d != nil {
		t.Fatal(d)
	}
	h.Settle(0)
	// MA5/MC.6: the run counter is the raw-scope key V6's REST capability
	// calls also write, never a derived one.
	r := w5bRedis(t)
	key := "mtb:{" + org + "}:shared-run:calls"
	if got := w5bCounter(t, r, key); got != 1 {
		t.Fatalf("MCP call not on the canonical MC.6 counter: %d", got)
	}
	// Another writer of the shared counter takes the second slot.
	if err := r.Incr(context.Background(), key).Err(); err != nil {
		t.Fatal(err)
	}
	next := w5bCall(org, `{"max_tool_calls_per_run":2}`)
	next.Agent.RunID = "child-two"
	if _, d := l.Hold(next, 0); d == nil || d.Code != "tool_budget_exhausted" {
		t.Fatal("child run escaped shared budget", d)
	}
}

func TestW5bBudgetFinalizerSettlesOrVoids(t *testing.T) {
	limits, org := w5bLimits(t)
	s := &Server{}
	s.SetDeps(Deps{ToolBudget: limits})
	cfg := `{"max_tool_calls_per_run":1}`
	cc := w5bCall(org, cfg)
	if d := reserveBudget(s, cc); d != nil {
		t.Fatal(d)
	}
	cc.runFinalizers()
	cc.runFinalizers()
	next := w5bCall(org, cfg)
	if d := reserveBudget(s, next); d != nil {
		t.Fatal("undispatched hold was not voided", d)
	}
	next.Dispatched = true // An upstream timeout may have executed and must count.
	next.runFinalizers()
	next.runFinalizers()
	if d := reserveBudget(s, w5bCall(org, cfg)); d == nil || d.Code != "tool_budget_exhausted" {
		t.Fatal("ambiguous dispatch did not count", d)
	}
}

func TestW5bLimitsFailClosed(t *testing.T) {
	l := NewToolLimits(nil)
	for _, cfg := range []string{`{"rpm_per_key":1}`, `{"max_tool_calls_per_run":1}`} {
		cc := w5bCall("org", cfg)
		_, rate := l.Admit(cc)
		_, count := l.Hold(cc, 0)
		if rate == nil && count == nil {
			t.Fatal("store outage allowed call")
		}
	}
	l, org := w5bLimits(t)
	cc := w5bCall(org, `{"max_tool_calls_per_run":0}`)
	if _, d := l.Hold(cc, 0); d == nil {
		t.Fatal("explicit zero allowed call")
	}
	cc = w5bCall(org, `{"max_tool_calls_per_run":1}`)
	cc.Agent.BudgetScope = ""
	cc.Agent.RunID = ""
	if _, d := l.Hold(cc, 0); d == nil {
		t.Fatal("missing run identity allowed scoped cap")
	}
}
