package cache

import (
	"encoding/json"
	"fmt"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func makeResp(id string) *models.ChatCompletionResponse {
	return &models.ChatCompletionResponse{
		ID:     id,
		Object: "chat.completion",
		Model:  "gpt-4o",
		Choices: []models.Choice{
			{
				Index: 0,
				Message: models.Message{
					Role:    "assistant",
					Content: json.RawMessage(`"hello"`),
				},
				FinishReason: "stop",
			},
		},
	}
}

func TestStore_SetAndGet(t *testing.T) {
	s := NewStore(100)
	resp := makeResp("1")

	s.Set("key1", resp, 1*time.Minute)

	got, ok := s.Get("key1")
	if !ok {
		t.Fatal("expected cache hit")
	}
	if got.ID != "1" {
		t.Errorf("got ID=%q, want %q", got.ID, "1")
	}
}

func TestStore_GetMiss(t *testing.T) {
	s := NewStore(100)

	_, ok := s.Get("nonexistent")
	if ok {
		t.Error("expected cache miss for nonexistent key")
	}
}

func TestStore_TTLExpiration(t *testing.T) {
	s := NewStore(100)
	resp := makeResp("1")

	s.Set("key1", resp, 10*time.Millisecond)

	// Should be found immediately.
	if _, ok := s.Get("key1"); !ok {
		t.Fatal("expected cache hit before expiration")
	}

	// Wait for expiration.
	time.Sleep(20 * time.Millisecond)

	_, ok := s.Get("key1")
	if ok {
		t.Error("expected cache miss after TTL expiration")
	}

	// Entry should be removed.
	if s.Len() != 0 {
		t.Errorf("store length = %d, want 0 after expiration", s.Len())
	}
}

func TestStore_LRUEviction(t *testing.T) {
	s := NewStore(3) // max 3 entries

	s.Set("a", makeResp("a"), 1*time.Minute)
	s.Set("b", makeResp("b"), 1*time.Minute)
	s.Set("c", makeResp("c"), 1*time.Minute)

	if s.Len() != 3 {
		t.Fatalf("store length = %d, want 3", s.Len())
	}

	// Adding a 4th entry should evict "a" (oldest).
	s.Set("d", makeResp("d"), 1*time.Minute)

	if s.Len() != 3 {
		t.Fatalf("store length = %d, want 3 after eviction", s.Len())
	}

	if _, ok := s.Get("a"); ok {
		t.Error("expected 'a' to be evicted")
	}

	// b, c, d should still be present.
	for _, key := range []string{"b", "c", "d"} {
		if _, ok := s.Get(key); !ok {
			t.Errorf("expected %q to be present", key)
		}
	}
}

func TestStore_LRUAccessPromotes(t *testing.T) {
	s := NewStore(3)

	s.Set("a", makeResp("a"), 1*time.Minute)
	s.Set("b", makeResp("b"), 1*time.Minute)
	s.Set("c", makeResp("c"), 1*time.Minute)

	// Access "a" to promote it to front.
	s.Get("a")

	// Add "d" — should evict "b" (now the oldest unused).
	s.Set("d", makeResp("d"), 1*time.Minute)

	if _, ok := s.Get("a"); !ok {
		t.Error("expected 'a' to be present (was promoted)")
	}
	if _, ok := s.Get("b"); ok {
		t.Error("expected 'b' to be evicted (oldest unused)")
	}
}

func TestStore_UpdateExisting(t *testing.T) {
	s := NewStore(100)

	s.Set("key1", makeResp("v1"), 1*time.Minute)
	s.Set("key1", makeResp("v2"), 1*time.Minute)

	got, ok := s.Get("key1")
	if !ok {
		t.Fatal("expected cache hit")
	}
	if got.ID != "v2" {
		t.Errorf("got ID=%q, want %q (updated value)", got.ID, "v2")
	}

	if s.Len() != 1 {
		t.Errorf("store length = %d, want 1 (no duplicate)", s.Len())
	}
}

func TestStore_ZeroTTLNotStored(t *testing.T) {
	s := NewStore(100)

	s.Set("key1", makeResp("1"), 0)

	if s.Len() != 0 {
		t.Error("expected zero TTL to not store entry")
	}
}

func TestStore_NegativeTTLNotStored(t *testing.T) {
	s := NewStore(100)

	s.Set("key1", makeResp("1"), -1*time.Second)

	if s.Len() != 0 {
		t.Error("expected negative TTL to not store entry")
	}
}

func TestStore_ConcurrentAccess(t *testing.T) {
	s := NewStore(1000)
	var wg sync.WaitGroup

	// Concurrent writers.
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			key := fmt.Sprintf("key-%d", i)
			s.Set(key, makeResp(key), 1*time.Minute)
		}(i)
	}

	// Concurrent readers.
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			key := fmt.Sprintf("key-%d", i)
			s.Get(key) // may or may not find it
		}(i)
	}

	wg.Wait()

	if s.Len() > 1000 {
		t.Errorf("store length = %d, exceeded max", s.Len())
	}
}

func TestStore_DefaultMaxEntries(t *testing.T) {
	s := NewStore(0) // should default to 10000

	if s.maxSize != 10000 {
		t.Errorf("maxSize = %d, want 10000 (default)", s.maxSize)
	}
}
