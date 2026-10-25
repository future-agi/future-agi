package capability

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
	"github.com/futureagi/agentcc-gateway/internal/routing"
)

// newAttempt is the i-th candidate's attempt: kind
// primary, then failover; provider the vendor; model <capability>/<sku>;
// the candidate's credential type.
func newAttempt(rc *models.RequestContext, index, i int, prev error, capability string, cand candidate, now time.Time) models.Attempt {
	a := models.Attempt{
		AttemptID:      models.AttemptID(rc.RequestID, index),
		Index:          index,
		Kind:           models.AttemptPrimary,
		Provider:       cand.vendor,
		Model:          capability + "/" + cand.sku.SKU,
		EndpointID:     "capability:" + capability + ":" + cand.vendor,
		CredentialType: cand.cred.Type,
		StartedAt:      now,
	}
	if i > 0 {
		a.Kind, a.Reason = models.AttemptFailover, attemptReason(prev)
	}
	return a
}

// attemptReason is why the next candidate was tried.
func attemptReason(prev error) string {
	var ve *VendorError
	switch {
	case errors.Is(prev, context.DeadlineExceeded):
		return models.ReasonTimeout
	case errors.As(prev, &ve) && ve.Status == http.StatusTooManyRequests:
		return models.ReasonStatus429
	case errors.As(prev, &ve) && ve.Status >= 500:
		return models.ReasonStatus5xx
	case errors.As(prev, &ve) && ve.Status == 0:
		return models.ReasonNetwork
	}
	return routing.AttemptReason(prev)
}

// refusedAttempt marks a managed attempt whose hold was refused: skipped by
// the gateway, never sent.
func refusedAttempt(a *models.Attempt, refused *models.APIError) {
	a.Outcome, a.ErrorOrigin, a.StatusCode = models.OutcomeSkipped, models.OriginGateway, refused.Status
	a.ErrorClass = models.ErrClassInsufficientCredits
	if refused.Status != http.StatusPaymentRequired {
		a.ErrorClass = models.ErrClassPolicy
	}
	a.ErrorMessage = refused.Message
}

// served prices and bills the attempt that answered, records it and the
// call's usage, and returns the result. A managed attempt settles its hold
// at the billed amount (accruing anything below a micro); a BYOK attempt is
// free and records the upstream cost estimate.
func (c *Core) served(rc *models.RequestContext, call *Call, capability string, cand candidate, req Request,
	a *models.Attempt, resp VendorResponse, holdMicros int64, latency time.Duration, org string) Result {
	units, reported := servedUnits(cand.sku, req, resp)
	upstream, source := c.upstreamOf(cand, units, resp)
	usage := Usage{Provider: cand.vendor, Units: units, CostSource: source,
		Estimated: !reported && source != CostVendorReported && cand.sku.Unit != "request"}
	a.Outcome, a.Billable = models.OutcomeSuccess, true
	if !cand.card.NoPerfDisclosure {
		a.LatencyMs = latency.Milliseconds()
	}
	a.CostSource = attemptCostSource(source)
	a.UsageEstimated = usage.Estimated

	b := callBilling{credentialType: cand.cred.Type, costSource: a.CostSource, estimated: usage.Estimated}
	if cand.managed() {
		billed := c.accrued.take(accrualKey(org, rc.Agent.RunID), withMarkupPico(upstream, c.opts.MarkupBps))
		up := picoToMicrosHalfUp(upstream)
		a.UpstreamCostMicros, a.BilledCostMicros = up, billed
		a.LandedCostMicros, a.ProcessingFeeBps = landedMicros(up, billed, c.opts.ProcessingFeeBps), c.opts.ProcessingFeeBps
		b.mode, b.upstreamMicros, b.billedMicros, b.landedMicros = BillingManaged, up, billed, a.LandedCostMicros
		b.markupBps, b.processingFeeBps = c.opts.MarkupBps, c.opts.ProcessingFeeBps
		usage.BillingMode, usage.UpstreamCostUSD, usage.ChargedUSD = managedMode(cand.card), microsUSD(up), microsUSD(billed)
		// Whatever was billed settles, whatever the hold was: a vendor may
		// report a dearer SKU than the one held (overshoot is allowed).
		rec := settlementOf(rc, org, a, holdMicros, c.opts.MarkupBps)
		if billed > 0 {
			c.settle(org, a.AttemptID, rec)
		} else {
			c.void(org, a.AttemptID, rec)
		}
	} else {
		a.UpstreamCostMicros = picoToMicrosHalfUp(upstream)
		b.mode, b.upstreamMicros = BillingBYOK, a.UpstreamCostMicros
		usage.BillingMode, usage.UpstreamCostUSD = BillingBYOK, microsUSD(a.UpstreamCostMicros)
	}
	if usage.BillingMode == BillingNative {
		usage.CostSource = CostNative
	}
	c.opts.Budget.Record(rc, capability, b.billedMicros)

	call.addAttempt(*a)
	call.served(cand.card, resp.Body, usage, b)
	recordServed(rc, capability, cand, usage.BillingMode)
	if c.cache.cacheable(org, cand) {
		c.cache.put(cacheKey(org, capability, cand, req), cacheEntry{body: resp.Body, units: units, cred: cand.cred.Type})
	}
	res := Result{Provider: cand.vendor, Body: resp.Body, Usage: usage, Attempts: call.attemptsCopy()}
	if req.MCP {
		res.Meta = billingMeta(usage, b.upstreamMicros, b.billedMicros)
	}
	return res
}

