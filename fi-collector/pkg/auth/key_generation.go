package auth

import (
	"context"
	"time"
)

// Fixed stripes retain no per-key tombstones. Every epoch access is protected
// by the single cache gate; there is no second lock or lock-order inversion.
type keyGeneration struct{ epoch uint64 }

func (c *cache) generationFor(key string) *keyGeneration {
	var stripe uint8
	for i := range key {
		stripe = stripe*31 + key[i]
	}
	return &c.generations[stripe%uint8(len(c.generations))]
}

func (c *cache) validationStamp(ctx context.Context, key string) (cacheStamp, uint64, error) {
	if err := c.mu.LockContext(ctx); err != nil {
		return cacheStamp{}, 0, err
	}
	stamp, epoch := c.stamp(), c.generationFor(key).epoch
	stamp.primaryRead = c.primaryReadSnapshotLocked(key)
	c.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return cacheStamp{}, 0, err
	}
	return stamp, epoch, nil
}

// Only the shared validation worker publishes. Comparison and insertion are
// atomic with every invalidation, including notices for uncached credentials.
// No project-map lock, persistence, or flight wait runs under c.mu.
func (c *cache) publishValidated(ctx context.Context, key string, result *ResolveResult, epoch uint64, stamp cacheStamp) (bool, error) {
	return c.publishValidatedWithBudget(ctx, key, result, epoch, stamp, nil)
}

// Only Authenticate, after a real successful resolver lookup, supplies now to
// enable budget handoff. Direct publication has no such PRIMARY evidence and
// cannot inherit a previous entry's window. Both paths share every fence below.
func (c *cache) publishValidatedWithBudget(ctx context.Context, key string, result *ResolveResult, epoch uint64, stamp cacheStamp, now func() time.Time) (bool, error) {
	if err := c.mu.LockContext(ctx); err != nil {
		return false, err
	}
	defer c.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if result != nil && result.Grant != nil && time.Now().Unix() >= result.Grant.Exp {
		return false, ErrUnauthenticated
	}
	if c.generation.Load() != stamp.generation || c.generationFor(key).epoch != epoch {
		return false, nil
	}
	if result == nil && c.rejectPrimaryReadLocked(key, stamp.primaryRead, stamp.generation, epoch) {
		// A delayed miss can snapshot an already-completed positive handoff.
		// Dropping its budget alone leaves that resident's window usable. As
		// with a cached negative, evict and fence exactly the observed entry,
		// including older in-flight work, but never a superseding publication.
		if raw, ok := c.m.Load(key); ok && raw == stamp.primaryRead.entry {
			c.generation.Add(1)
			c.invalidateKeyLocked(key)
		}
	}
	if time.Since(stamp.started) >= c.ttl {
		return false, nil
	}
	if result != nil {
		entry := &cacheEntry{result: result, fetchedAt: stamp.started}
		if now != nil {
			c.applyPrimaryBudgetLocked(key, entry, now(), stamp.generation, epoch)
		}
		if budget := c.primaryReads[key]; budget != nil {
			budget.publication++ // Fence old negatives even after this entry expires.
		}
		c.m.Store(key, entry)
	}
	return true, nil
}

func (c *cache) invalidateKey(key string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.generation.Add(1)
	c.invalidateKeyLocked(key)
}

// Caller owns c.mu and advances the global generation once per operation.
func (c *cache) invalidateKeyLocked(key string) {
	c.generationFor(key).epoch++
	c.m.Delete(key)
	c.dropPrimaryReadLocked(key)
}
