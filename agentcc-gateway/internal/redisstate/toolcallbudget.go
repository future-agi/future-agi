package redisstate

import (
	"context"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
)

// ToolCallBudgetStore is shared by all MCP replicas. Calls count on MC.6's
// run counter mtb:{<org>}:<scope>:calls, the raw canonical scope, which V6's
// REST capability calls also count on (GE MA5: one free-tool call budget per
// run). M19 stores per-tool counters and per-call holds as fields in the
// run's :state hash, not independently expiring keys. Field identities keep
// their :tool:<digest> and :hold:<digest> suffixes. Holds consume capacity
// before dispatch; a crashed or ambiguous call retains its count rather
// than opening capacity for a call that might have executed.
//
// Expiry: MCP and REST atomically slide the run's calls/searches counters
// and its entire state hash to one Redis-clock deadline, after
// routing.agents.budget_scope_ttl_seconds of run idleness (86400 s default).
// Activity on any tool preserves every per-tool ceiling and replay record.
// A genuinely idle run starts afresh; there is no scheduled reset
// (refusals carry resets_at null). No per-call key persists after that run.
type ToolCallBudgetStore struct{ client *Client }

func NewToolCallBudgetStore(client *Client) *ToolCallBudgetStore { return &ToolCallBudgetStore{client} }

type ToolCallLimit struct {
	Key   string
	Limit int64
}

func (s *ToolCallBudgetStore) do(ctx context.Context, fn func(context.Context, redis.UniversalClient) error) error {
	if s == nil || s.client == nil {
		return errors.New("tool limits unavailable")
	}
	ctx, cancel := context.WithTimeout(ctx, 500*time.Millisecond)
	defer cancel()
	return s.client.Do(func(r redis.UniversalClient) error { return fn(ctx, r) })
}

// Rate checks all matching ceilings atomically. Redis owns the clock/window;
// replicas cannot overshoot or extend a window by disagreeing about time.
var toolRate = redis.NewScript(`
for i,key in ipairs(KEYS) do
 local used=tonumber(redis.call('GET',key) or '0')
 if used >= tonumber(ARGV[i]) then
  return {i, math.max(1,math.ceil(redis.call('PTTL',key)/1000))}
 end
end
for i,key in ipairs(KEYS) do
 local n=redis.call('INCR',key)
 if n==1 then redis.call('PEXPIRE',key,60000) end
end
return {0,0}
`)

func (s *ToolCallBudgetStore) Rate(ctx context.Context, limits []ToolCallLimit) (index int, retry int64, err error) {
	keys, args := limitArgs(limits)
	if len(keys) == 0 {
		return -1, 0, nil
	}
	index = -1
	err = s.do(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		values, e := toolRate.Run(ctx, r, keys, args...).Int64Slice()
		if e == nil {
			index = int(values[0]) - 1
			retry = values[1]
		}
		return e
	})
	return
}

// toolReserve: KEYS are calls, searches, state; ARGV are hold field, TTL,
// run limit, then pairs of per-tool field and limit. Only matching fields
// count, but the whole run shares one expiry regardless of the tool called.
var toolReserve = redis.NewScript(toolScopeExpiryLua + `
expireScope(ARGV[2])
if redis.call('HEXISTS',KEYS[3],ARGV[1])==1 then return {-2,0} end
local used=tonumber(redis.call('GET',KEYS[1]) or '0')
local lim=tonumber(ARGV[3])
if lim>=0 and used>=lim then return {0,0} end
local remaining=-1
if lim>=0 then remaining=lim-used-1 end
for i=4,#ARGV,2 do
 used=tonumber(redis.call('HGET',KEYS[3],ARGV[i]) or '0')
 lim=tonumber(ARGV[i+1])
 if lim>=0 and used>=lim then return {(i-2)/2,0} end
 if lim>=0 and (remaining<0 or lim-used-1<remaining) then remaining=lim-used-1 end
end
redis.call('INCR',KEYS[1])
for i=4,#ARGV,2 do redis.call('HINCRBY',KEYS[3],ARGV[i],1) end
redis.call('HSET',KEYS[3],ARGV[1],'held')
expireScope(ARGV[2])
return {-1,remaining}
`)

// Reserve rejects reuse of a hold id. -1 is admitted, a nonnegative index
// names the exhausted ceiling, and -2 is a replay. Limit -1 is unlimited.
// limits[0] is always the canonical run counter, even without a run limit.
func (s *ToolCallBudgetStore) Reserve(ctx context.Context, hold string, limits []ToolCallLimit, ttl time.Duration) (index int, remaining int64, err error) {
	if len(limits) == 0 {
		return -1, 0, errors.New("tool limits require a run counter")
	}
	keys := toolScopeKeys(limits[0].Key)
	args := []any{hold, ttlSeconds(ttl), limits[0].Limit}
	for _, limit := range limits[1:] {
		args = append(args, limit.Key, limit.Limit)
	}
	err = s.do(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		values, e := toolReserve.Run(ctx, r, keys, args...).Int64Slice()
		if e == nil {
			index = int(values[0])
			remaining = values[1]
		}
		return e
	})
	return
}

// toolFinish: KEYS as toolReserve; ARGV are hold field, settled or void,
// TTL, then matching per-tool fields. Replay state lives until run expiry.
var toolFinish = redis.NewScript(toolScopeExpiryLua + `
if redis.call('HGET',KEYS[3],ARGV[1])~='held' then return 0 end
if ARGV[2]=='void' then
 if tonumber(redis.call('GET',KEYS[1]) or '0')>0 then redis.call('DECR',KEYS[1]) end
 for i=4,#ARGV do
  if tonumber(redis.call('HGET',KEYS[3],ARGV[i]) or '0')>0 then
   redis.call('HINCRBY',KEYS[3],ARGV[i],-1)
  end
 end
end
redis.call('HSET',KEYS[3],ARGV[1],ARGV[2])
expireScope(ARGV[3])
return 1
`)

func (s *ToolCallBudgetStore) Finish(ctx context.Context, hold string, limits []ToolCallLimit, executed bool, ttl time.Duration) error {
	if len(limits) == 0 {
		return errors.New("tool limits require a run counter")
	}
	keys := toolScopeKeys(limits[0].Key)
	state := "void"
	if executed {
		state = "settled"
	}
	args := []any{hold, state, ttlSeconds(ttl)}
	for _, limit := range limits[1:] {
		args = append(args, limit.Key)
	}
	return s.do(ctx, func(ctx context.Context, r redis.UniversalClient) error {
		return toolFinish.Run(ctx, r, keys, args...).Err()
	})
}
func limitArgs(limits []ToolCallLimit) ([]string, []any) {
	keys := make([]string, len(limits))
	args := make([]any, len(limits))
	for i, l := range limits {
		keys[i] = l.Key
		args[i] = l.Limit
	}
	return keys, args
}
