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
type Store struct {
	mu        sync.RWMutex
	responses map[string]*StoredResponse
	ttl       time.Duration
}

// NewStore creates a new response store.
func NewStore(ttl time.Duration) *Store {
	if ttl <= 0 {
		ttl = DefaultResponseTTL
	}
	return &Store{
		responses: make(map[string]*StoredResponse),
		ttl:       ttl,
	}
}

// Put stores a response under orgID and reports whether it did. It never
// replaces another org's unexpired response under the same id: it stores
// nothing and returns false.
func (s *Store) Put(id, orgID string, body []byte) bool {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()
	if existing := s.responses[id]; existing != nil && existing.OrgID != orgID && now.Before(existing.ExpiresAt) {
		return false
	}
	s.responses[id] = &StoredResponse{
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
	r := s.responses[id]
	if r == nil {
		return nil
	}
	if time.Now().After(r.ExpiresAt) {
		return nil
	}
	if r.OrgID != orgID {
		return nil
	}
	return r
}

// Delete removes a response stored under orgID exactly. Returns true if
// deleted.
func (s *Store) Delete(id, orgID string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	r := s.responses[id]
	if r == nil {
		return false
	}
	if r.OrgID != orgID {
		return false
	}
	delete(s.responses, id)
	return true
}

// CleanExpired removes all expired responses. Returns the count removed.
func (s *Store) CleanExpired() int {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()

	removed := 0
	for id, r := range s.responses {
		if now.After(r.ExpiresAt) {
			delete(s.responses, id)
			removed++
		}
	}
	return removed
}
