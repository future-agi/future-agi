package capability

import (
	"sort"
	"sync"
	"time"
)

// Breaker settings for a capability vendor: a run of failures opens its
// circuit for a while, and the ranking keeps a short latency window.
const (
	breakerFailures = 3
	breakerOpenFor  = 30 * time.Second
	latencyWindow   = 32
)

// healthBook is each (capability, vendor)'s circuit and recent latencies,
// in this replica. Its figures rank candidates and never leave the gateway
// (a no_perf_disclosure vendor's included).
type healthBook struct {
	mu   sync.Mutex
	now  func() time.Time
	byID map[string]*vendorHealth
}

type vendorHealth struct {
	failures  int
	openUntil time.Time
	latencies []time.Duration // ring of the last latencyWindow successes
	next      int
}

// healthView is a vendor's health as the ranking reads it.
type healthView struct {
	open bool
	p50  time.Duration // 0 with no sample
}

func newHealthBook(now func() time.Time) *healthBook {
	return &healthBook{now: now, byID: make(map[string]*vendorHealth)}
}

func (b *healthBook) entry(capability, vendor string) *vendorHealth {
	key := capability + "/" + vendor
	h := b.byID[key]
	if h == nil {
		h = &vendorHealth{}
		b.byID[key] = h
	}
	return h
}

// success records a successful call and its latency, and closes the circuit.
func (b *healthBook) success(capability, vendor string, latency time.Duration) {
	b.mu.Lock()
	defer b.mu.Unlock()
	h := b.entry(capability, vendor)
	h.failures, h.openUntil = 0, time.Time{}
	if len(h.latencies) < latencyWindow {
		h.latencies = append(h.latencies, latency)
		return
	}
	h.latencies[h.next] = latency
	h.next = (h.next + 1) % latencyWindow
}

// failure records a vendor failure; breakerFailures in a row open the circuit.
func (b *healthBook) failure(capability, vendor string) {
	b.mu.Lock()
	defer b.mu.Unlock()
	h := b.entry(capability, vendor)
	h.failures++
	if h.failures >= breakerFailures {
		h.openUntil = b.now().Add(breakerOpenFor)
	}
}

// view is the vendor's circuit state and median latency.
func (b *healthBook) view(capability, vendor string) healthView {
	b.mu.Lock()
	defer b.mu.Unlock()
	h := b.byID[capability+"/"+vendor]
	if h == nil {
		return healthView{}
	}
	v := healthView{open: b.now().Before(h.openUntil)}
	if n := len(h.latencies); n > 0 {
		sorted := append([]time.Duration(nil), h.latencies...)
		sort.Slice(sorted, func(i, j int) bool { return sorted[i] < sorted[j] })
		v.p50 = sorted[n/2]
	}
	return v
}
