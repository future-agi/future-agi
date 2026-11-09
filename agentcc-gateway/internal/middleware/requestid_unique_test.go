package middleware

import (
	"runtime"
	"sync"
	"testing"
	"time"
)

// Request and trace ids key holds and settlements, idempotency and the request
// log, so two requests that share an id lose a billed spend (the second
// settlement is refused as already settled) and mix their log entries.
// Concurrent requests must never share an id: not right after a garbage
// collection, which empties every sync.Pool, and not when every id is stamped
// in the same millisecond, where only the entropy can tell two ids apart.
func TestRequestIDsUniqueUnderConcurrency(t *testing.T) {
	for _, tc := range []struct {
		name   string
		frozen bool // every id gets one timestamp
	}{
		{"the clock stands still", true},
		{"the real clock", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.frozen {
				prev := now
				t.Cleanup(func() { now = prev })
				at := time.UnixMilli(1760000000000)
				now = func() time.Time { return at }
			}
			const workers, rounds = 8, 500
			seen := make(map[string]bool, workers*rounds)
			for r := 0; r < rounds; r++ {
				// Two collections empty a sync.Pool, its victim cache included.
				runtime.GC()
				runtime.GC()
				var wg sync.WaitGroup
				start := make(chan struct{})
				out := make([]string, workers)
				for w := 0; w < workers; w++ {
					wg.Add(1)
					go func(w int) { defer wg.Done(); <-start; out[w] = generateULID() }(w)
				}
				close(start)
				wg.Wait()
				for _, id := range out {
					if seen[id] {
						t.Fatalf("round %d: id %s was given to two requests", r, id)
					}
					seen[id] = true
				}
			}
		})
	}
}
