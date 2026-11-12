package spendguard

import (
	"context"
	"sync"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/pipeline"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
)

const (
	// Priority runs the admission check after the budget plugin (40) and
	// before the credits reserve (510), for managed requests only (plan
	// section 6.11).
	Priority = 505
	// PostPriority feeds the windows after cost (1) has priced the request
	// and the credits (510), run-budget (515) and envelope (516) settlements
	// have run.
	PostPriority = 517
)

// Plugin runs the spend guard on managed requests: Guard.Admit at the
// request's worst-case estimate before the credits reserve, then
// Guard.Settle at what it was billed, or Guard.Void when nothing was billed,
// after the call. The hold id is the request id.
type Plugin struct {
	guard    *Guard
	estimate func(rc *models.RequestContext) int64

	// admitted maps the request id of each admitted request to its org until
	// the post-plugin or the request finalizer, whichever runs first, takes
	// it and corrects the windows; the other finds nothing.
	admitted sync.Map
}

// New returns the plugin over guard, holding each request at estimate (an
// Estimator's Estimate in a gateway).
func New(guard *Guard, estimate func(rc *models.RequestContext) int64) *Plugin {
	return &Plugin{guard: guard, estimate: estimate}
}

func (p *Plugin) Name() string      { return "spendguard" }
func (p *Plugin) Priority() int     { return Priority }
func (p *Plugin) PostPriority() int { return PostPriority }

// ShouldSkipOnCacheHit is false: a request admitted before a cache answered
// it must still be voided.
func (p *Plugin) ShouldSkipOnCacheHit() bool { return false }

// ProcessRequest admits a managed request or refuses it with the guard's 429
// or 402. An admitted request registers a finalizer, so its estimate is
// corrected even on an exit path that skips the post-plugins (a client that
// aborts a stream, a panic).
func (p *Plugin) ProcessRequest(ctx context.Context, rc *models.RequestContext) pipeline.PluginResult {
	if !isManaged(rc) {
		return pipeline.ResultContinue()
	}
	native := capbudget.NativeCapsRequired(rc)
	var amount int64
	if native {
		q, ok := capbudget.NativeQuoteFor(rc)
		if !ok {
			return pipeline.ResultError(nativeGuardUnavailable())
		}
		var ready bool
		amount, ready = q.ManagedHoldMicros()
		if !ready || p.guard == nil || p.estimate == nil {
			return pipeline.ResultError(nativeGuardUnavailable())
		}
		if measured := p.estimate(rc); measured != amount || measured < 0 {
			return pipeline.ResultError(nativeGuardUnavailable())
		}
	} else {
		amount = p.estimate(rc)
	}
	org, requestID := orgIDOf(rc), rc.RequestID
	if apiErr := p.guard.Admit(ctx, org, rc.Metadata["auth_key_id"], amount, requestID); apiErr != nil {
		return pipeline.ResultError(apiErr)
	}
	p.admitted.Store(requestID, org)
	rc.AddFinalizer(func() { p.finish(rc, requestID) })
	if native {
		if err := capbudget.MarkNativeSpendGuardReady(rc, amount); err != nil {
			return pipeline.ResultError(err)
		}
	}
	return pipeline.ResultContinue()
}

func nativeGuardUnavailable() *models.APIError {
	return &models.APIError{Status: 503, Type: models.ErrTypeInvalidRequest, Code: "budget_state_unavailable", Message: "native managed quote unavailable"}
}

// ProcessResponse corrects an admitted request's estimate in the windows.
func (p *Plugin) ProcessResponse(_ context.Context, rc *models.RequestContext) pipeline.PluginResult {
	p.finish(rc, rc.RequestID)
	return pipeline.ResultContinue()
}

// finish settles the admitted request at what it was billed, or voids it
// when nothing was billed (an error, a refusal after admission, a cache
// hit), once. It runs on its own context: the request's may be done.
func (p *Plugin) finish(rc *models.RequestContext, requestID string) {
	org, ok := p.admitted.LoadAndDelete(requestID)
	if !ok {
		return
	}
	ctx := context.Background()
	if billed := rc.Billing.BilledMicros; billed > 0 && !rc.Flags.CacheHit {
		p.guard.Settle(ctx, org.(string), requestID, billed)
		return
	}
	p.guard.Void(ctx, org.(string), requestID)
}

// orgIDOf is the org a managed request bills to, as the credits plugin reads
// it.
func orgIDOf(rc *models.RequestContext) string {
	if id := rc.Metadata["org_id"]; id != "" {
		return id
	}
	return rc.Metadata["key_org_id"]
}

// isManaged reports whether rc is billed to its org's managed credit.
func isManaged(rc *models.RequestContext) bool {
	return rc.Metadata["billing_mode"] == models.BillingModeManaged && orgIDOf(rc) != ""
}
