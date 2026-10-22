package cache

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Request-level cache control headers (Cloudflare- and
// Portkey-style): a caller tunes the cache of one request without an org
// config edit.
const (
	// HeaderCacheKey replaces the request's cache key while the org cache
	// is enabled. The entry stays within the caller's org and namespace.
	HeaderCacheKey = "x-agentcc-cache-key"
	// HeaderCacheTTL is the entry's TTL in seconds (a Go duration such as
	// "10m" is also read), clamped to MinCacheTTL..MaxCacheTTL. An
	// unreadable value is ignored.
	HeaderCacheTTL = "x-agentcc-cache-ttl"
	// HeaderCacheMode "bypass" skips the cache: no read and no write.
	HeaderCacheMode = "x-agentcc-cache"
	// HeaderCacheStatus is set on every request the plugin checks and does
	// not refuse: HIT, MISS or BYPASS.
	HeaderCacheStatus = "x-agentcc-cache-status"

	// ErrCodeInvalidCacheHeader is the 400 for a malformed cache header;
	// the error's param names the header.
	ErrCodeInvalidCacheHeader = "invalid_cache_header"

	MaxCacheKeyLen = 256
	MinCacheTTL    = 60 * time.Second
	MaxCacheTTL    = 2_592_000 * time.Second // 30 days
)

// The canonical forms of the request header names, computed once:
// canonicalizing a lowercase name on every request allocates.
var (
	canonCacheKey  = http.CanonicalHeaderKey(HeaderCacheKey)
	canonCacheTTL  = http.CanonicalHeaderKey(HeaderCacheTTL)
	canonCacheMode = http.CanonicalHeaderKey(HeaderCacheMode)
)

// Values of HeaderCacheStatus.
const (
	statusHit    = "HIT"
	statusMiss   = "MISS"
	statusBypass = "BYPASS"
)

// requestControls are one request's cache headers, validated.
type requestControls struct {
	key    string // the caller's cache key, "" when not sent
	ttl    time.Duration
	hasTTL bool
	bypass bool
}

// parseControls reads and validates the request's cache headers. The key
// or the mode header, sent but malformed, is a 400 naming it. An
// unreadable or negative TTL is ignored, so no caller is refused for it.
func parseControls(rc *models.RequestContext) (requestControls, *models.APIError) {
	var c requestControls
	if v, ok := requestHeader(rc, canonCacheMode); ok {
		if !strings.EqualFold(v, "bypass") {
			return c, invalidCacheHeader(HeaderCacheMode, `must be "bypass"`)
		}
		c.bypass = true
	}
	if v, ok := requestHeader(rc, canonCacheKey); ok {
		if v == "" || len(v) > MaxCacheKeyLen {
			return c, invalidCacheHeader(HeaderCacheKey, "must be 1 to 256 characters")
		}
		c.key = v
	}
	v, ok := requestHeader(rc, canonCacheTTL)
	if !ok {
		// The chat and completion handlers copy the header here.
		v, ok = rc.Metadata["cache_ttl"], rc.Metadata["cache_ttl"] != ""
	}
	if ok {
		c.ttl, c.hasTTL = parseTTL(v)
	}
	return c, nil
}

// parseTTL reads a TTL as whole seconds, or as a Go duration, and clamps it
// to MinCacheTTL..MaxCacheTTL. A negative or unreadable value is invalid.
func parseTTL(v string) (time.Duration, bool) {
	var d time.Duration
	if secs, err := strconv.ParseInt(v, 10, 64); err == nil {
		if secs < 0 {
			return 0, false
		}
		if secs > int64(MaxCacheTTL/time.Second) {
			return MaxCacheTTL, true
		}
		d = time.Duration(secs) * time.Second
	} else {
		parsed, err := time.ParseDuration(v)
		if err != nil || parsed < 0 {
			return 0, false
		}
		d = parsed
	}
	return min(max(d, MinCacheTTL), MaxCacheTTL), true
}

// requestHeader is the request's trimmed value for the canonical header
// name, and whether the header was sent at all (an empty value counts as
// sent).
func requestHeader(rc *models.RequestContext, canonical string) (string, bool) {
	vs, ok := rc.RequestHeaders[canonical]
	if !ok || len(vs) == 0 {
		return "", false
	}
	return strings.TrimSpace(vs[0]), true
}

func invalidCacheHeader(header, reason string) *models.APIError {
	param := header
	apiErr := models.ErrBadRequest(ErrCodeInvalidCacheHeader, header+" "+reason)
	apiErr.Param = &param
	return apiErr
}

// buildCustomCacheKey is the store key for a caller's own cache key: a hash
// of the org's cache partition (scope, from cache.OrgScope), the namespace
// and the key, so each org's entries stay inside its own partition
// whatever key is sent, and the key never names a default-keyed entry. Like
// BuildCacheKey it is SHA-256 truncated to 128 bits: the caller chooses the
// key, so the hash must resist chosen-input collisions where it is the
// tenant boundary.
func buildCustomCacheKey(scope, namespace, key string) string {
	h := sha256.New()
	h.Write([]byte("custom-v2\x00"))
	h.Write([]byte(scope))
	h.Write([]byte{0})
	h.Write([]byte(namespace))
	h.Write([]byte{0})
	h.Write([]byte(key))
	return hex.EncodeToString(h.Sum(nil)[:16])
}

// cacheStatusHeader maps the plugin's internal cache_status to the
// HeaderCacheStatus value.
func cacheStatusHeader(cacheStatus string) string {
	switch cacheStatus {
	case "hit_exact", "hit_semantic", "hit_conditional":
		return statusHit
	case "miss":
		return statusMiss
	default:
		return statusBypass
	}
}

// setStatus records the internal cache_status and the response header for
// it.
func setStatus(rc *models.RequestContext, cacheStatus string) {
	rc.Metadata["cache_status"] = cacheStatus
	rc.SetResponseHeader(HeaderCacheStatus, cacheStatusHeader(cacheStatus))
}

// fuzzyAllowed reports whether the request may use the fuzzy (semantic,
// n-gram vector) layer: never for agent traffic, where replaying a
// near-match into a multi-turn loop is a stale answer,
// and never under a caller-chosen key, which is exact by definition.
func fuzzyAllowed(rc *models.RequestContext, c requestControls) bool {
	return rc.Metadata["agent_tagged"] != "true" && c.key == ""
}
