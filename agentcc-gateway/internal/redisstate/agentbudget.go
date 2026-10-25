package redisstate

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/futureagi/agentcc-gateway/internal/billing"
)

// AgentBudgetNoLimit is the limit of a call that sends no budget, and of a
// scope that has none.
const AgentBudgetNoLimit int64 = -1

// Statuses of a run-budget scope operation.
const (
	AgentBudgetOK        = "ok"
	AgentBudgetExhausted = "exhausted"
	AgentBudgetStopped   = "stopped"
)

// maxAgentBudgetScopeLen bounds a scope id (agent ids are at most 128 bytes).
const maxAgentBudgetScopeLen = 256

// ErrAgentBudgetUnavailable is every failure to reach the scope store.
var ErrAgentBudgetUnavailable = errors.New("redisstate: run budget store unavailable")

// errBadAgentBudgetKey refuses an org, scope or hold id that cannot be keyed.
var errBadAgentBudgetKey = errors.New("redisstate: run budget org, scope or hold id is empty or malformed")

// AgentBudgetState is a scope's counters after an operation, all in micros.
type AgentBudgetState struct {
	Status      string // one of the AgentBudget statuses ("" from Peek)
	LimitMicros int64  // AgentBudgetNoLimit when the scope has no limit
	SpentMicros int64
	HeldMicros  int64
	Stopped     bool
	StopReason  string // the stopped key's value
}

// HasLimit reports whether the scope has a limit.
func (s AgentBudgetState) HasLimit() bool { return s.LimitMicros >= 0 }

// RemainingMicros is what the scope can still hold: limit - spent - held,
// negative after an overshoot. Meaningful only with a limit.
func (s AgentBudgetState) RemainingMicros() int64 {
	return s.LimitMicros - s.SpentMicros - s.HeldMicros
}

// AgentBudgetStore is the per-run agent budget hot path in Redis:
// one scope's limit, spent and held counters, its holds and their expiries,
// and its stop marker, at abud:{<org>}:<scope>:limit|spent|held|holds|
// holdexp|stopped, every key sharing the org's hash tag. RESERVE, SETTLE and
// VOID are Lua scripts, so concurrent members of one run never hold more than
// the limit. Exported for callers outside the request pipeline, which pass
// their own hold id. Keys carry the prefix the store is made with: the
// gateway's redis.prefix (cmd/agentcc/wiring_agentbudget.go), a per-test
// prefix in tests.
type AgentBudgetStore struct {
	client *Client
	prefix string
	now    func() time.Time
}

// NewAgentBudgetStore returns the store over client. A nil client is never
// available: every operation returns ErrAgentBudgetUnavailable.
func NewAgentBudgetStore(client *Client, prefix string) *AgentBudgetStore {
	return &AgentBudgetStore{client: client, prefix: prefix, now: time.Now}
}

// keys are the scope's keys, in the order every script takes them: limit,
// spent, held, holds, holdexp, stopped.
func (s *AgentBudgetStore) keys(org, scope string) []string {
	base := s.prefix + "abud:{" + org + "}:" + scope + ":"
	return []string{base + "limit", base + "spent", base + "held", base + "holds", base + "holdexp", base + "stopped"}
}

func validAgentBudgetKey(org, scope string) error {
	if validOrg(org) != nil || scope == "" || len(scope) > maxAgentBudgetScopeLen {
		return errBadAgentBudgetKey
	}
	for i := 0; i < len(scope); i++ {
		if c := scope[i]; c <= ' ' || c == '{' || c == '}' || c == 0x7f {
			return errBadAgentBudgetKey
		}
	}
	return nil
}

// run executes fn through the client's circuit breaker on its own timeout,
// so a hold is still released after the request's context has ended.
func (s *AgentBudgetStore) run(ctx context.Context, fn func(ctx context.Context, rdb redis.UniversalClient) error) error {
	if s.client == nil || !s.client.Available() {
		return ErrAgentBudgetUnavailable
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), managedOpTimeout)
	defer cancel()
	if err := s.client.Do(func(rdb redis.UniversalClient) error { return fn(ctx, rdb) }); err != nil {
		return fmt.Errorf("%w: %v", ErrAgentBudgetUnavailable, err)
	}
	return nil
}

