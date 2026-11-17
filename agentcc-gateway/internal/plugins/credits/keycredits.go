package credits

import (
	"fmt"
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/auth"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
)

// RedisCreditBackend is an optional interface for Redis-backed managed-key
// credit balances.
type RedisCreditBackend interface {
	DeductMicros(keyID string, amount int64) (int64, bool)
	ForceDeductMicros(keyID string, amount int64) (int64, bool)
	GetBalance(keyID string) (int64, bool)
	Available() bool
}

// managedKey returns rc's key when it is a managed key (key_type managed)
// with its own prepaid balance, else nil.
func (p *Plugin) managedKey(rc *models.RequestContext) (*auth.APIKey, string) {
	if p.keyStore == nil || rc.Metadata["key_type"] != "managed" {
		return nil, ""
	}
	keyID := rc.Metadata["auth_key_id"]
	if keyID == "" {
		return nil, ""
	}
	return p.keyStore.Get(keyID), keyID
}

func errKeyCreditsExhausted(balanceUSD float64) *models.APIError {
	return &models.APIError{
		Status:  http.StatusPaymentRequired,
		Type:    "insufficient_credits",
		Code:    "payment_required",
		Message: fmt.Sprintf("Insufficient credits. Current balance: $%.4f. Top up to continue.", balanceUSD),
	}
}

// checkKeyBalance refuses a managed key whose balance is used up. Redis is
// authoritative across replicas when it is reachable.
func (p *Plugin) checkKeyBalance(rc *models.RequestContext) pipeline.PluginResult {
	key, keyID := p.managedKey(rc)
	if key == nil {
		return pipeline.ResultContinue()
	}
	if p.redisCredit != nil && p.redisCredit.Available() {
		if redisBal, ok := p.redisCredit.GetBalance(keyID); ok {
			key.SetBalanceMicros(redisBal) // sync local
			if auth.MicrosToUSD(redisBal) <= 0 {
				return pipeline.ResultError(errKeyCreditsExhausted(auth.MicrosToUSD(redisBal)))
			}
			return pipeline.ResultContinue()
		}
	}
	if balance := key.BalanceUSD(); balance <= 0 {
		return pipeline.ResultError(errKeyCreditsExhausted(balance))
	}
	return pipeline.ResultContinue()
}

// chargeKey deducts a request's cost from a managed key's balance. A cache
// hit made no upstream call and is never charged.
func (p *Plugin) chargeKey(rc *models.RequestContext) pipeline.PluginResult {
	key, keyID := p.managedKey(rc)
	if key == nil || rc.Flags.CacheHit {
		return pipeline.ResultContinue()
	}
	costUSD, err := strconv.ParseFloat(rc.Metadata["cost"], 64)
	if err != nil || costUSD <= 0 {
		return pipeline.ResultContinue()
	}
	micros := auth.USDToMicros(costUSD)

	// Write-through: unconditionally deduct from Redis (shared), then sync
	// local. ForceDeductMicros is used because the request already completed:
	// spend is recorded even if the balance dropped to zero since the check.
	if p.redisCredit != nil && p.redisCredit.Available() {
		if redisBal, ok := p.redisCredit.ForceDeductMicros(keyID, micros); ok {
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
