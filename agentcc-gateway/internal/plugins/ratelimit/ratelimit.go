package ratelimit

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Plugin is a pipeline plugin that enforces request and token rate limits:
// each key's RPM (the key's own limit, else its org's per_key_rpm, else the
// node's global_rpm), each key's own token limit (rate_limit_tpm), and the
// org's rate_limiting section (orglimits.go).
type Plugin struct {
	limiter  Limiter
	multi    redisstate.MultiLimiter
	keyStore *auth.KeyStore
	cfg      config.RateLimitConfig

	// own are the in-memory limiters New made itself, which Stop stops.
	own      []*RateLimiter
	stopOnce sync.Once

	noTokenBudgetOnce sync.Once
}

// New creates a new rate limit plugin.
// If limiter is nil, an in-memory RateLimiter is created as the default.
// The org's limits use the limiter too when it can check several counters at
// once (redisstate.MultiLimiter: the in-memory and the Redis-backed limiter
// both can); any other limiter gets an in-memory one for the org's limits,
// which then also counts a key's RPM for the requests of an org with limits
// (the given limiter counts it for the others). Stop stops the limiters New
// made; the caller stops its own.
func New(cfg config.RateLimitConfig, keyStore *auth.KeyStore, limiter Limiter) *Plugin {
	p := &Plugin{keyStore: keyStore, cfg: cfg}
	if limiter == nil {
		rl := NewRateLimiter()
		p.own = append(p.own, rl)
		limiter = rl
	}
	multi, ok := limiter.(redisstate.MultiLimiter)
	if !ok {
		rl := NewRateLimiter()
		p.own = append(p.own, rl)
		multi = rl
	}
	p.limiter, p.multi = limiter, multi
	return p
}

// Stop stops the cleanup goroutines of the in-memory limiters New made
// itself; never the limiter the caller passed in. Calling it again does
// nothing.
func (p *Plugin) Stop() {
	p.stopOnce.Do(func() {
		for _, rl := range p.own {
			rl.Stop()
		}
	})
}

func (p *Plugin) Name() string { return "ratelimit" }

// Priority 80: after validation (70). The response cache (35) runs before
// it, so an answer served from the cache is neither counted nor refused here.
func (p *Plugin) Priority() int { return 80 }

// ProcessRequest checks rate limits and rejects excess requests.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.cfg.Enabled {
		return pipeline.ResultContinue()
	}

	// Determine key ID and RPM limit.
	keyID := rc.Metadata["auth_key_id"]
	rpm := 0
	source := limitKeyOwn

	if keyID != "" && p.keyStore != nil {
		// Look up per-key RPM limit.
		key := p.keyStore.Get(keyID)
		if key != nil {
			rpm = key.RateLimitRPM
		}
	}

	// Per-org per-key RPM override (from tenant config via handler metadata).
	if rpm == 0 {
		if n := stampedLimit(rc, metaPerKeyRPM); n > 0 {
			rpm, source = int(n), limitPerKeyRPM
		}
	}

	realKeyID := keyID
	if keyID == "" {
		keyID = "global"
	}

	// The key's own token limit is read before any counter is touched: the
	// check charges nothing, so a request it refuses spends no request slot
	// and holds no org token reservation.
	if res := p.checkTokens(rc, keyID); res.Error != nil {
		return res
	}

	// Fall back to gateway-level global RPM.
	if rpm == 0 {
		rpm, source = p.cfg.GlobalRPM, limitNodeRPM
	}

	// The org's own limits, counted for the whole org (or for each of its
	// keys, models or users) rather than on this key's counter.
	if hasOrgLimits(rc) {
		if orgID := requestOrgID(rc); orgID != "" {
			return p.processOrg(rc, orgID, keyID, realKeyID, int64(rpm), source)
		}
	}

	// If no request limit configured, allow unlimited. The token limit, a
	// separate budget that may stand alone, was checked above.
	if rpm == 0 {
		return pipeline.ResultContinue()
	}

	allowed, remaining, resetSeconds := p.limiter.Allow(keyID, rpm)

	// Store rate limit info in metadata for response headers.
	rc.Metadata["ratelimit_limit"] = strconv.Itoa(rpm)
	rc.Metadata["ratelimit_remaining"] = strconv.Itoa(remaining)
	rc.Metadata["ratelimit_reset"] = strconv.Itoa(resetSeconds)

	if !allowed {
		return pipeline.ResultError(rateLimitExceeded(source, int64(rpm), resetSeconds))
	}

	return pipeline.ResultContinue()
}

// tokenWindowKey namespaces the token budget away from the request budget, so
// the two counters never share a window.
func tokenWindowKey(keyID string) string { return "tpm:" + keyID }

