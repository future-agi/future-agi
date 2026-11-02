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
	m           sync.Map
	ttl         time.Duration
	warmTTL     time.Duration
	generations [64]keyGeneration
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
	// An expired observation must not evict a concurrent fresh replacement.
	c.m.CompareAndDelete(key, e)
	return nil, "miss"
}

// peek is get without side effects: an expired entry is reported as a miss
// but left for get to remove.
func (c *cache) peek(key string) (*cacheEntry, string) {
	val, ok := c.m.Load(key)
	if !ok {
		return nil, "miss"
	}
	e := val.(*cacheEntry)
	switch {
	case e.isFresh(c.ttl):
		return e, "fresh"
	case e.isWarm(c.warmTTL):
		return e, "warm"
	}
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

// projectEntry requires the exact authenticated result and a live cache entry.
// Expiry is lazy, so loading sync.Map directly would accept an expired result
// whenever no other request happened to evict it during project persistence.
func (c *cache) projectEntry(key string, expected *ResolveResult) *cacheEntry {
	if c == nil || key == "" || expected == nil || expected.OrgID == "" || expected.WorkspaceID == "" {
		return nil
	}
	e, _ := c.get(key)
	if e == nil || e.result != expected {
		return nil
	}
	return e
}

// addProjectsScoped merges only into the exact result this request authenticated
// with. Even a same-scope refresh must be retried: a stale lookup may not update
// a replacement authentication result or resurrect an invalidated cache entry.
func (c *cache) addProjectsScoped(key string, expected *ResolveResult, projects map[string]string) bool {
	e := c.projectEntry(key, expected)
	if e == nil || !expected.SetProjectsInWorkspace(expected.WorkspaceID, projects) {
		return false
	}
	// Confirm that the same live entry survived the merge. Never store it back:
	// revocation, expiry or refresh may have happened during the merge.
	return c.projectEntry(key, expected) == e
}

// addProjectTypesScoped mirrors project trace_types into a cache entry under
// the same scope check as addProjectsScoped.
func (c *cache) addProjectTypesScoped(key string, expected *ResolveResult, types map[string]string) bool {
	e := c.projectEntry(key, expected)
	if e == nil || !expected.SetProjectTypesInWorkspace(expected.WorkspaceID, types) {
		return false
	}
	return c.projectEntry(key, expected) == e
}

// evictProjectID drops every name mapped to projectID from all cached entries
// so the next ingest re-resolves it from PG. Returns entries touched.
func (c *cache) evictProjectID(projectID string) int {
	if projectID == "" {
		return 0
	}
	touched := 0
	c.m.Range(func(_, val any) bool {
		if e, ok := val.(*cacheEntry); ok && e.result != nil {
			if e.result.DeleteProjectByID(projectID) {
				touched++
			}
		}
		return true
	})
	return touched
}
