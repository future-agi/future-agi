package cache

import (
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Backend is the pluggable interface for exact-match (L1) cache backends.
// All implementations must be safe for concurrent use.
type Backend interface {
	// Get retrieves a cached response by key. Returns nil, false on miss/expiry/error.
	Get(key string) (*models.ChatCompletionResponse, bool)

	// Set stores a response with the given TTL.
	Set(key string, resp *models.ChatCompletionResponse, ttl time.Duration)

	// Delete removes a cache entry.
	Delete(key string)
}

// SemanticBackend is the pluggable interface for semantic (L2) cache backends.
// Implementations perform vector similarity search to find semantically similar cached responses.
type SemanticBackend interface {
	// Search finds the most similar cached response for the given vector and model.
	// Returns nil if no match exceeds the threshold.
	Search(vector []float32, model string) *SearchResult

	// Set stores a vector entry.
	Set(key string, vector []float32, model string, resp *models.ChatCompletionResponse, ttl time.Duration)

	// Len returns the number of entries.
	Len() int

	// Dims returns the configured vector dimensions.
	Dims() int
}
