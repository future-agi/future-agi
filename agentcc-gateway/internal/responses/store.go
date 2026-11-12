package responses

import (
	"context"
	"sync"
	"time"
)

// DefaultResponseTTL is how long stored responses are kept.
const DefaultResponseTTL = 24 * time.Hour

// StoredResponse holds a response for later retrieval.
type StoredResponse struct {
	// deleted marks a payload-free tombstone retained for a full TTL after DELETE.
	deleted bool
	// ContinuationBinding is an opaque upstream namespace, never sent to callers.
	// An empty binding permits retrieval only, not native continuation.
	ContinuationBinding string
	ID                  string
	OrgID               string
	Body                []byte // Raw JSON response body
	CreatedAt           time.Time
	ExpiresAt           time.Time
	// Refresh is set only while a background response is pending. It captures
	// its originating provider, never a pooled request context.
	Refresh func(context.Context) ([]byte, error)
}

// Store is a thread-safe in-memory response store for the Responses API.
// Each org has its own response ids: the same id stores and reads separately
// per org, so no org can hold, read, replace or delete another org's
// response, whatever id its upstream answers with.
type Store struct {
	mu        sync.RWMutex
	responses map[storeKey]*StoredResponse
	ttl       time.Duration
}

// storeKey is a response's id within the org that stored it ("" is the
// no-org tenant, not a wildcard).
type storeKey struct {
	orgID, id string
}

// NewStore creates a new response store.
func NewStore(ttl time.Duration) *Store {
	if ttl <= 0 {
		ttl = DefaultResponseTTL
	}
	return &Store{
		responses: make(map[storeKey]*StoredResponse),
		ttl:       ttl,
	}
}

// Put stores a response under orgID unless that org has deleted the ID within
// the tombstone retention window. Another org's IDs never block or change this
// org's responses. An existing live response in the same org is replaced.
func (s *Store) Put(id, orgID string, body []byte) bool {
	return s.PutBound(id, orgID, "", body)
}

// PutBound stores an upstream response and its continuation authority together.
// A nil body records a streaming ID before a checked answer is available for
// retrieval. Such a record may authorize a continuation but Get cannot expose it.
func (s *Store) PutBound(id, orgID, binding string, body []byte) bool {
	return s.PutWithRefresh(id, orgID, binding, body, nil)
}

// PutWithRefresh stores a bound snapshot with an optional background retrieval
// call. Published snapshots are immutable, including their refresh function.
func (s *Store) PutWithRefresh(id, orgID, binding string, body []byte, refresh func(context.Context) ([]byte, error)) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	key := storeKey{orgID: orgID, id: id}
	if current := s.responses[key]; current != nil && current.deleted && !now.After(current.ExpiresAt) {
		return false
	}
	s.responses[key] = &StoredResponse{
		ContinuationBinding: binding,
		ID:                  id,
		OrgID:               orgID,
		Body:                body,
		CreatedAt:           now,
		ExpiresAt:           now.Add(s.ttl),
		Refresh:             refresh,
	}
	return true
}

// Get retrieves a response by ID. Returns nil if not found, expired, or not
// stored under orgID exactly: "" is the no-org tenant, not a wildcard.
func (s *Store) Get(id, orgID string) *StoredResponse {
	s.mu.RLock()
	defer s.mu.RUnlock()
	r := s.responses[storeKey{orgID: orgID, id: id}]
	if r == nil || r.deleted || len(r.Body) == 0 {
		return nil
	}
	if time.Now().After(r.ExpiresAt) {
		return nil
	}
	return r
}

// CanContinue requires both the exact tenant and the effective upstream
// namespace. Records from Put (without a binding), expired and deleted records
// cannot authorize provider-side context, even when the ID still exists upstream.
func (s *Store) CanContinue(id, orgID, binding string) bool {
	if binding == "" {
		return false
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	r := s.responses[storeKey{orgID: orgID, id: id}]
	return r != nil && !r.deleted && r.ContinuationBinding == binding && !time.Now().After(r.ExpiresAt)
}

// Update replaces only the snapshot that was polled. A concurrent refresh or
// new POST wins over a stale poll, and deletion or expiry cannot be undone.
// Refreshing preserves the original TTL and continuation binding. Terminal
// responses release the provider reference and need no more upstream calls.
func (s *Store) Update(previous *StoredResponse, body []byte, pending bool) *StoredResponse {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := storeKey{orgID: previous.OrgID, id: previous.ID}
	current := s.responses[key]
	if current == nil || current.deleted || time.Now().After(current.ExpiresAt) {
		return nil
	}
	if current != previous {
		return current
	}
	updated := *current
	updated.Body = body
	if !pending {
		updated.Refresh = nil
	}
	s.responses[key] = &updated
	return &updated
}

// Delete revokes a response stored under orgID exactly. A payload-free tombstone
// blocks in-flight publications for a full TTL from deletion, rather than only
// the old response's remaining lifetime. Returns false if absent or deleted.
func (s *Store) Delete(id, orgID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	k := storeKey{orgID: orgID, id: id}
	if current := s.responses[k]; current == nil || current.deleted {
		return false
	}
	now := time.Now()
	s.responses[k] = &StoredResponse{
		deleted:   true,
		ID:        id,
		OrgID:     orgID,
		CreatedAt: now,
		ExpiresAt: now.Add(s.ttl),
	}
	return true
}

// CleanExpired removes expired responses and tombstones. Returns the count removed.
func (s *Store) CleanExpired() int {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()

	removed := 0
	for k, r := range s.responses {
		if now.After(r.ExpiresAt) {
			delete(s.responses, k)
			removed++
		}
	}
	return removed
}