// failed records a failed attempt. A managed attempt's hold is settled at
// the rate card when the vendor answered and bills the failure (the card's
// free_on_failure is false, or the vendor says so), and voided otherwise.
func (c *Core) failed(ctx context.Context, rc *models.RequestContext, call *Call, cand candidate, req Request,
	a *models.Attempt, err error, holdMicros int64, latency time.Duration, org string) {
	ve, answered := vendorAnswered(err)
	a.Outcome = models.OutcomeError
	switch {
	case errors.Is(err, context.Canceled) || errors.Is(ctx.Err(), context.Canceled):
		a.Outcome = models.OutcomeCancelled
	case errors.Is(err, context.DeadlineExceeded):
		a.Outcome = models.OutcomeTimeout
	}
	status := 0
	if answered {
		status = ve.Status
	}
	a.StatusCode = status
	a.ErrorClass, a.ErrorOrigin = routing.ClassifyError(err, status)
	a.ErrorMessage = routing.AttemptErrorMessage(err)
	if !cand.card.NoPerfDisclosure {
		a.LatencyMs = latency.Milliseconds()
	}
	b := callBilling{mode: BillingBYOK, credentialType: cand.cred.Type}
	if cand.managed() {
		b.mode = BillingManaged
		billed := answered && (ve.Billed || !cand.sku.FreeOnFailure)
		c.finishFailedHold(rc, cand, req, a, holdMicros, org, billed, &b)
	}
	call.addAttempt(*a)
	call.tried(cand.card, b)
}

// finishFailedHold settles a failed managed attempt the vendor billed, at the
// rate card, or voids its hold.
func (c *Core) finishFailedHold(rc *models.RequestContext, cand candidate, req Request, a *models.Attempt,
	holdMicros int64, org string, billed bool, b *callBilling) {
	if !billed {
		c.void(org, a.AttemptID, settlementOf(rc, org, a, holdMicros, c.opts.MarkupBps))
		return
	}
	upstream := upstreamPico(cand.sku, callUnits(cand.sku, req.Units))
	up := picoToMicrosHalfUp(upstream)
	charged := c.accrued.take(accrualKey(org, rc.Agent.RunID), withMarkupPico(upstream, c.opts.MarkupBps))
	a.Billable, a.CostSource = true, models.CostSourcePriceCard
	a.UpstreamCostMicros, a.BilledCostMicros = up, charged
	a.LandedCostMicros, a.ProcessingFeeBps = landedMicros(up, charged, c.opts.ProcessingFeeBps), c.opts.ProcessingFeeBps
	b.upstreamMicros, b.billedMicros, b.landedMicros = up, charged, a.LandedCostMicros
	b.markupBps, b.processingFeeBps, b.costSource = c.opts.MarkupBps, c.opts.ProcessingFeeBps, models.CostSourcePriceCard
	rec := settlementOf(rc, org, a, holdMicros, c.opts.MarkupBps)
	if charged > 0 {
		c.settle(org, a.AttemptID, rec)
		return
	}
	c.void(org, a.AttemptID, rec)
}

