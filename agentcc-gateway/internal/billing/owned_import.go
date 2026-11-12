package billing

import "encoding/json"

// ImportJournal mirrors the durable transaction's transition from a legacy
// aggregate hold. No hold is acquired twice. Completed evidence is refreshed,
// while the dispatch identity and bound of an existing attempt cannot change.
func (o *OwnedRequest) ImportJournal(rec Settlement, held int64) error {
	for id, prior := range rec.ManagedJournal {
		if current, ok := o.Attempts[id]; ok && (current.Model != prior.Model || current.Micros != prior.Micros || current.TokenPriced != prior.TokenPriced) {
			return ErrOwnership
		}
	}
	for id, prior := range rec.ManagedJournal {
		if current, ok := o.Attempts[id]; ok {
			if prior.Anomaly != "" {
				current.Anomaly = prior.Anomaly
			}
			if prior.Summary != nil {
				current.Summary = prior.Summary
			}
			if prior.Evidence != nil {
				current.Evidence = prior.Evidence
			}
			o.Attempts[id] = current
		} else {
			o.Attempts[id] = prior
		}
		if prior.Dispatched {
			o.Dispatched = true
		}
	}
	var sum int64
	for _, a := range o.Attempts {
		sum += a.Micros
	}
	o.LegacyMicros = max(0, held-sum)
	data, _ := json.Marshal(rec)
	_ = json.Unmarshal(data, &o.Record)
	// Detach journal evidence from the request's pointer-bearing snapshots.
	data, _ = json.Marshal(o.Attempts)
	_ = json.Unmarshal(data, &o.Attempts)
	return nil
}
