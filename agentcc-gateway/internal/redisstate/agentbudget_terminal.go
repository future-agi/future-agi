package redisstate

import (
	"context"
	"fmt"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"
)

// Strict state is durable, including terminals and the initialized marker.
// Neither a hold deadline nor an idle scope TTL proves absence of spend.
func (s *AgentBudgetStore) strictKeys(org, scope string) []string {
	keys := s.keys(org, scope)
	return append(keys, s.prefix+"abud:{"+org+"}:"+scope+":terminal", keys[0]+":strict", keys[0]+":uncertain")
}

func (s *AgentBudgetStore) strictScript(ctx context.Context, script *redis.Script, org, scope string, args ...any) (AgentBudgetState, error) {
	if validAgentBudgetKey(org, scope) != nil {
		return AgentBudgetState{}, errBadAgentBudgetKey
	}
	var out []any
	err := s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var err error
		out, err = script.Run(ctx, rdb, s.strictKeys(org, scope), args...).Slice()
		return err
	})
	if err != nil {
		return AgentBudgetState{}, err
	}
	return parseStrictAgentBudgetState(out)
}

// Complete is replay-safe. A retained uncertain reservation may settle later,
// but an absent reservation or a conflicting terminal never implies zero.
func (s *AgentBudgetStore) Complete(ctx context.Context, org, scope, nonce string, micros int64, _ time.Duration) (AgentBudgetState, error) {
	if nonce == "" || len(nonce) > 128 || micros < 0 || micros > 1_000_000_000_000_000 {
		return AgentBudgetState{}, errBadAgentBudgetKey
	}
	return s.strictScript(ctx, agentBudgetCompleteScript, org, scope, nonce, micros)
}

// ReserveStrict never invokes the legacy release-on-expiry sweep. A scope with
// an expired hold is fenced until the work settles or is proven undispatched.
func (s *AgentBudgetStore) ReserveStrict(ctx context.Context, org, scope, nonce string, micros, limit int64, holdTTL, _ time.Duration) (AgentBudgetState, error) {
	if nonce == "" || len(nonce) > 128 || micros < 0 || micros > 1_000_000_000_000_000 || limit < -1 || limit > 1_000_000_000_000_000 || holdTTL <= 0 {
		return AgentBudgetState{}, errBadAgentBudgetKey
	}
	now := s.now()
	return s.strictScript(ctx, agentBudgetStrictReserveScript, org, scope, nonce, micros, now.Add(holdTTL).UnixMilli(), now.UnixMilli(), limit)
}

// FenceStrict marks a retained reservation's outcome uncertain, refusing new
// work on the scope until an authoritative Complete. It reads no counter and
// replies with a bare acknowledgement, not a state built from counters: a
// completion refused for a malformed or lost counter can still be fenced.
func (s *AgentBudgetStore) FenceStrict(ctx context.Context, org, scope, nonce string) error {
	if nonce == "" || len(nonce) > 128 || validAgentBudgetKey(org, scope) != nil {
		return errBadAgentBudgetKey
	}
	return s.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		result, err := agentBudgetStrictFenceScript.Run(ctx, rdb, s.strictKeys(org, scope), nonce).Int64()
		if err == nil && result != 1 {
			return fmt.Errorf("invalid strict fence reply")
		}
		return err
	})
}

func parseStrictAgentBudgetState(out []any) (AgentBudgetState, error) {
	bad := fmt.Errorf("%w: invalid strict run reply", ErrAgentBudgetUnavailable)
	if len(out) != 6 {
		return AgentBudgetState{}, bad
	}
	status, ok := out[0].(string)
	if !ok || (status != AgentBudgetOK && status != AgentBudgetExhausted && status != AgentBudgetStopped && status != "uncertain" && status != "terminal") {
		return AgentBudgetState{}, bad
	}
	values := [3]int64{}
	for i := range values {
		text, ok := out[i+1].(string)
		if !ok {
			return AgentBudgetState{}, bad
		}
		n, err := strconv.ParseInt(text, 10, 64)
		if err != nil || n < 0 && (i != 0 || n != AgentBudgetNoLimit) {
			return AgentBudgetState{}, bad
		}
		values[i] = n
	}
	stopped, ok := out[4].(int64)
	reason, reasonOK := out[5].(string)
	if !ok || !reasonOK || (stopped != 0 && stopped != 1) {
		return AgentBudgetState{}, bad
	}
	return AgentBudgetState{Status: status, LimitMicros: values[0], SpentMicros: values[1], HeldMicros: values[2], Stopped: stopped == 1, StopReason: reason}, nil
}

