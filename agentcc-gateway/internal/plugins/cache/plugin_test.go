package cache

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

func makeReq(model string, content string) *models.ChatCompletionRequest {
	return &models.ChatCompletionRequest{
		Model: model,
		Messages: []models.Message{
			{Role: "user", Content: json.RawMessage(`"` + content + `"`)},
		},
	}
}

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

func newTestPlugin() (*Plugin, *cache.Store) {
	store := cache.NewStore(1000)
	p := NewFromStore(store, 5*time.Minute)
	return p, store
}

func TestPlugin_CacheMiss(t *testing.T) {
	p, _ := newTestPlugin()
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = makeReq("gpt-4o", "hello")
	rc.Model = "gpt-4o"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Errorf("expected Continue on cache miss, got %v", result.Action)
	}
	if rc.Metadata["cache_status"] != "miss" {
		t.Errorf("cache_status = %q, want 'miss'", rc.Metadata["cache_status"])
	}
	if rc.Metadata["cache_key"] == "" {
		t.Error("cache_key should be set on miss")
	}
}

func TestPlugin_CacheHit(t *testing.T) {
	p, store := newTestPlugin()

	// Pre-populate cache.
	req := makeReq("gpt-4o", "hello")
	key := BuildCacheKey("default", req)
	cachedResp := makeResp("cached-1")
	store.Set(key, cachedResp, 5*time.Minute)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = req
	rc.Model = "gpt-4o"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("expected ShortCircuit on cache hit")
	}
	if rc.Metadata["cache_status"] != "hit_exact" {
		t.Errorf("cache_status = %q, want 'hit_exact'", rc.Metadata["cache_status"])
	}
	if !rc.Flags.CacheHit {
		t.Error("CacheHit flag should be set")
	}
	if rc.Response == nil || rc.Response.ID != "cached-1" {
		t.Error("response should be the cached response")
	}
}

func TestPlugin_StreamingBypass(t *testing.T) {
	p, _ := newTestPlugin()
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = makeReq("gpt-4o", "hello")
	rc.Model = "gpt-4o"
	rc.IsStream = true

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("streaming should bypass cache")
	}
	if rc.Metadata["cache_status"] != "skip" {
		t.Errorf("cache_status = %q, want 'skip'", rc.Metadata["cache_status"])
	}
}

func TestPlugin_ForceRefresh(t *testing.T) {
	p, store := newTestPlugin()

	// Pre-populate cache.
	req := makeReq("gpt-4o", "hello")
	key := BuildCacheKey("default", req)
	store.Set(key, makeResp("old"), 5*time.Minute)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = req
	rc.Model = "gpt-4o"
	rc.Metadata["cache_force_refresh"] = "true"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("force-refresh should skip cache read")
	}
	if rc.Metadata["cache_status"] != "miss" {
		t.Errorf("cache_status = %q, want 'miss'", rc.Metadata["cache_status"])
	}
}

func TestPlugin_CacheControlNoStore(t *testing.T) {
	p, _ := newTestPlugin()
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = makeReq("gpt-4o", "hello")
	rc.Model = "gpt-4o"
	rc.Metadata["cache_control"] = "no-store"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("no-store should skip cache")
	}
	if rc.Metadata["cache_status"] != "skip" {
		t.Errorf("cache_status = %q, want 'skip'", rc.Metadata["cache_status"])
	}
}

func TestPlugin_CacheControlNoCache(t *testing.T) {
	p, store := newTestPlugin()

	// Pre-populate cache.
	req := makeReq("gpt-4o", "hello")
	key := BuildCacheKey("default", req)
	store.Set(key, makeResp("old"), 5*time.Minute)

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = req
	rc.Model = "gpt-4o"
	rc.Metadata["cache_control"] = "no-cache"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("no-cache should skip cache read (but allow write)")
	}
	if rc.Metadata["cache_status"] != "miss" {
		t.Errorf("cache_status = %q, want 'miss'", rc.Metadata["cache_status"])
	}
}

func TestPlugin_PostResponseStores(t *testing.T) {
	p, store := newTestPlugin()

	req := makeReq("gpt-4o", "hello")

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = req
	rc.Model = "gpt-4o"

	// Simulate pre-request (miss).
	p.ProcessRequest(context.Background(), rc)

	// Simulate provider response.
	rc.Response = makeResp("provider-1")

	// Post-response should store it.
	p.ProcessResponse(context.Background(), rc)

	// Verify it's in the store.
	key := rc.Metadata["cache_key"]
	got, ok := store.Get(key)
	if !ok {
		t.Fatal("expected response to be cached after post-response")
	}
	if got.ID != "provider-1" {
		t.Errorf("cached ID = %q, want %q", got.ID, "provider-1")
	}
}

func TestPlugin_PostResponseSkipsOnCacheHit(t *testing.T) {
	p, store := newTestPlugin()

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Flags.CacheHit = true
	rc.Response = makeResp("should-not-store")
	rc.Metadata["cache_key"] = "some-key"

	p.ProcessResponse(context.Background(), rc)

	if store.Len() != 0 {
		t.Error("post-response should not store when CacheHit is true")
	}
}

