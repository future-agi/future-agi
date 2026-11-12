package outcomes

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/routing/affinity"
)

// Backend is where outcomes are kept between gateway nodes (redisstate's
// AgentSignalStore). An error means the backend is unusable for this call.
type Backend interface {
	// AddOutcome decays the counters hash at key to nowMs and adds d.
	AddOutcome(ctx context.Context, key string, d Delta, nowMs, halfLifeMs int64, ttl time.Duration) error
	OutcomeCounters(ctx context.Context, key string) (Counters, bool, error)
	SetLast(ctx context.Context, key string, l Last, ttl time.Duration) error
	GetLast(ctx context.Context, key string) (Last, bool, error)
	// ClaimJudgement marks the Last record at key judged. claimed is false
	// when it already was; found is false when there is no record.
	ClaimJudgement(ctx context.Context, key string) (claimed, found bool, err error)
	SetPrev(ctx context.Context, key, requestID string, ttl time.Duration) error
	GetPrev(ctx context.Context, key string) (string, bool, error)
}

// Store records outcomes and verdicts, in the backend when it answers, else
// in a bounded in-memory fallback. Counters read back as the sum of both, so
// samples recorded during a backend outage still count while they last.
type Store struct {
	backend Backend // nil: memory only
	mem     *memory
	now     func() time.Time
}

// NewStore returns a store on backend (nil for memory only) whose fallback
// holds at most memEntries records of each kind, timed by now (time.Now when
// nil).
func NewStore(backend Backend, memEntries int, now func() time.Time) *Store {
	if now == nil {
		now = time.Now
	}
	return &Store{backend: backend, mem: newMemory(memEntries, now), now: now}
}

// Now is the store's clock.
func (s *Store) Now() time.Time { return s.now() }

// Record adds one response's outcome to k's counters.
func (s *Store) Record(ctx context.Context, k Key, o Outcome, halfLife time.Duration) {
	s.add(ctx, k, o.delta(), halfLife)
}

// Counters is k's counters as of now: the backend's plus the fallback's.
func (s *Store) Counters(ctx context.Context, k Key, halfLife time.Duration) Counters {
	key, nowMs, hl := k.String(), s.now().UnixMilli(), msOf(halfLife)
	var c Counters
	if s.backend != nil {
		got, _, err := s.backend.OutcomeCounters(ctx, key)
		logBackendError("counters", err)
		c = got
	}
	if mem, ok := s.mem.counters.Get(key); ok {
		c = c.Plus(mem, nowMs, hl)
	}
	return c.DecayedTo(nowMs, hl)
}

// Stats is k's outcome record as of now.
func (s *Store) Stats(ctx context.Context, k Key, halfLife time.Duration) Stats {
	return s.Counters(ctx, k, halfLife).Stats()
}

// Remember keeps the generation's Last record for LastTTL, for the verdicts
// that may follow it.
func (s *Store) Remember(ctx context.Context, org, requestID string, l Last) {
	key := LastKey(org, requestID)
	if s.backend != nil {
		err := s.backend.SetLast(ctx, key, l, LastTTL)
		if err == nil {
			return
		}
		logBackendError("set last", err)
	}
	s.mem.last.Set(key, l, LastTTL)
}

// Last is the generation's Last record, when the gateway still has it.
func (s *Store) Last(ctx context.Context, org, requestID string) (Last, bool) {
	key := LastKey(org, requestID)
	if s.backend != nil {
		l, ok, err := s.backend.GetLast(ctx, key)
		if err == nil && ok {
			return l, true
		}
		logBackendError("get last", err)
	}
	return s.mem.last.Get(key)
}

// SetPrev records requestID as the subject's latest request of class, for
// LastTTL, whatever came of it.
func (s *Store) SetPrev(ctx context.Context, org, subject string, class affinity.Class, requestID string) {
	key := PrevKey(org, subject, class)
	if s.backend != nil {
		err := s.backend.SetPrev(ctx, key, requestID, LastTTL)
		if err == nil {
			return
		}
		logBackendError("set prev", err)
	}
	s.mem.prev.Set(key, requestID, LastTTL)
}

// Prev is the subject's latest request of class.
func (s *Store) Prev(ctx context.Context, org, subject string, class affinity.Class) (string, bool) {
	key := PrevKey(org, subject, class)
	if s.backend != nil {
		id, ok, err := s.backend.GetPrev(ctx, key)
		if err == nil && ok {
			return id, true
		}
		logBackendError("get prev", err)
	}
	return s.mem.prev.Get(key)
}

// Judge counts a caller's verdict on generation, whose Last record is last,
// in last's counters. verify_failed and bad count as a failed verification;
// empty and retried as an empty response, unless the gateway already
// classified it empty; ok counts nothing. A generation counts at most one
// negative verdict, however many callers or retries report one. It reports
// whether a counter changed.
func (s *Store) Judge(ctx context.Context, org, generationID string, last Last, verdict string, halfLife time.Duration) bool {
	field, ok := verdictField(verdict, last)
	if !ok || !s.claim(ctx, LastKey(org, generationID)) {
		return false
	}
	s.add(ctx, last.Key(org), Delta{field: 1}, halfLife)
	return true
}

func verdictField(verdict string, last Last) (string, bool) {
	switch verdict {
	case VerdictVerifyFailed, VerdictBad:
		return FieldVerifyFailed, true
	case VerdictEmpty, VerdictRetried:
		return FieldEmpty, last.Class != ClassEmpty
	}
	return "", false
}

// claim marks the Last record at key judged, where it is kept; it reports
// false when it already was, or when no record is left to mark.
func (s *Store) claim(ctx context.Context, key string) bool {
	if s.backend != nil {
		claimed, found, err := s.backend.ClaimJudgement(ctx, key)
		if err == nil && found {
			return claimed
		}
		logBackendError("claim", err)
	}
	return s.mem.claim(key)
}

func (s *Store) add(ctx context.Context, k Key, d Delta, halfLife time.Duration) {
	key, nowMs, hl := k.String(), s.now().UnixMilli(), msOf(halfLife)
	if s.backend != nil {
		err := s.backend.AddOutcome(ctx, key, d, nowMs, hl, CountersTTL)
		if err == nil {
			return
		}
		logBackendError("add", err)
	}
	s.mem.add(key, d, nowMs, hl)
}

func logBackendError(op string, err error) {
	if err != nil {
		slog.Debug("outcomes: backend unavailable, using the in-memory fallback", "op", op, "error", err)
	}
}
