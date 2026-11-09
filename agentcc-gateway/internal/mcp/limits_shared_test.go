package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/contracts/schematest"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// w5bRedisClient is a client on the shared limits database. Both the real
// REST ToolCallBudget API and MCP ToolCallBudgetStore use this database.
func w5bRedisClient(t *testing.T) *redisstate.Client {
	t.Helper()
	addr := os.Getenv("TEST_REDIS_ADDR")
	if addr == "" {
		t.Fatal("TEST_REDIS_ADDR required for shared limits")
	}
	client, err := redisstate.NewClient(redisstate.Config{Address: addr, DB: 13})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { client.Close() })
	return client
}

func w5bRedis(t *testing.T) redis.UniversalClient {
	t.Helper()
	return w5bRedisClient(t).Redis()
}

// mc6Key is MC.6's mtb:{<org>}:<scope>:<counter>, spelled out here rather
// than built by the code under test, as V6's ToolCallBudget.Key builds it.
func mc6Key(org, scope, counter string) string {
	return "mtb:{" + org + "}:" + scope + ":" + counter
}

// v6Take uses the real REST capability store, not a test-only copy of Lua:
// one call on the run's canonical calls counter, no searches or search limit.
func v6Take(t *testing.T, org, scope string, ttl time.Duration) int64 {
	t.Helper()
	store := redisstate.NewToolCallBudget(w5bRedisClient(t), "")
	reply, err := store.Take(context.Background(), org, scope, 1,
		redisstate.ToolCounterSearches, 0, redisstate.ToolCallNoLimit, ttl)
	if err != nil || !reply.Taken {
		t.Fatalf("V6 take: %+v %v", reply, err)
	}
	return reply.Calls
}

// w5bScopeTTL gives the call's org routing.agents.budget_scope_ttl_seconds,
// the run-scope TTL V6 and G19 slide their run keys to.
func w5bScopeTTL(t *testing.T, cc *CallContext, seconds int) *CallContext {
	t.Helper()
	if err := json.Unmarshal([]byte(fmt.Sprintf(`{"routing":{"agents":{"budget_scope_ttl_seconds":%d}}}`, seconds)), cc.OrgConfig); err != nil {
		t.Fatal(err)
	}
	return cc
}

func w5bCounter(t *testing.T, r redis.UniversalClient, key string) int64 {
	t.Helper()
	n, err := r.Get(context.Background(), key).Int64()
	if err == redis.Nil {
		return 0
	}
	if err != nil {
		t.Fatal(err)
	}
	return n
}

// w5bSharedScopes are canonical run identifiers (models.ValidAgentID) with
// every punctuation byte the alphabet allows, and one of the longest.
var w5bSharedScopes = []string{"shared-run", "run:42", "tenant/run-1", "runner@work", "AZaz09._:@/-", strings.Repeat("r", 127) + ":"}

// GE MA5 / MC.6: an MCP call and a REST capability call (V6) in one run
// count against one mtb:{<org>}:<scope>:calls key, in both directions, and
// both writers keep that key on the run scope's one TTL.
func TestCallCountsUseV6Counter(t *testing.T) {
	const ttl = 120
	for _, scope := range w5bSharedScopes {
		t.Run(scope, func(t *testing.T) {
			limits, org := w5bLimits(t)
			r := w5bRedis(t)
			ctx := context.Background()
			key := mc6Key(org, scope, "calls")
			call := func(field int) *CallContext {
				return w5bScopeTTL(t, w5bScopedCall(t, org, `{"max_tool_calls_per_run":3}`, field, scope), ttl)
			}

			// REST first: MCP's run limit sees the capability call.
			if got := v6Take(t, org, scope, ttl*time.Second); got != 1 {
				t.Fatalf("V6 calls = %d, want 1", got)
			}
			cc := call(models.AgentFieldBudgetScope)
			hold, denial := limits.Hold(cc, 0)
			if denial != nil || cc.Budget.CallsRemaining == nil || *cc.Budget.CallsRemaining != 1 {
				t.Fatalf("MCP run limit ignored a REST capability call: %v", denial)
			}
			hold.Settle(0)

			// MCP's call lands on the key V6 reads.
			if got := w5bCounter(t, r, key); got != 2 {
				t.Fatalf("MC.6 counter = %d after one REST and one MCP call, want 2", got)
			}

			// A voided MCP call gives its slot back on the same key.
			hold, denial = limits.Hold(call(models.AgentFieldRunID), 0)
			if denial != nil {
				t.Fatal(denial)
			}
			hold.Void()
			if got := w5bCounter(t, r, key); got != 2 {
				t.Fatalf("MC.6 counter = %d after a voided MCP call, want 2", got)
			}

			// REST takes the last slot; MCP is refused without dispatch.
			if got := v6Take(t, org, scope, ttl*time.Second); got != 3 {
				t.Fatalf("V6 calls = %d, want 3", got)
			}
			refused := call(models.AgentFieldBudgetScope)
			if _, denial = limits.Hold(refused, 0); denial == nil || denial.Code != "tool_budget_exhausted" {
				t.Fatalf("MCP call escaped the run's shared call budget: %v", denial)
			}
			if refused.Dispatched || refused.Executed {
				t.Fatal("refused call dispatched")
			}

			// MCP never touches V6's search counter.
			if n := r.Exists(ctx, mc6Key(org, scope, "searches")).Val(); n != 0 {
				t.Fatal("MCP wrote V6's searches counter")
			}

			// One expiry rule: MCP slides the shared key to the scope TTL
			// exactly as V6's TAKE does, so neither writer strands the other.
			if left := r.TTL(ctx, key).Val(); left <= 0 || left > ttl*time.Second {
				t.Fatalf("shared counter TTL %v, want (0, %ds]", left, ttl)
			}
		})
	}
}

