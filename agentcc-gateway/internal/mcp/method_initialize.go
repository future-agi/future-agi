package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

func (s *Server) handleInitialize(w http.ResponseWriter, r *http.Request, msg *Message) {
	var params InitializeParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid initialize params")
		return
	}

	// Create the session, owned by the key that opened it.
	session := s.sessions.Create(sessionOwner(r.Context()), params.ClientInfo, params.Capabilities)

	caps := ServerCapabilities{
		Tools: &ToolsCapability{ListChanged: true},
	}
	if s.registry.ResourceCount() > 0 {
		caps.Resources = &ResourcesCapability{ListChanged: true}
	}
	if s.registry.PromptCount() > 0 {
		caps.Prompts = &PromptsCapability{ListChanged: true}
	}

	result := InitializeResult{
		ProtocolVersion: ProtocolVersion,
		Capabilities:    caps,
		ServerInfo:      s.serverInfo,
		Instructions: fmt.Sprintf(
			"Agentcc MCP gateway with %d tools, %d resources, %d prompts from %d servers",
			s.registry.ToolCount(),
			s.registry.ResourceCount(),
			s.registry.PromptCount(),
			len(s.registry.ServerIDs()),
		),
	}

	// Set session ID in response header.
	w.Header().Set("MCP-Session-Id", session.ID)
	writeJSONRPCResult(w, msg.ID, result)

	slog.Info("mcp session created",
		"session", session.ID,
		"client", params.ClientInfo.Name,
		"version", params.ClientInfo.Version,
	)
}

// validateSession checks the MCP-Session-Id header and returns the session,
// or writes an error response and returns nil. A session opened by another
// key is answered like an unknown one.
func (s *Server) validateSession(w http.ResponseWriter, r *http.Request, msg *Message) *Session {
	sessionID := r.Header.Get("MCP-Session-Id")
	if sessionID == "" {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "missing MCP-Session-Id header")
		return nil
	}
	session := s.sessions.Get(sessionID, sessionOwner(r.Context()))
	if session == nil {
		// Per MCP spec, expired/invalid session → HTTP 404 so client re-initializes.
		w.WriteHeader(http.StatusNotFound)
		return nil
	}
	return session
}

// --- Memory session store (Deps.SessionStore default) ---

// memorySessionStore is the default SessionStore: process memory, entries
// expire after their TTL.
type memorySessionStore struct {
	mu      sync.Mutex
	entries map[string]sessionEntry
	now     func() time.Time
}

type sessionEntry struct {
	data    []byte
	expires time.Time // zero: no expiry
}

func newMemorySessionStore() *memorySessionStore {
	return &memorySessionStore{entries: map[string]sessionEntry{}, now: time.Now}
}

func (m *memorySessionStore) Save(_ context.Context, id string, data []byte, ttl time.Duration) error {
	e := sessionEntry{data: append([]byte(nil), data...)}
	if ttl > 0 {
		e.expires = m.now().Add(ttl)
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.entries[id] = e
	return nil
}

func (m *memorySessionStore) Load(_ context.Context, id string) ([]byte, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	e, ok := m.entries[id]
	if !ok {
		return nil, false, nil
	}
	if !e.expires.IsZero() && !m.now().Before(e.expires) {
		delete(m.entries, id)
		return nil, false, nil
	}
	return append([]byte(nil), e.data...), true, nil
}

func (m *memorySessionStore) Delete(_ context.Context, id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.entries, id)
	return nil
}
