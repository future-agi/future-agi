package cache

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"hash/fnv"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/cache"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin is a pipeline plugin that caches non-streaming chat completion responses.
// Supports dual-layer: L1 exact match + L2 semantic similarity.
// Supports pluggable backends via cache.Backend and cache.SemanticBackend interfaces.
type Plugin struct {
	store         cache.Backend
	semanticStore cache.SemanticBackend
	edgeHandler   *EdgeCacheHandler
	defaultTTL    time.Duration
}

// New creates a new cache plugin with a pluggable backend.
func New(store cache.Backend, defaultTTL time.Duration) *Plugin {
	if defaultTTL <= 0 {
		defaultTTL = 5 * time.Minute
	}
	return &Plugin{
		store:      store,
		defaultTTL: defaultTTL,
	}
}

// NewFromStore creates a cache plugin from an existing in-memory Store (backward compat).
func NewFromStore(store *cache.Store, defaultTTL time.Duration) *Plugin {
	return New(cache.NewMemoryBackend(store), defaultTTL)
}

// SetSemanticStore adds a semantic cache layer (L2) via the SemanticBackend interface.
func (p *Plugin) SetSemanticStore(s cache.SemanticBackend) {
	p.semanticStore = s
}

// SetSemanticStoreFromConcrete wraps an existing SemanticStore as a SemanticBackend (backward compat).
func (p *Plugin) SetSemanticStoreFromConcrete(s *cache.SemanticStore) {
	p.semanticStore = cache.NewMemorySemanticBackend(s)
}

// SetEdgeHandler configures edge (CDN) caching support.
func (p *Plugin) SetEdgeHandler(h *EdgeCacheHandler) {
	p.edgeHandler = h
}

func (p *Plugin) Name() string  { return "cache" }
func (p *Plugin) Priority() int { return 35 } // After auth (20) + RBAC (30), before budget/guardrails/validation/rate-limit. Cache hits skip all heavier plugins.

// ProcessRequest checks the cache for a hit before the provider call.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	// Per-org cache override: if the org has disabled caching, skip entirely.
	if rc.Metadata["org_cache_disabled"] == "true" {
		rc.Metadata["cache_status"] = "skip_org_disabled"
		return pipeline.ResultContinue()
	}

	// Streaming requests bypass cache.
	if rc.IsStream {
		rc.Metadata["cache_status"] = "skip"
		return pipeline.ResultContinue()
	}

	// Cache-Control: no-store skips caching entirely.
	if cc, ok := rc.Metadata["cache_control"]; ok && strings.Contains(cc, "no-store") {
		rc.Metadata["cache_status"] = "skip"
		return pipeline.ResultContinue()
	}

	if rc.Request == nil {
		rc.Metadata["cache_status"] = "skip"
		return pipeline.ResultContinue()
	}

	// Build cache key.
	namespace := rc.Metadata["cache_namespace"]
	if namespace == "" {
		namespace = "default"
	}
	key := BuildCacheKey(namespace, rc.Request)
	rc.Metadata["cache_key"] = key

	// Edge cache: check conditional request (If-None-Match → 304).
	if p.edgeHandler != nil && p.edgeHandler.IsEnabled() {
		if ifNoneMatch, ok := rc.Metadata["if_none_match"]; ok {
			if p.edgeHandler.CheckConditional(rc, ifNoneMatch) {
				rc.Metadata["cache_status"] = "hit_conditional"
				rc.Metadata["edge_cache"] = "HIT"
				// Return 304 via short-circuit with nil response (handler checks for 304).
				rc.Flags.CacheHit = true
				return pipeline.ResultShortCircuit(nil)
			}
		}
	}

	// Force-refresh: skip cache read but still write later.
	forceRefresh := rc.Metadata["cache_force_refresh"] == "true"
	noCache := false
	if cc, ok := rc.Metadata["cache_control"]; ok && strings.Contains(cc, "no-cache") {
		noCache = true
	}

	if forceRefresh || noCache {
		rc.Metadata["cache_status"] = "miss"
		return pipeline.ResultContinue()
	}

	// L1: Check exact-match cache.
	if resp, ok := p.store.Get(key); ok {
		rc.Response = resp
		rc.Flags.CacheHit = true
		rc.Metadata["cache_status"] = "hit_exact"

		// Edge cache headers.
		if p.edgeHandler != nil {
			p.edgeHandler.SetHeaders(rc, "HIT")
		}

		return pipeline.ResultShortCircuit(resp)
	}

	// L2: Check semantic cache.
	if p.semanticStore != nil {
		text := extractLastUserMessage(rc.Request)
		if text != "" {
			vec := cache.Vectorize(text, p.semanticStore.Dims())
			if result := p.semanticStore.Search(vec, rc.Request.Model); result != nil {
				rc.Response = result.Response
				rc.Flags.CacheHit = true
				rc.Metadata["cache_status"] = "hit_semantic"
				rc.Metadata["cache_similarity"] = strconv.FormatFloat(result.Similarity, 'f', 4, 64)

				if p.edgeHandler != nil {
					p.edgeHandler.SetHeaders(rc, "HIT")
				}

				return pipeline.ResultShortCircuit(result.Response)
			}
		}
	}

	rc.Metadata["cache_status"] = "miss"
	return pipeline.ResultContinue()
}

