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

// errCodeUnsupportedVersion is the JSON-RPC code for a revision the gateway
// does not serve (-32022, with data.supported).
const errCodeUnsupportedVersion = -32022

// initializeParams is InitializeParams with the client's capabilities kept
// as sent, so the session record carries the ones no type here names yet.
type initializeParams struct {
	ProtocolVersion string          `json:"protocolVersion"`
	Capabilities    json.RawMessage `json:"capabilities"`
	ClientInfo      Implementation  `json:"clientInfo"`
}

// handleInitialize opens a legacy session: it negotiates the revision,
// binds the session to the request's principal and virtual server, and
// answers with the sealed session id in MCP-Session-Id.
func (s *Server) handleInitialize(w http.ResponseWriter, r *http.Request, msg *Message) {
	var params initializeParams
	if err := json.Unmarshal(msg.Params, &params); err != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid initialize params")
		return
	}
	var clientCaps ClientCapabilities
	if len(params.Capabilities) > 0 && json.Unmarshal(params.Capabilities, &clientCaps) != nil {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidParams, "invalid initialize params")
		return
	}

	version, ok := s.sessions.negotiateVersion(params.ProtocolVersion)
	if !ok {
		data, _ := json.Marshal(map[string]any{"supported": s.sessions.supportedVersions(), "requested": params.ProtocolVersion})
		writeRPCDenial(w, msg.ID, &Denial{
			RPCCode: errCodeUnsupportedVersion, HTTPStatus: http.StatusBadRequest, Data: data,
			Message: "legacy sessions are not served on this gateway",
		})
		return
	}

	session, err := s.sessions.create(r.Context(), s.sessionStore(), s.sessionBindingOf(r), version,
		params.ClientInfo, clientCaps, params.Capabilities)
	if err != nil {
		writeSessionStoreUnavailable(w, msg.ID)
		return
	}
	s.ensureSessionTap()

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
		ProtocolVersion: version,
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

	w.Header().Set("MCP-Session-Id", session.ID)
	writeJSONRPCResult(w, msg.ID, result)

	slog.Info("mcp session created",
		"session_hash", session.Hash(),
		"protocol_version", version,
		"client", params.ClientInfo.Name,
		"version", params.ClientInfo.Version,
	)
}

// validateSession checks the MCP-Session-Id header and returns the session,
// or writes an error response and returns nil. A session that is unknown,
// ended, expired or bound to another principal or virtual server is HTTP 404
// with no body, so the client initializes again and learns nothing more.
//
// Shared per function (orchestrator ruling R1, PENDING 88): the first
// statement is M11's, the 2026-07-28 stateless path (a request-scoped,
// unstored session); the rest is M12's.
func (s *Server) validateSession(w http.ResponseWriter, r *http.Request, msg *Message) *Session {
	if sess, ok := s.statelessSession(r); ok {
		return sess // a 2026-07-28 request runs under its own unstored session (M11, ruling R1)
	}
	sessionID := r.Header.Get("MCP-Session-Id")
	if sessionID == "" {
		writeJSONRPCError(w, msg.ID, ErrCodeInvalidRequest, "missing MCP-Session-Id header")
		return nil
	}
	session, err := s.resumeSession(r.Context(), sessionID, r)
	if err != nil {
		writeSessionStoreUnavailable(w, msg.ID)
		return nil
	}
	if session == nil {
		w.WriteHeader(http.StatusNotFound)
		return nil
	}
	return session
}

// resumeSession is SessionManager.resume on this server's store, with the
// binding r presents.
func (s *Server) resumeSession(ctx context.Context, id string, r *http.Request) (*Session, error) {
	session, err := s.sessions.resume(ctx, s.sessionStore(), id, s.sessionBindingOf(r))
	if session != nil {
		s.ensureSessionTap()
	}
	return session, err
}