// script runs one of the scripts over the scope's keys.
func (s *AgentBudgetStore) script(ctx context.Context, sc *redis.Script, org, scope string, args ...any) (AgentBudgetState, error) {
	if err := validAgentBudgetKey(org, scope); err != nil {
		return AgentBudgetState{}, err
	}
	var reply []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		reply, err = sc.Run(ctx, rdb, s.keys(org, scope), args...).Slice()
		return err
	})
	if err != nil {
		return AgentBudgetState{}, err
	}
	return parseAgentBudgetState(reply)
}

// Reserve holds micros on the scope under holdID until holdTTL, unless the
// scope is stopped or the hold does not fit in its limit. limitMicros is the
// caller's limit for the scope (AgentBudgetNoLimit for none): the first one
// sets it, a lower one lowers it, a higher one is ignored. A hold id already
// held reuses its hold. Every key's TTL slides to scopeTTL.
func (s *AgentBudgetStore) Reserve(ctx context.Context, org, scope, holdID string, micros, limitMicros int64, holdTTL, scopeTTL time.Duration) (AgentBudgetState, error) {
	if holdID == "" {
		return AgentBudgetState{}, errBadAgentBudgetKey
	}
	now := s.now()
	return s.script(ctx, agentBudgetReserveScript, org, scope,
		holdID, max(micros, 0), now.Add(holdTTL).UnixMilli(), now.UnixMilli(), max(limitMicros, AgentBudgetNoLimit),
		ttlSeconds(scopeTTL), billing.SweepLimit)
}

// Settle releases holdID's hold, if it is still held, and adds micros to
// spent: a call settles once, and what it cost is counted even when its hold
// is gone (another call of the same attempt released it, or it expired), so
// a run budget over-counts rather than misses spend. Not idempotent by hold
// id: the caller settles each call once.
func (s *AgentBudgetStore) Settle(ctx context.Context, org, scope, holdID string, micros int64, scopeTTL time.Duration) (AgentBudgetState, error) {
	return s.script(ctx, agentBudgetSettleScript, org, scope, holdID, max(micros, 0), ttlSeconds(scopeTTL))
}

// Void releases holdID's hold, if it is still held, and adds nothing.
func (s *AgentBudgetStore) Void(ctx context.Context, org, scope, holdID string, scopeTTL time.Duration) (AgentBudgetState, error) {
	return s.script(ctx, agentBudgetSettleScript, org, scope, holdID, 0, ttlSeconds(scopeTTL))
}

// Peek reads the scope's counters and stop marker without changing them.
func (s *AgentBudgetStore) Peek(ctx context.Context, org, scope string) (AgentBudgetState, error) {
	if err := validAgentBudgetKey(org, scope); err != nil {
		return AgentBudgetState{}, err
	}
	k := s.keys(org, scope)
	var vals []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		vals, err = rdb.MGet(ctx, k[0], k[1], k[2], k[5]).Result()
		return err
	})
	if err != nil {
		return AgentBudgetState{}, err
	}
	st := AgentBudgetState{LimitMicros: AgentBudgetNoLimit, SpentMicros: asInt(vals[1]), HeldMicros: asInt(vals[2])}
	if vals[0] != nil {
		st.LimitMicros = asInt(vals[0])
	}
	if vals[3] != nil {
		st.Stopped = true
		st.StopReason, _ = vals[3].(string)
	}
	return st, nil
}

// Stop marks the scope stopped with reason for ttl: every later RESERVE on it
// is refused (the run stop of guardrails.tool_calls.stop_run_on_block).
func (s *AgentBudgetStore) Stop(ctx context.Context, org, scope, reason string, ttl time.Duration) error {
	if err := validAgentBudgetKey(org, scope); err != nil {
		return err
	}
	key := s.keys(org, scope)[5]
	return s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		return rdb.Set(ctx, key, reason, time.Duration(ttlSeconds(ttl))*time.Second).Err()
	})
}

// ttlSeconds is d in whole seconds, at least 1.
func ttlSeconds(d time.Duration) int64 { return max(int64(d/time.Second), 1) }

