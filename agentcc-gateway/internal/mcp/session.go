package mcp

import (
	"crypto/rand"
	"encoding/hex"
	"sync"
	"sync/atomic"
	"time"
)

// Session represents an active MCP client session.
type Session struct {
	ID           string
	ClientInfo   Implementation
	Capabilities ClientCapabilities
	CreatedAt    time.Time
	owner        SessionOwner // set at creation, never changed
	lastUsed     atomic.Int64 // unix nano for lock-free update
}

// SessionOwner is the API key that opened a session. Only that key may use
// the session. With per-key auth off every session has the zero owner.
type SessionOwner struct {
	KeyID string
	OrgID string
}

// Touch updates the session's last-used timestamp.
func (s *Session) Touch() {
	s.lastUsed.Store(time.Now().UnixNano())
}

// IdleSince returns the duration since the session was last used.
func (s *Session) IdleSince() time.Duration {
	last := s.lastUsed.Load()
	if last == 0 {
		return time.Since(s.CreatedAt)
	}
	return time.Since(time.Unix(0, last))
}

// SessionManager tracks active MCP sessions.
type SessionManager struct {
	sessions sync.Map // session ID → *Session
	ttl      time.Duration
	stopCh   chan struct{}
}

// NewSessionManager creates a session manager with a cleanup goroutine.
func NewSessionManager(ttl time.Duration) *SessionManager {
	if ttl <= 0 {
		ttl = 30 * time.Minute
	}
	sm := &SessionManager{
		ttl:    ttl,
		stopCh: make(chan struct{}),
	}
	go sm.reaper()
	return sm
}

// Create creates a new session owned by owner and returns it.
func (sm *SessionManager) Create(owner SessionOwner, clientInfo Implementation, caps ClientCapabilities) *Session {
	s := &Session{
		ID:           generateSessionID(),
		ClientInfo:   clientInfo,
		Capabilities: caps,
		CreatedAt:    time.Now(),
		owner:        owner,
	}
	s.Touch()
	sm.sessions.Store(s.ID, s)
	return s
}

// Get returns the session with this id when owner opened it and it has not
// expired, and marks it used; otherwise nil. A session opened by another owner
// is reported missing, exactly like an unknown id, and is left untouched: it
// is neither marked used nor removed.
func (sm *SessionManager) Get(id string, owner SessionOwner) *Session {
	v, ok := sm.sessions.Load(id)
	if !ok {
		return nil
	}
	s := v.(*Session)
	if s.owner != owner {
		return nil
	}
	if s.IdleSince() > sm.ttl {
		sm.sessions.Delete(id)
		return nil
	}
	s.Touch()
	return s
}

// Delete removes a session.
func (sm *SessionManager) Delete(id string) {
	sm.sessions.Delete(id)
}

// Count returns the number of active sessions.
func (sm *SessionManager) Count() int {
	count := 0
	sm.sessions.Range(func(_, _ interface{}) bool {
		count++
		return true
	})
	return count
}

// Close stops the reaper goroutine.
func (sm *SessionManager) Close() {
	close(sm.stopCh)
}

// reaper periodically removes expired sessions.
func (sm *SessionManager) reaper() {
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-sm.stopCh:
			return
		case <-ticker.C:
			sm.sessions.Range(func(key, value interface{}) bool {
				s := value.(*Session)
				if s.IdleSince() > sm.ttl {
					sm.sessions.Delete(key)
				}
				return true
			})
		}
	}
}

func generateSessionID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		panic("crypto/rand.Read failed: " + err.Error())
	}
	return hex.EncodeToString(b)
}
