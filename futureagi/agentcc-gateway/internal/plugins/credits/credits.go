package credits

import (
	"context"
	"fmt"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// RedisCreditBackend is an optional interface for Redis-backed credit balances.
type RedisCreditBackend interface {
	DeductMicros(keyID string, amount int64) (int64, bool)
	ForceDeductMicros(keyID string, amount int64) (int64, bool)
	GetBalance(keyID string) (int64, bool)
	Available() bool
}

// Plugin is a pipeline plugin that enforces credit balance for managed keys.
// Pre-request: blocks if balance <= 0.
// Post-request: deducts actual cost from balance.
type Plugin struct {
	keyStore    *auth.KeyStore
	enabled     bool
	redisCredit RedisCreditBackend // nil = local-only
}

// New creates a new credits plugin.
func New(enabled bool, keyStore *auth.KeyStore) *Plugin {
	return &Plugin{
		keyStore: keyStore,
		enabled:  enabled,
	}
}

// SetRedisCredit attaches a Redis-backed credit store for multi-replica support.
func (p *Plugin) SetRedisCredit(rc RedisCreditBackend) {
	p.redisCredit = rc
}

func (p *Plugin) Name() string               { return "credits" }
func (p *Plugin) Priority() int              { return 510 }  // After auth (100) and cost (500).
func (p *Plugin) ShouldSkipOnCacheHit() bool { return true } // No credits deducted on cache hits.

// ProcessRequest checks credit balance for managed keys.
func (p *Plugin) ProcessRequest(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled || p.keyStore == nil {
		return pipeline.ResultContinue()
	}
	if rc.Metadata["key_type"] != "managed" {
		return pipeline.ResultContinue()
	}

	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		return pipeline.ResultContinue()
	}

	key := p.keyStore.Get(keyID)
	if key == nil {
		return pipeline.ResultContinue()
	}

	// Check Redis first (cross-replica authoritative), fall back to local.
	if p.redisCredit != nil && p.redisCredit.Available() {
		if redisBal, ok := p.redisCredit.GetBalance(keyID); ok {
			key.SetBalanceMicros(redisBal) // sync local
			if auth.MicrosToUSD(redisBal) <= 0 {
				return pipeline.ResultError(&models.APIError{
					Status:  http.StatusPaymentRequired,
					Type:    "insufficient_credits",
					Code:    "payment_required",
					Message: fmt.Sprintf("Insufficient credits. Current balance: $%.4f. Top up to continue.", auth.MicrosToUSD(redisBal)),
				})
			}
			return pipeline.ResultContinue()
		}
	}

	balance := key.BalanceUSD()
	if balance <= 0 {
		return pipeline.ResultError(&models.APIError{
			Status:  http.StatusPaymentRequired,
			Type:    "insufficient_credits",
			Code:    "payment_required",
			Message: fmt.Sprintf("Insufficient credits. Current balance: $%.4f. Top up to continue.", balance),
		})
	}

	return pipeline.ResultContinue()
}

// ProcessResponse deducts cost from credit balance for managed keys.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !p.enabled || p.keyStore == nil {
		return pipeline.ResultContinue()
	}
	if rc.Metadata["key_type"] != "managed" {
		return pipeline.ResultContinue()
	}

	costStr, ok := rc.Metadata["cost"]
	if !ok || costStr == "" {
		return pipeline.ResultContinue()
	}

	costUSD, err := strconv.ParseFloat(costStr, 64)
	if err != nil || costUSD <= 0 {
		return pipeline.ResultContinue()
	}

	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		return pipeline.ResultContinue()
	}

	key := p.keyStore.Get(keyID)
	if key == nil {
		return pipeline.ResultContinue()
	}

	micros := auth.USDToMicros(costUSD)

	// Write-through: unconditionally deduct from Redis (shared), then sync local.
	// ForceDeductMicros is used because the request already completed — spend
	// must be recorded even if balance dropped to zero between pre-check and now.
	if p.redisCredit != nil && p.redisCredit.Available() {
		if redisBal, ok := p.redisCredit.ForceDeductMicros(keyID, micros); ok {
			// Sync local balance to match Redis (authoritative source).
			key.SetBalanceMicros(redisBal)
			rc.Metadata["credits_used"] = fmt.Sprintf("%.6f", costUSD)
			rc.Metadata["credits_remaining"] = fmt.Sprintf("%.6f", auth.MicrosToUSD(redisBal))
			return pipeline.ResultContinue()
		}
	}

	// Fallback: local-only deduction.
	newBalance := key.DeductMicros(micros)
	rc.Metadata["credits_used"] = fmt.Sprintf("%.6f", costUSD)
	rc.Metadata["credits_remaining"] = fmt.Sprintf("%.6f", newBalance)

	return pipeline.ResultContinue()
}
