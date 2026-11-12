package budget

import (
	"context"
	"reflect"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

func (p *Plugin) reserveNativeCaps(ctx context.Context, rc *models.RequestContext) *models.APIError {
	if err := p.quoteNative(ctx, rc); err != nil {
		return err
	}
	c := nativeFor(rc)
	c.mu.Lock()
	defer c.mu.Unlock()
	a := c.admission
	var buckets []redisstate.CapBucket
	if a.agentLimit >= 0 {
		buckets = append(buckets, redisstate.CapBucket{Level: "agent:" + a.agent, Limit: a.agentLimit, Amount: a.quoteMicros, ActivatedAt: a.agentActivation})
	}
	if a.keyLimit >= 0 {
		buckets = append(buckets, redisstate.CapBucket{Level: "key_id:" + a.key, Limit: a.keyLimit, Amount: a.quoteMicros, ActivatedAt: a.keyActivation})
	}
	if len(buckets) == 0 {
		a.qualified.Store(true)
		return nil
	}
	if p.capStore == nil {
		return capStateUnavailable()
	}
	st := &capHold{admission: a, buckets: buckets, native: c, nativeOrg: a.org, nativeDay: nativeDay(a.period), nativeNonce: a.nonce, nativeQuote: a.quoteMicros}
	// The pending binding exists BEFORE Reserve: an interrupted/lost reply is
	// not a known-zero hold and cannot escape fallback fencing.
	p.capStates.Store(rc, st)
	rc.AddFinalizer(func() { p.finishNativeDaily(rc, st, true); p.capStates.Delete(rc) })
	res, err := p.capStore.Reserve(ctx, a.org, nativeDay(a.period), a.nonce, a.revision, buckets, a.deadline.Add(time.Hour))
	if err != nil {
		c.failed = true
		return capStateUnavailable()
	}
	if res.Status == "ok" {
		st.nativeResolved = true
		a.qualified.Store(true)
		return nil
	}
	if res.Status == "exhausted" {
		st.nativeResult = NativeSettlement{State: NativeSettled}
		st.done.Store(true)
		stampRefusal(rc, res.Level, "daily", a.period, "block")
		return a.Refusal(&models.APIError{Status: 429, Type: models.ErrTypeRateLimit, Code: "org_budget_exceeded", Message: "daily model-spend cap cannot admit this request"}, "daily", res.Level)
	}
	c.failed = true
	return capStateUnavailable()
}

// InvalidateNative only removes authority. A lost reservation reply anywhere
// in the admission prevents every cap ledger from inferring undispatched zero.
func InvalidateNative(rc *models.RequestContext) {
	if c := nativeFor(rc); c != nil {
		c.mu.Lock()
		c.failed = true
		c.mu.Unlock()
	}
}

func (p *Plugin) FinishNativeCaps(_ context.Context, rc *models.RequestContext) NativeSettlement {
	c := nativeFor(rc)
	if c == nil {
		return NativeSettlement{State: NativeSettlementUnavailable, Error: capStateUnavailable()}
	}
	c.mu.Lock()
	if c.closing || (c.attempt != nil && c.attempt.ending) {
		c.mu.Unlock()
		return NativeSettlement{State: NativeSettlementUnavailable, Error: capStateUnavailable()}
	}
	// Only this explicit AfterBilling adapter opens the gate. It does not
	// call ProcessResponse, cost calculation, wallet debit or credits settlement.
	c.billing = true
	c.mu.Unlock()
	if v, ok := p.capStates.Load(rc); ok {
		return p.finishNativeDaily(rc, v.(*capHold), false)
	}
	if _, known := c.admission.ActualSpend(rc); !known {
		return NativeSettlement{State: NativeFenced, Error: capStateUnavailable()}
	}
	return NativeSettlement{State: NativeSettled}
}

func (p *Plugin) finishNativeDaily(rc *models.RequestContext, st *capHold, finalizer bool) NativeSettlement {
	st.nativeMu.Lock()
	defer st.nativeMu.Unlock()
	if st.done.Load() {
		return st.nativeResult
	}
	if !finalizer && !NativeBillingComplete(rc) {
		return NativeSettlement{State: NativeSettlementUnavailable, Error: capStateUnavailable()}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	a := st.admission
	actual, known := a.ActualSpend(rc)
	if p.capStore == nil {
		return NativeSettlement{State: NativeSettlementUnavailable, Error: capStateUnavailable()}
	}
	if known && st.nativeResolved && actual <= st.nativeQuote {
		terminal := make([]redisstate.CapBucket, len(st.buckets))
		for i, b := range st.buckets {
			terminal[i] = redisstate.CapBucket{Level: b.Level, Amount: actual}
		}
		// Identical same-nonce replay is the existing terminal protocol, not a
		// second debit. Never Complete over-bound usage and then try to fence it.
		err := p.capStore.Complete(ctx, st.nativeOrg, st.nativeDay, st.nativeNonce, terminal)
		if err != nil {
			err = p.capStore.Complete(ctx, st.nativeOrg, st.nativeDay, st.nativeNonce, terminal)
		}
		if err == nil {
			st.nativeResult = NativeSettlement{State: NativeSettled}
			st.done.Store(true)
			return st.nativeResult
		}
	}
	result := NativeSettlement{State: NativeFenced, Error: capStateUnavailable()}
	if p.capStore.Fence(ctx, st.nativeOrg, st.nativeDay, st.nativeNonce) != nil {
		result.State = NativeSettlementUnavailable
	}
	st.nativeResult = result // retry may only replay the same pinned terminal
	return result
}

func nativeActual(rc *models.RequestContext, a *Admission) (int64, bool) {
	c := nativeFor(rc)
	if c == nil {
		return 0, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.rc != rc || c.admission != a || c.failed {
		return 0, false
	}
	if !a.attempted.Load() {
		// No record and no successful claim is positive non-dispatch evidence;
		// unresolved reserves mark failed before any cleanup can use this path.
		return 0, !c.claimed && rc.AttemptCount() == 0
	}
	h := c.attempt
	if !c.billing || h == nil || !h.ended || h.ending || !h.terminal || h.end != NativeCompleted || c.quote == nil || rc.Flags.CacheHit || rc.Billing.UsageEstimated {
		return 0, false
	}
	attempts := rc.AttemptsSnapshot()
	if len(attempts) != 1 {
		return 0, false
	}
	at := attempts[0]
	// Only the expected finish representation is derived. Parsing Attempt.Usage
	// here would hide a sidecar changed after finish; raw/replay evidence stays exact.
	expected, valid := nativeFinishUsage(h.usage)
	if !valid || at.FinancialID != h.financialID || at.InFlight || at.Provider != c.binding.ProviderID || at.Model != c.binding.SentModel || at.CredentialType != c.binding.CredentialType || at.Generation != h.generation || at.Generation.Conflict || at.UsageEstimated || !nativeUsageValid(at.Usage) || !reflect.DeepEqual(at.Usage, expected) {
		return 0, false
	}
	switch at.CostSource {
	case models.CostSourceProviderReported, models.CostSourceCatalog, models.CostSourceCatalogMax, models.CostSourcePriceCard, models.CostSourceModelDB, models.CostSourceOrgCustom:
	default:
		return 0, false
	}
	actual := at.UpstreamCostMicros
	if a.managed {
		actual = at.BilledCostMicros
	}
	if actual < 0 || actual > nativeMaxMicros || actual > c.quote.bound.capMicros {
		return 0, false
	}
	return actual, true
}
