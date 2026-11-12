package affinity

import (
	"container/list"
	"sync"
	"time"
)

// LRU is a bounded in-memory cache whose entries also expire: the store the
// agent signals fall back to when Redis is down or absent. Once it holds
// capacity entries, adding one evicts the least recently used. It is safe
// for concurrent use.
type LRU[V any] struct {
	capacity int
	now      func() time.Time

	mu    sync.Mutex
	order *list.List // front: most recently used
	items map[string]*list.Element
}

type lruEntry[V any] struct {
	key     string
	val     V
	expires time.Time
}

// NewLRU returns an empty cache of at most capacity entries (at least 1),
// reading the time from now (time.Now when nil).
func NewLRU[V any](capacity int, now func() time.Time) *LRU[V] {
	if now == nil {
		now = time.Now
	}
	return &LRU[V]{capacity: max(capacity, 1), now: now, order: list.New(), items: make(map[string]*list.Element)}
}

// Get returns key's value unless it is absent or expired.
func (c *LRU[V]) Get(key string) (V, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.getLocked(key)
}

// Set stores v under key for ttl.
func (c *LRU[V]) Set(key string, v V, ttl time.Duration) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.setLocked(key, v, ttl)
}

// Update replaces key's value with fn(current, found) and keeps it for ttl,
// in one step, so concurrent updates of one key are never lost.
func (c *LRU[V]) Update(key string, ttl time.Duration, fn func(cur V, found bool) V) V {
	c.mu.Lock()
	defer c.mu.Unlock()
	cur, found := c.getLocked(key)
	next := fn(cur, found)
	c.setLocked(key, next, ttl)
	return next
}

// Modify applies fn to key's value in place, keeping its expiry. It reports
// false, and calls nothing, when key is absent or expired.
func (c *LRU[V]) Modify(key string, fn func(v *V)) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, ok := c.getLocked(key); !ok {
		return false
	}
	fn(&c.items[key].Value.(*lruEntry[V]).val)
	return true
}

// Delete removes key.
func (c *LRU[V]) Delete(key string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if el, ok := c.items[key]; ok {
		c.removeLocked(el)
	}
}

// Len is the number of entries held, expired ones not yet evicted included.
func (c *LRU[V]) Len() int {
	c.mu.Lock()
	defer c.mu.Unlock()
	return len(c.items)
}

func (c *LRU[V]) getLocked(key string) (V, bool) {
	var zero V
	el, ok := c.items[key]
	if !ok {
		return zero, false
	}
	e := el.Value.(*lruEntry[V])
	if !c.now().Before(e.expires) {
		c.removeLocked(el)
		return zero, false
	}
	c.order.MoveToFront(el)
	return e.val, true
}

func (c *LRU[V]) setLocked(key string, v V, ttl time.Duration) {
	expires := c.now().Add(ttl)
	if el, ok := c.items[key]; ok {
		e := el.Value.(*lruEntry[V])
		e.val, e.expires = v, expires
		c.order.MoveToFront(el)
		return
	}
	c.items[key] = c.order.PushFront(&lruEntry[V]{key: key, val: v, expires: expires})
	for len(c.items) > c.capacity {
		c.removeLocked(c.order.Back())
	}
}

func (c *LRU[V]) removeLocked(el *list.Element) {
	c.order.Remove(el)
	delete(c.items, el.Value.(*lruEntry[V]).key)
}
