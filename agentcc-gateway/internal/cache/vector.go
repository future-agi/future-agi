package cache

import (
	"hash/fnv"
	"math"
	"strings"
	"unicode"
)

// Vectorize generates a fixed-dimension vector from text using character n-gram hashing.
// Uses 3-grams and 4-grams hashed to buckets via FNV-1a, then L2-normalizes.
func Vectorize(text string, dims int) []float32 {
	if dims <= 0 {
		dims = 256
	}
	if text == "" {
		return make([]float32, dims)
	}

	// Normalize text.
	text = normalize(text)

	vec := make([]float32, dims)

	// Extract and hash 3-grams and 4-grams.
	for n := 3; n <= 4; n++ {
		if len(text) < n {
			continue
		}
		for i := 0; i <= len(text)-n; i++ {
			gram := text[i : i+n]
			bucket := hashToBucket(gram, dims)
			vec[bucket] += 1.0
		}
	}

	// L2 normalize.
	l2Normalize(vec)
	return vec
}

// CosineSimilarity computes the cosine similarity between two vectors.
// Returns 0.0 if either vector is zero.
func CosineSimilarity(a, b []float32) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0
	}

	var dot, normA, normB float64
	for i := range a {
		dot += float64(a[i]) * float64(b[i])
		normA += float64(a[i]) * float64(a[i])
		normB += float64(b[i]) * float64(b[i])
	}

	denom := math.Sqrt(normA) * math.Sqrt(normB)
	if denom == 0 {
		return 0
	}
	return dot / denom
}

func normalize(text string) string {
	text = strings.ToLower(text)
	var b strings.Builder
	b.Grow(len(text))
	prevSpace := false
	for _, r := range text {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			prevSpace = false
		} else if !prevSpace {
			b.WriteByte(' ')
			prevSpace = true
		}
	}
	return strings.TrimSpace(b.String())
}

func hashToBucket(gram string, dims int) int {
	h := fnv.New32a()
	h.Write([]byte(gram))
	return int(h.Sum32()) % dims
}

func l2Normalize(vec []float32) {
	var sum float64
	for _, v := range vec {
		sum += float64(v) * float64(v)
	}
	norm := math.Sqrt(sum)
	if norm == 0 {
		return
	}
	for i := range vec {
		vec[i] = float32(float64(vec[i]) / norm)
	}
}
