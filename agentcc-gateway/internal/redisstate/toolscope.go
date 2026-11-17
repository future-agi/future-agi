package redisstate

import "strings"

// All run-scoped writers use this key set and expiry primitive. The MC.6
// calls/searches keys remain strings shared by REST and MCP. The companion
// hash holds every per-tool count and replay record, so refreshing a scope
// is constant work, not a scan of all the tools or calls it has ever used.
func toolScopeKeys(run string) []string {
	return []string{run, strings.TrimSuffix(run, ":calls") + ":searches", run + ":state"}
}

// Redis owns the clock. One deadline applies to every existing scope key,
// including the entire state hash; no field has an independent expiry.
// Call after writes as well as on refused/replayed attempts (still activity).
const toolScopeExpiryLua = `
local function expireScope(ttl)
 local now=redis.call('TIME')
 local deadline=tonumber(now[1])*1000+math.floor(tonumber(now[2])/1000)+tonumber(ttl)*1000
 for _,key in ipairs(KEYS) do redis.call('PEXPIREAT',key,deadline) end
end
`
