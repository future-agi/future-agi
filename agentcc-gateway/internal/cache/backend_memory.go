package cache

import (
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// MemoryBackend wraps the existing in-memory LRU Store as a Backend.
type MemoryBackend struct {
	store *Store
}

// NewMemoryBackend wraps an existing Store.
func NewMemoryBackend(s *Store) *MemoryBackend {
	return &MemoryBackend{store: s}
}

func (m *MemoryBackend) Get(key string) (*models.ChatCompletionResponse, bool) {
	return m.store.Get(key)
}

func (m *MemoryBackend) Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	m.store.Set(key, resp, ttl)
}

func (m *MemoryBackend) Delete(key string) {
	m.store.mu.Lock()
	defer m.store.mu.Unlock()
	if e, ok := m.store.entries[key]; ok {
		m.store.removeLocked(e)
	}
}
