package auth

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"sync"
	"sync/atomic"
	"time"
)

// CacheKey uses a digest for personal pairs and the public jti for grants.
// Every grant use still verifies its exact token and immutable signed claims.
func CacheKey(apiKey, secretKey string) string {
	if grantPublicID.MatchString(apiKey) {
		return "grant:" + apiKey[4:]
	}
	h := sha256.Sum256([]byte(apiKey + ":" + secretKey))
	return hex.EncodeToString(h[:])
}

type cacheEntry struct {
	result    *ResolveResult
	fetchedAt time.Time

	// Successful PRIMARY window, guarded by cache.mu and bound to this entry.
	// A real cold lookup may adopt a recent credential read's remaining budget,
	// but only using its own newly validated authority; initial cold leaves zero.
	revalidatedAt         time.Time
	revalidatedGeneration uint64
	revalidatedEpoch      uint64
}

// cache stores only fresh positive results. Pub/Sub is an acceleration, not
// a prerequisite for the primary-read freshness bound.
type cache struct {
	m           sync.Map
	ttl         time.Duration
	mu          cacheMutex
	generation  atomic.Uint64
	generations [64]keyGeneration
	// Read-budget reservations outlive ordinary TTL eviction, not invalidation.
	// All access is gated by mu; each reservation expires within 30 seconds.
	primaryReads map[string]*primaryReadBudget
}

type cacheStamp struct {
	generation  uint64
	started     time.Time
	primaryRead primaryReadSnapshot
}

func newCache(ttl, _ time.Duration) *cache {
	if ttl <= 0 || ttl > 30*time.Second {
		ttl = 30 * time.Second
	}
	c := &cache{ttl: ttl}
	c.mu.init()
	return c
}

func (c *cache) stamp() cacheStamp {
	return cacheStamp{generation: c.generation.Load(), started: time.Now()}
}

func (e *cacheEntry) fresh(ttl time.Duration) bool {
	return e != nil && e.result != nil && time.Since(e.fetchedAt) < ttl &&
		(e.result.Grant == nil || time.Now().Unix() < e.result.Grant.Exp)
}

// There is no stale-serving state. Grant expiry also applies on every hit.
func (c *cache) get(key string) (*cacheEntry, string) {
	val, ok := c.m.Load(key)
	if !ok {
		return nil, "miss"
	}
	e := val.(*cacheEntry)
	if e.fresh(c.ttl) {
		return e, "fresh"
	}
	c.discardExpired(key, e)
	return nil, "miss"
}

// discardExpired removes only the observation made by the expired reader.
// Keeping this completion separate permits a deterministic replacement race
// without a runtime hook or any additional lock in the cache read path.
func (c *cache) discardExpired(key string, observed *cacheEntry) {
	c.m.CompareAndDelete(key, observed)
}

// peek has the identical lifetime predicate, without expiration cleanup.
func (c *cache) peek(key string) (*cacheEntry, string) {
	val, ok := c.m.Load(key)
	if ok {
		e := val.(*cacheEntry)
		if e.fresh(c.ttl) {
			return e, "fresh"
		}
	}
	return nil, "miss"
}

// currentResult is the admission linearization point, not scope equality.
// Caller cancellation is honored while acquiring and after observing the gate.
func (c *cache) currentResult(ctx context.Context, key string, expected *ResolveResult) (bool, error) {
	if err := c.mu.LockContext(ctx); err != nil {
		return false, err
	}
	e, _ := c.peek(key)
	current := e != nil && e.result == expected
	c.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return false, err
	}
	current = current && e.fresh(c.ttl)
	if err := ctx.Err(); err != nil {
		return false, err
	}
	return current, nil
}

func (c *cache) evict(key string) { c.invalidateKey(key) }

func (c *cache) flush() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.generation.Add(1)
	for i := range c.generations {
		c.generations[i].epoch++
	}
	c.m.Range(func(key, _ any) bool { c.m.Delete(key); return true })
	for key := range c.primaryReads {
		c.dropPrimaryReadLocked(key)
	}
}

func (c *cache) evictGrant(id string) { c.invalidateKey("grant:" + id) }

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

// evictAPIKey fences even a first uncached lookup. Unrelated resident entries
// remain usable: global generation governs publication, not resident lifetime.
func (c *cache) evictAPIKey(digest string) int {
	if c == nil || digest == "" {
		return 0
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	c.generation.Add(1)
	evicted := 0
	c.m.Range(func(k, val any) bool {
		if e, ok := val.(*cacheEntry); ok && e.result != nil && e.result.APIKeyDigest == digest {
			if key, isKey := k.(string); isKey {
				c.invalidateKeyLocked(key)
			} else {
				c.m.Delete(k)
			}
			evicted++
		}
		return true
	})
	// A matching credential may already have aged out of m while its read
	// budget remains. The global fence above also covers absent/in-flight keys.
	for key, budget := range c.primaryReads {
		if budget.authority.digest == digest {
			c.dropPrimaryReadLocked(key)
		}
	}
	return evicted
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
