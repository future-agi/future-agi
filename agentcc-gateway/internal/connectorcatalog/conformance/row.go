package conformance

// Row is one check of a case. A row with Skip set does not run: Skip says
// in plain words what it waits on (waiting.go).
type Row struct {
	ID   string `json:"id"`
	Skip string `json:"skip,omitempty"`
}

// Results of a row in a report.
const (
	ResultPass = "pass"
	ResultFail = "fail"
	ResultSkip = "skip"
)

// RowResult is a judged row.
type RowResult struct {
	ID     string `json:"id"`
	Result string `json:"result"`
	// Detail is why a row failed, or the reason a skipped row waits on.
	Detail string `json:"detail,omitempty"`
}

// Row ids that do not depend on a tool or a credential part. A tool's rows
// are <prefix><exposed name>; a credential part's are credential.<in>.<name>.
const (
	rowListAudit    = "list.through_virtual_server"
	rowListPosture  = "list.posture_view"
	rowEra          = "transport.era"
	rowNotForwarded = "credential.inbound_not_forwarded"
	rowNotLogged    = "credential.never_logged"
	rowRecords      = "records.one_per_call"
	rowDrift        = "recording.matches_manifest"
	rowAsk          = "posture.ask"
	rowGeneric      = "posture.generic"
	rowRateLimits   = "rate_limits"
	rowPins         = "pins.snapshot"
	rowUserMode     = "account.user"
	rowK2Runtime    = "runtime.k2"
	rowPartner      = "provider.partner"

	prefixAnnotations = "annotations."
	prefixAllowed     = "call.allowed."
	prefixDenied      = "call.denied."
)
