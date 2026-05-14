package cache

import (
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// EdgeCacheHandler adds HTTP cache-control headers for CDN edge caching.
type EdgeCacheHandler struct {
	enabled         bool
	defaultTTL      int // seconds
	maxSize         int // max response size in bytes
	cacheableModels map[string]bool
	requireOptIn    bool
}

// NewEdgeCacheHandler creates an edge cache handler from config.
func NewEdgeCacheHandler(cfg config.CacheEdgeConfig) *EdgeCacheHandler {
	models := make(map[string]bool, len(cfg.CacheableModels))
	for _, m := range cfg.CacheableModels {
		models[m] = true
	}
	ttl := cfg.DefaultTTL
	if ttl <= 0 {
		ttl = 300 // 5 minutes default
	}
	maxSize := cfg.MaxSize
	if maxSize <= 0 {
		maxSize = 65536 // 64KB default
	}
	return &EdgeCacheHandler{
		enabled:         cfg.Enabled,
		defaultTTL:      ttl,
		maxSize:         maxSize,
		cacheableModels: models,
		requireOptIn:    cfg.RequireOptIn,
	}
}

// IsEnabled returns whether edge caching is configured.
func (e *EdgeCacheHandler) IsEnabled() bool {
	return e != nil && e.enabled
}

// IsEligible checks if a request/response pair is eligible for edge caching.
func (e *EdgeCacheHandler) IsEligible(rc *models.RequestContext) bool {
	if !e.enabled {
		return false
	}

	// Streaming responses are not cacheable at edge.
	if rc.IsStream {
		return false
	}

	// Check opt-in requirement.
	if e.requireOptIn {
		if rc.Metadata["edge_cache_opt_in"] != "true" {
			return false
		}
	}

	// Check model allowlist.
	if len(e.cacheableModels) > 0 {
		if !e.cacheableModels[rc.Model] && !e.cacheableModels[rc.ResolvedModel] {
			return false
		}
	}

	return true
}

// SetHeaders sets edge cache-related metadata on the RequestContext.
// These are read by the handler to set HTTP response headers.
func (e *EdgeCacheHandler) SetHeaders(rc *models.RequestContext, cacheStatus string) {
	if !e.enabled {
		return
	}

	if !e.IsEligible(rc) {
		rc.SetMetadata("edge_cache", "SKIP")
		rc.SetMetadata("edge_cache_control", "no-store")
		return
	}

	rc.SetMetadata("edge_cache", cacheStatus)
	rc.SetMetadata("edge_cache_control", "public, max-age="+strconv.Itoa(e.defaultTTL))
	rc.SetMetadata("edge_cache_vary", "Authorization")

	// ETag from cache key.
	if cacheKey, ok := rc.Metadata["cache_key"]; ok && cacheKey != "" {
		rc.SetMetadata("edge_cache_etag", `"`+cacheKey+`"`)
	}
}

// CheckConditional checks If-None-Match against cache key for 304 responses.
// Returns true if the conditional matches (caller should return 304).
func (e *EdgeCacheHandler) CheckConditional(rc *models.RequestContext, ifNoneMatch string) bool {
	if !e.enabled || ifNoneMatch == "" {
		return false
	}
	cacheKey, ok := rc.Metadata["cache_key"]
	if !ok || cacheKey == "" {
		return false
	}
	return ifNoneMatch == `"`+cacheKey+`"` || ifNoneMatch == cacheKey
}
