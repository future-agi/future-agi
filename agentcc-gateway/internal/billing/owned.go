package billing

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"time"

	"github.com/futureagi/agentcc-gateway/internal/models"
)

var ErrOwnership = errors.New("billing: owned hold conflict")

// OwnedRequest is a durable request-level journal. Terminal records and attempt
// identities have no TTL. It contains billing evidence only, never request bodies.
type OwnedRequest struct {
	LegacyMicros int64                   `json:"legacy_micros,omitempty"`
	Attempts     map[string]OwnedAttempt `json:"attempts"`
	Dispatched   bool                    `json:"dispatched"`
	Terminal     string                  `json:"terminal,omitempty"`
	Finalizing   string                  `json:"finalizing,omitempty"`
	Record       Settlement              `json:"-"`
	RecordJSON   string                  `json:"record_json,omitempty"`
}
type OwnedAttempt struct {
	Anomaly     string                    `json:"anomaly,omitempty"`
	TokenPriced bool                      `json:"token_priced,omitempty"`
	Summary     *AttemptSummary           `json:"summary,omitempty"`
	Evidence    *models.InclusiveAttempt  `json:"evidence,omitempty"`
	Binding     *models.InclusiveIdentity `json:"binding,omitempty"`
	Model       string                    `json:"model"`
	Micros      int64                     `json:"micros"`
	Expires     int64                     `json:"expires"`
	Dispatched  bool                      `json:"dispatched"`
}

