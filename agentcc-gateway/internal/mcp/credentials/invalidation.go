package credentials

import (
	"context"
	"strconv"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

// Pub/sub is a wakeup, never an authorization cache. Every completion still
// compares its exact epoch in Redis. A missed message or reconnect is covered
// by the two-second poll; unavailable Redis withdraws grants rather than
// extending their lives. Neither the registry nor a pool lock spans Redis I/O.
const invalidationPoll = 2 * time.Second

func (c *TokenCache) invalidationChannel() string { return c.prefix + cacheKeyPrefix + "invalidate" }

type epochWatch struct {
	keys   []string
	values []string
	// revision > 0 watches a pushed grant: it is stale once the slot's
	// revoked-through value reaches it, never merely because that value moved.
	revision int64
	cancel   context.CancelFunc
}
type epochWatcher struct {
	cache   *TokenCache
	mu      sync.Mutex
	watches map[*epochWatch]struct{}
	wake    chan struct{}
	ready   chan struct{}
}

func (c *TokenCache) watchEpoch(life context.Context, k Key, epoch personalEpoch, cancel context.CancelFunc) {
	w := &epochWatch{keys: []string{c.personalEpochKey(k), c.personalServerEpochKey(k)}, values: []string{epoch.user, epoch.server}, cancel: cancel}
	if k.Shared {
		w.keys, w.values = []string{c.sharedEpochKey(k)}, []string{epoch.user}
	}
	if epoch.revision > 0 {
		w.keys, w.values, w.revision = []string{c.pushedRevokedKey(k)}, nil, epoch.revision
	}
	c.watchMu.Lock()
	h := c.watcher
	if h == nil {
		h = &epochWatcher{cache: c, watches: map[*epochWatch]struct{}{}, wake: make(chan struct{}, 1), ready: make(chan struct{})}
		c.watcher = h
		// Registration happens before the receiver starts or a purge can wake it.
		h.watches[w] = struct{}{}
		go h.run()
	} else {
		h.mu.Lock()
		h.watches[w] = struct{}{}
		h.mu.Unlock()
	}
	c.watchMu.Unlock()
	context.AfterFunc(life, func() {
		h.mu.Lock()
		delete(h.watches, w)
		h.mu.Unlock()
		select {
		case h.wake <- struct{}{}:
		default:
		}
	})
	// Initial subscription is acknowledged before a grant is handed to a factory.
	// A final epoch comparison by the caller covers the subscribe/register gap.
	select {
	case <-h.ready:
	case <-life.Done():
	}
}

func (h *epochWatcher) run() {
	c := h.cache
	var sub *redis.PubSub
	_ = c.do(context.Background(), func(ctx context.Context, rdb redis.UniversalClient) error {
		sub = rdb.Subscribe(ctx, c.invalidationChannel())
		_, err := sub.Receive(ctx)
		return err
	})
	var messages <-chan *redis.Message
	if sub != nil {
		messages = sub.Channel()
		defer sub.Close()
	}
	close(h.ready)
	ticker := time.NewTicker(invalidationPoll)
	defer ticker.Stop()
	for {
		// c.watchMu orders shutdown with registration, so a new grant never joins
		// a worker which has already decided to stop.
		c.watchMu.Lock()
		h.mu.Lock()
		empty := len(h.watches) == 0
		if empty && c.watcher == h {
			c.watcher = nil
		}
		h.mu.Unlock()
		c.watchMu.Unlock()
		if empty {
			return
		}
		select {
		case _, ok := <-messages:
			if !ok {
				messages = nil
			}
			h.check()
		case <-ticker.C:
			h.check()
		case <-h.wake:
		}
	}
}

func (h *epochWatcher) check() {
	h.mu.Lock()
	watches := make([]*epochWatch, 0, len(h.watches))
	for w := range h.watches {
		watches = append(watches, w)
	}
	h.mu.Unlock()
	if len(watches) == 0 {
		return
	}
	commands := make([]*redis.SliceCmd, len(watches))
	finished := make(chan error, 1)
	go func() {
		finished <- h.cache.do(context.Background(), func(ctx context.Context, rdb redis.UniversalClient) error {
			_, err := rdb.Pipelined(ctx, func(p redis.Pipeliner) error {
				for i, w := range watches {
					commands[i] = p.MGet(ctx, w.keys...)
				}
				return nil
			})
			return err
		})
	}()
	// Some go-redis clients use socket timeouts rather than context deadlines.
	// Do not let their retries extend the cancellation bound. The buffered
	// sender can finish later; it owns no entry lock or replacement lifetime.
	timer := time.NewTimer(cacheOpTimeout)
	defer timer.Stop()
	var err error
	select {
	case err = <-finished:
	case <-timer.C:
		for _, w := range watches {
			w.cancel()
		}
		return
	}
	for i, w := range watches {
		stale := err != nil
		if !stale {
			values, e := commands[i].Result()
			stale = e != nil
			if !stale && w.revision > 0 {
				stale = !pushedBelow(values[0], w.revision)
			} else if !stale {
				for j, v := range values {
					actual := "0"
					if v != nil {
						actual, _ = v.(string)
					}
					if actual != w.values[j] {
						stale = true
					}
				}
			}
		}
		if stale {
			w.cancel()
		} // Snapshot owns only this grant, never its successor.
	}
}

// pushedBelow reports whether a revoked-through value leaves revision live.
// An unreadable value withdraws the grant rather than extending it.
func pushedBelow(v any, revision int64) bool {
	if v == nil {
		return true
	}
	s, ok := v.(string)
	through, err := strconv.ParseInt(s, 10, 64)
	return ok && err == nil && revision > through
}

func (c *TokenCache) checkLifetimes() {
	c.watchMu.Lock()
	h := c.watcher
	c.watchMu.Unlock()
	if h != nil {
		h.check()
	}
}
