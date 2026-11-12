package redisstate

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// CapBucket counts model spend (including BYOK) by UTC admission day. Activation
// is assigned by the central policy writer, never by a request or a cap edit.
type CapBucket struct {
	Level       string `json:"level"`
	Limit       int64  `json:"limit"`
	Amount      int64  `json:"amount"`
	ActivatedAt string `json:"activated_at,omitempty"`
}
type CapHoldResult struct {
	Status, Level       string
	Available, Required int64
}

// CapHoldStore is the authoritative durable admission ledger, not a cache of
// request logs. It imports no history. Policy identity is org + level (not limit
// or config version); activation and daily manifests survive all cap edits.
// These keys MUST use the same durable, non-evicting Redis as wallet holds.
type CapHoldStore struct{ store *AgentBudgetStore }

func NewCapHoldStore(client *Client, prefix string) *CapHoldStore {
	return &CapHoldStore{NewAgentBudgetStore(client, prefix)}
}
func (s *BudgetStore) CapHolds() *CapHoldStore { return NewCapHoldStore(s.client, s.prefix) }
func (s *CapHoldStore) keys(org, day string) ([]string, error) {
	if validOrg(org) != nil {
		return nil, errBadAgentBudgetKey
	}
	if _, err := time.Parse("2006-01-02", day); err != nil {
		return nil, err
	}
	base := s.store.prefix + "bhold:{" + org + "}:" + day + ":"
	return []string{base + "state", base + "holds", base + "expiry", base + "terminal", s.store.prefix + "bhold:{" + org + "}:policies", base + "uncertain"}, nil
}

func (s *CapHoldStore) Reserve(ctx context.Context, org, day, nonce string, revision int, buckets []CapBucket, until time.Time) (CapHoldResult, error) {
	keys, err := s.keys(org, day)
	if err != nil {
		return CapHoldResult{}, err
	}
	if nonce == "" || revision < 1 || len(buckets) < 1 || len(buckets) > 2 || !until.After(time.Now()) {
		return CapHoldResult{}, errBadAgentBudgetKey
	}
	seen := make(map[string]bool)
	for _, b := range buckets {
		activation, e := time.Parse(time.RFC3339Nano, b.ActivatedAt)
		if e != nil || activation.After(time.Now()) || activation.UTC().Format("2006-01-02") > day || b.Level == "" || len(b.Level) > 256 || seen[b.Level] {
			return CapHoldResult{}, fmt.Errorf("cap activation or bucket identity unavailable")
		}
		seen[b.Level] = true
		if b.Amount < 0 || b.Limit < 0 || b.Amount > 1_000_000_000_000_000 || b.Limit > 1_000_000_000_000_000 {
			return CapHoldResult{}, errBadAgentBudgetKey
		}
	}
	raw, err := json.Marshal(buckets)
	if err != nil {
		return CapHoldResult{}, err
	}
	var out []any
	err = s.store.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		var e error
		out, e = capReserveScript.Run(ctx, rdb, keys, nonce, revision, string(raw), until.UnixMilli(), time.Now().UnixMilli()).Slice()
		return e
	})
	if err != nil {
		return CapHoldResult{}, err
	}
	if len(out) != 4 {
		return CapHoldResult{}, fmt.Errorf("invalid cap hold result")
	}
	status, statusOK := out[0].(string)
	level, levelOK := out[1].(string)
	available, availableOK := out[2].(int64)
	required, requiredOK := out[3].(int64)
	if !statusOK || !levelOK || !availableOK || !requiredOK {
		return CapHoldResult{}, fmt.Errorf("invalid cap hold reply types")
	}
	switch status {
	case "ok", "exhausted", "unavailable", "stale", "terminal":
		return CapHoldResult{status, level, available, required}, nil
	default:
		return CapHoldResult{}, fmt.Errorf("unknown cap hold reply status")
	}
}