// servePing answers ping on legacy sessions only (2026-07-28 removed it). A
// stateless (2026) request gets -32601 whatever MCP-Session-Id it carries,
// since that era ignores the header; a ping with MCP-Session-Id must name a
// live session of the same principal, like every request on a session; a
// ping with neither, before initialize, is answered.
func (s *Server) servePing(w http.ResponseWriter, r *http.Request, msg *Message) {
	if isStatelessRequest(msg) {
		writeMethodNotFound(w, msg)
		return
	}
	if r.Header.Get("MCP-Session-Id") != "" && s.validateSession(w, r, msg) == nil {
		return
	}
	writeJSONRPCResult(w, msg.ID, struct{}{})
}

// isStatelessRequest reports whether msg is a 2026-07-28 request: its
// params._meta names the protocol version (M11's era rule).
func isStatelessRequest(msg *Message) bool {
	var p struct {
		Meta map[string]json.RawMessage `json:"_meta"`
	}
	if json.Unmarshal(msg.Params, &p) != nil {
		return false
	}
	_, ok := p.Meta["io.modelcontextprotocol/protocolVersion"]
	return ok
}

// writeSessionStoreUnavailable answers a request whose session could not be
// read or written: HTTP 503, so the client retries rather than initializing.
func writeSessionStoreUnavailable(w http.ResponseWriter, id json.RawMessage) {
	writeRPCDenial(w, id, &Denial{
		RPCCode: ErrCodeInternal, HTTPStatus: http.StatusServiceUnavailable,
		Message: "session store unavailable",
	})
}

// sessionStore is the store sessions live in: Deps.SessionStore, or the
// manager's own memory store wherever the Deps default stands.
func (s *Server) sessionStore() SessionStore {
	switch st := s.Deps().SessionStore.(type) {
	case nil, *memorySessionStore:
		return s.sessions.mem
	default:
		return st
	}
}

// --- Memory session store (Deps.SessionStore default) ---

// memorySweepEvery is how often a Save also removes expired entries, so
// entries nobody loads again do not stay in memory.
const memorySweepEvery = time.Minute

// memorySessionStore is the default SessionStore: process memory, entries
// expire after their TTL.
type memorySessionStore struct {
	mu        sync.Mutex
	entries   map[string]sessionEntry
	now       func() time.Time
	lastSweep time.Time
}

type sessionEntry struct {
	data    []byte
	expires time.Time // zero: no expiry
}

func newMemorySessionStore() *memorySessionStore {
	return &memorySessionStore{entries: map[string]sessionEntry{}, now: time.Now}
}

func (m *memorySessionStore) Save(_ context.Context, id string, data []byte, ttl time.Duration) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	now := m.now()
	m.entries[id] = m.entry(data, ttl, now)
	if now.Sub(m.lastSweep) >= memorySweepEvery {
		m.sweepLocked(now)
	}
	return nil
}

// Refresh replaces an entry that still exists (SessionRefresher).
func (m *memorySessionStore) Refresh(_ context.Context, id, _ string, data []byte, ttl time.Duration) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	now := m.now()
	e, ok := m.entries[id]
	if !ok || e.expired(now) {
		delete(m.entries, id)
		return false, nil
	}
	m.entries[id] = m.entry(data, ttl, now)
	return true, nil
}

func (m *memorySessionStore) Load(_ context.Context, id string) ([]byte, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	e, ok := m.entries[id]
	if !ok {
		return nil, false, nil
	}
	if e.expired(m.now()) {
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

// sweep removes every expired entry.
func (m *memorySessionStore) sweep() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.sweepLocked(m.now())
}

func (m *memorySessionStore) sweepLocked(now time.Time) {
	for id, e := range m.entries {
		if e.expired(now) {
			delete(m.entries, id)
		}
	}
	m.lastSweep = now
}

func (m *memorySessionStore) entry(data []byte, ttl time.Duration, now time.Time) sessionEntry {
	e := sessionEntry{data: append([]byte(nil), data...)}
	if ttl > 0 {
		e.expires = now.Add(ttl)
	}
	return e
}

func (e sessionEntry) expired(now time.Time) bool {
	return !e.expires.IsZero() && !now.Before(e.expires)
}
