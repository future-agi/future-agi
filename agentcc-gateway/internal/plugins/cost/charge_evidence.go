package cost

import "github.com/futureagi/agentcc-gateway/internal/models"

// finalizeChargeEvidence is a display-only projection of the same finalized
// attempts the ledger uses. It neither prices again nor changes settlement.
func finalizeChargeEvidence(rc *models.RequestContext, attempts []models.Attempt, costs []attemptCost) {
	evidence := models.ChargeEvidence{Finalized: true, Known: true}
	for i, c := range costs {
		a := attempts[i]
		if a.InFlight || (!c.priced && (a.Outcome == models.OutcomeCancelled || a.Outcome == models.OutcomeTimeout)) {
			evidence.Known = false
		}
		if !c.billable {
			// The ledger bills this attempt nothing; that alone does not prove
			// its provider charged nothing. Only a non-serving attempt is left
			// unbilled without a price (priceAttempt), so its own usage is all
			// the evidence there is.
			if !c.priced && !provenNoCharge(&a, attemptUsage(rc, &a, false)) {
				evidence.Known = false
			}
			continue
		}
		evidence.Billable = true
		// An empty source on a billable serving attempt is the existing measured
		// zero-usage case. Unpriced charges always retain their canonical source.
		if !c.priced && c.source != "" {
			evidence.Known = false
		}
		if c.managed {
			evidence.Micros += c.billed
		} else {
			evidence.Micros += c.upstream
		}
	}
	rc.Billing.Charge = evidence
}

// provenNoCharge reports whether an unbilled attempt is a known zero: it was
// never dispatched, or its provider explicitly measured zero tokens. A
// dispatched attempt whose answer carried no usage and no cost, such as a 200
// whose body could not be read, is unknown, not free.
func provenNoCharge(a *models.Attempt, usage *models.Usage) bool {
	if a.Outcome == models.OutcomeSkipped || a.Outcome == models.OutcomeCircuitOpen {
		return true
	}
	return usage.HasPromptTokens() && usage.HasCompletionTokens() && usage.PromptTokens+usage.CompletionTokens == 0
}