// parseAgentBudgetState reads a script's reply: {status, limit, spent, held,
// stopped, reason}.
func parseAgentBudgetState(reply []any) (AgentBudgetState, error) {
	if len(reply) != 6 {
		return AgentBudgetState{}, fmt.Errorf("%w: unexpected script reply %v", ErrAgentBudgetUnavailable, reply)
	}
	status, _ := reply[0].(string)
	reason, _ := reply[5].(string)
	return AgentBudgetState{Status: status, LimitMicros: asInt(reply[1]), SpentMicros: asInt(reply[2]),
		HeldMicros: asInt(reply[3]), Stopped: asInt(reply[4]) == 1, StopReason: reason}, nil
}

// agentBudgetLua is what the scripts share. KEYS: limit, spent, held, holds,
// holdexp, stopped. state() is the reply every script ends with; release()
// drops one hold; slide() moves the TTL of the counters and holds (never the
// stop marker, whose TTL is its setter's). luaInt renders a Lua number as an
// integer string: tostring would write large values in exponent form.
const agentBudgetLua = `
local function luaInt(v) return string.format('%d', v) end
local function num(k) return tonumber(redis.call('GET', k) or '0') or 0 end
local function state(status)
  local stopped, reason = 0, ''
  local stop = redis.call('GET', KEYS[6])
  if stop then stopped, reason = 1, stop end
  return {status, redis.call('GET', KEYS[1]) or '-1', luaInt(num(KEYS[2])), luaInt(num(KEYS[3])), stopped, reason}
end
local function release(id)
  redis.call('ZREM', KEYS[5], id)
  local amt = tonumber(redis.call('HGET', KEYS[4], id) or '0') or 0
  if redis.call('HDEL', KEYS[4], id) == 0 then return end
  if amt > 0 and redis.call('DECRBY', KEYS[3], luaInt(amt)) < 0 then redis.call('SET', KEYS[3], '0') end
end
local function slide(ttl)
  for i = 1, 5 do redis.call('EXPIRE', KEYS[i], ttl) end
end
`

// agentBudgetReserveScript is RESERVE: sweep up to ARGV[7] expired holds; a
// stopped scope refuses; apply the sent limit ARGV[5] (-1 for none); reuse a
// hold already held under ARGV[1], else hold ARGV[2] micros until ARGV[3]
// (ms) when it fits: refused when limit - spent <= 0 or limit - spent - held
// < micros. A hold of 0 is not recorded. ARGV[4] is now (ms), ARGV[6] the
// scope TTL (s).
var agentBudgetReserveScript = redis.NewScript(agentBudgetLua + `
for _, id in ipairs(redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', ARGV[4], 'LIMIT', 0, tonumber(ARGV[7]))) do release(id) end
if redis.call('EXISTS', KEYS[6]) == 1 then
  slide(ARGV[6])
  return state('stopped')
end
local sent = tonumber(ARGV[5])
if sent >= 0 then
  local cur = redis.call('GET', KEYS[1])
  if not cur or sent < tonumber(cur) then redis.call('SET', KEYS[1], ARGV[5]) end
end
local id, micros, status = ARGV[1], tonumber(ARGV[2]), 'ok'
if redis.call('HEXISTS', KEYS[4], id) == 1 then
  local exp = tonumber(redis.call('ZSCORE', KEYS[5], id) or '0') or 0
  if exp < tonumber(ARGV[3]) then redis.call('ZADD', KEYS[5], ARGV[3], id) end
else
  local limit = redis.call('GET', KEYS[1])
  if limit then
    local left = tonumber(limit) - num(KEYS[2])
    if left <= 0 or left - num(KEYS[3]) < micros then status = 'exhausted' end
  end
  if status == 'ok' and micros > 0 then
    redis.call('HSET', KEYS[4], id, ARGV[2])
    redis.call('ZADD', KEYS[5], ARGV[3], id)
    redis.call('INCRBY', KEYS[3], ARGV[2])
  end
end
slide(ARGV[6])
return state(status)
`)

// agentBudgetSettleScript is SETTLE and VOID: release the hold of ARGV[1] if
// it is still held, then add ARGV[2] micros to spent (0 for a void).
// ARGV[3] is the scope TTL (s).
var agentBudgetSettleScript = redis.NewScript(agentBudgetLua + `
release(ARGV[1])
if tonumber(ARGV[2]) > 0 then redis.call('INCRBY', KEYS[2], ARGV[2]) end
slide(ARGV[3])
return state('ok')
`)
