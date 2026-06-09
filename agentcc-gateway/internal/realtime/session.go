package realtime

import (
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// SessionUsage tracks accumulated usage for a realtime session.
type SessionUsage struct {
	InputTokens        int
	OutputTokens       int
	InputAudioSeconds  float64
	OutputAudioSeconds float64
	TotalResponses     int
	TotalMessages      int64
	mu                 sync.Mutex
}

// AddResponse adds token and audio usage from a response event.
func (u *SessionUsage) AddResponse(inputTokens, outputTokens int, inputAudioSecs, outputAudioSecs float64) {
	u.mu.Lock()
	u.InputTokens += inputTokens
	u.OutputTokens += outputTokens
	u.InputAudioSeconds += inputAudioSecs
	u.OutputAudioSeconds += outputAudioSecs
	u.TotalResponses++
	u.mu.Unlock()
}

// IncrementMessages atomically increments the message counter.
func (u *SessionUsage) IncrementMessages() {
	atomic.AddInt64(&u.TotalMessages, 1)
}

// Snapshot returns a safe copy of the usage data.
func (u *SessionUsage) Snapshot() SessionUsage {
	u.mu.Lock()
	defer u.mu.Unlock()
	return SessionUsage{
		InputTokens:        u.InputTokens,
		OutputTokens:       u.OutputTokens,
		InputAudioSeconds:  u.InputAudioSeconds,
		OutputAudioSeconds: u.OutputAudioSeconds,
		TotalResponses:     u.TotalResponses,
		TotalMessages:      atomic.LoadInt64(&u.TotalMessages),
	}
}

// Session represents a bidirectional WebSocket realtime session.
type Session struct {
	ID           string
	RequestID    string
	OrgID        string
	Model        string
	Provider     string
	ClientConn   *websocket.Conn
	ProviderConn *websocket.Conn
	CreatedAt    time.Time
	ClosedAt     *time.Time
	CloseReason  string
	Usage        *SessionUsage
	Metadata     map[string]string
	stopCh       chan struct{}
	stopped      atomic.Bool
	mu           sync.Mutex
}

// NewSession creates a new realtime session.
func NewSession(id, requestID, orgID, model, provider string, clientConn, providerConn *websocket.Conn) *Session {
	return &Session{
		ID:           id,
		RequestID:    requestID,
		OrgID:        orgID,
		Model:        model,
		Provider:     provider,
		ClientConn:   clientConn,
		ProviderConn: providerConn,
		CreatedAt:    time.Now(),
		Usage:        &SessionUsage{},
		Metadata:     make(map[string]string),
		stopCh:       make(chan struct{}),
	}
}

// Close terminates the session. Safe to call from multiple goroutines.
func (s *Session) Close(reason string) {
	if s.stopped.Swap(true) {
		return // already closed
	}

	s.mu.Lock()
	now := time.Now()
	s.ClosedAt = &now
	s.CloseReason = reason
	s.mu.Unlock()

	close(s.stopCh)

	// Best-effort close frames.
	closeMsg := websocket.FormatCloseMessage(websocket.CloseNormalClosure, reason)
	deadline := time.Now().Add(2 * time.Second)
	if s.ClientConn != nil {
		s.ClientConn.WriteControl(websocket.CloseMessage, closeMsg, deadline)
		s.ClientConn.Close()
	}
	if s.ProviderConn != nil {
		s.ProviderConn.WriteControl(websocket.CloseMessage, closeMsg, deadline)
		s.ProviderConn.Close()
	}
}

// IsClosed returns true if the session has been closed.
func (s *Session) IsClosed() bool {
	return s.stopped.Load()
}

// Duration returns the session duration.
func (s *Session) Duration() time.Duration {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ClosedAt != nil {
		return s.ClosedAt.Sub(s.CreatedAt)
	}
	return time.Since(s.CreatedAt)
}

// StopChan returns a channel that is closed when the session stops.
func (s *Session) StopChan() <-chan struct{} {
	return s.stopCh
}
