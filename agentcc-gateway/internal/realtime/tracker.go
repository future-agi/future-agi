package realtime

import (
	"sync"
	"sync/atomic"
)

// SessionTracker manages concurrent realtime sessions per org.
type SessionTracker struct {
	activeCounts   sync.Map // orgID -> *atomic.Int64
	activeSessions sync.Map // sessionID -> *Session
	maxPerOrg      int
}

// NewSessionTracker creates a new tracker with the given per-org limit.
func NewSessionTracker(maxPerOrg int) *SessionTracker {
	return &SessionTracker{maxPerOrg: maxPerOrg}
}

// TryAcquire tries to reserve a session slot for the given org.
func (t *SessionTracker) TryAcquire(orgID string) bool {
	val, _ := t.activeCounts.LoadOrStore(orgID, &atomic.Int64{})
	counter := val.(*atomic.Int64)
	for {
		current := counter.Load()
		if current >= int64(t.maxPerOrg) {
			return false
		}
		if counter.CompareAndSwap(current, current+1) {
			return true
		}
	}
}

// Release frees a session slot for the given org.
func (t *SessionTracker) Release(orgID string) {
	val, ok := t.activeCounts.Load(orgID)
	if !ok {
		return
	}
	counter := val.(*atomic.Int64)
	for {
		current := counter.Load()
		if current <= 0 {
			return
		}
		if counter.CompareAndSwap(current, current-1) {
			return
		}
	}
}

// Register adds a session to the active set.
func (t *SessionTracker) Register(session *Session) {
	t.activeSessions.Store(session.ID, session)
}

// Unregister removes a session from the active set.
func (t *SessionTracker) Unregister(sessionID string) {
	t.activeSessions.Delete(sessionID)
}

// ActiveCount returns the number of active sessions for an org.
func (t *SessionTracker) ActiveCount(orgID string) int {
	val, ok := t.activeCounts.Load(orgID)
	if !ok {
		return 0
	}
	return int(val.(*atomic.Int64).Load())
}

// TotalActive returns the total number of active sessions.
func (t *SessionTracker) TotalActive() int {
	count := 0
	t.activeSessions.Range(func(_, _ interface{}) bool {
		count++
		return true
	})
	return count
}

// ShutdownAll closes all active sessions with the given reason.
func (t *SessionTracker) ShutdownAll(reason string) {
	t.activeSessions.Range(func(_, v interface{}) bool {
		session := v.(*Session)
		session.Close(reason)
		return true
	})
}
