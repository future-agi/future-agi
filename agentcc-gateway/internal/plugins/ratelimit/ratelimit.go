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
)

// Plugin is a pipeline plugin that enforces request and token rate limits.
type Plugin struct {
	limiter  Limiter
	keyStore *auth.KeyStore
	cfg      config.RateLimitConfig

	noTokenBudgetOnce sync.Once
}

// New creates a new rate limit plugin.
// If limiter is nil, an in-memory RateLimiter is created as the default.
func New(cfg config.RateLimitConfig, keyStore *auth.KeyStore, limiter Limiter) *Plugin {
	if limiter == nil {
		limiter = NewRateLimiter()
	}
	return &Plugin{
		limiter:  limiter,
		keyStore: keyStore,
		cfg:      cfg,
	}
}

func (p *Plugin) Name() string  { return "ratelimit" }
func (p *Plugin) Priority() int { return 80 } // After validation (70), before cache (200).

// ProcessRequest checks rate limits and rejects excess requests.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.cfg.Enabled {
		return pipeline.ResultContinue()
	}

	// Determine key ID and RPM limit.
	keyID := rc.Metadata["auth_key_id"]
	rpm := 0

	if keyID != "" && p.keyStore != nil {
		// Look up per-key RPM limit.
		key := p.keyStore.Get(keyID)
		if key != nil {
			rpm = key.RateLimitRPM
		}
	}

	// Per-org per-key RPM override (from tenant config via handler metadata).
	if rpm == 0 {
		if v, ok := rc.Metadata["org_rate_limit_per_key_rpm"]; ok {
			if n, err := strconv.Atoi(v); err == nil && n > 0 {
				rpm = n
			}
		}
	}

	if keyID == "" {
		keyID = "global"
	}

	// Fall back to per-org global RPM if per-key is not set.
	if rpm == 0 {
		if v, ok := rc.Metadata["org_rate_limit_rpm"]; ok {
			if n, err := strconv.Atoi(v); err == nil && n > 0 {
				rpm = n
			}
		}
	}

	// Fall back to gateway-level global RPM.
	if rpm == 0 {
		rpm = p.cfg.GlobalRPM
	}

	// No request limit set. A token limit is independent of one, so it is
	// still checked: the two are separate budgets and either may stand alone.
	if rpm == 0 {
		return p.checkTokens(rc, keyID)
	}

	allowed, remaining, resetSeconds := p.limiter.Allow(keyID, rpm)

	// Store rate limit info in metadata for response headers.
	rc.Metadata["ratelimit_limit"] = strconv.Itoa(rpm)
	rc.Metadata["ratelimit_remaining"] = strconv.Itoa(remaining)
	rc.Metadata["ratelimit_reset"] = strconv.Itoa(resetSeconds)

	if !allowed {
		return pipeline.ResultError(&models.APIError{
			Status:  http.StatusTooManyRequests,
			Type:    "rate_limit_error",
			Code:    "rate_limit_exceeded",
			Message: fmt.Sprintf("Rate limit exceeded. Please retry after %d seconds.", resetSeconds),
		})
	}

	return p.checkTokens(rc, keyID)
}

// tokenWindowKey namespaces the token budget away from the request budget, so
// the two counters never share a window.
func tokenWindowKey(keyID string) string { return "tpm:" + keyID }

// tokenLimit resolves the token-per-minute limit for a request, by the same
// precedence as RPM: the key's own limit, then the organization's.
func (p *Plugin) tokenLimit(rc *models.RequestContext, keyID string) int {
	if keyID != "" && p.keyStore != nil {
		if key := p.keyStore.Get(keyID); key != nil && key.RateLimitTPM > 0 {
			return key.RateLimitTPM
		}
	}
	if v, ok := rc.Metadata["org_rate_limit_tpm"]; ok {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return 0
}

// checkTokens refuses a request whose token window is already spent.
func (p *Plugin) checkTokens(rc *models.RequestContext, keyID string) pipeline.PluginResult {
	tpm := p.tokenLimit(rc, keyID)
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
		return pipeline.ResultError(&models.APIError{
			Status:  http.StatusTooManyRequests,
			Type:    "rate_limit_error",
			Code:    "token_rate_limit_exceeded",
			Message: fmt.Sprintf("Token rate limit exceeded. Please retry after %d seconds.", resetSeconds),
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

// ProcessResponse charges the tokens the request used against its window.
// The cost is only known now, which is why the check in ProcessRequest reads
// the window rather than reserving against it.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.cfg.Enabled {
		return pipeline.ResultContinue()
	}

	// A served cache hit carries the usage of the provider call that filled
	// the cache, and no new tokens were spent. Charging it again exhausts the
	// window on repeated identical requests and refuses the next real one.
	//
	// The flag is what to read, not the pipeline's SkipOnCacheHit interface:
	// the engine only applies that to cache_status == "hit_exact", so a
	// conditional or semantic hit would still be charged.
	if rc.Flags.CacheHit {
		return pipeline.ResultContinue()
	}
	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		keyID = "global"
	}
	if p.tokenLimit(rc, keyID) == 0 {
		return pipeline.ResultContinue()
	}
	limiter, ok := p.limiter.(TokenLimiter)
	if !ok {
		return pipeline.ResultContinue()
	}
	limiter.AddTokens(tokenWindowKey(keyID), tokensUsed(rc))
	return pipeline.ResultContinue()
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
