package redisstate

import "github.com/redis/go-redis/v9"

// Merge only immutable attempt identities, updating completed evidence from the
// request snapshot. Called inside the money transaction before writing anything.
const mergeOwnedJournal = `
local record = cjson.decode(recordJSON)
for id, prior in pairs(record.ManagedJournal or {}) do
 local current = o.attempts[id]
 if current then
  if current.model ~= prior.model or current.micros ~= prior.micros or (current.token_priced or false) ~= (prior.token_priced or false) then return redis.error_reply('journal attempt conflict') end
  if prior.anomaly then current.anomaly = prior.anomaly end
  if prior.summary then current.summary = prior.summary end
  if prior.evidence then current.evidence = prior.evidence end
 else
  o.attempts[id] = prior
 end
 if prior.dispatched then o.dispatched = true end
end
local journalHold = 0
for _, attempt in pairs(o.attempts) do journalHold = journalHold + attempt.micros end
o.legacy_micros = math.max(0, heldBefore - journalHold)
o.record_json = recordJSON
`

// All keys share {org}. Ownership is tested inside the SAME script as expiry,
// cleanup or settlement. No process-local lock is part of the money protocol.
// KEYS: acct, holds, holdexp, settled, outbox, owned, quarantine, recovery, pending.
var ownedReserveScript = redis.NewScript(luaState + `
local raw = redis.call('GET', KEYS[6])
local o = raw and cjson.decode(raw) or {attempts={},dispatched=false}
if o.terminal or o.finalizing or redis.call('EXISTS',KEYS[4]) == 1 then return redis.error_reply('terminal request') end
if redis.call('HEXISTS',KEYS[7],ARGV[3]) == 1 then return redis.error_reply('listing quarantined') end
local prior = o.attempts[ARGV[2]]
if prior then
 if prior.micros ~= tonumber(ARGV[4]) or prior.model ~= ARGV[3] or (prior.token_priced or false) ~= (ARGV[8] == "1") then return redis.error_reply('attempt conflict') end
 if not prior.dispatched and (prior.expires <= tonumber(ARGV[5]) or redis.call('HEXISTS',KEYS[2],ARGV[1]) == 0) then return redis.error_reply('expired attempt') end
 return state('ok')
end
if redis.call('EXISTS',KEYS[1]) == 0 then return state('no_account') end
local expired = redis.call('ZRANGEBYSCORE',KEYS[3],'-inf',ARGV[5],'LIMIT',0,50)
for _, id in ipairs(expired) do release(id) end
local a = redis.call('HMGET',KEYS[1],'granted','spent','held')
local left = tonumber(a[1]) - tonumber(a[2] or '0') + tonumber(ARGV[7])
local held, amount = tonumber(a[3] or '0'), tonumber(ARGV[4])
if left <= 0 then return state('exhausted') end
if left-held < amount then
 if left >= amount then return state('in_flight') end
 return state('insufficient')
end
local recordJSON = ARGV[9]
local heldBefore = tonumber(redis.call('HGET',KEYS[2],ARGV[1]) or '0')
` + mergeOwnedJournal + `
o.attempts[ARGV[2]] = {model=ARGV[3],micros=amount,expires=tonumber(ARGV[6]),dispatched=false,token_priced=ARGV[8] == "1"}
redis.call('HINCRBY',KEYS[2],ARGV[1],ARGV[4])
redis.call('HINCRBY',KEYS[1],'held',ARGV[4])
if not o.dispatched then redis.call('ZADD',KEYS[3],ARGV[6],ARGV[1]) else redis.call('ZREM',KEYS[3],ARGV[1]); redis.call('ZADD',KEYS[8],ARGV[10],ARGV[1]) end
redis.call('SET',KEYS[6],cjson.encode(o))
return state('ok')
`)

var ownedBeginScript = redis.NewScript(`
local raw = redis.call('GET',KEYS[1])
if not raw then return redis.error_reply('missing ownership') end
local o = cjson.decode(raw)
local a = o.attempts[ARGV[2]]
if o.terminal or o.finalizing or not a then return redis.error_reply('terminal or missing attempt') end
if a.dispatched then return 1 end
if a.expires <= tonumber(ARGV[3]) or redis.call('HEXISTS',KEYS[2],ARGV[1]) == 0 then return redis.error_reply('expired hold') end
if redis.call('HEXISTS',KEYS[5],a.model) == 1 then return redis.error_reply('listing quarantined') end
local recordJSON = ARGV[4]
local heldBefore = tonumber(redis.call('HGET',KEYS[2],ARGV[1]) or '0')
` + mergeOwnedJournal + `
if record.InclusiveBindings then a.binding=record.InclusiveBindings[ARGV[2]] end
a.dispatched, o.dispatched = true, true
o.record_json = ARGV[4]
redis.call('SET',KEYS[1],cjson.encode(o))
redis.call('ZREM',KEYS[3],ARGV[1])
redis.call('ZADD',KEYS[4],ARGV[5],ARGV[1])
return 1
`)