// Complete is idempotent by the server nonce. Conflicting terminals are refused
// rather than silently changing a void into spend or vice versa. Terminal
// records are durable; expiry is never permission to forget possible spend.
func (s *CapHoldStore) Complete(ctx context.Context, org, day, nonce string, actual []CapBucket) error {
	keys, err := s.keys(org, day)
	if err != nil {
		return err
	}
	raw, err := json.Marshal(actual)
	if err != nil {
		return err
	}
	if nonce == "" || len(actual) == 0 || len(actual) > 2 {
		return errBadAgentBudgetKey
	}
	for _, b := range actual {
		if b.Amount < 0 || b.Amount > 1_000_000_000_000_000 {
			return errBadAgentBudgetKey
		}
	}
	return s.store.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		result, err := capCompleteScript.Run(ctx, rdb, keys, nonce, string(raw)).Int64()
		if err == nil && result != 1 {
			return fmt.Errorf("invalid cap terminal reply")
		}
		return err
	})
}

const capLua = `
local function num(f) return tonumber(redis.call('HGET',KEYS[1],f) or '0') end
local function slide() for i=1,#KEYS do redis.call('PERSIST',KEYS[i]) end end
local function fence(id)
 local raw=redis.call('HGET',KEYS[2],id)
 if not raw then return false end
 if redis.call('HSETNX',KEYS[6],id,1)==1 then
  for _,b in ipairs(cjson.decode(raw)) do redis.call('HINCRBY',KEYS[1],'uncertain:'..b.level,1) end
 end
 redis.call('ZREM',KEYS[3],id)
 return true
end
local function release(id)
 local raw=redis.call('HGET',KEYS[2],id)
 if raw then
  local fenced=redis.call('HDEL',KEYS[6],id)
  for _,b in ipairs(cjson.decode(raw)) do
   redis.call('HINCRBY',KEYS[1],'held:'..b.level,-b.amount)
   if fenced==1 then redis.call('HINCRBY',KEYS[1],'uncertain:'..b.level,-1) end
  end
  redis.call('HDEL',KEYS[2],id)
 end
 redis.call('ZREM',KEYS[3],id)
 return raw
end
`

