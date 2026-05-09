package cache

import (
	"sync"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// SemanticStore provides vector similarity-based caching.
// Thread-safe with per-store mutex. Uses brute-force linear scan.
type SemanticStore struct {
	mu        sync.Mutex
	entries   []*semanticEntry
	threshold float64
	dims      int
	maxSize   int
}

type semanticEntry struct {
	vector    []float32
	model     string
	key       string // exact-match key for dedup
	response  *models.ChatCompletionResponse
	expiresAt time.Time
	lastUsed  time.Time
}

// NewSemanticStore creates a new semantic cache store.
func NewSemanticStore(threshold float64, dims, maxEntries int) *SemanticStore {
	if threshold <= 0 || threshold > 1 {
		threshold = 0.85
	}
	if dims <= 0 {
		dims = 256
	}
	if maxEntries <= 0 {
		maxEntries = 50000
	}
	return &SemanticStore{
		entries:   make([]*semanticEntry, 0, 1024),
		threshold: threshold,
		dims:      dims,
		maxSize:   maxEntries,
	}
}

// SearchResult contains a semantic cache hit.
type SearchResult struct {
	Response   *models.ChatCompletionResponse
	Similarity float64
}

// Search finds the most similar cached response for the given vector and model.
// Returns nil if no match exceeds the threshold.
func (s *SemanticStore) Search(vector []float32, model string) *SearchResult {
	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()
	var bestEntry *semanticEntry
	var bestSim float64

	for _, e := range s.entries {
		// Skip expired.
		if now.After(e.expiresAt) {
			continue
		}
		// Model must match.
		if e.model != model {
			continue
		}

		sim := CosineSimilarity(vector, e.vector)
		if sim >= s.threshold && sim > bestSim {
			bestSim = sim
			bestEntry = e
		}
	}

	if bestEntry == nil {
		return nil
	}

	bestEntry.lastUsed = now
	return &SearchResult{
		Response:   copyResponse(bestEntry.response),
		Similarity: bestSim,
	}
}

// Set stores a vector entry in the semantic cache.
func (s *SemanticStore) Set(key string, vector []float32, model string, resp *models.ChatCompletionResponse, ttl time.Duration) {
	if ttl <= 0 || resp == nil {
		return
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	now := time.Now()
	expiresAt := now.Add(ttl)

	// Check for dedup by key.
	for _, e := range s.entries {
		if e.key == key && e.model == model {
			e.vector = vector
			e.response = resp
			e.expiresAt = expiresAt
			e.lastUsed = now
			return
		}
	}

	// Evict expired entries first.
	s.evictExpiredLocked(now)

	// Evict LRU if at capacity.
	for len(s.entries) >= s.maxSize {
		s.evictOldestLocked()
	}

	s.entries = append(s.entries, &semanticEntry{
		vector:    vector,
		model:     model,
		key:       key,
		response:  resp,
		expiresAt: expiresAt,
		lastUsed:  now,
	})
}

// Len returns the number of entries.
func (s *SemanticStore) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.entries)
}

// Dims returns the configured vector dimensions.
func (s *SemanticStore) Dims() int {
	return s.dims
}

func (s *SemanticStore) evictExpiredLocked(now time.Time) {
	n := 0
	for _, e := range s.entries {
		if !now.After(e.expiresAt) {
			s.entries[n] = e
			n++
		}
	}
	// Clear dangling pointers.
	for i := n; i < len(s.entries); i++ {
		s.entries[i] = nil
	}
	s.entries = s.entries[:n]
}

func (s *SemanticStore) evictOldestLocked() {
	if len(s.entries) == 0 {
		return
	}
	oldest := 0
	for i := 1; i < len(s.entries); i++ {
		if s.entries[i].lastUsed.Before(s.entries[oldest].lastUsed) {
			oldest = i
		}
	}
	// Remove by swapping with last.
	last := len(s.entries) - 1
	s.entries[oldest] = s.entries[last]
	s.entries[last] = nil
	s.entries = s.entries[:last]
}
