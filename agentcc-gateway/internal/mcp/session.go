package mcp

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/config"
)

// legacySessionVersions are the revisions served with Streamable HTTP
// sessions, newest first. 2024-11-05 (HTTP+SSE) is M12x's; 2026-07-28 is
// stateless (M11).
var legacySessionVersions = []string{"2025-11-25", "2025-06-18", "2025-03-26"}

// sessionTokenLifetime bounds a session id however active the session is: a
// client past it gets 404 and initializes again. The record's ttl, renewed
// by use, ends an idle session long before.
const sessionTokenLifetime = 24 * time.Hour

// errSessionStore: the session store could not be read or written. The
// session is neither found nor missing; the request gets 503.
var errSessionStore = errors.New("mcp: session store unavailable")

// Session is a legacy session as this process holds it. ID is the sealed
// session id the client sends back: a bearer of the session, so it is never
// logged or stored (Hash names it instead).
type Session struct {
	ID           string
	ClientInfo   Implementation
	Capabilities ClientCapabilities
	CreatedAt    time.Time
	// ProtocolVersion is the revision negotiated at initialize.
	ProtocolVersion string

	org  string
	hash string

	mu       sync.Mutex
	record   sessionRecord // as last written to the store
	lastUsed atomic.Int64  // unix nano, lock-free
}

// Hash is SessionHash(ID).
func (s *Session) Hash() string { return s.hash }

// Touch marks the session used now.
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

func (s *Session) key() string       { return sessionKey(s.org, s.hash) }
func (s *Session) eventsKey() string { return sessionEventsKey(s.org, s.hash) }

// sessionBinding is who and what a session is bound to. Every request on the
// session must present the same binding, else it is answered as an unknown
// session (404).
type sessionBinding struct {
	Kind          PrincipalKind
	KeyID         string
	UserID        string
	ClientID      string
	OrgID         string
	KeyHash       string
	VirtualServer string
}

// sessionPrincipalKey is a request-context key only M12's tests set, to pin
// the principals they bind sessions to (principalOfRequest reads it first).
type sessionPrincipalKey struct{}

// principalOfRequest is the principal authenticateHTTP resolved for r (M8's
// callerAuth, integrator request M8 IR-4 at the W3b merge), or the zero
// Principal when none was. A principal that names nobody (a bare SetKeyAuth
// authenticator's {Kind: key} with no id) counts as none, so such a session
// stays bound to its key's hash (sessionBindingOf) instead of every key
// sharing one binding.
func principalOfRequest(r *http.Request) Principal {
	if p, ok := r.Context().Value(sessionPrincipalKey{}).(Principal); ok {
		return p
	}
	if c, ok := callerAuthFrom(r.Context()); ok {
		if p := c.principal; p.KeyID != "" || p.UserID != "" || p.OrgID != "" || p.ClientID != "" {
			return p
		}
	}
	return Principal{}
}

// sessionBindingOf is the binding a request presents: its principal and the
// virtual server its path addresses. With no principal resolved but a key
// that per-key auth checked, the key's SHA-256 stands in for it, so sessions
// stay bound to their key until M8 resolves principals. A bearer token's
// hash is never used next to a resolved principal: those rotate.
func (s *Server) sessionBindingOf(r *http.Request) sessionBinding {
	b := sessionBinding{VirtualServer: virtualServerOf(r)}
	p := principalOfRequest(r)
	if p.Kind != "" || p.KeyID != "" || p.UserID != "" || p.OrgID != "" || p.ClientID != "" {
		b.Kind, b.KeyID, b.UserID, b.ClientID, b.OrgID = p.Kind, p.KeyID, p.UserID, p.ClientID, p.OrgID
		return b
	}
	if s.keyAuth != nil {
		if raw := requestAPIKey(r); raw != "" {
			// Domain-separated, so it never equals the key store's own
			// SHA-256 key hash (auth.HashKey), which identifies the key.
			sum := sha256.Sum256([]byte("agentcc-mcp-session-key\x00" + raw))
			b.Kind, b.KeyHash = PrincipalKey, hex.EncodeToString(sum[:])
		}
	}
	return b
}

