package auth

import (
	"crypto/sha256"
	"encoding/hex"
	"sync"
	"time"
)

// CacheKey computes a sha256 hash of apiKey+secretKey for use as a cache key.
func CacheKey(apiKey, secretKey string) string {
	h := sha256.Sum256([]byte(apiKey + ":" + secretKey))
	return hex.EncodeToString(h[:])
}

type cacheEntry struct {
	result    *ResolveResult
	fetchedAt time.Time
}

func (e *cacheEntry) isFresh(ttl time.Duration) bool {
	return time.Since(e.fetchedAt) < ttl
}

func (e *cacheEntry) isWarm(warmTTL time.Duration) bool {
	return time.Since(e.fetchedAt) < warmTTL
}

// cache stores valid auth results only. Invalid keys are never cached
// — they always hit PG (deduplicated by singleflight). This prevents
// unbounded memory growth from scanners sending random keys.
type cache struct {
	m       sync.Map
	ttl     time.Duration
	warmTTL time.Duration
}

func newCache(ttl, warmTTL time.Duration) *cache {
	return &cache{ttl: ttl, warmTTL: warmTTL}
}

// get returns the cached entry and a status:
//   - "fresh" — valid, serve immediately
//   - "warm"  — stale but within warm window, serve + trigger background refresh
//   - "miss"  — not found or expired past warm window
func (c *cache) get(key string) (*cacheEntry, string) {
	val, ok := c.m.Load(key)
	if !ok {
		return nil, "miss"
	}
	e := val.(*cacheEntry)

	if e.isFresh(c.ttl) {
		return e, "fresh"
	}
	if e.isWarm(c.warmTTL) {
		return e, "warm"
	}
	c.m.Delete(key)
	return nil, "miss"
}

func (c *cache) putPositive(key string, result *ResolveResult) {
	c.m.Store(key, &cacheEntry{
		result:    result,
		fetchedAt: time.Now(),
	})
}

func (c *cache) addProjects(key string, projects map[string]string) {
	val, ok := c.m.Load(key)
	if !ok {
		return
	}
	e := val.(*cacheEntry)
	if e.result == nil {
		return
	}
	e.result.SetProjects(projects)
}
