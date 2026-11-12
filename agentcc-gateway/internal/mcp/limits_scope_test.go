package mcp

import (
	"strings"
	"testing"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func w5bScopedCall(t *testing.T, org, cfg string, field int, scope string) *CallContext {
	t.Helper()
	cc := w5bCall(org, cfg)
	cc.Agent.BudgetScope = ""
	cc.Agent.RunID = ""
	if err := cc.Agent.SetAgentField(field, scope); err != nil {
		t.Fatalf("canonical agent contract rejected fixture: %v", err)
	}
	return cc
}

func TestW5bCountAcceptsCanonicalScopeIDs(t *testing.T) {
	for _, field := range []struct {
		name string
		id   int
	}{
		{"budget_scope", models.AgentFieldBudgetScope},
		{"run_id", models.AgentFieldRunID},
	} {
		for _, scope := range []string{"run:42", "tenant/run-1", "runner@work", "AZaz09._:@/-", strings.Repeat("r", 127) + ":"} {
			t.Run(field.name+"/"+scope, func(t *testing.T) {
				limits, org := w5bLimits(t)
				cfg := `{"max_tool_calls_per_run":2,"per_tool":{"mock/*":{"max_calls_per_run":2}}}`
				cc := w5bScopedCall(t, org, cfg, field.id, scope)
				hold, denial := limits.Hold(cc, 0)
				if denial != nil {
					t.Fatalf("valid canonical scope rejected: %s", denial.Code)
				}
				if cc.Budget.Scope != scope || cc.Budget.CallsRemaining == nil || *cc.Budget.CallsRemaining != 1 {
					t.Fatal("response lost original scope or remaining count")
				}
				// The run counter is MC.6's raw-scope key, the one V6's REST
				// capability calls write (GE MA5); the org stays the Redis
				// Cluster hash tag. Holds and per-tool counters hang off it
				// and always end in a digest, never in a counter name.
				run := "mtb:{" + org + "}:" + scope + ":calls"
				count := hold.(*toolCountHold)
				if len(count.limits) != 2 || count.limits[0].Key != run {
					t.Fatalf("run counter %q is not MC.6's shared key %q", count.limits[0].Key, run)
				}
				if !w5bDigestUnder(count.id, run+":hold:") {
					t.Fatalf("hold %q is not a digest under the run counter", count.id)
				}
				if !w5bDigestUnder(count.limits[1].Key, run+":tool:") {
					t.Fatalf("per-tool counter %q is not a digest under the run counter", count.limits[1].Key)
				}
				hold.Settle(0)

				// A second replica and the alternate field must resolve to the
				// same counter: an explicit scope can name a parent's run ID.
				otherField := models.AgentFieldRunID
				if field.id == otherField {
					otherField = models.AgentFieldBudgetScope
				}
				replica := NewToolLimits(limits.store)
				next := w5bScopedCall(t, org, cfg, otherField, scope)
				hold, denial = replica.Hold(next, 0)
				if denial != nil || next.Budget.CallsRemaining == nil || *next.Budget.CallsRemaining != 0 {
					t.Fatalf("replica did not share the scope counter: %v", denial)
				}
				hold.Settle(0)
				if _, denial = limits.Hold(w5bScopedCall(t, org, cfg, field.id, scope), 0); denial == nil || denial.Code != "tool_budget_exhausted" {
					t.Fatalf("canonical scope escaped its count limit: %v", denial)
				}
			})
		}
	}
}

// w5bDigestUnder reports whether key is under prefix and ends in one
// SHA-256 hex digest, which no MC.6 counter name (calls, searches, usd,
// held, holds, holdexp) can equal.
func w5bDigestUnder(key, prefix string) bool {
	rest, ok := strings.CutPrefix(key, prefix)
	if !ok || len(rest) != 64 {
		return false
	}
	for _, c := range rest {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}

func TestW5bCountScopeCollisionResistance(t *testing.T) {
	limits, org := w5bLimits(t)
	cfg := `{"max_tool_calls_per_run":1,"per_tool":{"mock/*":{"max_calls_per_run":1}}}`
	scopes := []string{
		"tenant:run", "tenant/run", "tenant@run", "tenant_run", "tenant.run", "tenant-run", "Tenant:run",
		"a:bc", "ab:c", "run", "run:calls", "run:hold:call", "run:tool:x",
		"run:hold", "run:tool", "run:searches", "run:calls:hold", "run:calls:tool", "run:calls:hold:calls",
		strings.Repeat("r", 127) + "a", strings.Repeat("r", 127) + "b",
	}
	// Every scope's MC.6 counters, which only that scope's own run counter
	// may name: an MCP hold or per-tool counter that equals one would count
	// against, or reset, another run's shared budget.
	counters := map[string]string{}
	for _, scope := range scopes {
		for _, counter := range []string{"calls", "searches", "usd", "held", "holds", "holdexp"} {
			counters["mtb:{"+org+"}:"+scope+":"+counter] = scope
		}
	}
	seen := map[string]string{}
	for _, scope := range scopes {
		t.Run(scope, func(t *testing.T) {
			cc := w5bScopedCall(t, org, cfg, models.AgentFieldBudgetScope, scope)
			cc.ToolCallID = "calls" // a valid id that spells a counter name
			hold, denial := limits.Hold(cc, 0)
			if denial != nil {
				t.Fatalf("distinct scope shared another scope's counter or hold: %s", denial.Code)
			}
			count := hold.(*toolCountHold)
			if len(count.limits) != 2 || count.limits[0].Key != "mtb:{"+org+"}:"+scope+":calls" {
				t.Fatalf("run counter %v is not MC.6's shared key", count.limits)
			}
			for _, identity := range []string{count.id, count.limits[1].Key} {
				if owner, clash := counters[identity]; clash {
					t.Fatalf("%q equals scope %q's MC.6 counter", identity, owner)
				}
			}
			for _, identity := range []string{count.id, count.limits[0].Key, count.limits[1].Key} {
				if previous, exists := seen[identity]; exists {
					t.Fatalf("scope identity collided with %q", previous)
				}
				seen[identity] = scope
			}
			hold.Settle(0)
			if _, denial = limits.Hold(w5bScopedCall(t, org, cfg, models.AgentFieldRunID, scope), 0); denial == nil || denial.Code != "tool_budget_exhausted" {
				t.Fatalf("scope counter did not remain exhausted: %v", denial)
			}
		})
	}
}

func TestW5bCountScopeOrgIsolation(t *testing.T) {
	first, orgA := w5bLimits(t)
	second, orgB := w5bLimits(t)
	cfg := `{"max_tool_calls_per_run":1}`
	for _, row := range []struct {
		limits *ToolLimits
		org    string
	}{{first, orgA}, {second, orgB}} {
		cc := w5bScopedCall(t, row.org, cfg, models.AgentFieldBudgetScope, "tenant/run:42@work")
		cc.ToolCallID = "same-call-id"
		hold, denial := row.limits.Hold(cc, 0)
		if denial != nil {
			t.Fatalf("org shared another org's counter or hold: %v", denial)
		}
		hold.Settle(0)
		if _, denial = row.limits.Hold(w5bScopedCall(t, row.org, cfg, models.AgentFieldRunID, "tenant/run:42@work"), 0); denial == nil || denial.Code != "tool_budget_exhausted" {
			t.Fatalf("org counter escaped its limit: %v", denial)
		}
	}
}
