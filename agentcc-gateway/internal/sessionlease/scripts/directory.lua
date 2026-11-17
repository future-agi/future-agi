-- Immutable, single-ID-slot preparation. This is never a quota grant.
local op, value, id = ARGV[1], ARGV[2], ARGV[3]
if #KEYS ~= 1 or #id ~= 64 or id:find('[^0-9a-f]') or KEYS[1] ~= 'mcpl4:dir:{i:' .. id .. '}' then return 'CORRUPT' end
local kind = redis.call('TYPE', KEYS[1]).ok
if kind ~= 'none' and kind ~= 'string' then return 'CORRUPT' end
local old = redis.call('GET', KEYS[1])
if op == 'release' then
  if not old then redis.call('SET', KEYS[1], '*'); return '*' end
  return old
end
if op == 'read' then
  if not old or old == '*' then return 'GONE' end
  return old
end
if op ~= 'acquire' or #value > 1024 or #value == 0 then return 'CORRUPT' end
if old == '*' then return 'GONE' end
if old and old ~= value then return 'BINDING' end
if not old then redis.call('SET', KEYS[1], value) end
return value
