package agentbudget

import (
	"context"
	"math"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	capbudget "github.com/futureagi/agentcc-gateway/internal/plugins/budget"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

func nativeRunUnavailable() *models.APIError {
	return &models.APIError{Status: 503, Type: models.ErrTypeInvalidRequest, Code: "budget_state_unavailable", Message: "native run admission state unavailable"}
}

func nativeRunCheck(rc *models.RequestContext) *models.APIError {
	if !capbudget.NativeControlled(rc) || capbudget.RequestAdmission(rc) == nil {
		return nativeRunUnavailable()
	}
	if _, ok := capbudget.NativeRunScope(rc); !ok {
		return nativeRunUnavailable()
	}
	for _, p := range []*float64{rc.Agent.MaxCostUSD, rc.Agent.BudgetUSD, rc.Agent.BudgetRemainingUSD, rc.Billing.CallCapUSD} {
		if p != nil && (math.IsNaN(*p) || math.IsInf(*p, 0) || *p < 0 || *p > 1_000_000_000) {
			return capbudget.CapUnenforceable("native call ceiling is invalid")
		}
	}
	return nil
}

func (p *Plugin) holdNative(ctx context.Context, rc *models.RequestContext, st *runState, est estimate) *models.APIError {
	if !est.priced || est.micros < 0 || est.micros > 1_000_000_000_000_000 || st.strict == nil {
		return nativeRunUnavailable()
	}
	if st.scope == "" {
		// A verified unscoped per-call ceiling has no run bucket to invent.
		if _, ok := capbudget.NativeRunScope(rc); !ok {
			return nativeRunUnavailable()
		}
		return nil
	}
	if p.deps.Store == nil {
		return nativeRunUnavailable()
	}
	st.nativeMu.Lock()
	defer st.nativeMu.Unlock()
	if st.nativePending || st.reserved {
		return nativeRunUnavailable()
	}
	// begin already registered exit; pending is set before Redis. A lost
	// reserve reply must be fenced with this same server nonce, never voided.
	st.nativePending, st.estMicros, st.estPriced = true, est.micros, true
	res, err := p.deps.Store.ReserveStrict(ctx, st.org, st.scope, st.holdID, est.micros, st.limitMicros, time.Until(st.strict.Deadline())+time.Hour, st.pol.scopeTTL)
	if err != nil {
		capbudget.InvalidateNative(rc)
		return nativeRunUnavailable()
	}
	switch res.Status {
	case redisstate.AgentBudgetOK:
		st.reserved, st.nativeResolved = true, true
		st.strict.MarkRunReady()
		if res.HasLimit() {
			recordRemaining(rc, res.RemainingMicros())
		}
		return nil
	case redisstate.AgentBudgetExhausted, redisstate.AgentBudgetStopped:
		st.nativePending = false
		if res.Status == redisstate.AgentBudgetExhausted {
			return st.budgetExhausted(est.micros, res.RemainingMicros(), res.LimitMicros)
		}
		return ErrRunStopped(st.scope, res.StopReason)
	default:
		capbudget.InvalidateNative(rc)
		return nativeRunUnavailable()
	}
}

func (p *Plugin) FinishNativeCaps(_ context.Context, rc *models.RequestContext) capbudget.NativeSettlement {
	if !capbudget.NativeControlled(rc) || !capbudget.NativeBillingComplete(rc) {
		return capbudget.NativeSettlement{State: capbudget.NativeSettlementUnavailable, Error: nativeRunUnavailable()}
	}
	v, ok := p.states.Load(rc)
	if !ok {
		scope, accepted := capbudget.NativeRunScope(rc)
		if !accepted || scope != "" {
			return capbudget.NativeSettlement{State: capbudget.NativeSettlementUnavailable, Error: nativeRunUnavailable()}
		}
		return capbudget.NativeSettlement{State: capbudget.NativeSettled}
	}
	st := v.(*runState)
	if !st.native {
		return capbudget.NativeSettlement{State: capbudget.NativeSettlementUnavailable, Error: nativeRunUnavailable()}
	}
	return p.finishNative(rc, st, false)
}

func (p *Plugin) finishNative(rc *models.RequestContext, st *runState, finalizer bool) capbudget.NativeSettlement {
	st.nativeMu.Lock()
	defer st.nativeMu.Unlock()
	if st.done.Load() {
		return st.nativeResult
	}
	if !finalizer && !capbudget.NativeBillingComplete(rc) {
		return capbudget.NativeSettlement{State: capbudget.NativeSettlementUnavailable, Error: nativeRunUnavailable()}
	}
	if !st.nativePending && !st.reserved {
		return capbudget.NativeSettlement{State: capbudget.NativeSettled}
	}
	if p.deps.Store == nil || st.strict == nil {
		return capbudget.NativeSettlement{State: capbudget.NativeSettlementUnavailable, Error: nativeRunUnavailable()}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cost, known := st.strict.ActualSpend(rc)
	if known && st.nativeResolved && cost >= 0 && cost <= st.estMicros && cost <= 1_000_000_000_000_000 {
		res, err := p.deps.Store.Complete(ctx, st.org, st.scope, st.holdID, cost, st.pol.scopeTTL)
		if err != nil {
			res, err = p.deps.Store.Complete(ctx, st.org, st.scope, st.holdID, cost, st.pol.scopeTTL)
		}
		if err == nil && res.Status == redisstate.AgentBudgetOK {
			st.nativeResult = capbudget.NativeSettlement{State: capbudget.NativeSettled}
			st.done.Store(true)
			if res.HasLimit() {
				recordRemaining(rc, res.RemainingMicros())
			}
			return st.nativeResult
		}
	}
	// Unknown reserves, partial usage, over-bound actual and failed cleanup
	// retain/fence. Never Complete first and attempt to quarantine a terminal.
	result := capbudget.NativeSettlement{State: capbudget.NativeFenced, Error: nativeRunUnavailable()}
	if p.deps.Store.FenceStrict(ctx, st.org, st.scope, st.holdID) != nil {
		result.State = capbudget.NativeSettlementUnavailable
	}
	st.nativeResult = result
	return result
}
