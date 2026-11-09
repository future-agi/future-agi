package tenancy

import (
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/mcp"
)

// listCache keeps built lists per mcp.ListCacheKey within one org's pool
// (MCA C17): nothing is shared across orgs, and two principals share an
// entry only when their effective access rules hash the same. Any change to
// the pool (its stamp) empties it.
type listCache struct {
	mu    sync.Mutex
	stamp [2]uint64
	items map[mcp.ListCacheKey][]mcp.Tool
}

// get returns key's list for this stamp, building it with build on a miss.
// The caller gets its own copy, so filtering it in place changes nothing
// cached.
func (c *listCache) get(stamp [2]uint64, key mcp.ListCacheKey, build func() []mcp.Tool) []mcp.Tool {
	c.mu.Lock()
	if c.items == nil || c.stamp != stamp {
		c.items, c.stamp = map[mcp.ListCacheKey][]mcp.Tool{}, stamp
	}
	if list, ok := c.items[key]; ok {
		c.mu.Unlock()
		return append([]mcp.Tool(nil), list...)
	}
	c.mu.Unlock()

	list := build()
	c.mu.Lock()
	if c.stamp == stamp {
		c.items[key] = append([]mcp.Tool(nil), list...)
	}
	c.mu.Unlock()
	return list
}
