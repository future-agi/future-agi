package billing

import (
	"encoding/json"
	"strconv"
)

// Settlement kinds (managed-settlement-v1 "kind").
const (
	KindSpend = "spend"
	KindVoid  = "void"
)

// SettlementVersion is managed-settlement-v1's "v".
const SettlementVersion = "1"

// costSourceUnknown is written when the caller recorded no cost source: the
// record's cost_source is an enum, so it is never empty.
const costSourceUnknown = "unpriced"

// Settlement is one managed-settlement-v1 outbox record, built by its caller
// (the credits plugin from a request, or a caller outside the pipeline from
// its own struct). The Ledger sets Kind, and zeroes the amounts of a void.
type Settlement struct {
	Kind         string
	RequestID    string
	GenerationID string
	OrgID        string
	APIKeyID     string

	BilledMicros     int64
	UpstreamMicros   int64
	MarkupMicros     int64
	MarkupBps        int
	HoldMicros       int64
	LandedMicros     int64
	UpstreamFeeBps   int
	ProcessingFeeBps int

	ServedUpstreamProvider string
	// UpstreamKeyID is the key_hash of the per-org upstream key. Never the key.
	UpstreamKeyID string

	Model          string
	Provider       string
	EndpointID     string
	CostSource     string
	UsageEstimated bool
	Attempts       []AttemptSummary

	CatalogSnapshotID string
	PolicyVersion     string
	AgentRunID        string
	AgentID           string
	TsMs              int64
}

// AttemptSummary is one entry of the record's compact attempts JSON.
type AttemptSummary struct {
	AttemptID      string `json:"attempt_id"`
	Kind           string `json:"kind"`
	Provider       string `json:"provider"`
	Model          string `json:"model"`
	CredentialType string `json:"credential_type"`
	UpstreamMicros int64  `json:"upstream_micros"`
	BilledMicros   int64  `json:"billed_micros"`
	Outcome        string `json:"outcome"`
}

// asVoid is s as a void record: kind void and zero amounts. The hold it
// released is kept in hold_micros.
func (s Settlement) asVoid() Settlement {
	s.Kind = KindVoid
	s.BilledMicros, s.UpstreamMicros, s.MarkupMicros, s.LandedMicros = 0, 0, 0, 0
	return s
}

// asSpend is s as a spend record.
func (s Settlement) asSpend() Settlement {
	s.Kind = KindSpend
	return s
}

// Fields returns the record exactly as XADD writes it: field, value pairs in
// a fixed order, every value a string (integers in decimal, usage_estimated
// "0" or "1", attempts compact JSON). Negative amounts are written as 0.
func (s Settlement) Fields() []string {
	costSource := s.CostSource
	if costSource == "" {
		costSource = costSourceUnknown
	}
	generationID := s.GenerationID
	if generationID == "" {
		generationID = s.RequestID
	}
	estimated := "0"
	if s.UsageEstimated {
		estimated = "1"
	}
	return []string{
		"v", SettlementVersion,
		"kind", s.Kind,
		"request_id", s.RequestID,
		"generation_id", generationID,
		"org_id", s.OrgID,
		"api_key_id", s.APIKeyID,
		"billed_micros", nonNeg(s.BilledMicros),
		"upstream_micros", nonNeg(s.UpstreamMicros),
		"markup_micros", nonNeg(s.MarkupMicros),
		"markup_bps", nonNeg(int64(s.MarkupBps)),
		"hold_micros", nonNeg(s.HoldMicros),
		"landed_micros", nonNeg(s.LandedMicros),
		"upstream_fee_bps", nonNeg(int64(s.UpstreamFeeBps)),
		"processing_fee_bps", nonNeg(int64(s.ProcessingFeeBps)),
		"served_upstream_provider", s.ServedUpstreamProvider,
		"upstream_key_id", s.UpstreamKeyID,
		"model", s.Model,
		"provider", s.Provider,
		"endpoint_id", s.EndpointID,
		"cost_source", costSource,
		"usage_estimated", estimated,
		"attempts", attemptsJSON(s.Attempts),
		"catalog_snapshot_id", s.CatalogSnapshotID,
		"policy_version", s.PolicyVersion,
		"agent_run_id", s.AgentRunID,
		"agent_id", s.AgentID,
		"ts_ms", nonNeg(s.TsMs),
	}
}

// FieldMap is Fields as a map, the shape a stream entry reads back as.
func (s Settlement) FieldMap() map[string]string {
	f := s.Fields()
	out := make(map[string]string, len(f)/2)
	for i := 0; i+1 < len(f); i += 2 {
		out[f[i]] = f[i+1]
	}
	return out
}

func nonNeg(v int64) string {
	if v < 0 {
		v = 0
	}
	return strconv.FormatInt(v, 10)
}

// attemptsJSON is the compact attempts array; never null.
func attemptsJSON(a []AttemptSummary) string {
	if len(a) == 0 {
		return "[]"
	}
	out := make([]AttemptSummary, len(a))
	for i, s := range a {
		if s.UpstreamMicros < 0 {
			s.UpstreamMicros = 0
		}
		if s.BilledMicros < 0 {
			s.BilledMicros = 0
		}
		out[i] = s
	}
	b, err := json.Marshal(out)
	if err != nil {
		return "[]"
	}
	return string(b)
}
