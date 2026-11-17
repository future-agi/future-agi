// Package routebinding supplies a process-local publication fence. It owns no
// route, credential, configuration, or admission records. Installing it on one
// owner does not qualify a multi-owner native publication closure.
package routebinding

import (
	"crypto/rand"
	"errors"
	"math"
	"sync"
)

var (
	ErrGate      = errors.New("publication gate is uninitialized or copied")
	ErrClosed    = errors.New("publication gate is closed")
	ErrToken     = errors.New("invalid publication token")
	ErrScope     = errors.New("publication scope has ended")
	ErrExhausted = errors.New("publication ordinals exhausted")
	ErrCallback  = errors.New("publication callback is required")
)

// Stamp is a nonsecret runtime revision, not an authorization capability. Its
// private epoch is fresh for each gate incarnation, including process restart.
// Copies are intentional; equality is meaningful only for nonzero stamps.
// An ordinal reserved but never published is permanently burned.
type Stamp struct {
	epoch   [16]byte
	ordinal uint64
}

func (s Stamp) Valid() bool            { return s.epoch != [16]byte{} && s.ordinal != 0 }
func (s Stamp) Equal(other Stamp) bool { return s.Valid() && s == other }

// Gate must be constructed with NewGate, must not be copied, and must be shared
// by all participating owners before serving. The zero value refuses use.
// Lock order is gate -> scoped use -> owner locks. Never enter Gate while
// holding an owner lock. Callbacks and scoped uses are trusted, bounded memory
// operations: no I/O, waits for external work, recursive Gate/Use/Next calls,
// Close, or owner callbacks that can do any of those things.
//
// Read and Publish do not roll back mutations when their callback fails or
// panics. Prepare fallible work outside the fence. Owners must revoke readiness
// on allocation failure; a stamp alone does not establish owner readiness.
type Gate struct {
	self    *Gate
	mu      sync.RWMutex
	epoch   [16]byte
	ordinal uint64
	closed  bool
}

// NewGate obtains randomness before any fence is held. No global registry or
// persistent config fingerprint is used; failure exposes only a fixed error.
func NewGate() (*Gate, error) {
	g := &Gate{}
	if _, err := rand.Read(g.epoch[:]); err != nil || g.epoch == [16]byte{} {
		return nil, ErrGate
	}
	g.self = g
	return g, nil
}

func (g *Gate) valid() bool { return g != nil && g.self == g }

// scope serializes bounded owner uses. Closing admission before draining mu
// ensures no new use can extend the fence once the outer callback returns.
// State is constant-size; no goroutine identity or unbounded use registry.
type scope struct {
	gate *Gate
	mu   sync.Mutex
	done chan struct{}
}

func newScope(g *Gate) *scope { return &scope{gate: g, done: make(chan struct{})} }

func (s *scope) ended() bool {
	select {
	case <-s.done:
		return true
	default:
		return false
	}
}

func (s *scope) use(fn func() error) error {
	if fn == nil {
		return ErrCallback
	}
	if s.ended() {
		return ErrScope
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ended() {
		return ErrScope
	}
	return fn()
}

func (s *scope) finish() {
	close(s.done)
	// An admitted concurrent owner use may still be executing. Retain the
	// actual gate fence until it finishes, not merely until an active check.
	s.mu.Lock()
	s.mu.Unlock()
}

// Read is an opaque callback-scoped token. Copying the value invalidates the
// copy. Retaining the pointer does not retain admission after callback return.
// Owner methods must perform ALL protected reads inside Use, never check a
// token then continue accessing mutable owner state after Use returns.
type Read struct {
	self  *Read
	scope *scope
}

// Use authenticates this read for the expected installed gate and executes fn
// while the actual read fence remains held. Concurrent uses are serialized and
// already-admitted uses drain on callback return. Go cannot identify the caller
// goroutine: a shared token can be used during its live scope, never afterward.
// An owner must not expose mutable borrowed state from fn or start deferred work
// in fn; a returned detached snapshot needs a fresh fenced currentness check at
// every later point that relies on it. Do not call Use recursively.
func (r *Read) Use(expected *Gate, fn func() error) error {
	if r == nil || r.self != r || r.scope == nil || !expected.valid() || r.scope.gate != expected {
		return ErrToken
	}
	return r.scope.use(fn)
}

// Write is the exclusive callback-scoped counterpart of Read.
type Write struct {
	self  *Write
	scope *scope
}

func (w *Write) Use(expected *Gate, fn func() error) error {
	if w == nil || w.self != w || w.scope == nil || !expected.valid() || w.scope.gate != expected {
		return ErrToken
	}
	return w.scope.use(fn)
}

// Next issues a fresh ordinal within this write scope. It must not be called
// from inside Use (which already owns the scope mutex). A failed callback never
// returns an ordinal to the allocator. Exhaustion does not close the gate.
func (w *Write) Next() (Stamp, error) {
	if w == nil || w.scope == nil {
		return Stamp{}, ErrToken
	}
	var stamp Stamp
	err := w.Use(w.scope.gate, func() error {
		var err error
		stamp, err = w.scope.gate.next()
		return err
	})
	return stamp, err
}

// next requires an authenticated Write.Use; never export a counter setter.
func (g *Gate) next() (Stamp, error) {
	if g.ordinal == math.MaxUint64 {
		return Stamp{}, ErrExhausted
	}
	g.ordinal++
	return Stamp{epoch: g.epoch, ordinal: g.ordinal}, nil
}

func (g *Gate) Read(fn func(*Read) error) error {
	if !g.valid() {
		return ErrGate
	}
	if fn == nil {
		return ErrCallback
	}
	g.mu.RLock()
	defer g.mu.RUnlock()
	if g.closed {
		return ErrClosed
	}
	s := newScope(g)
	defer s.finish() // Runs before the gate unlock, including on panic.
	r := &Read{scope: s}
	r.self = r
	return fn(r)
}

// Publish deliberately does not allocate a stamp. Existing owner reservations
// and readiness revocations must still commit after fresh ordinal exhaustion.
func (g *Gate) Publish(fn func(*Write) error) error {
	if !g.valid() {
		return ErrGate
	}
	if fn == nil {
		return ErrCallback
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.closed {
		return ErrClosed
	}
	s := newScope(g)
	defer s.finish()
	w := &Write{scope: s}
	w.self = w
	return fn(w)
}

// Close is final, idempotent shutdown, not an individual owner's revocation.
// First quiesce new work and drain all owners/builders/sequencers OUTSIDE Gate,
// including reserved operations between required disk work and memory commit.
// Then Close drains active fenced scopes. It cannot discover unfenced work and
// must never be called from a callback or while holding any owner lock.
func (g *Gate) Close() error {
	if !g.valid() {
		return ErrGate
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.closed = true
	return nil
}