// The prepare and financial phases enforce the same identity, evidence and CAS
// checks. Reject malformed records BEFORE making them authoritative for recovery.
const validateOwnedFinish = `
if ARGV[7] ~= '' and ARGV[7] ~= raw then return redis.error_reply('recovery raced request') end
if ARGV[4] == 'void' and o.dispatched then return redis.error_reply('dispatched hold cannot void') end
if ARGV[4] == 'spend' and not o.dispatched then return redis.error_reply('undispatched hold cannot spend') end
local record = cjson.decode(ARGV[5])
local models = cjson.decode(ARGV[6])
if record.OrgID ~= ARGV[8] or record.RequestID ~= ARGV[1] or tonumber(ARGV[2]) < 0 or tonumber(ARGV[2]) > 1125899906842624 then return redis.error_reply('invalid spend identity or amount') end
if o.dispatched then
 if not record.Dimensional or record.Dimensional == cjson.null then return redis.error_reply('missing owned evidence') end
 local seen, total = {}, 0
 for _, evidence in ipairs(record.Dimensional.inclusive_attempts or {}) do
  local a = o.attempts[evidence.attempt_id]
  if seen[evidence.attempt_id] or not a or not a.dispatched or a.token_priced or a.model ~= evidence.model or a.micros ~= evidence.hold_micros or evidence.billed_micros < 0 then return redis.error_reply('attempt evidence conflict') end
  if evidence.estimated and evidence.billed_micros ~= a.micros then return redis.error_reply('estimate must consume full hold') end
  if a.binding then
   local b=record.InclusiveBindings and record.InclusiveBindings[evidence.attempt_id]
   if not b or b.Provider~=a.binding.Provider or b.Credential~=a.binding.Credential or b.Scope~=a.binding.Scope or b.SnapshotModels~=a.binding.SnapshotModels then return redis.error_reply('attempt credential binding conflict') end
  end
  seen[evidence.attempt_id] = true; total = total + evidence.billed_micros
 end
 for _, evidence in ipairs(type(record.Attempts) == "table" and record.Attempts or {}) do
  local a = o.attempts[evidence.attempt_id]
  if a and a.token_priced and a.dispatched then
   if seen[evidence.attempt_id] or evidence.model ~= a.model or evidence.credential_type ~= 'managed' or evidence.billed_micros < 0 then return redis.error_reply('token attempt evidence conflict') end
   if a.summary and evidence.billed_micros < a.summary.billed_micros then return redis.error_reply('prior charge lost') end
   seen[evidence.attempt_id] = true; total = total + evidence.billed_micros
  end
 end
 for id, a in pairs(o.attempts) do if a.dispatched and not seen[id] then return redis.error_reply('missing dispatched evidence') end end
 if total > tonumber(ARGV[2]) then return redis.error_reply('attempt sum exceeds spend') end
end
`

// Prepare freezes the completed record and final-hop journal in the org slot
// before the cross-slot catalogue seal. It writes no spend or outbox event.
// The finalizing fingerprint blocks later admission/evidence mutation, makes
// retries idempotent and lets recovery replay the exact known provider charges.
var ownedPrepareFinishScript = redis.NewScript(`
local raw = redis.call('GET',KEYS[6])
if not raw then return '' end
local o = cjson.decode(raw)
if o.terminal then
 if o.terminal ~= ARGV[3] then return redis.error_reply('terminal conflict') end
 return raw
end
` + validateOwnedFinish + `
if o.finalizing then
 if o.finalizing ~= ARGV[3] then return redis.error_reply('finalizing conflict') end
 return raw
end
local recordJSON = ARGV[5]
local heldBefore = tonumber(redis.call('HGET',KEYS[2],ARGV[1]) or '0')
` + mergeOwnedJournal + `
o.finalizing = ARGV[3]
local saved = cjson.encode(o)
redis.call('SET',KEYS[6],saved)
for _, model in ipairs(models) do redis.call('HSET',KEYS[7],model,ARGV[1]) end
return saved
`)

// Only this phase releases money and writes the v1 event, after the seal. The
// prepared bytes are the CAS fence even for the live writer, not just recovery.
var ownedFinishScript = redis.NewScript(luaState + `
local raw = redis.call('GET',KEYS[6])
if not raw then return {'legacy'} end
local o = cjson.decode(raw)
if o.terminal then
 if o.terminal ~= ARGV[3] then return redis.error_reply('terminal conflict') end
 return state('duplicate')
end
` + validateOwnedFinish + `
if o.finalizing ~= ARGV[3] then return redis.error_reply('missing finalizing record') end
release(ARGV[1])
local status = 'ok'
if redis.call('EXISTS',KEYS[1]) == 0 then status = 'ok_missing'
elseif tonumber(ARGV[2]) > 0 then redis.call('HINCRBY',KEYS[1],'spent',ARGV[2]) end
redis.call('XADD',KEYS[5],'*',unpack(ARGV,9))
o.terminal, o.record_json = ARGV[3],ARGV[5]
redis.call('SET',KEYS[6],cjson.encode(o))
redis.call('SET',KEYS[4],ARGV[3])
redis.call('ZREM',KEYS[8],ARGV[1])
if ARGV[4] == 'spend' then redis.call('SADD',KEYS[9],ARGV[1]) end
for _, model in ipairs(models) do redis.call('HSET',KEYS[7],model,ARGV[1]) end
return state(status)
`)
