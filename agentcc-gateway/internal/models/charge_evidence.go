package models

// ChargeEvidence is display-only, finalized whole-request charge evidence.
// Billable uses the ledger's attempt definition, not HTTP success or a positive
// amount. Micros is what the organization pays, never a margin breakdown.
// The zero value deliberately proves neither a charge nor a free request.
type ChargeEvidence struct {
	Finalized bool
	Known     bool
	Billable  bool
	Micros    int64
}
