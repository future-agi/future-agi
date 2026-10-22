package capability

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// Codes of the managed-credit refusals a capability call reuses unchanged
// (error-402-v1).
const (
	codeCreditsExhausted    = "managed_credits_exhausted"
	codeCreditsInsufficient = "managed_credits_insufficient"
	codeBillingUnavailable  = "managed_billing_unavailable"
	remedyAddCredits        = "add_credits"
	remedyRetry             = "retry"
	inFlightRetryAfterSec   = 2
)

// hold reserves micros on the org's managed credit for one attempt, keyed by
// the attempt id. It returns the refusal to send instead: the managed
// credits 402 objects for credit that does not fit, and the 503
// managed_billing_unavailable when the store is unreachable or has no
// account (managed billing fails closed, never a 402).
func (c *Core) hold(ctx context.Context, org, holdID string, micros int64) *models.APIError {
	if c.opts.Ledger == nil {
		return billingUnavailable(models.ReasonRedisUnavailable, 0)
	}
	res, err := c.opts.Ledger.Reserve(ctx, org, holdID, micros, c.opts.HoldTTL, billing.ReserveNew)
	if err != nil {
		slog.Error("capability: managed reserve failed; refusing the call", "hold_id", holdID, "org_id", org, "error", err)
		return billingUnavailable(models.ReasonRedisUnavailable, 0)
	}
	switch res.Status {
	case billing.ReserveOK:
		return nil
	case billing.ReserveNoAccount:
		if err := c.opts.Ledger.MarkNeedsPush(ctx, org); err != nil {
			slog.Error("capability: could not ask for the account's re-push", "org_id", org, "error", err)
		}
		return billingUnavailable(models.ReasonAccountStateMissing, int(c.opts.AccountMissingRetryAfter/time.Second))
	default:
		return creditRefusal(res.Status, micros, res.Account)
	}
}

// settle writes the attempt's spend (and releases its hold), once per hold
// id. A failure is retried once; after that the hold expires unsettled, as
// for model calls.
func (c *Core) settle(org, holdID string, rec billing.Settlement) {
	_, err := c.opts.Ledger.Settle(context.Background(), org, holdID, rec)
	if err != nil {
		_, err = c.opts.Ledger.Settle(context.Background(), org, holdID, rec)
	}
	if err != nil {
		slog.Error("capability: settlement failed; the hold expires unsettled", "hold_id", holdID, "org_id", org,
			"billed_micros", rec.BilledMicros, "error", err)
	}
}

// void releases the attempt's hold with a void record, once per hold id.
func (c *Core) void(org, holdID string, rec billing.Settlement) {
	if _, err := c.opts.Ledger.Void(context.Background(), org, holdID, rec); err != nil {
		slog.Error("capability: void failed; the hold expires on its own", "hold_id", holdID, "org_id", org, "error", err)
	}
}

// settlementOf is the attempt's managed-settlement-v1 record:
// provider is the vendor, model <capability>/<sku>, no upstream fee, the
// processing estimate and the tool markup. request_id is the attempt id,
// the hold's key, so two attempts of one request are two ledger entries
// (Django keys a spend spend:<request_id>); generation_id is the request.
func settlementOf(rc *models.RequestContext, org string, a *models.Attempt, holdMicros int64, markupBps int) billing.Settlement {
	return billing.Settlement{
		RequestID:        a.AttemptID,
		GenerationID:     rc.RequestID,
		OrgID:            org,
		APIKeyID:         rc.Metadata["auth_key_id"],
		BilledMicros:     a.BilledCostMicros,
		UpstreamMicros:   a.UpstreamCostMicros,
		MarkupMicros:     a.BilledCostMicros - a.UpstreamCostMicros,
		MarkupBps:        markupBps,
		HoldMicros:       holdMicros,
		LandedMicros:     a.LandedCostMicros,
		UpstreamFeeBps:   0,
		ProcessingFeeBps: a.ProcessingFeeBps,
		Model:            a.Model,
		Provider:         a.Provider,
		EndpointID:       a.EndpointID,
		CostSource:       string(a.CostSource),
		UsageEstimated:   a.UsageEstimated,
		Attempts: []billing.AttemptSummary{{
			AttemptID: a.AttemptID, Kind: string(a.Kind), Provider: a.Provider, Model: a.Model,
			CredentialType: string(a.CredentialType), UpstreamMicros: a.UpstreamCostMicros,
			BilledMicros: a.BilledCostMicros, Outcome: string(a.Outcome),
		}},
		CatalogSnapshotID: rc.Evidence.CatalogSnapshotID,
		PolicyVersion:     rc.Evidence.PolicyVersion,
		AgentRunID:        rc.Agent.RunID,
		AgentID:           rc.Agent.AgentID,
		TsMs:              time.Now().UnixMilli(),
	}
}

// creditRefusal is the managed-credit 402 for a RESERVE that did not fit, unchanged.
func creditRefusal(status billing.ReserveStatus, requiredMicros int64, acct billing.Account) *models.APIError {
	required := billing.MicrosToUSD(requiredMicros)
	available := billing.MicrosToUSD(max(acct.AvailableMicros(), 0))
	meta := models.PaymentMeta{LimitSource: models.LimitSourceCredits, RemedyHint: remedyAddCredits,
		RequiredUSD: &required, AvailableUSD: &available, Scope: models.ScopeOrg}
	switch status {
	case billing.ReserveExhausted:
		return models.ErrPaymentRequired(models.ErrTypePaymentRequired, codeCreditsExhausted,
			"Your organization has no managed credits left. Add credits or use your own provider keys.", meta)
	case billing.ReserveInFlight:
		meta.LimitSource, meta.RemedyHint, meta.RetryAfterSec = models.LimitSourceInFlight, remedyRetry, inFlightRetryAfterSec
		return models.ErrPaymentRequired(models.ErrTypePaymentRequired, codeCreditsInsufficient,
			"Your organization's managed credits are held by requests in flight. Retry shortly, or add credits.", meta)
	default:
		return models.ErrPaymentRequired(models.ErrTypePaymentRequired, codeCreditsInsufficient,
			fmt.Sprintf("Your organization's managed credits ($%.6f) do not cover this call's worst-case cost ($%.6f). "+
				"Add credits or use your own vendor key.", available, required), meta)
	}
}

// billingUnavailable is the 503 managed_billing_unavailable: the credit
// store is unreachable, or the org's account is being restored.
func billingUnavailable(reason string, retryAfterSec int) *models.APIError {
	msg := "Managed billing is temporarily unavailable. Retry shortly, or use your own vendor key."
	if reason == models.ReasonAccountStateMissing {
		msg = "Your organization's managed credit balance is being restored. Retry shortly."
	}
	return models.ErrLimit(http.StatusServiceUnavailable, models.ErrTypeServer, codeBillingUnavailable, msg,
		models.PaymentMeta{Reason: reason, RemedyHint: remedyRetry, RetryAfterSec: retryAfterSec})
}