// virtualServerOf is the virtual server id a request path addresses
// (<endpoint>/v/{vs_id}, decision 32), or "" for the endpoint itself.
func virtualServerOf(r *http.Request) string {
	path := r.URL.Path
	i := strings.LastIndex(path, "/v/")
	if i < 0 || strings.Contains(path[i+3:], "/") {
		return ""
	}
	return path[i+3:]
}

// SessionOptions configure legacy sessions; the wiring sets them from the
// mcp config (ConfigureSessions).
type SessionOptions struct {
	// Codec seals session ids (state purpose "session"). Without one, ids
	// are sealed with the process's ephemeral development key (tests only).
	Codec *StateCodec
	// Versions are the served revisions (mcp.protocol.versions); only the
	// 2025 ones apply here. Empty serves all three.
	Versions []string
	// LegacyDisabled refuses initialize (mcp.protocol.legacy_sessions=false).
	LegacyDisabled bool
}

// ConfigureSessions sets the session options. The wiring calls it once,
// before the server serves.
func (s *Server) ConfigureSessions(o SessionOptions) {
	s.sessions.opts.Store(&o)
}

// SessionManager keeps the legacy sessions this process created or served.
// The records live in Deps.SessionStore; sessions is a cache of them that
// carries what only this process knows (last use, open streams).
type SessionManager struct {
	sessions sync.Map // session ID → *Session
	ttl      time.Duration
	opts     atomic.Pointer[SessionOptions]
	// mem stands in for the Deps.SessionStore default: SetDeps builds a new
	// default store on every call, which would drop every session a late
	// SetDeps finds, so all of them resolve to this one.
	mem    *memorySessionStore
	events *memoryEventLog
	hub    *streamHub

	stopCh    chan struct{}
	closeOnce sync.Once
}

// NewSessionManager creates a session manager with a cleanup goroutine.
func NewSessionManager(ttl time.Duration) *SessionManager {
	if ttl <= 0 {
		ttl = 30 * time.Minute
	}
	sm := &SessionManager{
		ttl:    ttl,
		mem:    newMemorySessionStore(),
		events: newMemoryEventLog(),
		hub:    newStreamHub(),
		stopCh: make(chan struct{}),
	}
	go sm.reaper()
	return sm
}

func (sm *SessionManager) options() SessionOptions {
	if o := sm.opts.Load(); o != nil {
		return *o
	}
	return SessionOptions{}
}

func (sm *SessionManager) codec() (*StateCodec, error) {
	if c := sm.options().Codec; c != nil {
		return c, nil
	}
	return devSessionCodec()
}

// devSessionCodec seals with the process's ephemeral key (M1x's
// mcp.dev_insecure_state) when the wiring set no codec: tests only, since
// config validation refuses mcp.enabled without a state key.
var devSessionCodec = sync.OnceValues(func() (*StateCodec, error) { return NewStateCodec(nil, true) })

// refreshEvery is how stale a record's last_seen may grow before a request
// writes it back: a tenth of the ttl, at most a minute, so an active session
// expires no sooner than nine tenths of the ttl after its last use.
func (sm *SessionManager) refreshEvery() time.Duration {
	return min(sm.ttl/10, time.Minute)
}

// negotiateVersion picks the revision initialize answers with: the one the
// client asked for when it is served, else the newest served one. ok is
// false when no 2025 revision is served.
func (sm *SessionManager) negotiateVersion(requested string) (string, bool) {
	o := sm.options()
	if o.LegacyDisabled {
		return "", false
	}
	served := legacySessionVersions
	if len(o.Versions) > 0 {
		served = slices.DeleteFunc(slices.Clone(legacySessionVersions), func(v string) bool {
			return !slices.Contains(o.Versions, v)
		})
	}
	if len(served) == 0 {
		return "", false
	}
	if slices.Contains(served, requested) {
		return requested, true
	}
	return served[0], true
}

