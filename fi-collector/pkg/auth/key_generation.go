package auth

import "sync"

// Fixed stripes fence validation publication without retaining tombstones for
// revoked or unknown keys. A collision can require revalidation, never allow a
// stale result. Cache keys are already SHA-256 digests.
type keyGeneration struct {
	mu    sync.Mutex
	epoch uint64
}

func (c *cache) generationFor(key string) *keyGeneration {
	var stripe uint8
	for i := range key {
		stripe = stripe*31 + key[i]
	}
	return &c.generations[stripe%uint8(len(c.generations))]
}

func (c *cache) keyGeneration(key string) uint64 {
	g := c.generationFor(key)
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.epoch
}

// publishValidated and invalidation share a lock: checking the epoch and then
// publishing outside that lock would still let an old lookup resurrect a key.
// A nil result is not cached, so arbitrary invalid credentials retain no state.
func (c *cache) publishValidated(key string, result *ResolveResult, epoch uint64) bool {
	g := c.generationFor(key)
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.epoch != epoch {
		return false
	}
	if result != nil {
		c.putPositive(key, result)
	}
	return true
}

func (c *cache) invalidateKey(key string) {
	g := c.generationFor(key)
	g.mu.Lock()
	defer g.mu.Unlock()
	g.epoch++
	c.m.Delete(key)
}

// A negative warm refresh may evict only the generation it validated. A late
// negative result must not remove a replacement authenticated after a remap.
func (c *cache) invalidateGeneration(key string, epoch uint64) {
	g := c.generationFor(key)
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.epoch == epoch {
		g.epoch++
		c.m.Delete(key)
	}
}