var capReserveScript = redis.NewScript(capLua + capAdmissionValidationLua + `
local buckets=cjson.decode(ARGV[3])
-- Redis script errors do not roll back prior writes. Validate every key type,
-- counter and policy used by admission or its expiry sweep before any
-- mutation, including the nonce-replay path. Malformed values are never
-- repaired.
if not validCapTypes() then return {'unavailable','',0,0} end
local expired=redis.call('ZRANGEBYSCORE',KEYS[3],'-inf',ARGV[5],'LIMIT',0,64)
if not validCapGlobal() then return {'unavailable','',0,0} end
for _,b in ipairs(buckets) do
 if not validCapBucket(b.level) then return {'unavailable',b.level,0,b.amount} end
end
if not validCapField(KEYS[6],ARGV[1]) then return {'unavailable','',0,0} end
for _,id in ipairs(expired) do
 if not validCapExpired(id) then return {'unavailable','',0,0} end
end
for _,b in ipairs(buckets) do
 if not validCapUncertainty(b.level) then return {'unavailable',b.level,0,b.amount} end
end
local existing=redis.call('HGET',KEYS[2],ARGV[1])
if existing and existing~=ARGV[3] then return redis.error_reply('conflicting cap reservation') end
local version=tonumber(ARGV[2])
local previousVersion=tonumber(redis.call('HGET',KEYS[5],'revision') or '0')
local dayVersion=num('version')
-- The global fence outlives all day buckets. The local version comparison
-- also refuses stale callers while adopting a pre-revision day ledger.
if version<math.max(previousVersion,dayVersion) then return {'stale','',0,0} end
-- Activation identity is part of the preflight: a mismatched or malformed
-- stored activation is refused before the expiry sweep and the replay path.
for _,b in ipairs(buckets) do
 local activation=redis.call('HGET',KEYS[5],'activation:'..b.level)
 if activation and activation~=b.activated_at then return {'unavailable',b.level,0,b.amount} end
end
-- Expiry fences a valid reservation, never settles or releases it.
for _,id in ipairs(expired) do fence(id) end
if redis.call('HEXISTS',KEYS[4],ARGV[1])==1 then return {'terminal','',0,0} end
if existing then
 if redis.call('HEXISTS',KEYS[6],ARGV[1])==1 then return {'unavailable','',0,0} end
 return {'ok','',0,0}
end
-- The durable policy registry distinguishes a first bucket from lost state.
-- Initialize every policy at zero, without a history import, atomically with
-- admission. A limit/version update never creates a new policy identity.
for _,b in ipairs(buckets) do
 redis.call('HSETNX',KEYS[5],'activation:'..b.level,b.activated_at)
 redis.call('HSETNX',KEYS[5],KEYS[1]..':'..b.level,1)
 redis.call('HSETNX',KEYS[1],'spent:'..b.level,0)
 redis.call('HSETNX',KEYS[1],'held:'..b.level,0)
end
redis.call('HSET',KEYS[5],'revision',ARGV[2],'schema',2)
redis.call('HSET',KEYS[1],'version',ARGV[2])
for _,b in ipairs(buckets) do
 local limit=b.limit
 local prior=tonumber(redis.call('HGET',KEYS[5],'revision:'..b.level) or '0')
 local ceiling=redis.call('HGET',KEYS[5],'limit:'..b.level)
 if prior==0 and version==dayVersion and redis.call('HEXISTS',KEYS[1],'limit:'..b.level)==1 then limit=math.min(limit,num('limit:'..b.level)) end
 if version==prior and ceiling then limit=math.min(limit,tonumber(ceiling)) end
 redis.call('HSET',KEYS[5],'revision:'..b.level,ARGV[2],'limit:'..b.level,limit,'policy:'..b.level,1)
 redis.call('HSET',KEYS[1],'limit:'..b.level,limit)
end
slide()
for _,b in ipairs(buckets) do
 if num('uncertain:'..b.level)>0 or num('overshot:'..b.level)>0 then return {'unavailable',b.level,0,b.amount} end
 local available=num('limit:'..b.level)-num('spent:'..b.level)-num('held:'..b.level)
 if available<b.amount then return {'exhausted',b.level,available,b.amount} end
end
redis.call('HSET',KEYS[1],'version',ARGV[2])
for _,b in ipairs(buckets) do
 redis.call('HINCRBY',KEYS[1],'held:'..b.level,b.amount)
end
redis.call('HSET',KEYS[2],ARGV[1],ARGV[3])
redis.call('ZADD',KEYS[3],ARGV[4],ARGV[1])
slide()
return {'ok','',0,0}
`)
var capCompleteScript = redis.NewScript(capLua + capAdmissionValidationLua + `
-- Redis keeps a script's earlier writes when a later command fails. Preflight
-- every key type, record and counter that release and settlement read or
-- write before the first write. A refusal leaves the reservation, its expiry
-- entry and any fence intact, so Fence and a later authoritative Complete of
-- the same nonce still work.
if not validCapTypes() then return redis.error_reply('cap state unavailable') end
local prev=redis.call('HGET',KEYS[4],ARGV[1])
if prev then
 if prev~=ARGV[2] then return redis.error_reply('conflicting cap terminal') end
 return 1
end
local held=redis.call('HGET',KEYS[2],ARGV[1])
if not held then return redis.error_reply('unknown cap reservation') end
local expected=capRecordBuckets(held)
if not expected then return redis.error_reply('invalid cap reservation') end
local actual=capRecordBuckets(ARGV[2])
if not actual or #actual~=#expected then return redis.error_reply('incomplete cap terminal') end
local estimates={}
for _,b in ipairs(expected) do estimates[b.level]=b.amount end
local charges={}
for _,b in ipairs(actual) do
 if estimates[b.level]==nil then return redis.error_reply('invalid cap terminal bucket') end
 charges[b.level]=b.amount
end
if not validCapField(KEYS[6],ARGV[1]) then return redis.error_reply('cap counters unavailable') end
local fenced=redis.call('HEXISTS',KEYS[6],ARGV[1])==1
for _,b in ipairs(expected) do
 if not validCapBucket(b.level) then return redis.error_reply('cap counters unavailable') end
 local spent=redis.call('HGET',KEYS[1],'spent:'..b.level)
 local totalHeld=redis.call('HGET',KEYS[1],'held:'..b.level)
 if not capInteger(spent) or not capInteger(totalHeld) or tonumber(totalHeld)<b.amount then return redis.error_reply('cap counters unavailable') end
 -- release() decrements a fenced reservation's uncertainty once.
 if fenced and num('uncertain:'..b.level)<1 then return redis.error_reply('cap counters unavailable') end
 if tonumber(spent)>9007199254740991-charges[b.level] then return redis.error_reply('cap spent overflow') end
end
release(ARGV[1])
for _,b in ipairs(actual) do
 if b.amount>0 then redis.call('HINCRBY',KEYS[1],'spent:'..b.level,b.amount) end
 if b.amount>estimates[b.level] then redis.call('HSET',KEYS[1],'overshot:'..b.level,1) end
end
redis.call('HSET',KEYS[4],ARGV[1],ARGV[2])
slide()
return 1
`)