// supportedVersions are the revisions an unsupported-version answer lists:
// the served ones, without the session revisions when those are off.
func (sm *SessionManager) supportedVersions() []string {
	o := sm.options()
	versions := o.Versions
	if len(versions) == 0 {
		versions = config.MCPProtocolVersions()
	}
	if !o.LegacyDisabled {
		return versions
	}
	return slices.DeleteFunc(slices.Clone(versions), func(v string) bool {
		return slices.Contains(legacySessionVersions, v)
	})
}

// sessionToken is the sealed payload of a session id: the org that names
// the record's key, and a random part that makes every id unique.
type sessionToken struct {
	Org   string `json:"o"`
	Nonce string `json:"n"`
}

// create opens a session bound to b and stores its record.
func (sm *SessionManager) create(ctx context.Context, store SessionStore, b sessionBinding, version string, info Implementation, caps ClientCapabilities, rawCaps json.RawMessage) (*Session, error) {
	codec, err := sm.codec()
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	payload, _ := json.Marshal(sessionToken{Org: b.OrgID, Nonce: hex.EncodeToString(nonce)})
	id, err := codec.Seal(StatePurposeSession, payload, max(sessionTokenLifetime, sm.ttl))
	if err != nil {
		return nil, err
	}
	now := time.Now()
	rec := sessionRecord{
		V: 1, PrincipalKind: b.Kind, KeyID: b.KeyID, UserID: b.UserID, ClientID: b.ClientID,
		OrgID: b.OrgID, KeyHash: b.KeyHash, VirtualServer: b.VirtualServer, Era: version,
		ClientInfo: info, ClientCapabilities: rawCaps, CreatedMs: now.UnixMilli(), LastSeenMs: now.UnixMilli(),
	}
	sess := &Session{
		ID: id, ClientInfo: info, Capabilities: caps, CreatedAt: now, ProtocolVersion: version,
		org: b.OrgID, hash: SessionHash(id), record: rec,
	}
	data, _ := json.Marshal(rec)
	if err := store.Save(ctx, sess.key(), data, sm.ttl); err != nil {
		slog.Warn("mcp session store write failed", "session_hash", sess.hash, "error", err)
		return nil, errSessionStore
	}
	sess.Touch()
	sm.sessions.Store(id, sess)
	return sess, nil
}

// resume returns the session id names when it exists and b is its binding,
// and uses it: marks it used and renews its record (refresh). An id that does
// not open, a session that ended or expired, and a session bound to anything
// else are all answered alike with nil, and another principal's request
// writes nothing.
func (sm *SessionManager) resume(ctx context.Context, store SessionStore, id string, b sessionBinding) (*Session, error) {
	codec, err := sm.codec()
	if err != nil {
		return nil, err
	}
	payload, err := codec.Open(StatePurposeSession, id)
	if err != nil {
		return nil, nil
	}
	var tok sessionToken
	if json.Unmarshal(payload, &tok) != nil {
		return nil, nil
	}
	hash := SessionHash(id)
	data, ok, err := store.Load(ctx, sessionKey(tok.Org, hash))
	if err != nil {
		slog.Warn("mcp session store read failed", "session_hash", hash, "error", err)
		return nil, errSessionStore
	}
	if !ok {
		sm.forget(id)
		return nil, nil
	}
	var rec sessionRecord
	if json.Unmarshal(data, &rec) != nil || rec.binding() != b {
		return nil, nil
	}
	sess := sm.cached(id, tok.Org, hash, &rec)
	sess.Touch()
	if alive, err := sm.refresh(ctx, store, sess, false); err != nil || !alive {
		return nil, err
	}
	return sess, nil
}