// MCP first: V6's next TAKE counts on top of MCP's calls, and an MCP call
// slides the shared key's TTL back to the scope TTL.
func TestCallCountsMCPFirstThenV6(t *testing.T) {
	const ttl = 120
	for _, scope := range w5bSharedScopes {
		t.Run(scope, func(t *testing.T) {
			limits, org := w5bLimits(t)
			r := w5bRedis(t)
			ctx := context.Background()
			key := mc6Key(org, scope, "calls")
			cfg := `{"max_tool_calls_per_run":2}`
			hold, denial := limits.Hold(w5bScopeTTL(t, w5bScopedCall(t, org, cfg, models.AgentFieldRunID, scope), ttl), 0)
			if denial != nil {
				t.Fatal(denial)
			}
			hold.Settle(0)
			if left := r.TTL(ctx, key).Val(); left <= 0 || left > ttl*time.Second {
				t.Fatalf("MCP-created shared counter TTL %v, want (0, %ds]", left, ttl)
			}
			if got := v6Take(t, org, scope, ttl*time.Second); got != 2 {
				t.Fatalf("V6 calls = %d after one MCP call, want 2", got)
			}
			if _, denial = limits.Hold(w5bScopeTTL(t, w5bScopedCall(t, org, cfg, models.AgentFieldBudgetScope, scope), ttl), 0); denial == nil || denial.Code != "tool_budget_exhausted" {
				t.Fatalf("MCP call escaped the run's shared call budget: %v", denial)
			}

			// A shortened TTL (for instance V6's last write long ago) is slid
			// back by the next MCP call.
			if err := r.Expire(ctx, key, 5*time.Second).Err(); err != nil {
				t.Fatal(err)
			}
			hold, denial = limits.Hold(w5bScopeTTL(t, w5bScopedCall(t, org, `{"max_tool_calls_per_run":9}`, models.AgentFieldBudgetScope, scope), ttl), 0)
			if denial != nil {
				t.Fatal(denial)
			}
			hold.Settle(0)
			if left := r.TTL(ctx, key).Val(); left <= 60*time.Second {
				t.Fatalf("MCP call did not slide the shared counter TTL: %v", left)
			}
		})
	}
}

