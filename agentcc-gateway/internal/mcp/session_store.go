package mcp

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strconv"
	"strings"
	"sync"
	"time"
)

// A legacy session (the 2025 revisions) lives in Deps.SessionStore as one
// record under its shared key, so any replica holding the same state keys and
// store resumes it. Its server-initiated messages live in an event log under
// the companion key, for Last-Event-ID replay.

// sessionEventLogMax is how many server events a session keeps for replay.
const sessionEventLogMax = 1000

// sessionKey and sessionEventsKey are the shared keys of a session, by its org
// and SessionHash. The org is the key's hash tag, so both keys of a session
// share a Redis slot.
func sessionKey(org, hash string) string       { return "mcps:{" + org + "}:" + hash }
func sessionEventsKey(org, hash string) string { return "mcpev:{" + org + "}:" + hash }

// SessionHash names a session without its id: the first 32 hex digits of the
// SHA-256 of the id. Logs, store keys and records carry it; the id itself is
// a bearer of the session and is never logged or stored.
func SessionHash(id string) string {
	sum := sha256.Sum256([]byte(id))
	return hex.EncodeToString(sum[:16])
}

// sessionRecord is a session as the store keeps it. It binds the session to
// the principal and virtual server that opened it.
type sessionRecord struct {
	V             int           `json:"v"`
	PrincipalKind PrincipalKind `json:"principal_kind,omitempty"`
	KeyID         string        `json:"key_id,omitempty"`
	UserID        string        `json:"user_id,omitempty"`
	ClientID      string        `json:"client_id,omitempty"`
	OrgID         string        `json:"org_id,omitempty"`
	// KeyHash binds a session opened before a principal is resolved to
	// the SHA-256 of the key authenticateHTTP checked.
	KeyHash       string `json:"key_hash,omitempty"`
	VirtualServer string `json:"virtual_server,omitempty"`
	// Era is the negotiated protocol revision.
	Era                string          `json:"era"`
	ClientInfo         Implementation  `json:"client_info"`
	ClientCapabilities json.RawMessage `json:"client_capabilities,omitempty"`
	CreatedMs          int64           `json:"created_ms"`
	LastSeenMs         int64           `json:"last_seen_ms"`
}

func (r *sessionRecord) binding() sessionBinding {
	return sessionBinding{
		Kind: r.PrincipalKind, KeyID: r.KeyID, UserID: r.UserID, ClientID: r.ClientID,
		OrgID: r.OrgID, KeyHash: r.KeyHash, VirtualServer: r.VirtualServer,
	}
}

// SessionRefresher is optional on a Deps.SessionStore: Refresh replaces the
// record under id and renews its ttl, and that of the event log under
// eventsID, only when the record still exists. It reports false when it does
// not, so a session ended elsewhere is never written back. A store without it
// is refreshed with Save.
type SessionRefresher interface {
	Refresh(ctx context.Context, id, eventsID string, data []byte, ttl time.Duration) (bool, error)
}

// SessionEventLog is optional on a Deps.SessionStore: it keeps a session's
// server events beside its record, so a stream resumed on another replica
// replays them. A store without it keeps them in process memory.
type SessionEventLog interface {
	// AppendSessionEvent appends data to the log under eventsID, keeping the
	// last maxLen events and renewing the log's ttl, and returns the event's
	// id. It appends nothing and reports false when the record under id no
	// longer exists.
	AppendSessionEvent(ctx context.Context, id, eventsID string, data []byte, maxLen int, ttl time.Duration) (string, bool, error)
	// SessionEventsAfter calls each, oldest first, for at most limit events
	// logged after afterID.
	SessionEventsAfter(ctx context.Context, eventsID, afterID string, limit int, each func(id string, data []byte)) error
	// DeleteSessionEvents removes the log.
	DeleteSessionEvents(ctx context.Context, eventsID string) error
}

// eventIDLess orders event ids of the form "<ms>-<seq>" (Redis stream ids,
// and the memory log's "0-<seq>"). ok is false when either is not one.
func eventIDLess(a, b string) (less, ok bool) {
	am, as, aok := parseEventID(a)
	bm, bs, bok := parseEventID(b)
	if !aok || !bok {
		return false, false
	}
	if am != bm {
		return am < bm, true
	}
	return as < bs, true
}

func parseEventID(id string) (ms, seq uint64, ok bool) {
	head, tail, found := strings.Cut(id, "-")
	if !found {
		return 0, 0, false
	}
	ms, err1 := strconv.ParseUint(head, 10, 64)
	seq, err2 := strconv.ParseUint(tail, 10, 64)
	return ms, seq, err1 == nil && err2 == nil
}

// memoryEventLog is the event log of sessions whose store keeps none: the
// last sessionEventLogMax events per session, ids "0-<seq>" increasing across
// the process.
type memoryEventLog struct {
	mu   sync.Mutex
	logs map[string][]loggedEvent
	seq  uint64
}

type loggedEvent struct {
	id   string
	data []byte
}

func newMemoryEventLog() *memoryEventLog {
	return &memoryEventLog{logs: map[string][]loggedEvent{}}
}

func (m *memoryEventLog) append(eventsID string, data []byte) string {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	id := "0-" + strconv.FormatUint(m.seq, 10)
	log := append(m.logs[eventsID], loggedEvent{id: id, data: append([]byte(nil), data...)})
	if len(log) > sessionEventLogMax {
		log = append([]loggedEvent(nil), log[len(log)-sessionEventLogMax:]...)
	}
	m.logs[eventsID] = log
	return id
}

func (m *memoryEventLog) after(eventsID, afterID string, limit int, each func(id string, data []byte)) {
	m.mu.Lock()
	log := append([]loggedEvent(nil), m.logs[eventsID]...)
	m.mu.Unlock()
	for _, ev := range log {
		if limit <= 0 {
			return
		}
		if less, ok := eventIDLess(afterID, ev.id); ok && less {
			each(ev.id, ev.data)
			limit--
		}
	}
}

func (m *memoryEventLog) delete(eventsID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.logs, eventsID)
}

// Compile-time checks: the default store refreshes without resurrecting.
var _ SessionRefresher = (*memorySessionStore)(nil)