// cached returns this process's Session for a record read from the store,
// creating it when the session was opened on another replica.
func (sm *SessionManager) cached(id, org, hash string, rec *sessionRecord) *Session {
	if v, ok := sm.sessions.Load(id); ok {
		sess := v.(*Session)
		sess.mu.Lock()
		sess.record = *rec
		sess.mu.Unlock()
		return sess
	}
	var caps ClientCapabilities
	_ = json.Unmarshal(rec.ClientCapabilities, &caps)
	sess := &Session{
		ID: id, ClientInfo: rec.ClientInfo, Capabilities: caps, CreatedAt: time.UnixMilli(rec.CreatedMs),
		ProtocolVersion: rec.Era, org: org, hash: hash, record: *rec,
	}
	actual, _ := sm.sessions.LoadOrStore(id, sess)
	return actual.(*Session)
}

// refresh writes the record back with last_seen now, renewing its ttl and
// its event log's, when last_seen is older than refreshEvery (always when
// force is set). It reports false when the session no longer exists.
func (sm *SessionManager) refresh(ctx context.Context, store SessionStore, sess *Session, force bool) (bool, error) {
	sess.mu.Lock()
	now := time.Now()
	if !force && now.Sub(time.UnixMilli(sess.record.LastSeenMs)) < sm.refreshEvery() {
		sess.mu.Unlock()
		return true, nil
	}
	rec := sess.record
	rec.LastSeenMs = now.UnixMilli()
	sess.record = rec
	sess.mu.Unlock()

	data, _ := json.Marshal(rec)
	alive := true
	var err error
	if r, ok := store.(SessionRefresher); ok {
		alive, err = r.Refresh(ctx, sess.key(), sess.eventsKey(), data, sm.ttl)
	} else {
		err = store.Save(ctx, sess.key(), data, sm.ttl)
	}
	if err != nil {
		slog.Warn("mcp session store write failed", "session_hash", sess.hash, "error", err)
		return false, errSessionStore
	}
	if !alive {
		sm.forget(sess.ID)
	}
	return alive, nil
}

// end removes a session and its event log, and ends its streams here.
func (sm *SessionManager) end(ctx context.Context, store SessionStore, sess *Session) error {
	if err := store.Delete(ctx, sess.key()); err != nil {
		slog.Warn("mcp session store delete failed", "session_hash", sess.hash, "error", err)
		return errSessionStore
	}
	if log, ok := store.(SessionEventLog); ok {
		if err := log.DeleteSessionEvents(ctx, sess.eventsKey()); err != nil {
			slog.Warn("mcp session event log delete failed", "session_hash", sess.hash, "error", err)
		}
	}
	sm.forget(sess.ID)
	return nil
}

// forget drops this process's state of a session that ended: its cache
// entry, its in-memory event log and its open streams.
func (sm *SessionManager) forget(id string) {
	v, ok := sm.sessions.LoadAndDelete(id)
	if !ok {
		return
	}
	sess := v.(*Session)
	sm.events.delete(sess.eventsKey())
	sm.hub.endSession(sess.hash)
}

// Count returns the number of live sessions this process holds.
func (sm *SessionManager) Count() int {
	count := 0
	sm.sessions.Range(func(_, v interface{}) bool {
		if v.(*Session).IdleSince() <= sm.ttl {
			count++
		}
		return true
	})
	return count
}

// Close stops the reaper and ends every open stream.
func (sm *SessionManager) Close() {
	sm.closeOnce.Do(func() {
		close(sm.stopCh)
		sm.hub.close()
	})
}

// reaper periodically drops the sessions this process has not used for a
// ttl, and sweeps the memory store.
func (sm *SessionManager) reaper() {
	ticker := time.NewTicker(max(min(sm.ttl, time.Minute), time.Second))
	defer ticker.Stop()
	for {
		select {
		case <-sm.stopCh:
			return
		case <-ticker.C:
			sm.sessions.Range(func(key, value interface{}) bool {
				s := value.(*Session)
				if s.IdleSince() > sm.ttl && !sm.hub.hasStreams(s.hash) {
					sm.forget(key.(string))
				}
				return true
			})
			sm.mem.sweep()
		}
	}
}
