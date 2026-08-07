-- Atomic quota check (READ-ONLY — does NOT increment).
-- Consumer handles all incrementing.
--
-- KEYS[1] = usage:{org_id}:{dimension}:{period}
-- ARGV[1] = limit (0 = no limit enforcement, -1 = unlimited)
-- ARGV[2] = amount to check against
--
-- Returns:
--   current total (int) if under limit or no limit
--   -1 if adding amount would exceed limit
--
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local amount = tonumber(ARGV[2]) or 1
local current = tonumber(redis.call('GET', key) or '0')

-- No limit or unlimited: always allow
if limit <= 0 then
    return current
end

-- Would exceed limit?
if (current + amount) > limit then
    return -1
end

return current