const strictAgentBudgetLua = agentBudgetLua + capIntegerLua + capKeyTypesLua + `
local function durable()
 for _,i in ipairs({1,2,3,4,5,7,8,9}) do redis.call('PERSIST',KEYS[i]) end
end
-- KEYS: limit, spent, held, holds, holdexp, stopped, terminal, strict marker,
-- uncertainty set. Reserve, Complete and Peek touch all of them (directly,
-- through durable() or through state()); Fence checks only the keys it
-- reads or writes. A key of another Redis type, or a marker this code never
-- writes, is refused before adoption, persistence, expiry fencing, policy
-- updates or release: a later WRONGTYPE would keep the script's earlier
-- writes.
local function validStructure()
 if not validKeyTypes({'string','string','string','hash','zset','string','hash','string','set'}) then return false end
 local marker=redis.call('GET',KEYS[8])
 return not marker or marker=='1'
end
local function validCounters(required)
 if not validStructure() then return false end
 for i=1,3 do
  local value=redis.call('GET',KEYS[i])
  if not value then
   if required then return false end
  elseif not capInteger(value) and not (i==1 and value=='-1') then
   return false
  end
 end
 return true
end
local function valid()
 return redis.call('EXISTS',KEYS[8])==1 and validCounters(true)
end
-- Pending holds partition into expiring reservations and fenced uncertainty.
-- Admission checks the cardinalities in O(1), after validating key types but
-- before any write. Losing the uncertainty set (or expiry index) must not
-- reopen a retained hold. Complete deliberately does not require this check:
-- authoritative settlement can resolve the orphan without restoring its index.
-- Return the ordinary uncertainty status, not a Redis error, so repeated
-- refusal cannot open the shared client breaker and block late settlement.
-- This is not an integrity audit of arbitrary Redis corruption; persistent,
-- non-evicting Redis remains a deployment prerequisite.
local function indexedHolds()
 return redis.call('HLEN',KEYS[4])==redis.call('ZCARD',KEYS[5])+redis.call('SCARD',KEYS[9])
end
local function uncertain(id)
 if redis.call('HEXISTS',KEYS[4],id)==0 then return false end
 redis.call('SADD',KEYS[9],id)
 redis.call('ZREM',KEYS[5],id)
 return true
end
`

var agentBudgetStrictReserveScript = redis.NewScript(strictAgentBudgetLua + `
local strict=redis.call('EXISTS',KEYS[8])==1
-- Validate key types, the marker and the counters before adoption,
-- persistence, expiry fencing, or policy updates: Redis does not roll back a
-- script's writes when a later command fails.
if not validCounters(strict) then return redis.error_reply('strict run state unavailable') end
if not indexedHolds() then return state('uncertain') end
local micros=tonumber(ARGV[2])
local existing=redis.call('HGET',KEYS[4],ARGV[1])
local totalHeld=num(KEYS[3])
if existing then
 if not capInteger(existing) or tonumber(existing)>totalHeld then return redis.error_reply('invalid strict hold') end
 if existing~=ARGV[2] then return redis.error_reply('conflicting strict hold') end
elseif totalHeld>9007199254740991-micros then
 return redis.error_reply('strict run held overflow')
end
if not strict then
 -- Adopt only intact legacy counters, allowing missing first-use counters.
 redis.call('SETNX',KEYS[1],ARGV[5])
 redis.call('SETNX',KEYS[2],0)
 redis.call('SETNX',KEYS[3],0)
 redis.call('SET',KEYS[8],1)
end
durable()
for _,id in ipairs(redis.call('ZRANGEBYSCORE',KEYS[5],'-inf',ARGV[4],'LIMIT',0,64)) do uncertain(id) end
local sent=tonumber(ARGV[5])
local limit=tonumber(redis.call('GET',KEYS[1]))
if sent>=0 and (limit<0 or sent<limit) then limit=sent; redis.call('SET',KEYS[1],ARGV[5]) end
if redis.call('EXISTS',KEYS[6])==1 then return state('stopped') end
if redis.call('SCARD',KEYS[9])>0 then return state('uncertain') end
if redis.call('HEXISTS',KEYS[7],ARGV[1])==1 then return state('terminal') end
if existing then return state('ok') end
if limit>=0 and limit-num(KEYS[2])-num(KEYS[3])<micros then return state('exhausted') end
redis.call('HSET',KEYS[4],ARGV[1],ARGV[2])
redis.call('ZADD',KEYS[5],ARGV[3],ARGV[1])
redis.call('INCRBY',KEYS[3],ARGV[2])
durable()
return state('ok')
`)