// Fence keeps the qualified reservation and explicitly records its uncertain
// outcome. It is replay-safe and can be resolved by a later Complete. A
// repeated Fence also re-establishes a lost or under-counted uncertainty
// counter from the retained fence records.
func (s *CapHoldStore) Fence(ctx context.Context, org, day, nonce string) error {
	keys, err := s.keys(org, day)
	if err != nil {
		return err
	}
	if nonce == "" {
		return errBadAgentBudgetKey
	}
	return s.store.run(ctx, func(ctx context.Context, rdb redis.UniversalClient) error {
		result, err := capFenceScript.Run(ctx, rdb, keys, nonce).Int64()
		if err == nil && result != 1 {
			return fmt.Errorf("invalid cap fence reply")
		}
		return err
	})
}

var capFenceScript = redis.NewScript(capLua + capAdmissionValidationLua + `
if not validCapFenceTypes() then return redis.error_reply('cap state unavailable') end
if redis.call('HEXISTS',KEYS[4],ARGV[1])==1 then return 1 end
local raw=redis.call('HGET',KEYS[2],ARGV[1])
if not raw then return redis.error_reply('unknown cap reservation') end
-- Fence writes only the uncertainty fence, its buckets' uncertainty counters
-- and the expiry index. It never reads spend, so a reservation whose
-- completion was refused for a malformed spend counter can still be fenced.
-- Preflight what it writes.
local buckets=capRecordBuckets(raw)
if not buckets or not validCapField(KEYS[6],ARGV[1]) then return redis.error_reply('invalid cap reservation') end
local refence=redis.call('HEXISTS',KEYS[6],ARGV[1])==1
for _,b in ipairs(buckets) do
 if not validCapField(KEYS[1],'uncertain:'..b.level) then return redis.error_reply('cap counters unavailable') end
 if not refence and num('uncertain:'..b.level)>=9007199254740991 then return redis.error_reply('cap counters unavailable') end
end
fence(ARGV[1])
-- A counter below the fence records covering its bucket was lost or
-- under-counted: raise it to that count (never lower it), so the fence is
-- effective and each later Complete lifts exactly its own share.
local evidence=capFenceEvidence(false)
for _,b in ipairs(buckets) do
 local required=math.max(evidence[b.level] or 0,1)
 if num('uncertain:'..b.level)<required then redis.call('HSET',KEYS[1],'uncertain:'..b.level,required) end
end
slide()
return 1
`)
