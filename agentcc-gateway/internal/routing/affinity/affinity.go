// Package affinity keeps an agent session on the model it last used for each
// class of steps. Switching models mid-run throws away the provider's prompt
// cache, and some providers reject replayed tool calls made by another model
// (Gemini wants each call's thought_signature), so auto routing reuses
// the pinned model unless it has a reason to move. The outcomes plugin pins
// the model that served each completed response; auto routing reads and
// breaks the pin.
//
// Pins live in Redis under aff:{<org>}:<subject>:<class> and fall back to a
// bounded in-memory cache when Redis is down or absent.
package affinity

import (
	"context"
	"log/slog"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Class groups the steps that share a pin: the main loop's steps share one
// model, organ steps (reflection, memory, coordination) another, and verify
// a third, so a verifier can use a different model from the one it checks.
type Class string

const (
	ClassMain   Class = "main"
	ClassOrgan  Class = "organ"
	ClassVerify Class = "verify"
)

// ClassOf is step's class. Step other, and an unknown or empty step, have
// none, so they are never pinned.
func ClassOf(step models.Step) (Class, bool) {
	switch step {
	case models.StepPlan, models.StepAct, models.StepTool, models.StepExtract, models.StepFinal, models.StepSummarize:
		return ClassMain, true
	case models.StepReflect, models.StepMemory, models.StepCoordinate:
		return ClassOrgan, true
	case models.StepVerify:
		return ClassVerify, true
	}
	return "", false
}

// Subject is what a pin is kept for: the agent's session, else its run as
// "run:<run_id>", so a run that sends a run id without a session still has
// affinity. It is "" when the call names neither.
func Subject(a models.AgentContext) string {
	if a.SessionID != "" {
		return a.SessionID
	}
	if a.RunID != "" {
		return "run:" + a.RunID
	}
	return ""
}

// Value is a pin: the model (and the provider that served it), the request
// whose response last ran under the pin, and that request's prompt tokens,
// which auto routing uses to price a switch to another model by the cached
// prefix the switch throws away.
type Value struct {
	Model        string
	Provider     string
	RequestID    string
	PrefixTokens int
}

// Key is the Redis key of org's pin for subject and class. Every key of an
// org shares the hash tag {<org>}.
func Key(org, subject string, class Class) string {
	return "aff:{" + org + "}:" + subject + ":" + string(class)
}

// Backend is where pins are kept between gateway nodes (redisstate's
// AgentSignalStore). An error means the backend is unusable for this call.
type Backend interface {
	GetAffinity(ctx context.Context, key string) (Value, bool, error)
	SetAffinity(ctx context.Context, key string, v Value, ttl time.Duration) error
	DelAffinity(ctx context.Context, key string) error
}

// DefaultMemoryEntries bounds the in-memory fallback.
const DefaultMemoryEntries = 100_000

// Store reads and writes pins: in the backend when it answers, else in the
// in-memory cache. A write that reaches the backend leaves the cache alone,
// so a read checks the cache only when the backend fails or misses (a pin
// written while it was down).
type Store struct {
	backend Backend // nil: memory only
	mem     *LRU[Value]
}

// NewStore returns a store on backend (nil for memory only) whose fallback
// holds at most memEntries pins, timed by now (time.Now when nil).
func NewStore(backend Backend, memEntries int, now func() time.Time) *Store {
	return &Store{backend: backend, mem: NewLRU[Value](memEntries, now)}
}

// Get returns org's pin for subject and class.
func (s *Store) Get(ctx context.Context, org, subject string, class Class) (Value, bool) {
	key := Key(org, subject, class)
	if s.backend != nil {
		v, ok, err := s.backend.GetAffinity(ctx, key)
		if err == nil && ok {
			return v, true
		}
		logBackendError("get", err)
	}
	return s.mem.Get(key)
}

// Set pins v for subject and class until ttl passes without another Set.
// RequestID and PrefixTokens describe a completed response; a caller that
// only refreshes the pin before calling the model keeps the ones it read.
func (s *Store) Set(ctx context.Context, org, subject string, class Class, v Value, ttl time.Duration) {
	key := Key(org, subject, class)
	if s.backend != nil {
		err := s.backend.SetAffinity(ctx, key, v, ttl)
		if err == nil {
			return
		}
		logBackendError("set", err)
	}
	s.mem.Set(key, v, ttl)
}

// Break removes the pin for subject and class, from the backend and the
// cache alike.
func (s *Store) Break(ctx context.Context, org, subject string, class Class) {
	key := Key(org, subject, class)
	if s.backend != nil {
		logBackendError("break", s.backend.DelAffinity(ctx, key))
	}
	s.mem.Delete(key)
}

func logBackendError(op string, err error) {
	if err != nil {
		slog.Debug("affinity: backend unavailable, using the in-memory cache", "op", op, "error", err)
	}
}
