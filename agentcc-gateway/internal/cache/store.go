package cache

import (
	"container/list"
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Store is an in-memory LRU cache with TTL-based expiration.
type Store struct {
	mu      sync.Mutex
	entries map[string]*entry
	order   *list.List // front = most recently used
	maxSize int
}

type entry struct {
	key       string
	value     *models.ChatCompletionResponse
	expiresAt time.Time
	element   *list.Element
}

// NewStore creates a new cache store with the given maximum number of entries.
func NewStore(maxEntries int) *Store {
	if maxEntries <= 0 {
		maxEntries = 10000
	}
	return &Store{
		entries: make(map[string]*entry, maxEntries),
		order:   list.New(),
		maxSize: maxEntries,
	}
}

// Get retrieves a cached response by key. Returns nil, false if not found or expired.
// The returned response is a shallow copy so callers don't race with Set().
func (s *Store) Get(key string) (*models.ChatCompletionResponse, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()

	e, ok := s.entries[key]
	if !ok {
		return nil, false
	}

	// Check expiration.
	if time.Now().After(e.expiresAt) {
		s.removeLocked(e)
		return nil, false
	}

	// Move to front (most recently used).
	s.order.MoveToFront(e.element)

	// Return a copy to prevent races with concurrent Set() on the same key.
	cpy := copyResponse(e.value)
	return cpy, true
}

// copyResponse creates a shallow copy of the response with independent slices.
func copyResponse(src *models.ChatCompletionResponse) *models.ChatCompletionResponse {
	if src == nil {
		return nil
	}
	cpy := *src
	if len(src.Choices) > 0 {
		cpy.Choices = make([]models.Choice, len(src.Choices))
		copy(cpy.Choices, src.Choices)
	}
	if src.Usage != nil {
		u := *src.Usage
		cpy.Usage = &u
	}
	return &cpy
}

// Set stores a response in the cache with the given TTL.
func (s *Store) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 {
		return
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	expiresAt := time.Now().Add(ttl)

	// Update existing entry.
	if e, ok := s.entries[key]; ok {
		e.value = resp
		e.expiresAt = expiresAt
		s.order.MoveToFront(e.element)
		return
	}

	// Evict if at capacity.
	for s.order.Len() >= s.maxSize {
		s.evictOldestLocked()
	}

	// Insert new entry.
	e := &entry{
		key:       key,
		value:     resp,
		expiresAt: expiresAt,
	}
	e.element = s.order.PushFront(e)
	s.entries[key] = e
}

// Len returns the current number of entries in the cache.
func (s *Store) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.entries)
}

// removeLocked removes an entry (caller must hold lock).
func (s *Store) removeLocked(e *entry) {
	s.order.Remove(e.element)
	delete(s.entries, e.key)
}

// evictOldestLocked removes the least recently used entry (caller must hold lock).
func (s *Store) evictOldestLocked() {
	back := s.order.Back()
	if back == nil {
		return
	}
	e := back.Value.(*entry)
	s.removeLocked(e)
}
