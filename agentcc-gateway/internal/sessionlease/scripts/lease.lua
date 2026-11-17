-- One org-slot atomic operation. No global scans, counters or cross-slot EVAL.
-- Lua errors do not roll back: a persistent guard fences partial mutations.
local op, directory, id, policy = ARGV[1], ARGV[2], ARGV[3], ARGV[4]
local parts = {}
for part in string.gmatch(directory, '[^|]+') do parts[#parts+1] = part end
if #KEYS ~= 7 or #parts ~= 8 or table.concat(parts, '|') ~= directory then return {'CORRUPT'} end
for _, i in ipairs({1,2,3,4,6,7,8}) do
  if #parts[i] ~= 64 or parts[i]:find('[^0-9a-f]') then return {'CORRUPT'} end
end
if parts[4] ~= id or parts[8] ~= policy or not (parts[5] == '1' or parts[5] == '2' or parts[5] == '3') then return {'BINDING'} end
local prefix = 'mcpl4:{o:' .. parts[1] .. '}:'
local budget = prefix .. 'budget:' .. parts[5] .. ':'
local expected = {prefix..'record:'..id, prefix..'live:'..id, budget..'org', budget..'workspace:'..parts[2], budget..'session:'..parts[3], prefix..'policy:'..parts[5], prefix..'guard:'..parts[5]}
local types = {'hash','string','zset','zset','zset','hash','string'}
for i=1,7 do
  if KEYS[i] ~= expected[i] then return {'BINDING'} end
  local kind = redis.call('TYPE', KEYS[i]).ok
  if kind ~= 'none' and kind ~= types[i] then return {'CORRUPT'} end
end
if redis.call('EXISTS', KEYS[7]) ~= 0 then return {'CORRUPT'} end
if op ~= 'acquire' and op ~= 'renew' and op ~= 'release' then return {'CORRUPT'} end
local function integer(v, low, high)
  local n = tonumber(v)
  if not n or n ~= math.floor(n) or n < low or n > high then return nil end
  return n
end
local oc, wc, sc = integer(ARGV[8],1,1000000), integer(ARGV[9],1,1000000), integer(ARGV[10],0,1000000)
if not oc or not wc or not sc or wc > oc or sc > wc then return {'CORRUPT'} end
local indexes = {KEYS[3],KEYS[4]}
if sc > 0 then indexes[3] = KEYS[5] end
local nowParts = redis.call('TIME')
local now = tonumber(nowParts[1])*1000 + math.floor(tonumber(nowParts[2])/1000)
local oldPolicy = redis.call('HGET', KEYS[6], 'digest')
local lastPolicy = redis.call('HGET', KEYS[6], 'last')
if oldPolicy and oldPolicy ~= policy then return {'BINDING'} end
if redis.call('EXISTS',KEYS[6]) == 1 and (not oldPolicy or not integer(lastPolicy,1,9007199254740991)) then return {'CORRUPT'} end
if lastPolicy and now < tonumber(lastPolicy) then return {'CLOCK'} end
local state = redis.call('HGET',KEYS[1],'state')
if redis.call('EXISTS',KEYS[1]) == 1 then
  if not state or not oldPolicy then return {'CORRUPT'} end
  if redis.call('HGET',KEYS[1],'directory') ~= directory then return {'BINDING'} end
  if state ~= 'active' and state ~= 'closed' and state ~= 'denied' then return {'CORRUPT'} end
end
local function begin()
  redis.call('SET',KEYS[7],'busy')
  redis.call('HSET',KEYS[6],'digest',policy,'last',now)
end
local function finish()
  redis.call('DEL',KEYS[7])
end
local function expireIndex(key)
  local last = redis.call('ZREVRANGE',key,0,0,'WITHSCORES')
  if #last == 0 then redis.call('DEL',key)
  else redis.call('PEXPIREAT',key,math.floor(tonumber(last[2]))) end
end
if op == 'release' then
  if state == 'closed' or state == 'denied' then return {'OK'} end
  begin()
  redis.call('HSET',KEYS[1],'directory',directory,'state','closed')
  redis.call('DEL',KEYS[2])
  for _, key in ipairs(indexes) do redis.call('ZREM',key,id);expireIndex(key) end
  finish()
  return {'OK'}
end
if state == 'closed' then return {'GONE'} end
if state == 'denied' then
  if op == 'acquire' then return {'CAPACITY'} else return {'GONE'} end
end
local ttl, idleSpan, absolute, idle, expires
if state == 'active' then
  ttl = integer(redis.call('HGET',KEYS[1],'ttl'),1,3000)
  idleSpan = integer(redis.call('HGET',KEYS[1],'idleSpan'),1,86400000)
  absolute = integer(redis.call('HGET',KEYS[1],'absolute'),1,9007199254740991)
  idle = integer(redis.call('HGET',KEYS[1],'idle'),1,9007199254740991)
  expires = integer(redis.call('HGET',KEYS[1],'expires'),1,9007199254740991)
  local last = integer(redis.call('HGET',KEYS[1],'last'),1,9007199254740991)
  if not ttl or not idleSpan or not absolute or not idle or not expires or not last or expires > idle or idle > absolute then return {'CORRUPT'} end
  if now < last then return {'CLOCK'} end
  if now >= expires or now >= idle or now >= absolute then return {'GONE'} end
  local live = redis.call('GET',KEYS[2])
  if not live then return {'GONE'} end
  if live ~= parts[7] then return {'CORRUPT'} end
  for _,key in ipairs(indexes) do
    if tonumber(redis.call('ZSCORE',key,id)) ~= expires then return {'CORRUPT'} end
  end
  if op == 'acquire' then
    begin();redis.call('HSET',KEYS[1],'last',now);finish()
    return {'OK',now,expires,idle,absolute}
  end
  if ARGV[11] ~= '0' and ARGV[11] ~= '1' then return {'CORRUPT'} end
  if ARGV[11] == '1' then idle = math.min(now+idleSpan,absolute) end
else
  if op ~= 'acquire' then return {'GONE'} end
  -- A missing record with live/index remnants is corruption, not fresh capacity.
  if redis.call('EXISTS',KEYS[2]) ~= 0 then return {'CORRUPT'} end
  for _,key in ipairs(indexes) do if redis.call('ZSCORE',key,id) then return {'CORRUPT'} end end
  ttl = integer(ARGV[5],1,3000)
  idleSpan = integer(ARGV[6],1,86400000)
  local absoluteSpan = integer(ARGV[7],1,86400000)
  if not ttl or not idleSpan or not absoluteSpan or idleSpan > absoluteSpan then return {'CORRUPT'} end
  if parts[5] == '2' and absoluteSpan > 30000 then return {'CORRUPT'} end
  local counts = {oc,wc,sc}
  for i,key in ipairs(indexes) do
    if redis.call('ZCOUNT',key,'('..now,'+inf') >= counts[i] then
      begin();redis.call('HSET',KEYS[1],'directory',directory,'state','denied');finish()
      return {'CAPACITY'}
    end
  end
  absolute = now+absoluteSpan
  idle = math.min(now+idleSpan,absolute)
end
expires = math.min(now+ttl,idle,absolute)
begin()
redis.call('HSET',KEYS[1],'directory',directory,'state','active','ttl',ttl,'idleSpan',idleSpan,'absolute',absolute,'idle',idle,'expires',expires,'last',now)
redis.call('SET',KEYS[2],parts[7],'PXAT',expires)
for _,key in ipairs(indexes) do
  redis.call('ZREMRANGEBYSCORE',key,'-inf',now)
  redis.call('ZADD',key,expires,id)
  expireIndex(key)
end
finish()
return {'OK',now,expires,idle,absolute}
