package cache

import (
	"math"
	"sync"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

func testResponse(content string) *models.ChatCompletionResponse {
	return &models.ChatCompletionResponse{
		ID:    "resp-" + content,
		Model: "gpt-4o",
	}
}

// --- Vectorize ---

func TestVectorize_NonEmpty(t *testing.T) {
	vec := Vectorize("hello world", 256)
	if len(vec) != 256 {
		t.Fatalf("expected 256 dims, got %d", len(vec))
	}
	// Should be normalized (L2 norm ≈ 1.0).
	var norm float64
	for _, v := range vec {
		norm += float64(v) * float64(v)
	}
	norm = math.Sqrt(norm)
	if norm < 0.99 || norm > 1.01 {
		t.Errorf("L2 norm = %f, want ~1.0", norm)
	}
}

func TestVectorize_Empty(t *testing.T) {
	vec := Vectorize("", 256)
	if len(vec) != 256 {
		t.Fatalf("expected 256 dims, got %d", len(vec))
	}
	// All zeros.
	for _, v := range vec {
		if v != 0 {
			t.Error("empty text should produce zero vector")
			break
		}
	}
}

func TestVectorize_SimilarTexts(t *testing.T) {
	a := Vectorize("what is the capital of France", 256)
	b := Vectorize("what is the capital of france?", 256)
	c := Vectorize("explain quantum computing in simple terms", 256)

	simAB := CosineSimilarity(a, b)
	simAC := CosineSimilarity(a, c)

	if simAB < 0.9 {
		t.Errorf("similar texts should have high similarity: %f", simAB)
	}
	if simAC > simAB {
		t.Errorf("dissimilar should have lower sim than similar: AB=%f, AC=%f", simAB, simAC)
	}
}

func TestVectorize_DifferentTexts(t *testing.T) {
	a := Vectorize("what is the meaning of life", 256)
	b := Vectorize("how to cook pasta carbonara", 256)

	sim := CosineSimilarity(a, b)
	if sim > 0.9 {
		t.Errorf("dissimilar texts should have low similarity: %f", sim)
	}
}

func TestVectorize_DefaultDims(t *testing.T) {
	vec := Vectorize("test", 0)
	if len(vec) != 256 {
		t.Errorf("default dims should be 256, got %d", len(vec))
	}
}

// --- CosineSimilarity ---

func TestCosineSimilarity_Identical(t *testing.T) {
	a := Vectorize("hello world", 256)
	sim := CosineSimilarity(a, a)
	if sim < 0.999 {
		t.Errorf("identical vectors should have sim ~1.0: %f", sim)
	}
}

func TestCosineSimilarity_Orthogonal(t *testing.T) {
	a := make([]float32, 4)
	b := make([]float32, 4)
	a[0] = 1.0
	b[1] = 1.0
	sim := CosineSimilarity(a, b)
	if sim != 0 {
		t.Errorf("orthogonal vectors should have sim 0: %f", sim)
	}
}

func TestCosineSimilarity_DifferentLengths(t *testing.T) {
	a := make([]float32, 3)
	b := make([]float32, 4)
	sim := CosineSimilarity(a, b)
	if sim != 0 {
		t.Error("different length vectors should return 0")
	}
}

func TestCosineSimilarity_ZeroVector(t *testing.T) {
	a := make([]float32, 4)
	b := Vectorize("test", 4)
	sim := CosineSimilarity(a, b)
	if sim != 0 {
		t.Errorf("zero vector should have sim 0: %f", sim)
	}
}

// --- SemanticStore ---

func TestSemanticStore_SetAndSearch(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	text := "what is the capital of France"
	vec := Vectorize(text, 256)
	resp := testResponse("paris")

	s.Set("key1", vec, "gpt-4o", resp, 5*time.Minute)

	// Search with similar text.
	queryVec := Vectorize("what is the capital of france?", 256)
	result := s.Search(queryVec, "gpt-4o")
	if result == nil {
		t.Fatal("should find similar cached response")
	}
	if result.Response.ID != "resp-paris" {
		t.Errorf("response ID = %q", result.Response.ID)
	}
	if result.Similarity < 0.8 {
		t.Errorf("similarity = %f", result.Similarity)
	}
}

func TestSemanticStore_ModelMismatch(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	vec := Vectorize("what is the capital of France", 256)
	s.Set("key1", vec, "gpt-4o", testResponse("test"), 5*time.Minute)

	// Search with different model.
	result := s.Search(vec, "gpt-3.5-turbo")
	if result != nil {
		t.Error("different model should not match")
	}
}

func TestSemanticStore_BelowThreshold(t *testing.T) {
	s := NewSemanticStore(0.95, 256, 100) // Very high threshold.
	vec1 := Vectorize("what is the capital of France", 256)
	vec2 := Vectorize("explain quantum computing basics", 256)
	s.Set("key1", vec1, "gpt-4o", testResponse("test"), 5*time.Minute)

	result := s.Search(vec2, "gpt-4o")
	if result != nil {
		t.Error("dissimilar text should not match with high threshold")
	}
}

func TestSemanticStore_Expiration(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	vec := Vectorize("test", 256)
	s.Set("key1", vec, "gpt-4o", testResponse("test"), 1*time.Millisecond)

	time.Sleep(5 * time.Millisecond)
	result := s.Search(vec, "gpt-4o")
	if result != nil {
		t.Error("expired entry should not match")
	}
}

func TestSemanticStore_Dedup(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	vec := Vectorize("test", 256)

	s.Set("key1", vec, "gpt-4o", testResponse("first"), 5*time.Minute)
	s.Set("key1", vec, "gpt-4o", testResponse("second"), 5*time.Minute)

	if s.Len() != 1 {
		t.Errorf("dedup should keep 1 entry, got %d", s.Len())
	}

	result := s.Search(vec, "gpt-4o")
	if result == nil || result.Response.ID != "resp-second" {
		t.Error("should return updated response")
	}
}

func TestSemanticStore_LRUEviction(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 3)

	for i := 0; i < 5; i++ {
		vec := Vectorize("entry "+string(rune('a'+i)), 256)
		s.Set("key"+string(rune('0'+i)), vec, "gpt-4o", testResponse("test"), 5*time.Minute)
	}

	if s.Len() > 3 {
		t.Errorf("should evict to max size, got %d", s.Len())
	}
}

