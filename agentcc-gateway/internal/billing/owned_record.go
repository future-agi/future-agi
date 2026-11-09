package billing

import "github.com/futureagi/agentcc-gateway/internal/models"

// CompleteRecord restores evidence that has left the bounded public log from
// the durable journal. It does not infer a zero charge from an absent log row.
// The caller first seals its current evidence; all terminal ownership, binding,
// amount and completeness validation still runs in the ledger transaction.
func (o OwnedRequest) CompleteRecord(rec Settlement) (Settlement, error) {
	if rec.ManagedJournal == nil {
		rec.ManagedJournal = make(map[string]OwnedAttempt)
	}
	if rec.InclusiveBindings == nil {
		rec.InclusiveBindings = make(map[string]models.InclusiveIdentity)
	}
	var retained []AttemptSummary
	for _, id := range o.AttemptIDs() {
		a := o.Attempts[id]
		if _, present := rec.ManagedJournal[id]; present {
			continue
		}
		if !a.Dispatched {
			continue // unused reservations are released, never charged
		}
		if a.Summary == nil || a.Summary.AttemptID != id || a.Summary.Model != a.Model || a.Summary.CredentialType != "managed" {
			return Settlement{}, ErrOwnership
		}
		if !a.TokenPriced && a.Evidence == nil {
			return Settlement{}, ErrOwnership
		}
		if a.Evidence != nil && (a.Evidence.AttemptID != id || a.Evidence.Model != a.Model || rec.Dimensional == nil) {
			return Settlement{}, ErrOwnership
		}
		rec.ManagedJournal[id] = a
		retained = append(retained, *a.Summary)
		rec.BilledMicros += a.Summary.BilledMicros
		rec.UpstreamMicros += a.Summary.UpstreamMicros
		if a.Evidence != nil {
			rec.Dimensional.InclusiveAttempts = append(rec.Dimensional.InclusiveAttempts, *a.Evidence)
			rec.UsageEstimated = rec.UsageEstimated || a.Evidence.Estimated
		}
		if a.Binding != nil {
			rec.InclusiveBindings[id] = *a.Binding
		}
	}
	rec.Attempts = append(retained, rec.Attempts...)
	rec.MarkupMicros = rec.BilledMicros - rec.UpstreamMicros
	return rec, nil
}