// ProcessResponse stores the response in the cache after a provider call.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	// Skip if already a cache hit, streaming, or no response.
	if rc.Flags.CacheHit || rc.IsStream || rc.Response == nil {
		return pipeline.ResultContinue()
	}

	// Skip if org has disabled caching.
	if rc.Metadata["org_cache_disabled"] == "true" {
		return pipeline.ResultContinue()
	}

	// Skip if no-store.
	if cc, ok := rc.Metadata["cache_control"]; ok && strings.Contains(cc, "no-store") {
		return pipeline.ResultContinue()
	}

	// Skip if there were errors.
	if len(rc.Errors) > 0 {
		return pipeline.ResultContinue()
	}

	key, ok := rc.Metadata["cache_key"]
	if !ok || key == "" {
		return pipeline.ResultContinue()
	}

	ttl := p.resolveTTL(rc)
	p.store.Set(key, rc.Response, ttl)

	// Also store in semantic cache (L2).
	if p.semanticStore != nil && rc.Request != nil {
		text := extractLastUserMessage(rc.Request)
		if text != "" {
			vec := cache.Vectorize(text, p.semanticStore.Dims())
			p.semanticStore.Set(key, vec, rc.Request.Model, rc.Response, ttl)
		}
	}

	// Edge cache headers for fresh responses.
	if p.edgeHandler != nil {
		p.edgeHandler.SetHeaders(rc, "MISS")
	}

	return pipeline.ResultContinue()
}

// resolveTTL returns the TTL for the cache entry.
// Priority: per-request header > per-org config > global default.
func (p *Plugin) resolveTTL(rc *models.RequestContext) time.Duration {
	// 1. Per-request TTL override (from x-agentcc-cache-ttl header).
	if v, ok := rc.Metadata["cache_ttl"]; ok {
		if d, err := time.ParseDuration(v); err == nil && d > 0 {
			return d
		}
	}
	// 2. Per-org TTL override (from org config).
	if v, ok := rc.Metadata["org_cache_ttl"]; ok {
		if d, err := time.ParseDuration(v); err == nil && d > 0 {
			return d
		}
	}
	return p.defaultTTL
}

// extractLastUserMessage gets the last user message text for semantic vectorization.
func extractLastUserMessage(req *models.ChatCompletionRequest) string {
	if req == nil || len(req.Messages) == 0 {
		return ""
	}
	// Walk backwards to find the last user message.
	for i := len(req.Messages) - 1; i >= 0; i-- {
		msg := req.Messages[i]
		if msg.Role != "user" {
			continue
		}
		// Content is json.RawMessage — try string first.
		var s string
		if err := json.Unmarshal(msg.Content, &s); err == nil {
			return s
		}
		// Try array of content parts.
		var parts []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		}
		if err := json.Unmarshal(msg.Content, &parts); err == nil {
			for _, part := range parts {
				if part.Type == "text" && part.Text != "" {
					return part.Text
				}
			}
		}
		// Fallback: use raw string representation.
		return string(msg.Content)
	}
	return ""
}

// BuildCacheKey generates a deterministic cache key from the request.
// Uses FNV-1a 128-bit for speed (no crypto needed, just collision resistance).
func BuildCacheKey(namespace string, req *models.ChatCompletionRequest) string {
	h := fnv.New128a()
	h.Write([]byte(namespace))
	h.Write([]byte("|"))
	h.Write([]byte(req.Model))
	h.Write([]byte("|"))

	// Messages: serialize deterministically.
	msgBytes, _ := json.Marshal(req.Messages)
	h.Write(msgBytes)

	// Include parameters that affect output — direct byte writes, no fmt.Fprintf.
	if req.Temperature != nil {
		h.Write([]byte("|t="))
		h.Write([]byte(strconv.FormatFloat(float64(*req.Temperature), 'g', -1, 32)))
	}
	if req.TopP != nil {
		h.Write([]byte("|p="))
		h.Write([]byte(strconv.FormatFloat(float64(*req.TopP), 'g', -1, 32)))
	}
	if req.MaxTokens != nil {
		h.Write([]byte("|m="))
		h.Write([]byte(strconv.Itoa(*req.MaxTokens)))
	}
	if req.MaxCompletionTokens != nil {
		h.Write([]byte("|mc="))
		h.Write([]byte(strconv.Itoa(*req.MaxCompletionTokens)))
	}
	if len(req.Tools) > 0 {
		toolBytes, _ := json.Marshal(req.Tools)
		h.Write([]byte("|tools="))
		h.Write(toolBytes)
	}
	if req.ToolChoice != nil {
		h.Write([]byte("|tool_choice="))
		h.Write(req.ToolChoice)
	}
	if req.ResponseFormat != nil {
		responseFormatBytes, _ := json.Marshal(req.ResponseFormat)
		h.Write([]byte("|response_format="))
		h.Write(responseFormatBytes)
	}
	if req.Seed != nil {
		h.Write([]byte("|seed="))
		h.Write([]byte(strconv.Itoa(*req.Seed)))
	}

	return hex.EncodeToString(h.Sum(nil))
}
