package budget

import (
	"context"
	"log/slog"
	"net/http"
	"sync/atomic"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/redisstate"
)

type capHold struct {
	admission *Admission
	buckets   []redisstate.CapBucket
	done      atomic.Bool
}

func capStateUnavailable() *models.APIError {
	return &models.APIError{Status: http.StatusServiceUnavailable, Type: models.ErrTypeInvalidRequest,
		Code: "budget_state_unavailable", Message: "durable admission-cap ledger cannot answer: activation missing, state unavailable, stale policy, or an unresolved prior hold; request not admitted"}
}

func (p *Plugin) reserveCaps(ctx context.Context, rc *models.RequestContext) (refusal *models.APIError) {
	a := RequestAdmission(rc)
	defer func() {
		if refusal != nil && refusal.Metadata == nil {
			refusal = a.Refusal(refusal, "", "")
		}
	}()
	if a == nil {
		if !CapRequestChecked(rc) && p.tenantStore != nil && CapsEnabled(p.tenantStore.Get(rc.Metadata["org_id"])) {
			return CapUnenforceable("strict pipeline admission requires trusted central request state")
		}
		return nil
	}
	if !a.quoteReady || (rc.Metadata["auth_key_id"] != "" && rc.Metadata["auth_key_id"] != a.key) || rc.Metadata["org_id"] != a.org {
		return CapUnenforceable("strict admission identity or qualified route is inconsistent")
	}
	if err := RecheckCapChat(rc); err != nil {
		return err
	}
	if p.capStore == nil {
		return a.Refusal(capStateUnavailable(), "", "")
	}
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
	st := &capHold{admission: a, buckets: buckets}
	// Register before Redis, so a lost reserve reply cannot leak an untracked
	// hold. Terminal replay uses the same nonce, independent of caller IDs.
	p.capStates.Store(rc, st)
	rc.AddFinalizer(func() { p.finishCaps(rc); p.capStates.Delete(rc) })
	res, err := p.capStore.Reserve(ctx, a.org, a.period.Format("2006-01-02"), a.nonce, a.revision, buckets, a.deadline.Add(time.Hour))
	if err != nil {
		return a.Refusal(capStateUnavailable(), "daily", "")
	}
	switch res.Status {
	case "ok":
		a.qualified.Store(true)
		return nil
	case "exhausted":
		st.done.Store(true)
		stampRefusal(rc, res.Level, "daily", a.period, "block")
		return a.Refusal(&models.APIError{Status: http.StatusTooManyRequests, Type: models.ErrTypeRateLimit, Code: "org_budget_exceeded",
			Message: "daily model-spend cap cannot admit this request"}, "daily", res.Level)
	default:
		st.done.Store(true)
		return a.Refusal(capStateUnavailable(), "daily", res.Level)
	}
}

func (p *Plugin) finishCaps(rc *models.RequestContext) {
	v, ok := p.capStates.Load(rc)
	if !ok {
		return
	}
	st := v.(*capHold)
	if !st.done.CompareAndSwap(false, true) {
		return
	}
	a := st.admission
	actual, known := a.ActualSpend(rc)
	if !known {
		rc.Metadata["budget_cap_state"] = "uncertain"
		// Fence rather than invent a terminal zero or a wallet charge. Complete
		// remains available for a later authoritative settlement of this nonce.
		// Uncertainty is not terminal: a later finish may carry actual usage.
		defer st.done.Store(false)
		if err := p.capStore.Fence(context.Background(), a.org, a.period.Format("2006-01-02"), a.nonce); err != nil {
			slog.Error("strict cap uncertainty fence unavailable; reservation retained", "org", a.org, "error", err)
		}
		return
	}
	if actual > a.quoteMicros {
		rc.Metadata["budget_cap_estimate_overshot"] = "true"
	}
	terminal := make([]redisstate.CapBucket, len(st.buckets))
	for i, b := range st.buckets {
		terminal[i] = redisstate.CapBucket{Level: b.Level, Amount: actual}
	}
	ctx := context.Background()
	err := p.capStore.Complete(ctx, a.org, a.period.Format("2006-01-02"), a.nonce, terminal)
	if err != nil {
		err = p.capStore.Complete(ctx, a.org, a.period.Format("2006-01-02"), a.nonce, terminal)
	}
	if err != nil {
		// A refused actual (including an out-of-range charge) is not evidence
		// that only the quote was spent. Fence immediately, not at expiry.
		// This is also safe after a lost reply: Fence preserves any terminal.
		defer st.done.Store(false)
		rc.Metadata["budget_cap_state"] = "uncertain"
		slog.Error("strict cap terminal unavailable; fencing reservation", "org", a.org, "error", err)
		if fenceErr := p.capStore.Fence(ctx, a.org, a.period.Format("2006-01-02"), a.nonce); fenceErr != nil {
			slog.Error("strict cap uncertainty fence unavailable; reservation retained", "org", a.org, "error", fenceErr)
		}
	}
}

// SetCapStore permits explicit, synthetic composition and uses the same Redis
// store in production. It does not seed periods or enable a policy.
func (p *Plugin) SetCapStore(store *redisstate.CapHoldStore) { p.capStore = store }
func (p *Plugin) ShouldSkipOnCacheHit() bool                 { return false }