func TestSemanticStore_Len(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	if s.Len() != 0 {
		t.Error("empty store should have length 0")
	}
	vec := Vectorize("test", 256)
	s.Set("key1", vec, "gpt-4o", testResponse("test"), 5*time.Minute)
	if s.Len() != 1 {
		t.Errorf("length = %d", s.Len())
	}
}

func TestSemanticStore_Dims(t *testing.T) {
	s := NewSemanticStore(0.8, 128, 100)
	if s.Dims() != 128 {
		t.Errorf("dims = %d", s.Dims())
	}
}

func TestSemanticStore_Defaults(t *testing.T) {
	s := NewSemanticStore(0, 0, 0)
	if s.threshold != 0.85 {
		t.Errorf("default threshold = %f", s.threshold)
	}
	if s.dims != 256 {
		t.Errorf("default dims = %d", s.dims)
	}
	if s.maxSize != 50000 {
		t.Errorf("default maxSize = %d", s.maxSize)
	}
}

func TestSemanticStore_NilResponse(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	vec := Vectorize("test", 256)
	s.Set("key1", vec, "gpt-4o", nil, 5*time.Minute)
	if s.Len() != 0 {
		t.Error("nil response should not be stored")
	}
}

func TestSemanticStore_ZeroTTL(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 100)
	vec := Vectorize("test", 256)
	s.Set("key1", vec, "gpt-4o", testResponse("test"), 0)
	if s.Len() != 0 {
		t.Error("zero TTL should not be stored")
	}
}

func TestSemanticStore_Concurrent(t *testing.T) {
	s := NewSemanticStore(0.8, 256, 1000)
	var wg sync.WaitGroup
	n := 100
	wg.Add(n * 2)
	for i := 0; i < n; i++ {
		go func(idx int) {
			defer wg.Done()
			vec := Vectorize("concurrent test entry", 256)
			s.Set("key"+string(rune(idx)), vec, "gpt-4o", testResponse("test"), 5*time.Minute)
		}(i)
		go func() {
			defer wg.Done()
			vec := Vectorize("concurrent test entry", 256)
			s.Search(vec, "gpt-4o")
		}()
	}
	wg.Wait()
}

// --- normalize ---

func TestNormalize(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"Hello, World!", "hello world"},
		{"  multiple   spaces  ", "multiple spaces"},
		{"UPPER CASE", "upper case"},
		{"special @#$ chars", "special chars"},
	}
	for _, tt := range tests {
		got := normalize(tt.input)
		if got != tt.expected {
			t.Errorf("normalize(%q) = %q, want %q", tt.input, got, tt.expected)
		}
	}
}
