package capability

import (
	"encoding/json"
	"strconv"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// maxCacheEntries bounds the result cache in this replica.
const maxCacheEntries = 10_000

// resultCache keeps capability results per org only, keyed by (org,
// capability, vendor, normalized request), for tools.cache_ttl_seconds. A
// vendor whose card says cacheable=none is never cached; nothing is ever
// served across orgs, whatever the card says.
type resultCache struct {
	mu      sync.Mutex
	ttl     time.Duration
	now     func() time.Time
	entries map[string]cacheEntry
}

type cacheEntry struct {
	body    json.RawMessage
	units   []Unit
	cred    models.CredentialType
	expires time.Time
}

func newResultCache(ttl time.Duration, now func() time.Time) *resultCache {
	return &resultCache{ttl: ttl, now: now, entries: make(map[string]cacheEntry)}
}

// cacheable reports whether a call of cand for org may be cached at all.
func (c *resultCache) cacheable(org string, cand candidate) bool {
	return c != nil && c.ttl > 0 && org != "" && cand.card.Cacheable != "" && cand.card.Cacheable != CacheNone
}

// key is the cache key: the org, the capability, the vendor, the SKU and the
// request body as sent.
func cacheKey(org, capability string, cand candidate, req Request) string {
	return strconv.Quote(org) + "|" + capability + "|" + cand.vendor + "|" + cand.sku.SKU + "|" + sha256Hex(req.Body)
}

func (c *resultCache) get(key string) (cacheEntry, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.entries[key]
	if !ok {
		return cacheEntry{}, false
	}
	if !c.now().Before(e.expires) {
		delete(c.entries, key)
		return cacheEntry{}, false
	}
	return e, true
}

func (c *resultCache) put(key string, e cacheEntry) {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := c.now()
	if len(c.entries) >= maxCacheEntries {
		for k, old := range c.entries {
			if !now.Before(old.expires) {
				delete(c.entries, k)
			}
		}
		if len(c.entries) >= maxCacheEntries {
			return
		}
	}
	e.expires = now.Add(c.ttl)
	c.entries[key] = e
}