var agentBudgetCompleteScript = redis.NewScript(strictAgentBudgetLua + `
if not valid() then return redis.error_reply('strict run state unavailable') end
local prior=redis.call('HGET',KEYS[7],ARGV[1])
if prior then
 if prior~=ARGV[2] then return redis.error_reply('conflicting run terminal') end
 return state('ok')
end
local held=redis.call('HGET',KEYS[4],ARGV[1])
if not held then return redis.error_reply('unknown strict run reservation') end
-- Preflight every counter release/settlement will write, including the hold
-- amount and the resulting sum. Never let legacy release() clamp bad state.
if not capInteger(held) or tonumber(held)>num(KEYS[3]) then return redis.error_reply('invalid strict hold') end
if num(KEYS[2])>9007199254740991-tonumber(ARGV[2]) then return redis.error_reply('strict run spent overflow') end
release(ARGV[1])
redis.call('SREM',KEYS[9],ARGV[1])
if tonumber(ARGV[2])>0 then redis.call('INCRBY',KEYS[2],ARGV[2]) end
redis.call('HSET',KEYS[7],ARGV[1],ARGV[2])
durable()
return state('ok')
`)

// Fence reads only the holds and terminals, writes only the uncertainty set
// and the expiry index, and persists the scope (PERSIST takes any type). It
// checks those four keys' types before any write, but never reads limit,
// spent, held, the stop reason or the adoption marker: the retained hold is
// fenced even when they were corrupted or lost. Every other script still
// refuses that state, and once it is repaired the next reservation refuses
// ('uncertain') until the authoritative Complete resolves the hold.
var agentBudgetStrictFenceScript = redis.NewScript(strictAgentBudgetLua + `
if not validKeyTypes({'any','any','any','hash','zset','any','hash','any','set'}) then return redis.error_reply('strict run state unavailable') end
if redis.call('HEXISTS',KEYS[7],ARGV[1])==1 then return 1 end
if not uncertain(ARGV[1]) then return redis.error_reply('unknown strict run reservation') end
durable()
return 1
`)

// PeekStrict refuses malformed or uncertain strict state before routing. The
// atomic reservation remains the authority; this never releases expired work.
func (s *AgentBudgetStore) PeekStrict(ctx context.Context, org, scope string) (AgentBudgetState, error) {
	return s.strictScript(ctx, agentBudgetStrictPeekScript, org, scope, s.now().UnixMilli())
}

var agentBudgetStrictPeekScript = redis.NewScript(strictAgentBudgetLua + `
if not validCounters(redis.call('EXISTS',KEYS[8])==1) then return redis.error_reply('strict run state unavailable') end
if not indexedHolds() or redis.call('SCARD',KEYS[9])>0 or redis.call('ZCOUNT',KEYS[5],'-inf',ARGV[1])>0 then return state('uncertain') end
if redis.call('EXISTS',KEYS[6])==1 then return state('stopped') end
return state('ok')
`)
