package ratelimit

import (
	"context"
	"fmt"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/config"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// Plugin is a pre-request pipeline plugin that enforces RPM rate limits.
type Plugin struct {
	limiter  Limiter
	keyStore *auth.KeyStore
	cfg      config.RateLimitConfig
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

	// If no limit configured, allow unlimited.
	if rpm == 0 {
		return pipeline.ResultContinue()
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

	return pipeline.ResultContinue()
}

// ProcessResponse is a no-op for the rate limit plugin.
func (p *Plugin) ProcessResponse(_ context.Context, _ *models.RequestContext) pipeline.PluginResult {
	return pipeline.ResultContinue()
}
