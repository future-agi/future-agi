package cost

import (
	"encoding/json"
	"math"
	"strings"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/billing/inclusive"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// OwnedLiability normalizes the attempt once for the shared liability table.
// G7 and journal snapshots both use this function; recovery consumes the saved
// resolution, or the same table's full hold when dispatch never returned.
func OwnedLiability(a *models.Attempt, u *models.Usage, card models.PriceCard, markupBps int) (billing.Liability, *models.InclusiveAttempt) {
	hold := int64(0)
	if a.Pricing != nil {
		hold = a.Pricing.HoldMicros
		if card.Inclusive != nil {
			hold = a.Pricing.InclusiveHoldMicros
		}
	}
	dispatched := a.Outcome != models.OutcomeSkipped && a.Outcome != models.OutcomeCircuitOpen
	evidence := ownedBillingEvidence(a, u)
	var reported *billing.Liability
	var receipt *models.InclusiveAttempt
	if card.Inclusive != nil {
		r := inclusive.Price(u, hold)
		r.AttemptID, r.Model, r.Identity = a.FinancialID, a.Model, a.InclusiveIdentity
		r.GenerationID, r.ServedModel = a.Generation.ID, a.Generation.ServedModel
		if r.GenerationID == "" {
			r.GenerationID = a.UpstreamGenerationID
		}
		if r.ServedModel == "" {
			r.ServedModel = a.ServedModel
		}
		if a.Generation.Conflict {
			r.GenerationID = ""
			r.Estimated = true
			r.Anomaly = "conflicting generation identity; reconciliation requires review"
		}
		if card.Inclusive.RouterUniverse != "" && !inclusive.SnapshotContains(card.Inclusive.SnapshotModels, r.ServedModel) {
			r.Estimated = true
			r.Anomaly = "served model outside admission snapshot; full hold pending review"
		}
		if r.GenerationID == "" {
			r.Anomaly = "upstream generation identity missing; listing quarantined"
		}
		if !r.Estimated {
			reported = &billing.Liability{UpstreamMicros: r.UpstreamMicros, BilledMicros: r.BilledMicros}
		}
		receipt = &r
	} else if u != nil && !u.CostContested() &&
		(u.HasReportedCost() || ownedNumericCost(u.CostDecimal) || (!u.Estimated && !a.UsageEstimated && u.HasPromptTokens() && u.HasCompletionTokens() && !ownedToolEvidence(u.ServerToolUse))) {
		up, billed, _, _, ok := PriceOwnedAttempt(a, u, card, markupBps, 0)
		if ok {
			reported = &billing.Liability{UpstreamMicros: up, BilledMicros: billed}
		}
	}
	result := billing.ResolveLiability(dispatched, a.ProviderRejected, evidence, reported, hold)
	if receipt != nil {
		receipt.UpstreamMicros, receipt.BilledMicros, receipt.Estimated = result.UpstreamMicros, result.BilledMicros, result.Estimated
		if result.Estimated {
			receipt.ProviderTotal = ""
		}
		if result.Released {
			receipt.Anomaly, receipt.ProviderTotal = "", ""
		}
	}
	return result, receipt
}

func ownedBillingEvidence(a *models.Attempt, u *models.Usage) bool {
	if a.Generation.ID != "" || a.Generation.Conflict || a.UpstreamGenerationID != "" ||
		a.PromptTokens != 0 || a.CompletionTokens != 0 || a.CachedTokens != 0 || a.CacheWriteTokens != 0 || a.ReasoningTokens != 0 {
		return true
	}
	if u == nil {
		return false
	}
	v := *u
	v.ParseDetails()
	if v.PromptTokens != 0 || v.CompletionTokens != 0 || v.TotalTokens != 0 ||
		v.PromptDetails.CachedTokens != 0 || v.PromptDetails.CacheWriteTokens != 0 || v.PromptDetails.AudioTokens != 0 || v.CompletionDetails.ReasoningTokens != 0 || ownedToolEvidence(v.ServerToolUse) {
		return true
	}
	// A numeric wire value is evidence even if outside the supported monetary
	// range. Null, a quoted string, and an empty field are not numeric evidence.
	if v.CostDecimal != "" {
		if ownedNumericCost(v.CostDecimal) {
			return true
		}
	} else if v.Cost != nil && !math.IsNaN(*v.Cost) && !math.IsInf(*v.Cost, 0) {
		return true
	}
	return false
}

func ownedToolEvidence(u *models.ServerToolUsage) bool {
	return u != nil && (u.WebSearchRequests != 0 || u.WebFetchRequests != 0 || u.CodeExecutionRequests != 0 ||
		u.WebSearchPreviewRequests != 0 || u.FileSearchRequests != 0 || u.WebSearchQueries != 0 ||
		u.GroundedPrompts != 0 || u.MapsGroundedPrompts != 0 || len(u.Containers) != 0)
}

func ownedNumericCost(raw string) bool {
	raw = strings.TrimSpace(raw)
	if raw == "" || raw[0] == '"' {
		return false
	}
	var number json.Number
	return json.Unmarshal([]byte(raw), &number) == nil && number != ""
}