// fromCache serves a per-org cache hit on the first candidate: $0, with
// cost_source cache_hit, and one attempt that is not billable.
func (c *Core) fromCache(rc *models.RequestContext, call *Call, capability string, cand candidate, req Request, org string) (Result, bool) {
	if !c.cache.cacheable(org, cand) {
		return Result{}, false
	}
	e, ok := c.cache.get(cacheKey(org, capability, cand, req))
	if !ok {
		return Result{}, false
	}
	a := newAttempt(rc, call.nextIndex(rc), 0, nil, capability, cand, c.opts.Now())
	a.CredentialType, a.Outcome = e.cred, models.OutcomeSuccess
	mode := BillingBYOK
	if e.cred == models.CredentialManaged {
		mode = managedMode(cand.card)
	}
	usage := Usage{Provider: cand.vendor, Units: e.units, BillingMode: mode, CostSource: CostCacheHit}
	call.addAttempt(a)
	call.served(cand.card, e.body, usage, callBilling{mode: billingModeOf(e.cred), credentialType: e.cred})
	recordServed(rc, capability, cand, mode)
	res := Result{Provider: cand.vendor, Body: e.body, Usage: usage, Attempts: call.attemptsCopy()}
	if req.MCP {
		res.Meta = billingMeta(usage, 0, 0)
	}
	return res, true
}

// servedUnits are the units a served call is priced on: what the vendor
// reported, else what the request asked for (at least min_units).
func servedUnits(sku SKU, req Request, resp VendorResponse) ([]Unit, bool) {
	if len(resp.Units) > 0 {
		return resp.Units, true
	}
	return []Unit{{SKU: sku.SKU, Count: callUnits(sku, req.Units)}}, false
}

// upstreamOf is the served call's upstream cost in picodollars, and where it
// came from: the vendor's reported cost for a SKU priced from the result's
// meta, else the units at the card's rate.
func (c *Core) upstreamOf(cand candidate, units []Unit, resp VendorResponse) (int64, string) {
	if cand.sku.CostFromResultMeta && resp.CostUSD != nil {
		return usdToPico(*resp.CostUSD), CostVendorReported
	}
	var total int64
	for _, u := range units {
		sku := cand.sku
		if u.SKU != "" && u.SKU != sku.SKU {
			for _, s := range cand.card.SKUs {
				if s.SKU == u.SKU {
					sku = s
				}
			}
		}
		total += upstreamPico(sku, u.Count)
	}
	return total, CostRateCard
}

// managedMode is a managed call's billing mode: native for a FutureAGI-owned
// service, managed for a vendor.
func managedMode(card *Card) string {
	if card.ResaleStatus == ResaleNative {
		return BillingNative
	}
	return BillingManaged
}

func billingModeOf(t models.CredentialType) string {
	if t == models.CredentialManaged {
		return BillingManaged
	}
	return BillingBYOK
}

// attemptCostSource maps a usage cost source to the attempt's enum.
func attemptCostSource(source string) models.CostSource {
	if source == CostVendorReported {
		return models.CostSourceProviderReported
	}
	return models.CostSourcePriceCard
}

func (c *Call) attemptsCopy() []models.Attempt {
	c.mu.Lock()
	defer c.mu.Unlock()
	return append([]models.Attempt(nil), c.attempts...)
}
