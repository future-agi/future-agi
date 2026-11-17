package budget

import (
	"math"
	"net/http"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// ActualSpend distinguishes a proven zero from absent metering. An attempted
// request may have spent money even when it has no response or wallet debit.
// Uncertain work keeps its reservation; only authoritative usage settles it.
// This reads billing results, but never changes wallet charging or estimates.
func (a *Admission) ActualSpend(rc *models.RequestContext) (int64, bool) {
	if NativeControlled(rc) || NativeCapsRequired(rc) {
		return nativeActual(rc, a)
	}
	if !a.attempted.Load() || rc.Flags.CacheHit {
		return 0, true
	}
	if rc.Billing.UsageEstimated {
		return 0, false
	}
	attempts := rc.AttemptsSnapshot()
	if len(attempts) == 0 {
		if rc.Response == nil || !capActualUsage(rc.Response.Usage) || rc.Billing.CostSource == models.CostSourceUnpriced {
			return 0, false
		}
		actual := rc.Billing.UpstreamMicros
		if a.managed {
			actual = rc.Billing.BilledMicros
		}
		if actual < 0 {
			return 0, false
		}
		return actual, true
	}
	// attemptRun.finish sets BusyCredential only for the typed gateway-local
	// provider_busy refusal. It describes the last completed attempt, not an
	// entire chain; skipped fallback entries do not overwrite that evidence.
	last := len(attempts) - 1
	for last >= 0 && attempts[last].Outcome == models.OutcomeSkipped {
		last--
	}
	var actual int64
	metered := false
	for i, attempt := range attempts {
		if attempt.Outcome == models.OutcomeSkipped {
			continue
		}
		if i == last && capUndispatchedBusy(attempt, rc.Billing.BusyCredential) {
			metered = true // authoritative non-dispatch is a known zero
			continue
		}
		if !capActualUsage(attempt.Usage) || attempt.UsageEstimated || attempt.CostSource == models.CostSourceUnpriced {
			return 0, false
		}
		amount := attempt.UpstreamCostMicros
		if a.managed {
			amount = attempt.BilledCostMicros
		}
		if amount < 0 || amount > math.MaxInt64-actual {
			return 0, false
		}
		actual += amount
		metered = true
	}
	return actual, metered
}

// Reuse server-owned dispatch evidence, never a message, caller metadata, or a
// generic gateway error. Conflicting usage/charge evidence still needs metering.
func capUndispatchedBusy(a models.Attempt, busy models.CredentialType) bool {
	return (busy == models.CredentialBYOK || busy == models.CredentialManaged) && a.CredentialType == busy &&
		a.Outcome == models.OutcomeError && a.ErrorOrigin == models.OriginGateway &&
		a.ErrorClass == models.ErrClassUpstream5xx && a.StatusCode == http.StatusServiceUnavailable &&
		!a.InFlight && !a.ProviderRejected && !a.Billable && !a.UsageEstimated && a.Usage == nil &&
		a.PromptTokens == 0 && a.CompletionTokens == 0 && a.CachedTokens == 0 && a.CacheWriteTokens == 0 && a.ReasoningTokens == 0 &&
		a.UpstreamCostMicros == 0 && a.BilledCostMicros == 0 && a.LandedCostMicros == 0
}

// Both the daily ledger and strict run ledger use this shared evidence gate.
// Native adapters must supply explicit presence via models.NewUsage, just like
// decoded OpenAI usage: a nonnil object or unflagged counts prove nothing.
// A measured zero (or valid reported charge) can settle; partial reports and
// estimates, including managed native hold-floor billing, retain the cap hold.
func capActualUsage(usage *models.Usage) bool {
	return usage != nil && !usage.Estimated && !usage.NeedsTokenEstimate()
}