func (o *OwnedRequest) UnmarshalJSON(data []byte) error {
	type wire OwnedRequest
	if err := json.Unmarshal(data, (*wire)(o)); err != nil {
		return err
	}
	if o.RecordJSON != "" {
		return json.Unmarshal([]byte(o.RecordJSON), &o.Record)
	}
	return nil
}
func (o OwnedRequest) AttemptIDs() []string {
	ids := make([]string, 0, len(o.Attempts))
	for id := range o.Attempts {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	return ids
}

// OwnedFingerprint excludes only the delivery timestamp; a replay must retain
// the original identity, money and evidence. There is never a second spend.
func OwnedFingerprint(rec Settlement) string {
	rec.TsMs = 0
	b, _ := json.Marshal(rec)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

// OwnedFinancial projects the financial event onto the existing ledger v1
// drain. Evidence is committed beside it atomically, not sent to a v1 decoder.
func OwnedFinancial(rec Settlement) []string { rec.Dimensional = nil; return rec.Fields() }

func OwnedQuarantines(rec Settlement) []string {
	var out []string
	if rec.Dimensional != nil {
		for _, a := range rec.Dimensional.InclusiveAttempts {
			if a.Anomaly != "" || a.BilledMicros > a.HoldMicros {
				out = append(out, a.Model)
			}
		}
	}
	// The journal contains managed hops only. Still require the credential
	// binding, so neither BYOK nor an undispatched reservation can quarantine
	// or create liability. These entries are evidence, never a second spend.
	for _, a := range rec.ManagedJournal {
		if !a.Dispatched || a.Summary == nil || a.Summary.CredentialType != "managed" {
			continue
		}
		if a.Anomaly != "" || a.Summary.BilledMicros > a.Micros || (a.Evidence != nil && a.Evidence.Anomaly != "") {
			out = append(out, a.Model)
		}
	}
	sort.Strings(out)
	return out
}

// RecoveryRecord conservatively charges every dispatched attempt's full hold.
// Undispatched reservations are released, never billed. Missing identity remains
// pending indefinitely and quarantined; absence is not proof of zero liability.
func (o OwnedRequest) RecoveryRecord(org, request string, now time.Time) Settlement {
	// Terminal preparation already validated and froze the complete financial
	// record. Replay it exactly after a failed catalogue seal or acknowledgement;
	// replacing known provider totals with admission holds would lose money.
	if o.Finalizing != "" {
		return o.Record
	}
	rec := o.Record
	rec.ManagedJournal = o.Attempts
	// Credentials belong to the durable financial identity too, including
	// inclusive failures no longer present in the compacted public log.
	rec.InclusiveBindings = make(map[string]models.InclusiveIdentity, len(o.Attempts))
	for id, a := range o.Attempts {
		if a.Binding != nil {
			rec.InclusiveBindings[id] = *a.Binding
		}
	}
	rec.Kind, rec.OrgID, rec.RequestID, rec.GenerationID = KindSpend, org, request, request
	rec.TsMs, rec.MarkupBps, rec.UsageEstimated = now.UnixMilli(), 500, true
	rec.CostSource = "catalog_max"
	rec.BilledMicros, rec.HoldMicros, rec.UpstreamMicros = 0, 0, 0
	prior := rec.Attempts
	recorded := make(map[string]AttemptSummary, len(prior))
	for _, a := range prior {
		recorded[a.AttemptID] = a
	}
	legacyUnknown := len(prior) == 0
	var legacyBilled int64
	rec.Attempts = nil
	for _, a := range prior {
		if _, ok := o.Attempts[a.AttemptID]; !ok && a.CredentialType == "managed" {
			legacyUnknown = legacyUnknown || a.BilledMicros == 0
			legacyBilled += a.BilledMicros
			rec.Attempts = append(rec.Attempts, a)
			rec.BilledMicros += a.BilledMicros
			rec.UpstreamMicros += a.UpstreamMicros
		}
	}
	rec.HoldMicros += o.LegacyMicros
	if missing := max(int64(0), o.LegacyMicros-legacyBilled); legacyUnknown && missing > 0 {
		up := missing * 100 / 105
		rec.BilledMicros += missing
		rec.UpstreamMicros += up
		rec.Attempts = append(rec.Attempts, AttemptSummary{AttemptID: request + ".legacy", Model: rec.Model, Provider: rec.Provider, CredentialType: "managed", Outcome: "error", BilledMicros: missing, UpstreamMicros: up})
	}
	rec.Dimensional = &DimensionalReceipt{Contract: "openrouter-inclusive-r601-image-1", Model: rec.Model, Endpoint: rec.Provider, Snapshot: rec.CatalogSnapshotID, State: "estimated_pending_reconciliation", Lines: []DimensionalLine{}}
	// Stable ordering is important for concurrent recovery fingerprinting.
	for _, id := range o.AttemptIDs() {
		a := o.Attempts[id]
		rec.HoldMicros += a.Micros
		if !a.Dispatched {
			continue
		}
		summary := AttemptSummary{AttemptID: id, Model: a.Model, Provider: rec.Provider, CredentialType: "managed", Kind: "primary", Outcome: "error", UpstreamMicros: a.Micros * 100 / 105, BilledMicros: a.Micros}
		if metadata, ok := recorded[id]; ok {
			// A zero monetary summary is not proof of zero liability, but its
			// provider, hop kind and outcome remain original attempt evidence.
			summary.Provider, summary.Kind, summary.Outcome = metadata.Provider, metadata.Kind, metadata.Outcome
		}
		var reported *Liability
		if a.Summary != nil {
			summary = *a.Summary
			reported = &Liability{UpstreamMicros: summary.UpstreamMicros, BilledMicros: summary.BilledMicros}
		}
		resolved := ResolveLiability(a.Dispatched, false, true, reported, a.Micros)
		summary.UpstreamMicros, summary.BilledMicros = resolved.UpstreamMicros, resolved.BilledMicros
		rec.BilledMicros += summary.BilledMicros
		rec.UpstreamMicros += summary.UpstreamMicros
		rec.Attempts = append(rec.Attempts, summary)
		if !a.TokenPriced {
			evidence := models.InclusiveAttempt{AttemptID: id, Model: a.Model, HoldMicros: a.Micros, UpstreamMicros: summary.UpstreamMicros, BilledMicros: summary.BilledMicros, Estimated: true, Anomaly: "crash recovery missing generation identity"}
			if a.Evidence != nil {
				evidence = *a.Evidence
			}
			rec.Dimensional.InclusiveAttempts = append(rec.Dimensional.InclusiveAttempts, evidence)
		}
	}
	rec.MarkupMicros = rec.BilledMicros - rec.UpstreamMicros
	return rec
}
