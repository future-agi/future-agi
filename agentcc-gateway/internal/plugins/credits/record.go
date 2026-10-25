package credits

import (
	"time"

	"github.com/futureagi/agentcc-gateway/internal/billing"
	"github.com/futureagi/agentcc-gateway/internal/models"
)

// settlementOf is rc's managed-settlement-v1 record, from what routing,
// pricing and the hold recorded on rc.Billing. The generation id is the
// request id. The Ledger sets the kind.
func settlementOf(rc *models.RequestContext, org string) billing.Settlement {
	b := &rc.Billing
	return billing.Settlement{
		RequestID:              rc.RequestID,
		GenerationID:           rc.RequestID,
		OrgID:                  org,
		APIKeyID:               rc.Metadata["auth_key_id"],
		BilledMicros:           b.BilledMicros,
		UpstreamMicros:         b.UpstreamMicros,
		MarkupMicros:           b.MarkupMicros,
		MarkupBps:              b.MarkupBps,
		HoldMicros:             b.HoldMicros,
		LandedMicros:           b.LandedMicros,
		UpstreamFeeBps:         b.UpstreamFeeBps,
		ProcessingFeeBps:       b.ProcessingFeeBps,
		ServedUpstreamProvider: b.ServedUpstreamProvider,
		UpstreamKeyID:          b.UpstreamKeyID,
		Model:                  modelOf(rc),
		Provider:               rc.Provider,
		EndpointID:             b.EndpointID,
		CostSource:             string(b.CostSource),
		UsageEstimated:         b.UsageEstimated,
		Attempts:               attemptSummaries(rc.AttemptsSnapshot()),
		CatalogSnapshotID:      rc.Evidence.CatalogSnapshotID,
		PolicyVersion:          rc.Evidence.PolicyVersion,
		AgentRunID:             rc.Agent.RunID,
		AgentID:                rc.Agent.AgentID,
		TsMs:                   time.Now().UnixMilli(),
	}
}

// attemptSummaries are the record's compact attempts.
func attemptSummaries(attempts []models.Attempt) []billing.AttemptSummary {
	if len(attempts) == 0 {
		return nil
	}
	out := make([]billing.AttemptSummary, 0, len(attempts))
	for _, a := range attempts {
		out = append(out, billing.AttemptSummary{
			AttemptID:      a.AttemptID,
			Kind:           string(a.Kind),
			Provider:       a.Provider,
			Model:          a.Model,
			CredentialType: string(a.CredentialType),
			UpstreamMicros: a.UpstreamCostMicros,
			BilledMicros:   a.BilledCostMicros,
			Outcome:        string(a.Outcome),
		})
	}
	return out
}
