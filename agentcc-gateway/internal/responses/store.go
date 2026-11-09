package responses

import (
	"sync"
	"time"
)

// DefaultResponseTTL is how long stored responses are kept.
const DefaultResponseTTL = 24 * time.Hour

// StoredResponse holds a response for later retrieval.
type StoredResponse struct {
	ID        string
	OrgID     string
	Body      []byte // Raw JSON response body
	CreatedAt time.Time
	ExpiresAt time.Time
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

// Put stores a response under orgID and reports whether it did, which it
// always does: the id comes from the upstream provider, which an org may
// configure itself, and an id another org holds is that org's alone, so it
// neither blocks this store nor is replaced by it. A Put under an id the org
// already holds replaces its own response.
func (s *Store) Put(id, orgID string, body []byte) bool {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	s.responses[storeKey{orgID: orgID, id: id}] = &StoredResponse{
		ID:        id,
		OrgID:     orgID,
		Body:      body,
		CreatedAt: now,
		ExpiresAt: now.Add(s.ttl),
	}
	return true
}

// Get retrieves a response by ID. Returns nil if not found, expired, or not
// stored under orgID exactly: "" is the no-org tenant, not a wildcard.
func (s *Store) Get(id, orgID string) *StoredResponse {
	s.mu.RLock()
	defer s.mu.RUnlock()
	r := s.responses[storeKey{orgID: orgID, id: id}]
	if r == nil {
		return nil
	}
	if time.Now().After(r.ExpiresAt) {
		return nil
	}
	return r
}

// Delete removes a response stored under orgID exactly. Returns true if
// deleted.
func (s *Store) Delete(id, orgID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	k := storeKey{orgID: orgID, id: id}
	if s.responses[k] == nil {
		return false
	}
	delete(s.responses, k)
	return true
}

// CleanExpired removes all expired responses. Returns the count removed.
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
