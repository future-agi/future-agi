package server

import "github.com/futureagi/agentcc-gateway/internal/models"

// latestStreamAttempt freezes the owner once stream admission has succeeded.
// Callers must retain this ID while consuming terminal/error events.
func latestStreamAttempt(rc *models.RequestContext) string {
	attempts := rc.AttemptsSnapshot()
	if len(attempts) == 0 {
		return ""
	}
	return attempts[len(attempts)-1].AttemptID
}

func bindStreamEvidence(rc *models.RequestContext, attemptID string, generation models.GenerationEvidence, usage *models.Usage) {
	rc.UpdateAttempt(attemptID, func(a *models.Attempt) {
		a.Generation.Observe(generation)
		merged := models.MergeUsage(a.Usage, usage)
		if merged != nil {
			copyUsage := *merged
			if merged.Cost != nil {
				cost := *merged.Cost
				copyUsage.Cost = &cost
			}
			a.Usage = &copyUsage
		}
	})
}

// Error evidence is retained even if the error channel wins against the tee.
// Do this before post-plugins, while IDs and provider cost are still original.
func applyStreamFailureEvidence(rc *models.RequestContext, attemptID string, evidence *models.ProviderBillingEvidence) {
	if evidence == nil {
		return
	}
	bindStreamEvidence(rc, attemptID, evidence.Generation, evidence.Usage)
	if evidence.Usage == nil {
		return
	}
	if rc.Response == nil {
		rc.Response = &models.ChatCompletionResponse{}
	}
	rc.Response.Usage = models.MergeUsage(rc.Response.Usage, evidence.Usage)
}
