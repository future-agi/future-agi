package credits

import (
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Error codes and remedy hints of the managed-credit refusals (error-402-v1,
// plan section 6.10).
const (
	codeCreditsExhausted    = "managed_credits_exhausted"
	codeCreditsInsufficient = "managed_credits_insufficient"
	codeBillingUnavailable  = "managed_billing_unavailable"

	remedyAddCredits = "add_credits"
	remedyRetry      = "retry"

	// inFlightRetryAfterSec is the in-flight 402's Retry-After.
	inFlightRetryAfterSec = 2
)

// refusal is the 402 for a RESERVE that did not fit: exhausted and
// insufficient name agentcc_credits, in_flight names agentcc_in_flight with
// Retry-After. Each is org-scoped and lifts only on a grant or a top-up
// (resets_at null).
// The message says Future AGI credit, says "no credit" to an org never
// granted any, and names where to add credit, or, with grantOnly (credit
// purchase not installed), that an operator grants it (models'
// Credits*Message).
func refusal(status billing.ReserveStatus, requiredMicros int64, acct billing.Account, grantOnly bool) *models.APIError {
	required := billing.MicrosToUSD(requiredMicros)
	available := billing.MicrosToUSD(max(acct.AvailableMicros(), 0))
	meta := models.PaymentMeta{LimitSource: models.LimitSourceCredits, RemedyHint: remedyAddCredits,
		RequiredUSD: &required, AvailableUSD: &available, Scope: models.ScopeOrg}
	switch status {
	case billing.ReserveExhausted:
		return models.ErrPaymentRequired(models.ErrTypePaymentRequired, codeCreditsExhausted,
			models.CreditsExhaustedMessage(acct.GrantedMicros <= 0, grantOnly), meta)
	case billing.ReserveInFlight:
		meta.LimitSource, meta.RemedyHint, meta.RetryAfterSec = models.LimitSourceInFlight, remedyRetry, inFlightRetryAfterSec
		return models.ErrPaymentRequired(models.ErrTypePaymentRequired, codeCreditsInsufficient,
			models.CreditsInFlightMessage(grantOnly), meta)
	default:
		return models.ErrPaymentRequired(models.ErrTypePaymentRequired, codeCreditsInsufficient,
			models.CreditsInsufficientMessage("request", available, required, true, grantOnly), meta)
	}
}

// ErrBillingUnavailable is the 503 a managed request (or a credits admin
// read or push) gets when the credit store cannot be reached: managed
// billing fails closed (decision 6).
func ErrBillingUnavailable() *models.APIError {
	return models.ErrLimit(http.StatusServiceUnavailable, models.ErrTypeServer, codeBillingUnavailable,
		"Managed billing is temporarily unavailable. Retry shortly, or use your own provider keys.",
		models.PaymentMeta{Reason: models.ReasonRedisUnavailable, RemedyHint: remedyRetry})
}

// errAccountMissing is the 503 an opted-in org gets while its account is
// being re-pushed after a Redis loss: never a 402 (BPC#13).
func errAccountMissing(retryAfterSec int) *models.APIError {
	return models.ErrLimit(http.StatusServiceUnavailable, models.ErrTypeServer, codeBillingUnavailable,
		"Your organization's managed credit balance is being restored. Retry shortly.",
		models.PaymentMeta{Reason: models.ReasonAccountStateMissing, RemedyHint: remedyRetry, RetryAfterSec: retryAfterSec})
}
