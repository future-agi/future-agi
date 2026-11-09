/** Fixed read-only program for the runner-owned Redis connection. No client,
 * endpoint discovery, EVAL, mutation or credentials here. The runner MUST use
 * Redis >=7 EVAL_RO, bind the actual server/container and permit these exact
 * source-bound bytes only. Atomicity avoids a torn hold/expiry/account capture.
 *
 * Provenance at da4c81cc623f909ce38f53e01b557ee8374d5adb:
 * agentcc-gateway/internal/redisstate/managedcredits.go:keys, Account,
 * OutboxPending, settleScript (marker='1'; group='django-settlement').
 * agentcc-gateway/internal/redisstate/agentbudget.go:keys, Peek, agentBudgetLua.
 * cmd/agentcc/wiring_credits.go uses EMPTY managed prefix; wiring_agentbudget.go
 * uses effective redisKeyPrefix. No guessed mc credential/closed/outstanding key.
 * HLEN/ZCARD are bounded BEFORE reading exact synthetic-org holds; no SCAN,
 * KEYS, MONITOR, CONFIG, full outbox payload or credential/session value reads.
 */
export const REDIS_READ_SCRIPT = `
local nh, ns = tonumber(ARGV[1]), tonumber(ARGV[2])
if not nh or nh < 1 or nh > 256 or not ns or ns < 0 or ns > 32 then
  return redis.error_reply('F02.cohort')
end
if #KEYS ~= 4 + nh + 6 * ns or #ARGV ~= 2 + nh + ns then
  return redis.error_reply('F02.plan')
end
local function holds(h, e)
  if redis.call('HLEN', h) > 256 or redis.call('ZCARD', e) > 256 then
    error('F02.inventory-limit')
  end
  return redis.call('HGETALL', h), redis.call('ZRANGE', e, 0, 255, 'WITHSCORES')
end
local hm, ex = holds(KEYS[2], KEYS[3])
local markers = {}
for i = 1, nh do
  markers[i] = {ARGV[2+i], redis.call('GET', KEYS[4+i]), redis.call('PTTL', KEYS[4+i])}
end
local length = redis.call('XLEN', KEYS[4])
local group, pending, lag = 0, false, false
if redis.call('EXISTS', KEYS[4]) == 1 then
  for _, g in ipairs(redis.call('XINFO', 'GROUPS', KEYS[4])) do
    local name, p, l
    for i = 1, #g, 2 do
      if g[i] == 'name' then name = g[i+1] end
      if g[i] == 'pending' then p = g[i+1] end
      if g[i] == 'lag' then l = g[i+1] end
    end
    if name == 'django-settlement' then
      if group == 1 then error('F02.duplicate-group') end
      group, pending, lag = 1, p, l or false
    end
  end
end
local scopes = {}
for i = 1, ns do
  local k = 4 + nh + (i-1)*6
  local sh, se = holds(KEYS[k+4], KEYS[k+5])
  local ttls = {}
  for j = 1, 6 do ttls[j] = redis.call('PTTL', KEYS[k+j]) end
  scopes[i] = {ARGV[2+nh+i], redis.call('GET', KEYS[k+1]),
    redis.call('GET', KEYS[k+2]), redis.call('GET', KEYS[k+3]), sh, se,
    redis.call('EXISTS', KEYS[k+6]), ttls}
end
return {redis.call('HMGET', KEYS[1], 'granted', 'granted_version', 'spent', 'held'),
  hm, ex, markers, {length, group, pending, lag}, scopes}
`;