// Every key a counted tool call leaves (run counter, per-tool counter and the
// per-call holds, settled or voided) expires with the run scope; none
// persists to grow Redis per call.
func TestW5bToolLimitKeysExpire(t *testing.T) {
	for _, row := range []struct {
		name string
		ttl  int // 0: the org sets none; the 86400 s default applies
	}{{"configured", 120}, {"default", 0}} {
		t.Run(row.name, func(t *testing.T) {
			limits, org := w5bLimits(t)
			r := w5bRedis(t)
			ctx := context.Background()
			cfg := `{"max_tool_calls_per_run":2,"per_tool":{"mock/*":{"max_calls_per_run":2}}}`
			call := func() *CallContext {
				cc := w5bCall(org, cfg)
				if row.ttl > 0 {
					w5bScopeTTL(t, cc, row.ttl)
				}
				return cc
			}
			settled, denial := limits.Hold(call(), 0)
			if denial != nil {
				t.Fatal(denial)
			}
			settled.Settle(0)
			voided, denial := limits.Hold(call(), 0)
			if denial != nil {
				t.Fatal(denial)
			}
			voided.Void()

			want := time.Duration(row.ttl) * time.Second
			if row.ttl == 0 {
				want = 24 * time.Hour
			}
			keys, err := r.Keys(ctx, "mtb:{"+org+"}:*").Result()
			if err != nil {
				t.Fatal(err)
			}
			// One canonical run counter plus one hash: per-tool counts and
			// both holds are fields that cannot expire independently.
			if len(keys) != 2 {
				t.Fatalf("keys %v", keys)
			}
			run := mc6Key(org, call().Agent.BudgetScope, "calls")
			fields, err := r.HGetAll(ctx, run+":state").Result()
			if err != nil || len(fields) != 3 || fields[settled.(*toolCountHold).id] != "settled" || fields[voided.(*toolCountHold).id] != "void" {
				t.Fatalf("run state %v: %v", fields, err)
			}
			// PEXPIRETIME measures the stored deadline, not two PTTL reads
			// separated by network time. Every run key must agree exactly.
			deadline := r.Do(ctx, "PEXPIRETIME", run).Val()
			if other := r.Do(ctx, "PEXPIRETIME", run+":state").Val(); deadline != other {
				t.Fatalf("split run expiry: calls=%v state=%v", deadline, other)
			}
			for _, key := range keys {
				if left := r.PTTL(ctx, key).Val(); left <= want-time.Minute || left > want {
					t.Fatalf("%s TTL %v, want about %v", key, left, want)
				}
			}
		})
	}
}

// A configured zero blocks the tool for good: the refusal says so and gives
// no retry hint that a client would honour forever. No window is written.
func TestW5bZeroRateBlocksWithoutRetry(t *testing.T) {
	for _, cfg := range []string{`{"rpm_per_key":0}`, `{"per_tool":{"mock/*":{"rpm":0}}}`, `{"rpm_per_key":5,"per_tool":{"mock/*":{"rpm":0}}}`} {
		t.Run(cfg, func(t *testing.T) {
			limits, org := w5bLimits(t)
			r := w5bRedis(t)
			for _, l := range []*ToolLimits{limits, NewToolLimits(nil)} {
				cc := w5bCall(org, cfg)
				_, denial := l.Admit(cc)
				if denial == nil || denial.Code != "tool_denied" {
					t.Fatalf("zero limit: %v", denial)
				}
				obj := denial.Object()
				raw, _ := json.Marshal(obj.Metadata)
				if strings.Contains(string(raw), "retry_after") || strings.Contains(obj.Message, "Try again") {
					t.Fatalf("permanent block carries a retry hint: %s %s", obj.Message, raw)
				}
				if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", obj); len(errs) > 0 {
					t.Fatalf("zero-limit refusal breaks mcp-denial-v1: %v", errs)
				}
				if cc.Dispatched || cc.Executed {
					t.Fatal("refused call dispatched")
				}
			}
			if keys := r.Keys(context.Background(), "mrl:{"+org+"}:*").Val(); len(keys) != 0 {
				t.Fatalf("zero limit wrote rate windows %v", keys)
			}
		})
	}
}

// Both rate refusals, per key and per tool, are the tool_rate_limited object
// mcp-denial-v1 publishes.
func TestW5bRateRefusalsMatchContract(t *testing.T) {
	for _, cfg := range []string{`{"rpm_per_key":1}`, `{"per_tool":{"mock/*":{"rpm":1}}}`} {
		t.Run(cfg, func(t *testing.T) {
			limits, org := w5bLimits(t)
			if _, denial := limits.Admit(w5bCall(org, cfg)); denial != nil {
				t.Fatal(denial)
			}
			_, denial := limits.Admit(w5bCall(org, cfg))
			if denial == nil || denial.Code != "tool_rate_limited" {
				t.Fatalf("second call in the window: %v", denial)
			}
			if errs := schematest.ValidateDef(t, "mcp-denial-v1.schema.json", "Error", denial.Object()); len(errs) > 0 {
				t.Fatalf("rate refusal breaks mcp-denial-v1: %v", errs)
			}
		})
	}
}