// tokenLimit is the key's own token-per-minute limit (rate_limit_tpm), 0 when
// it has none. The organization's token limits (global_tpm, all of its keys
// together, and per_key_tpm) are counted by processOrg (orglimits.go), across
// the org's keys and, with Redis, across replicas; they are not a fallback
// for this per-key window.
func (p *Plugin) tokenLimit(keyID string) int {
	if keyID != "" && p.keyStore != nil {
		if key := p.keyStore.Get(keyID); key != nil && key.RateLimitTPM > 0 {
			return key.RateLimitTPM
		}
	}
	return 0
}

// checkTokens refuses a request whose token window is already spent.
func (p *Plugin) checkTokens(rc *models.RequestContext, keyID string) pipeline.PluginResult {
	tpm := p.tokenLimit(keyID)
	if tpm == 0 {
		return pipeline.ResultContinue()
	}

	limiter, ok := p.limiter.(TokenLimiter)
	if !ok {
		// A limiter with no token budget would enforce nothing, and a token
		// limit nobody enforces is worse than one nobody set: the operator
		// believes they have a ceiling.
		p.warnNoTokenBudget()
		return pipeline.ResultContinue()
	}

	allowed, remaining, resetSeconds := limiter.AllowTokens(tokenWindowKey(keyID), tpm)
	rc.Metadata["ratelimit_tokens_limit"] = strconv.Itoa(tpm)
	rc.Metadata["ratelimit_tokens_remaining"] = strconv.Itoa(remaining)

	if !allowed {
		field := "rate_limit_tpm"
		return pipeline.ResultError(&models.APIError{
			Status:        http.StatusTooManyRequests,
			Type:          "rate_limit_error",
			Code:          "token_rate_limit_exceeded",
			Message:       fmt.Sprintf("Token rate limit exceeded. Please retry after %d seconds.", resetSeconds),
			Param:         &field,
			RetryAfterSec: resetSeconds,
		})
	}
	return pipeline.ResultContinue()
}

func (p *Plugin) warnNoTokenBudget() {
	p.noTokenBudgetOnce.Do(func() {
		slog.Warn("a token rate limit is configured but the active limiter cannot hold one; it is not enforced",
			"limiter", fmt.Sprintf("%T", p.limiter))
	})
}

// ProcessResponse settles the request's token reservation on the tokens it
// used (orglimits.go), and charges them to the key's own token window. The
// cost is only known now, which is why the key's check in ProcessRequest reads
// the window rather than reserving against it.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if _, ok := rc.Metadata[metaTPMHold]; ok {
		p.settleTokens(rc)
	}
	p.chargeKeyTokens(rc)
	return pipeline.ResultContinue()
}

// chargeKeyTokens adds an answered request's tokens to its key's own
// token window (rate_limit_tpm).
func (p *Plugin) chargeKeyTokens(rc *models.RequestContext) {
	if !p.cfg.Enabled {
		return
	}

	// A served cache hit carries the usage of the provider call that filled
	// the cache, and no new tokens were spent. Charging it again exhausts the
	// window on repeated identical requests and refuses the next real one.
	//
	// The flag is what to read, not the pipeline's SkipOnCacheHit interface:
	// the engine only applies that to cache_status == "hit_exact", so a
	// conditional or semantic hit would still be charged.
	if rc.Flags.CacheHit {
		return
	}
	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		keyID = "global"
	}
	if p.tokenLimit(keyID) == 0 {
		return
	}
	limiter, ok := p.limiter.(TokenLimiter)
	if !ok {
		return
	}
	limiter.AddTokens(tokenWindowKey(keyID), tokensUsed(rc))
}

// rateLimitExceeded is the 429 for a request the limit refused. It names the
// limit: param is the field that set it (an org config field such as
// global_rpm, the key's rate_limit_rpm, or the node's
// rate_limiting.global_rpm), and Retry-After is when its window rolls.
func rateLimitExceeded(l limitName, limit int64, resetSeconds int) *models.APIError {
	field := l.field
	return &models.APIError{
		Status: http.StatusTooManyRequests,
		Type:   "rate_limit_error",
		Code:   "rate_limit_exceeded",
		Message: fmt.Sprintf("Rate limit exceeded: %s (%s = %d). Please retry after %d seconds.",
			l.what, l.field, limit, resetSeconds),
		Param:         &field,
		RetryAfterSec: resetSeconds,
	}
}

// tokensUsed reports what the answered request cost, from whichever response
// the endpoint produced. An endpoint that reports no tokens charges nothing.
func tokensUsed(rc *models.RequestContext) int {
	switch {
	case rc.Response != nil && rc.Response.Usage != nil:
		if total := rc.Response.Usage.TotalTokens; total > 0 {
			return total
		}
		return rc.Response.Usage.PromptTokens + rc.Response.Usage.CompletionTokens
	case rc.EmbeddingResponse != nil && rc.EmbeddingResponse.Usage != nil:
		if total := rc.EmbeddingResponse.Usage.TotalTokens; total > 0 {
			return total
		}
		return rc.EmbeddingResponse.Usage.PromptTokens
	}
	return 0
}
