// Package admission bounds the upstream calls of one provider entry: at most
// max_concurrent of them in flight, and a call that finds every slot in use
// waits at most queue_timeout for one. Past that it is refused with
// provider_busy (503, Retry-After) before anything is sent upstream, so the
// refusal costs the caller nothing and holds no connection.
//
// A provider entry is a provider instance's ID: a config.yaml provider name,
// or the ID of an org's own instance. Each instance has its own slots; the
// counters and the snapshot are kept per entry, summed over the instances
// that share an ID (a key rotation's old and new instance while the old one
// drains).
package admission

import (
	"context"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

const (
	// DefaultMaxConcurrent is a remote entry's max_concurrent when its
	// config leaves it unset. At 1024 a 200 ms upstream carries about 5000
	// requests per second per instance before calls queue.
	DefaultMaxConcurrent = 1024
	// DefaultConnPoolSize is a remote entry's conn_pool_size (idle
	// connections kept per host) when its config leaves it unset.
	DefaultConnPoolSize = 1024
	// LocalMaxConcurrent and LocalConnPoolSize are a local or self-hosted
	// entry's defaults (providers.IsLocalProvider): such a server runs few
	// requests at once.
	LocalMaxConcurrent = 10
	LocalConnPoolSize  = 10
	// DefaultQueueTimeout is how long a call waits for a slot when neither
	// the entry's queue_timeout nor provider_defaults.queue_timeout is set.
	DefaultQueueTimeout = 5 * time.Second

	// saturatedFor is how long after a busy refusal an entry still reads as
	// saturated, so the signal does not flicker between scrapes.
	saturatedFor = 10 * time.Second
)

// waitBoundsMs are the upper bounds, in milliseconds, of the queue wait
// histogram's buckets.
var waitBoundsMs = [...]float64{1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000}

// Limiter holds one provider instance's upstream slots. Acquire before an
// upstream call and Release when the call (a streamed body included) ends.
type Limiter struct {
	entry  string
	slots  chan struct{}
	wait   time.Duration
	queued atomic.Int64
	stats  *entryStats
}

// New returns the slots of an instance of provider entry: maxConcurrent of
// them (DefaultMaxConcurrent when not positive), each call waiting at most
// queueTimeout (DefaultQueueTimeout when not positive) for one. The limiter
// counts in the entry's snapshot until Close.
func New(entry string, maxConcurrent int, queueTimeout time.Duration) *Limiter {
	if maxConcurrent <= 0 {
		maxConcurrent = DefaultMaxConcurrent
	}
	if queueTimeout <= 0 {
		queueTimeout = DefaultQueueTimeout
	}
	l := &Limiter{
		entry: entry,
		slots: make(chan struct{}, maxConcurrent),
		wait:  queueTimeout,
	}
	register(l)
	return l
}

// Acquire takes a slot. A call that finds every slot in use waits for one up
// to the queue timeout and is then refused with models.ErrProviderBusy. When
// ctx ends first (the request's deadline, or the caller left) it returns the
// gateway timeout the adapters always answered a cut-short wait with.
func (l *Limiter) Acquire(ctx context.Context) error {
	select {
	case l.slots <- struct{}{}:
		l.stats.observe(0)
		return nil
	default:
	}

	l.queued.Add(1)
	defer l.queued.Add(-1)
	start := time.Now()
	timer := time.NewTimer(l.wait)
	defer timer.Stop()
	select {
	case l.slots <- struct{}{}:
		l.stats.observe(time.Since(start))
		return nil
	case <-timer.C:
		l.stats.refuse(time.Since(start))
		return models.ErrProviderBusy(l.entry)
	case <-ctx.Done():
		return models.ErrGatewayTimeout("provider concurrency limit reached")
	}
}

// Release returns a slot Acquire took.
func (l *Limiter) Release() {
	<-l.slots
}

// Entry is the provider entry the slots belong to.
func (l *Limiter) Entry() string { return l.entry }

// Cap is the instance's max_concurrent.
func (l *Limiter) Cap() int { return cap(l.slots) }

// InFlight is how many slots are taken.
func (l *Limiter) InFlight() int { return len(l.slots) }

// Queued is how many calls wait for a slot.
func (l *Limiter) Queued() int64 { return l.queued.Load() }

// QueueTimeout is the longest a call waits for a slot.
func (l *Limiter) QueueTimeout() time.Duration { return l.wait }

// Close takes the instance out of its entry's snapshot (its gauges); its
// counters stay. Calls still holding slots may release them afterwards.
func (l *Limiter) Close() {
	mu.Lock()
	delete(live, l)
	mu.Unlock()
}

// entryStats are an entry's cumulative counters, kept across its instances.
type entryStats struct {
	refused     atomic.Int64
	lastRefusal atomic.Int64 // unix nanoseconds, 0 before the first
	// buckets counts each wait in the first bucket whose bound holds it;
	// waits past the last bound are counted only in count.
	buckets   [len(waitBoundsMs)]atomic.Int64
	count     atomic.Int64
	sumMicros atomic.Int64
}

func (s *entryStats) observe(d time.Duration) {
	ms := float64(d) / float64(time.Millisecond)
	for i, bound := range waitBoundsMs {
		if ms <= bound {
			s.buckets[i].Add(1)
			break
		}
	}
	s.count.Add(1)
	s.sumMicros.Add(d.Microseconds())
}

func (s *entryStats) refuse(d time.Duration) {
	s.observe(d)
	s.refused.Add(1)
	s.lastRefusal.Store(time.Now().UnixNano())
}

var (
	mu    sync.Mutex
	live  = map[*Limiter]struct{}{}
	stats = map[string]*entryStats{}
)

func register(l *Limiter) {
	mu.Lock()
	defer mu.Unlock()
	s, ok := stats[l.entry]
	if !ok {
		s = &entryStats{}
		stats[l.entry] = s
	}
	l.stats = s
	live[l] = struct{}{}
}

// WaitHistogram is an entry's queue wait distribution in milliseconds:
// Cumulative[i] counts the waits up to BoundsMs[i]; Count counts them all.
type WaitHistogram struct {
	BoundsMs   []float64
	Cumulative []int64
	Count      int64
	SumMs      float64
}

// EntryState is one provider entry's slots now, summed over its live
// instances, and its counters since start.
type EntryState struct {
	Entry string `json:"provider"`
	// Live is false for an entry whose instances were all closed (an
	// evicted org instance): only its counters remain.
	Live          bool  `json:"-"`
	InFlight      int   `json:"in_flight"`
	MaxConcurrent int   `json:"max_concurrent"`
	Queued        int64 `json:"queued"`
	BusyRefusals  int64 `json:"busy_refusals"`
	// Saturated: a call waits for a slot, every slot is taken, or a call was
	// refused busy in the last 10 seconds.
	Saturated bool          `json:"saturated"`
	Wait      WaitHistogram `json:"-"`
}

// Snapshot is every entry's state, sorted by entry.
func Snapshot() []EntryState {
	now := time.Now()
	mu.Lock()
	byEntry := make(map[string]*EntryState, len(stats))
	for entry := range stats {
		byEntry[entry] = &EntryState{Entry: entry}
	}
	for l := range live {
		st := byEntry[l.entry]
		st.Live = true
		st.InFlight += l.InFlight()
		st.MaxConcurrent += l.Cap()
		st.Queued += l.Queued()
	}
	entries := make([]*entryStats, 0, len(stats))
	out := make([]EntryState, 0, len(stats))
	for entry, s := range stats {
		out = append(out, *byEntry[entry])
		entries = append(entries, s)
	}
	mu.Unlock()

	for i := range out {
		s := entries[i]
		st := &out[i]
		st.BusyRefusals = s.refused.Load()
		last := s.lastRefusal.Load()
		recent := last != 0 && now.Sub(time.Unix(0, last)) < saturatedFor
		st.Saturated = st.Live && (st.Queued > 0 || (st.MaxConcurrent > 0 && st.InFlight >= st.MaxConcurrent) || recent)
		st.Wait = s.histogram()
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Entry < out[j].Entry })
	return out
}

func (s *entryStats) histogram() WaitHistogram {
	h := WaitHistogram{
		BoundsMs:   waitBoundsMs[:],
		Cumulative: make([]int64, len(waitBoundsMs)),
	}
	var total int64
	for i := range waitBoundsMs {
		total += s.buckets[i].Load()
		h.Cumulative[i] = total
	}
	// Read after the buckets: observe adds to its bucket first, so the
	// count is never below the last bucket, as the exposition format needs.
	h.Count = max(s.count.Load(), total)
	h.SumMs = float64(s.sumMicros.Load()) / 1000
	return h
}

// SaturatedEntries is how many live entries are saturated now.
func SaturatedEntries() int {
	n := 0
	for _, st := range Snapshot() {
		if st.Saturated {
			n++
		}
	}
	return n
}
