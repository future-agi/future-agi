package server

import (
	"net/http"
	"strconv"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

// setChargeEvidenceHeaders is deliberately billing-only: errors must not run
// all success hooks, re-price attempts, or repeat settlement. It runs after
// response-header overrides so caller/provider metadata cannot grant certainty.
// Completeness is the finalized per-attempt evidence, never the log's
// compatibility cost_source, which calls a mixed request with a measured-zero
// attempt unpriced; every unpriced billable attempt already leaves it unknown.
func setChargeEvidenceHeaders(w http.ResponseWriter, rc *models.RequestContext) {
	evidence := rc.Billing.Charge
	source := rc.Billing.CostSource
	if rc.Flags.CacheHit {
		evidence = models.ChargeEvidence{Finalized: true, Known: true}
		source = models.CostSourceCacheHit
	}
	known := evidence.Finalized && evidence.Known && evidence.Micros >= 0
	header := w.Header()
	header.Set("x-agentcc-charge-known", strconv.FormatBool(known))
	header.Set("x-agentcc-charge-billable", strconv.FormatBool(evidence.Finalized && evidence.Billable))
	header.Del("x-agentcc-charge")
	if known {
		header.Set("x-agentcc-charge", strconv.FormatFloat(float64(evidence.Micros)/1e6, 'f', 6, 64))
	}
	header.Del("x-agentcc-cost-source")
	if source != "" {
		header.Set("x-agentcc-cost-source", string(source))
	}
}

// A failure at this boundary with no attempt and no served answer is a
// gateway refusal before dispatch, not a guess based on HTTP status or cost.
func setFailedChargeEvidenceHeaders(w http.ResponseWriter, rc *models.RequestContext) {
	if !rc.Billing.Charge.Finalized && rc.AttemptCount() == 0 && rc.Response == nil && !rc.Billing.InclusiveDispatched {
		rc.Billing.Charge = models.ChargeEvidence{Finalized: true, Known: true}
	}
	setChargeEvidenceHeaders(w, rc)
}
