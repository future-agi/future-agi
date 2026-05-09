package routing

import (
	"sync"
	"time"
)

// ShadowResult holds the captured production + shadow response pair.
type ShadowResult struct {
	RequestID       string    `json:"request_id"`
	ExperimentID    string    `json:"experiment_id,omitempty"`
	SourceModel     string    `json:"source_model"`
	ShadowModel     string    `json:"shadow_model"`
	SourceResponse  string    `json:"source_response"`
	ShadowResponse  string    `json:"shadow_response"`
	SourceLatencyMs int64     `json:"source_latency_ms"`
	ShadowLatencyMs int64     `json:"shadow_latency_ms"`
	SourceTokens    int       `json:"source_tokens"`
	ShadowTokens    int       `json:"shadow_tokens"`
	SourceStatus    int       `json:"source_status_code"`
	ShadowStatus    int       `json:"shadow_status_code"`
	ShadowError     string    `json:"shadow_error,omitempty"`
	PromptHash      string    `json:"prompt_hash,omitempty"`
	CreatedAt       time.Time `json:"created_at"`
}

// ShadowStore is a thread-safe ring buffer for captured shadow results.
type ShadowStore struct {
	mu      sync.Mutex
	buf     []ShadowResult
	cap     int
	head    int // next write position
	count   int
	flushed int64 // total results flushed so far
}

// NewShadowStore creates a new ring buffer with the given capacity.
func NewShadowStore(capacity int) *ShadowStore {
	if capacity <= 0 {
		capacity = 10000
	}
	return &ShadowStore{
		buf: make([]ShadowResult, capacity),
		cap: capacity,
	}
}

// Add appends a result to the ring buffer. If full, overwrites the oldest entry.
func (s *ShadowStore) Add(r ShadowResult) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.buf[s.head] = r
	s.head = (s.head + 1) % s.cap
	if s.count < s.cap {
		s.count++
	}
}

// DrainAll returns all buffered results and clears the buffer.
// This is used by the flusher to send results to Django.
func (s *ShadowStore) DrainAll() []ShadowResult {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.count == 0 {
		return nil
	}

	out := make([]ShadowResult, s.count)
	if s.count == s.cap {
		// Buffer is full — read from head (oldest) to end, then from start to head
		start := s.head // oldest entry
		for i := 0; i < s.count; i++ {
			out[i] = s.buf[(start+i)%s.cap]
		}
	} else {
		// Buffer is not full — entries are 0..count-1
		copy(out, s.buf[:s.count])
	}

	s.flushed += int64(s.count)
	s.count = 0
	s.head = 0
	return out
}

// Len returns the current number of buffered results.
func (s *ShadowStore) Len() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.count
}

// Stats returns summary statistics about the store.
type ShadowStoreStats struct {
	Buffered   int   `json:"buffered"`
	Capacity   int   `json:"capacity"`
	TotalAdded int64 `json:"total_added"`
}

func (s *ShadowStore) Stats() ShadowStoreStats {
	s.mu.Lock()
	defer s.mu.Unlock()
	return ShadowStoreStats{
		Buffered:   s.count,
		Capacity:   s.cap,
		TotalAdded: s.flushed + int64(s.count),
	}
}