func TestPlugin_PostResponseSkipsOnError(t *testing.T) {
	p, store := newTestPlugin()

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = makeReq("gpt-4o", "hello")
	rc.Model = "gpt-4o"
	rc.Response = makeResp("error-resp")
	rc.Metadata["cache_key"] = "some-key"
	rc.AddError(fmt.Errorf("provider error"))

	p.ProcessResponse(context.Background(), rc)

	if store.Len() != 0 {
		t.Error("post-response should not store when there are errors")
	}
}

func TestPlugin_PostResponseSkipsOnNoStore(t *testing.T) {
	p, store := newTestPlugin()

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Response = makeResp("resp")
	rc.Metadata["cache_key"] = "some-key"
	rc.Metadata["cache_control"] = "no-store"

	p.ProcessResponse(context.Background(), rc)

	if store.Len() != 0 {
		t.Error("post-response should not store with no-store")
	}
}

func TestPlugin_CustomTTL(t *testing.T) {
	p, store := newTestPlugin()

	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = makeReq("gpt-4o", "hello")
	rc.Model = "gpt-4o"
	rc.Metadata["cache_ttl"] = "50ms"

	// Pre-request.
	p.ProcessRequest(context.Background(), rc)

	// Simulate response.
	rc.Response = makeResp("ttl-test")

	// Post-response with custom TTL.
	p.ProcessResponse(context.Background(), rc)

	// Should be in cache.
	key := rc.Metadata["cache_key"]
	if _, ok := store.Get(key); !ok {
		t.Fatal("expected entry in cache immediately after set")
	}

	// Wait for custom TTL to expire.
	time.Sleep(60 * time.Millisecond)

	if _, ok := store.Get(key); ok {
		t.Error("expected cache entry to expire after custom TTL")
	}
}

func TestPlugin_NamespaceIsolation(t *testing.T) {
	p, store := newTestPlugin()

	req := makeReq("gpt-4o", "hello")

	// Store in namespace "ns1".
	key1 := BuildCacheKey("ns1", req)
	store.Set(key1, makeResp("ns1-resp"), 5*time.Minute)

	// Request in namespace "ns2" — should miss.
	rc := models.AcquireRequestContext()
	defer rc.Release()

	rc.Request = req
	rc.Model = "gpt-4o"
	rc.Metadata["cache_namespace"] = "ns2"

	result := p.ProcessRequest(context.Background(), rc)
	if result.Action != pipeline.Continue {
		t.Error("different namespace should be a cache miss")
	}

	// Request in namespace "ns1" — should hit.
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()

	rc2.Request = req
	rc2.Model = "gpt-4o"
	rc2.Metadata["cache_namespace"] = "ns1"

	result2 := p.ProcessRequest(context.Background(), rc2)
	if result2.Action != pipeline.ShortCircuit {
		t.Error("same namespace should be a cache hit")
	}
}

func TestBuildCacheKey_SameRequestSameKey(t *testing.T) {
	req1 := makeReq("gpt-4o", "hello world")
	req2 := makeReq("gpt-4o", "hello world")

	k1 := BuildCacheKey("default", req1)
	k2 := BuildCacheKey("default", req2)

	if k1 != k2 {
		t.Errorf("same requests should produce same key:\n  k1=%s\n  k2=%s", k1, k2)
	}
}

func TestBuildCacheKey_DifferentMessagesDifferentKey(t *testing.T) {
	req1 := makeReq("gpt-4o", "hello")
	req2 := makeReq("gpt-4o", "world")

	k1 := BuildCacheKey("default", req1)
	k2 := BuildCacheKey("default", req2)

	if k1 == k2 {
		t.Error("different messages should produce different keys")
	}
}

func TestBuildCacheKey_DifferentModelsDifferentKey(t *testing.T) {
	req1 := makeReq("gpt-4o", "hello")
	req2 := makeReq("gpt-4o-mini", "hello")

	k1 := BuildCacheKey("default", req1)
	k2 := BuildCacheKey("default", req2)

	if k1 == k2 {
		t.Error("different models should produce different keys")
	}
}

func TestBuildCacheKey_DifferentNamespacesDifferentKey(t *testing.T) {
	req := makeReq("gpt-4o", "hello")

	k1 := BuildCacheKey("ns1", req)
	k2 := BuildCacheKey("ns2", req)

	if k1 == k2 {
		t.Error("different namespaces should produce different keys")
	}
}

func TestBuildCacheKey_TemperatureAffectsKey(t *testing.T) {
	temp1 := 0.5
	temp2 := 0.9

	req1 := makeReq("gpt-4o", "hello")
	req1.Temperature = &temp1

	req2 := makeReq("gpt-4o", "hello")
	req2.Temperature = &temp2

	k1 := BuildCacheKey("default", req1)
	k2 := BuildCacheKey("default", req2)

	if k1 == k2 {
		t.Error("different temperatures should produce different keys")
	}
}

