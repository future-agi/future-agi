package spendguard

import (
	"fmt"
	"math"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

// Error codes and remedy hints of the spend guard's refusals (error-402-v1,
// plan section 6.10).
const (
	codeVelocity = "managed_spend_velocity"
	codePaused   = "managed_spend_paused"

	remedyRetry        = "retry"
	remedyRaiseBudget  = "raise_budget"
	remedyContactAdmin = "contact_admin"
)

// errRequestExceedsMinuteCap carries no amounts, identifiers or reset time:
// a smaller request is required, so there is deliberately no Retry-After.
func errRequestExceedsMinuteCap() *models.APIError {
	return &models.APIError{
		Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Code: codeVelocity,
		Message:  "This request's worst-case cost exceeds the per-minute spending limit. Lower max_tokens to fit within the limit.",
		Metadata: map[string]any{"reason": "request_exceeds_minute_cap"},
	}
}

// errRequestExceedsHourCap is likewise not a retryable window-full refusal.
func errRequestExceedsHourCap() *models.APIError {
	return &models.APIError{
		Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Code: codeVelocity,
		Message:  "This request's worst-case cost exceeds the per-hour spending limit. Lower max_tokens to fit within the limit.",
		Metadata: map[string]any{"reason": "request_exceeds_hour_cap"},
	}
}

// errNewOrgDailyCap discloses the first-week spending policy without amounts,
// identifiers or account state. Waiting is not a remedy for a request larger
// than the whole daily cap, so do not promise success with Retry-After.
func errNewOrgDailyCap() *models.APIError {
	return &models.APIError{
		Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Code: codeVelocity,
		Message:  "New organizations have a daily spending limit for Future AGI credits. This request would exceed the remaining daily allowance. Contact support to raise the limit.",
		Metadata: map[string]any{"reason": "new_org_daily_cap"},
	}
}

// errVelocity is the 429 for a velocity window: it names the scope that
// tripped, resets_at when the oldest counted bucket leaves the window,
// and Retry-After the seconds until then.
func errVelocity(v redisstate.SpendGuardVerdict, keyID string, now time.Time) *models.APIError {
	scope, who := models.ScopeOrg, "Your organization's"
	if v.Scope == redisstate.SpendGuardScopeKey {
		scope, who = models.ScopeKey(keyID), "This API key's"
	}
	resets := v.ResetsAt
	meta := models.PaymentMeta{LimitSource: models.LimitSourceSpendGuard, RemedyHint: remedyRetry, Scope: scope,
		ResetsAt: &resets, RetryAfterSec: max(int(math.Ceil(resets.Sub(now).Seconds())), 1)}
	capUSD, counted := billing.MicrosToUSD(v.CapMicros), billing.MicrosToUSD(v.CountedMicros)
	msg := fmt.Sprintf("%s managed spend is at its $%.6f per-%s cap ($%.6f counted, including requests in flight). "+
		"Retry after %s, or use your own provider keys.", who, capUSD, v.Window, counted, resets.Format(time.RFC3339))
	return models.ErrLimit(http.StatusTooManyRequests, models.ErrTypeRateLimit, codeVelocity, msg, meta)
}

// pauseCause is who paused the org's managed spend.
type pauseCause int

const (
	pausedByBudget  pauseCause = iota // the EE budget enforcer
	pausedByAnomaly                   // the spend guard's auto-pause
)

// errPaused is the 402 while the org's managed spend is paused. It is
// org-scoped and lifts only when the pause is cleared (resets_at null).
func errPaused(cause pauseCause) *models.APIError {
	meta := models.PaymentMeta{LimitSource: models.LimitSourceBudget, Scope: models.ScopeOrg}
	var msg string
	switch cause {
	case pausedByBudget:
		meta.RemedyHint = remedyRaiseBudget
		msg = "Your organization's managed-spend budget is used up, so managed models are paused. " +
			"An admin can raise the budget; your own provider keys still work."
	default:
		meta.RemedyHint = remedyContactAdmin
		msg = "Managed models are paused for your organization after unusual spend. " +
			"An admin can resume them; your own provider keys still work."
	}
	return models.ErrPaymentRequired(models.ErrTypePaymentRequired, codePaused, msg, meta)
}
