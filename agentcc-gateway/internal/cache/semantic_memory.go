package cache

import (
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// MemorySemanticBackend wraps the existing in-memory SemanticStore as a SemanticBackend.
type MemorySemanticBackend struct {
	store *SemanticStore
}

// NewMemorySemanticBackend wraps an existing SemanticStore.
func NewMemorySemanticBackend(s *SemanticStore) *MemorySemanticBackend {
	return &MemorySemanticBackend{store: s}
}

func (m *MemorySemanticBackend) Search(vector []float32, model string) *SearchResult {
	return m.store.Search(vector, model)
}

func (m *MemorySemanticBackend) Set(key string, vector []float32, model string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	m.store.Set(key, vector, model, resp, ttl)
}

func (m *MemorySemanticBackend) Len() int {
	return m.store.Len()
}

func (m *MemorySemanticBackend) Dims() int {
	return m.store.Dims()
}
