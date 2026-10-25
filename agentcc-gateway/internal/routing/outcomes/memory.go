package outcomes

import (
	"time"

	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
)

// memory is the Store's in-memory fallback: bounded caches of counters,
// Last records and previous-request pointers, each kept as long as its
// Redis twin would be.
type memory struct {
	counters *affinity.LRU[Counters]
	last     *affinity.LRU[Last]
	prev     *affinity.LRU[string]
}

func newMemory(entries int, now func() time.Time) *memory {
	return &memory{
		counters: affinity.NewLRU[Counters](entries, now),
		last:     affinity.NewLRU[Last](entries, now),
		prev:     affinity.NewLRU[string](entries, now),
	}
}

func (m *memory) add(key string, d Delta, nowMs, halfLifeMs int64) {
	m.counters.Update(key, CountersTTL, func(c Counters, _ bool) Counters {
		return c.Add(d, nowMs, halfLifeMs)
	})
}

// claim marks the cached Last record at key judged; it reports false when
// it already was or is gone.
func (m *memory) claim(key string) bool {
	claimed := false
	found := m.last.Modify(key, func(l *Last) {
		claimed = !l.Judged
		l.Judged = true
	})
	return found && claimed
}