// --- Semantic Cache Tests ---

func newTestSemanticPlugin() (*Plugin, *cache.Store, *cache.SemanticStore) {
	store := cache.NewStore(1000)
	sem := cache.NewSemanticStore(0.8, 256, 1000)
	p := NewFromStore(store, 5*time.Minute)
	p.SetSemanticStoreFromConcrete(sem)
	return p, store, sem
}

func TestPlugin_SemanticCacheHit(t *testing.T) {
	p, _, _ := newTestSemanticPlugin()

	// Store a response via ProcessResponse.
	rc1 := models.AcquireRequestContext()
	rc1.Request = makeReq("gpt-4o", "what is the capital of France")
	rc1.Model = "gpt-4o"
	p.ProcessRequest(context.Background(), rc1)
	rc1.Response = makeResp("france-resp")
	p.ProcessResponse(context.Background(), rc1)
	rc1.Release()

	// Query with a similar prompt — should hit semantic cache.
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	rc2.Request = makeReq("gpt-4o", "what is the capital of france?")
	rc2.Model = "gpt-4o"

	result := p.ProcessRequest(context.Background(), rc2)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("expected semantic cache hit")
	}
	if rc2.Metadata["cache_status"] != "hit_semantic" {
		t.Errorf("cache_status = %q, want 'hit_semantic'", rc2.Metadata["cache_status"])
	}
	if rc2.Response == nil || rc2.Response.ID != "france-resp" {
		t.Error("should return semantically cached response")
	}
	if rc2.Metadata["cache_similarity"] == "" {
		t.Error("cache_similarity should be set")
	}
}

func TestPlugin_SemanticCacheMiss(t *testing.T) {
	p, _, _ := newTestSemanticPlugin()

	// Store a response.
	rc1 := models.AcquireRequestContext()
	rc1.Request = makeReq("gpt-4o", "what is the capital of France")
	rc1.Model = "gpt-4o"
	p.ProcessRequest(context.Background(), rc1)
	rc1.Response = makeResp("france-resp")
	p.ProcessResponse(context.Background(), rc1)
	rc1.Release()

	// Query with a very different prompt — should miss.
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	rc2.Request = makeReq("gpt-4o", "explain quantum computing in detail with examples of entanglement")
	rc2.Model = "gpt-4o"

	result := p.ProcessRequest(context.Background(), rc2)
	if result.Action != pipeline.Continue {
		t.Error("dissimilar prompt should miss semantic cache")
	}
	if rc2.Metadata["cache_status"] != "miss" {
		t.Errorf("cache_status = %q, want 'miss'", rc2.Metadata["cache_status"])
	}
}

func TestPlugin_SemanticCacheModelIsolation(t *testing.T) {
	p, _, _ := newTestSemanticPlugin()

	// Store for gpt-4o.
	rc1 := models.AcquireRequestContext()
	rc1.Request = makeReq("gpt-4o", "what is the capital of France")
	rc1.Model = "gpt-4o"
	p.ProcessRequest(context.Background(), rc1)
	rc1.Response = makeResp("france-4o")
	p.ProcessResponse(context.Background(), rc1)
	rc1.Release()

	// Query same text but different model — should miss.
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	rc2.Request = makeReq("gpt-3.5-turbo", "what is the capital of France")
	rc2.Model = "gpt-3.5-turbo"

	result := p.ProcessRequest(context.Background(), rc2)
	if result.Action != pipeline.Continue {
		t.Error("different model should not match semantic cache")
	}
}

func TestPlugin_ExactHitTakesPriority(t *testing.T) {
	p, _, _ := newTestSemanticPlugin()

	// Store a response (both exact and semantic).
	rc1 := models.AcquireRequestContext()
	rc1.Request = makeReq("gpt-4o", "what is the capital of France")
	rc1.Model = "gpt-4o"
	p.ProcessRequest(context.Background(), rc1)
	rc1.Response = makeResp("exact-resp")
	p.ProcessResponse(context.Background(), rc1)
	rc1.Release()

	// Query with identical prompt — should hit exact, not semantic.
	rc2 := models.AcquireRequestContext()
	defer rc2.Release()
	rc2.Request = makeReq("gpt-4o", "what is the capital of France")
	rc2.Model = "gpt-4o"

	result := p.ProcessRequest(context.Background(), rc2)
	if result.Action != pipeline.ShortCircuit {
		t.Fatal("should hit cache")
	}
	if rc2.Metadata["cache_status"] != "hit_exact" {
		t.Errorf("exact hit should take priority, got %q", rc2.Metadata["cache_status"])
	}
}

func TestPlugin_ExtractLastUserMessage(t *testing.T) {
	req := makeReq("gpt-4o", "hello world")
	text := extractLastUserMessage(req)
	if text != "hello world" {
		t.Errorf("extracted = %q", text)
	}
}

func TestPlugin_ExtractLastUserMessage_Empty(t *testing.T) {
	text := extractLastUserMessage(nil)
	if text != "" {
		t.Error("nil request should return empty")
	}
}
