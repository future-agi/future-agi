package auth

import "time"

// putPositive is a fixture seam, not a production publication path. In
// particular an expired nonnil seed replaces a live seed so get AND peek can
// independently prove that expiry is enforced. Production uses publishValidated.
func (c *cache) putPositive(key string, result *ResolveResult, stamps ...cacheStamp) {
	stamp := c.stamp()
	if len(stamps) != 0 {
		stamp = stamps[0]
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if result == nil || stamp.generation != c.generation.Load() || time.Since(stamp.started) >= c.ttl {
		return
	}
	c.m.Store(key, &cacheEntry{result: result, fetchedAt: stamp.started})
}
