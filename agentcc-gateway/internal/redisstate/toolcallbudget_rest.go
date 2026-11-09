package redisstate

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// This is V6's ToolCallBudget API, brought forward from gw/V6 so REST and
// MCP use the same run expiry primitive. At integration retain this store
// instead of adding V6's older duplicate toolcallbudget.go implementation.

// ToolCallNoLimit is the limit of a counter that has none.
const ToolCallNoLimit int64 = -1

// Tool-budget counters of a run scope (plan section 6.21, MC.6), every key
// sharing the org's hash tag:
//
//   - ToolCounterCalls, mtb:{<org>}:<scope>:calls, is the run's tool-call
//     count. V6's capability budget adds one per REST capability call and
//     M19 (W4b) one per MCP tool call, so one run's free-tool call budget
//     covers both (GE MA5).
//   - ToolCounterSearches, mtb:{<org>}:<scope>:searches, is the run's search
//     count, gateway and native, that budgets.max_searches_per_run limits
//     (V6). It is apart from calls so that MCP tool calls never use up the
//     search budget.
const (
	ToolCounterCalls    = "calls"
	ToolCounterSearches = "searches"
)

// ErrToolCallBudgetUnavailable is every failure to reach the counters.
var ErrToolCallBudgetUnavailable = errors.New("redisstate: tool call budget store unavailable")

// errBadToolCallKey refuses an org, scope or counter that cannot be keyed.
var errBadToolCallKey = errors.New("redisstate: tool call budget org, scope or counter is empty or malformed")

// ToolCallTake is what a Take did and where the counters stand after it.
type ToolCallTake struct {
	// Taken is false when the take would have passed its limit; then nothing
	// was counted.
	Taken bool
	// Calls is the run's tool-call count, Count the limited counter's.
	Calls, Count int64
}

// ToolCallBudget is the per-run tool-budget counters in Redis. Every update
// is one Lua script over the run's calls key, limited counter and state
// hash, so concurrent calls never count past a limit or split run expiry.
// Production keys have no prefix; tests isolate theirs.
type ToolCallBudget struct {
	client *Client
	prefix string
}

// NewToolCallBudget returns the counters over client. A nil client is never
// available: every operation returns ErrToolCallBudgetUnavailable.
func NewToolCallBudget(client *Client, prefix string) *ToolCallBudget {
	return &ToolCallBudget{client: client, prefix: prefix}
}

// Key is a counter's key: <prefix>mtb:{<org>}:<scope>:<counter>.
func (b *ToolCallBudget) Key(org, scope, counter string) string {
	return b.prefix + "mtb:{" + org + "}:" + scope + ":" + counter
}

func validToolCallKey(org, scope, counter string) error {
	if validAgentBudgetKey(org, scope) != nil || counter != ToolCounterSearches {
		return errBadToolCallKey
	}
	return nil
}

// Take adds calls to the run's tool-call count and n to counter, unless the
// counter would pass limit (ToolCallNoLimit for none): then it counts
// nothing and reports Taken false. A negative calls or n gives counts back
// (Release); a counter never goes below zero. All run state slides to ttl.
// counter is ToolCounterSearches, the one limited counter today.
func (b *ToolCallBudget) Take(ctx context.Context, org, scope string, calls int64, counter string, n, limit int64, ttl time.Duration) (ToolCallTake, error) {
	if err := validToolCallKey(org, scope, counter); err != nil {
		return ToolCallTake{}, err
	}
	var reply []any
	err := b.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		reply, err = toolCallTakeScript.Run(ctx, rdb,
			toolScopeKeys(b.Key(org, scope, ToolCounterCalls)),
			calls, n, limit, ttlSeconds(ttl)).Slice()
		return err
	})
	if err != nil {
		return ToolCallTake{}, err
	}
	if len(reply) != 3 {
		return ToolCallTake{}, fmt.Errorf("%w: unexpected script reply %v", ErrToolCallBudgetUnavailable, reply)
	}
	return ToolCallTake{Taken: asInt(reply[0]) == 1, Calls: asInt(reply[1]), Count: asInt(reply[2])}, nil
}

// Release gives back calls and n taken by a call that never reached a
// vendor.
func (b *ToolCallBudget) Release(ctx context.Context, org, scope string, calls int64, counter string, n int64, ttl time.Duration) error {
	_, err := b.Take(ctx, org, scope, -calls, counter, -n, ToolCallNoLimit, ttl)
	return err
}

// Counts reads the run's tool-call count and counter's count.
func (b *ToolCallBudget) Counts(ctx context.Context, org, scope, counter string) (calls, count int64, err error) {
	if err := validToolCallKey(org, scope, counter); err != nil {
		return 0, 0, err
	}
	var vals []any
	err = b.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		vals, err = rdb.MGet(ctx, b.Key(org, scope, ToolCounterCalls), b.Key(org, scope, counter)).Result()
		return err
	})
	if err != nil {
		return 0, 0, err
	}
	return asInt(vals[0]), asInt(vals[1]), nil
}

// run executes fn through the client's circuit breaker on its own timeout,
// so a release still lands after the request's context has ended.
func (b *ToolCallBudget) run(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if b.client == nil || !b.client.Available() {
		return ErrToolCallBudgetUnavailable
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), managedOpTimeout)
	defer cancel()
	if err := b.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) }); err != nil {
		return fmt.Errorf("%w: %v", ErrToolCallBudgetUnavailable, err)
	}
	return nil
}

// toolCallTakeScript is TAKE. KEYS: calls, searches, state. ARGV: calls to
// add, n to add to searches, the search limit (-1 for none), TTL seconds.
// A refused take counts nothing, but is activity on the run. Expiry includes
// the MCP state even when REST is the only writer for longer than one TTL.
var toolCallTakeScript = redis.NewScript(toolScopeExpiryLua + `
local function luaInt(v) return string.format('%d', v) end
local function num(k) return tonumber(redis.call('GET', k) or '0') or 0 end
local calls, count = num(KEYS[1]), num(KEYS[2])
local inc, n, limit, ttl = tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
if limit >= 0 and n > 0 and count + n > limit then
  expireScope(ttl)
  return {0, luaInt(calls), luaInt(count)}
end
local function add(k, cur, by)
  if by == 0 then return cur end
  local v = cur + by
  if v < 0 then v = 0 end
  redis.call('SET', k, luaInt(v))
  return v
end
calls = add(KEYS[1], calls, inc)
count = add(KEYS[2], count, n)
expireScope(ttl)
return {1, luaInt(calls), luaInt(count)}
`)
