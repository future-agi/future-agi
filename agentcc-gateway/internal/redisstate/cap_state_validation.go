package redisstate

// Both cap ledgers use the same integer grammar. Lua numbers are doubles:
// accept only canonical, exactly representable nonnegative integers, not
// strings tonumber coerces (fractions, exponents, whitespace, NaN), or rounded
// large counters.
const capIntegerLua = `
local function capInteger(raw)
 if type(raw)~='string' or not string.match(raw,'^%d+$') then return false end
 if #raw>1 and string.sub(raw,1,1)=='0' then return false end
 local n=tonumber(raw)
 return n and n>=0 and n<=9007199254740991 and n==math.floor(n)
end
`

// Redis keeps a script's earlier writes when a later command fails, including
// a WRONGTYPE error. Each ledger script names the Redis type of every key it
// reads or writes ('any' for a key it only persists) and refuses any other
// existing type before its first write.
const capKeyTypesLua = `
local function validKeyTypes(types)
 for i,expected in ipairs(types) do
  local reply=redis.call('TYPE',KEYS[i])
  local actual=reply
  if type(reply)=='table' then actual=reply.ok end
  if expected~='any' and actual~='none' and actual~=expected then return false end
 end
 return true
end
`

// Admission validates before any mutation. Missing optional fields support
// first activation and pre-revision ledgers; a durable manifest makes
// spent/held and the policy's revision/limit mandatory. Completion and fencing
// share these checks so a refusal leaves the reservation intact.
const capAdmissionValidationLua = capIntegerLua + capKeyTypesLua + `
-- KEYS: state, holds, expiry, terminal, policies, fences.
local function validCapTypes()
 return validKeyTypes({'hash','hash','zset','hash','hash','hash'})
end
-- Fence reads no policy; it only persists the registry.
local function validCapFenceTypes()
 return validKeyTypes({'hash','hash','zset','hash','any','hash'})
end
local function validCapField(key,field)
 local raw=redis.call('HGET',key,field)
 return not raw or capInteger(raw)
end
local function validCapGlobal()
 if not validCapField(KEYS[1],'version') or not validCapField(KEYS[5],'revision') or not validCapField(KEYS[5],'schema') then return false end
 if redis.call('HEXISTS',KEYS[5],'schema')==1 and redis.call('HEXISTS',KEYS[5],'revision')==0 then return false end
 return true
end
local function validCapBucket(level)
 for _,prefix in ipairs({'spent:','held:','uncertain:','overshot:','limit:'}) do
  if not validCapField(KEYS[1],prefix..level) then return false end
 end
 for _,prefix in ipairs({'revision:','limit:','policy:'}) do
  if not validCapField(KEYS[5],prefix..level) then return false end
 end
 local manifest=KEYS[1]..':'..level
 if not validCapField(KEYS[5],manifest) then return false end
 if redis.call('HEXISTS',KEYS[5],manifest)==1 and (redis.call('HEXISTS',KEYS[1],'spent:'..level)==0 or redis.call('HEXISTS',KEYS[1],'held:'..level)==0) then return false end
 if redis.call('HEXISTS',KEYS[5],'policy:'..level)==1 and (redis.call('HEXISTS',KEYS[5],'revision:'..level)==0 or redis.call('HEXISTS',KEYS[5],'limit:'..level)==0) then return false end
 return true
end
-- A reservation or terminal record: one or two distinct named buckets, each
-- with an exact nonnegative integer amount within the Go-side bound.
local function capRecordBuckets(raw)
 if type(raw)~='string' then return nil end
 local ok,buckets=pcall(cjson.decode,raw)
 if not ok or type(buckets)~='table' or #buckets<1 or #buckets>2 then return nil end
 local seen={}
 for _,b in ipairs(buckets) do
  if type(b)~='table' or type(b.level)~='string' or b.level=='' or seen[b.level] then return nil end
  if type(b.amount)~='number' or b.amount<0 or b.amount>1000000000000000 or b.amount~=math.floor(b.amount) then return nil end
  seen[b.level]=true
 end
 return buckets
end
-- A fence record is durable evidence of unresolved work on every bucket of
-- its reservation: fence() adds it only beside a reservation and release()
-- removes both together. capFenceEvidence counts fenced reservations per
-- bucket. With strict it returns nil for evidence it cannot read (a
-- noncanonical fence value, a fence whose reservation is missing or
-- undecodable), since that may cover any bucket. Otherwise it counts every
-- fence whose reservation decodes and skips the rest, which prove nothing
-- about a particular bucket.
local function capFenceEvidence(strict)
 local counts={}
 local fences=redis.call('HGETALL',KEYS[6])
 for i=1,#fences,2 do
  local buckets=capRecordBuckets(redis.call('HGET',KEYS[2],fences[i]))
  if not buckets or not capInteger(fences[i+1]) then
   if strict then return nil end
  end
  if buckets then
   for _,b in ipairs(buckets) do counts[b.level]=(counts[b.level] or 0)+1 end
  end
 end
 return counts
end
-- A missing or zero uncertainty counter is clear only when no fence record
-- covers the bucket; beside such evidence it is lost state, never zero. A
-- positive counter already refuses admission.
local function validCapUncertainty(level)
 if num('uncertain:'..level)>0 or redis.call('HLEN',KEYS[6])==0 then return true end
 local evidence=capFenceEvidence(true)
 return evidence~=nil and evidence[level]==nil
end
local function validCapExpired(id)
 if not validCapField(KEYS[6],id) then return false end
 local buckets=capRecordBuckets(redis.call('HGET',KEYS[2],id))
 if not buckets then return false end
 for _,b in ipairs(buckets) do
  if not validCapBucket(b.level) then return false end
  local spent=redis.call('HGET',KEYS[1],'spent:'..b.level)
  local held=redis.call('HGET',KEYS[1],'held:'..b.level)
  if not capInteger(spent) or not capInteger(held) or tonumber(held)<b.amount then return false end
  -- The sweep increments uncertainty once; do not cross Lua's exact range.
  if num('uncertain:'..b.level)>9007199254740991-64 then return false end
 end
 return true
end
`
