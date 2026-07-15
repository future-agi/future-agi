package cache

import (
	"context"
	"encoding/binary"
	"math"
	"os"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func TestFloat32SliceToBytes(t *testing.T) {
	input := []float32{1.0, 0.5, -1.0, 0.0}
	got := float32SliceToBytes(input)

	if len(got) != 16 {
		t.Fatalf("expected 16 bytes, got %d", len(got))
	}

	for i, want := range input {
		bits := binary.LittleEndian.Uint32(got[i*4:])
		gotVal := math.Float32frombits(bits)
		if gotVal != want {
			t.Errorf("index %d: want %f, got %f", i, want, gotVal)
		}
	}
}

func TestFloat32SliceToBytesEmpty(t *testing.T) {
	got := float32SliceToBytes(nil)
	if len(got) != 0 {
		t.Fatalf("expected 0 bytes for nil input, got %d", len(got))
	}
}

func TestEscapeTag(t *testing.T) {
	cases := []struct {
		input string
		want  string
	}{
		{"gpt-4o", "gpt\\-4o"},
		{"simple", "simple"},
		{"has space", "has\\ space"},
		{"colon:value", "colon\\:value"},
		{"meta/llama-3", "meta\\/llama\\-3"},
	}
	for _, tc := range cases {
		got := escapeTag(tc.input)
		if got != tc.want {
			t.Errorf("escapeTag(%q) = %q, want %q", tc.input, got, tc.want)
		}
	}
}

// TestValkeyBackendIntegration runs against a live Valkey instance.
// Set VALKEY_TEST_ADDRESS (e.g. "localhost:6389") to enable.
func TestValkeyBackendIntegration(t *testing.T) {
	addr := os.Getenv("VALKEY_TEST_ADDRESS")
	if addr == "" {
		t.Skip("VALKEY_TEST_ADDRESS not set; skipping Valkey integration test")
	}
	password := os.Getenv("VALKEY_TEST_PASSWORD")

	dims := 4
	threshold := 0.80

	backend, err := NewValkeyBackend(addr, password, "test_semantic_cache", "test_sc:", threshold, dims, 5*time.Second)
	if err != nil {
		t.Fatalf("NewValkeyBackend: %v", err)
	}
	defer backend.Close()

	// Clean up test index on exit.
	defer func() {
		ctx := context.Background()
		backend.client.Do(ctx, backend.client.B().Arbitrary("FT.DROPINDEX", "test_semantic_cache", "DD").Build())
	}()

	t.Run("SetAndSearch", func(t *testing.T) {
		resp := &models.ChatCompletionResponse{
			ID:    "test-1",
			Model: "gpt-4o",
		}

		vec := []float32{0.5, 0.5, 0.5, 0.5}
		backend.Set("key1", vec, "gpt-4o", resp, 60*time.Second)

		// Wait for indexing.
		time.Sleep(1500 * time.Millisecond)

		// Verify data was stored and indexed.
		ctx := context.Background()
		keysResp := backend.client.Do(ctx, backend.client.B().Arbitrary("KEYS", "test_sc:*").Build())
		if keys, err := keysResp.AsStrSlice(); err == nil {
			t.Logf("Keys matching test_sc:*: %v", keys)
		}
		t.Logf("Len after Set: %d", backend.Len())

		// Search with the same vector — should hit.
		result := backend.Search(vec, "gpt-4o")
		if result == nil {
			t.Fatal("expected a search hit, got nil")
		}
		if result.Response.ID != "test-1" {
			t.Errorf("expected response ID 'test-1', got %q", result.Response.ID)
		}
		if result.Similarity < threshold {
			t.Errorf("similarity %f below threshold %f", result.Similarity, threshold)
		}
	})

	t.Run("SearchMiss_DifferentModel", func(t *testing.T) {
		vec := []float32{0.5, 0.5, 0.5, 0.5}
		result := backend.Search(vec, "claude-3-opus")
		if result != nil {
			t.Errorf("expected nil for different model, got similarity %f", result.Similarity)
		}
	})

	t.Run("SearchMiss_OrthogonalVector", func(t *testing.T) {
		vec := []float32{1.0, -1.0, 0.0, 0.0}
		result := backend.Search(vec, "gpt-4o")
		if result != nil && result.Similarity >= threshold {
			t.Errorf("expected miss for orthogonal vector, got similarity %f", result.Similarity)
		}
	})

	t.Run("Len", func(t *testing.T) {
		n := backend.Len()
		if n < 1 {
			t.Errorf("expected at least 1 entry, got %d", n)
		}
	})

	t.Run("Dims", func(t *testing.T) {
		if backend.Dims() != dims {
			t.Errorf("expected dims=%d, got %d", dims, backend.Dims())
		}
	})
}
